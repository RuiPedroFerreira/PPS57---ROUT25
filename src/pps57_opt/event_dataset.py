#!/usr/bin/env python3
"""Build learning rows and scenarios from C-ITS/TSP event logs.

Lê JSONL no shape ETSI-aligned actual (`MessageType.SREM/SPATEM/...`). Logs
gerados pela versão v0.3 do protocolo (`SREM_like`/`SPATEM_like` com campos
top-level) **não são** compatíveis e devem ser regenerados.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import json

from pps57_cits.messages import (
    BasicVehicleRole,
    MessageType,
    OperatorPriorityClass,
    OperatorTelemetry,
    Position3D,
    Requestor,
    RequestType,
    SREMLike,
    SecurityEnvelope,
    SignalRequest,
    StationType,
    build_security_envelope,
    derive_station_id,
)
from pps57_cits.models import SignalState

from .models import OfflineScenario


def build_event_training_rows(
    *,
    cits_log: str | Path,
    decision_log: str | Path,
    actuation_log: str | Path,
) -> List[Dict[str, Any]]:
    cits_rows = list(_read_jsonl(Path(cits_log)))
    # Os SREMs novos têm `correlation_token` derivado de
    # (station_id, request_id, sequence_number) — usamos como chave.
    requests: Dict[str, Dict[str, Any]] = {}
    for item in cits_rows:
        if item.get("message_type") != MessageType.SREM.value:
            continue
        token = _correlation_token_from_payload(item)
        if token:
            requests[token] = item
    spatem_rows = [item for item in cits_rows if item.get("message_type") == MessageType.SPATEM.value]
    decisions = [
        item
        for item in _read_jsonl(Path(decision_log))
        if item.get("request_id")
    ]
    actuations_by_decision = {
        str(item.get("decision_id")): item
        for item in _read_jsonl(Path(actuation_log))
        if item.get("decision_id")
    }

    rows: List[Dict[str, Any]] = []
    for decision in decisions:
        request = requests.get(str(decision.get("request_id")), {})
        actuation = actuations_by_decision.get(str(decision.get("decision_id")), {})
        signal_state = _latest_spatem(spatem_rows, str(decision.get("tls_id")), _float(decision.get("timestamp_s")))
        rows.append(
            {
                "request_id": decision.get("request_id"),
                "decision_id": decision.get("decision_id"),
                "timestamp_s": decision.get("timestamp_s"),
                "vehicle_id": decision.get("vehicle_id"),
                "tls_id": decision.get("tls_id"),
                "action": decision.get("action"),
                "status": decision.get("status"),
                "reason": decision.get("reason"),
                "priority_score": decision.get("priority_score"),
                "eta_to_stopline_s": decision.get("eta_to_stopline_s"),
                "schedule_delay_s": decision.get("schedule_delay_s"),
                "headway_deviation_s": decision.get("headway_deviation_s"),
                "current_phase_index": decision.get("current_phase_index"),
                "current_signal_state": decision.get("current_signal_state"),
                "applied": bool(actuation.get("applied", False)),
                "actuation_reason": actuation.get("reason", ""),
                "source_message_type": request.get("message_type", ""),
                "expires_at_s": request.get("expires_at_s"),
                "request": request,
                "signal_state": signal_state,
                "network_state": _network_state_from_notes(decision.get("notes", [])),
            }
        )
    return rows


def write_event_training_dataset(
    *,
    cits_log: str | Path,
    decision_log: str | Path,
    actuation_log: str | Path,
    output_path: str | Path,
) -> Dict[str, object]:
    rows = build_event_training_rows(
        cits_log=cits_log,
        decision_log=decision_log,
        actuation_log=actuation_log,
    )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n", encoding="utf-8")
    return {
        "row_count": len(rows),
        "output_path": str(output),
        "source_cits_log": str(cits_log),
        "source_decision_log": str(decision_log),
        "source_actuation_log": str(actuation_log),
    }


def load_event_training_scenarios(path: str | Path) -> List[OfflineScenario]:
    """Load optimization/RL scenarios from SUMO/TraCI event-derived rows.

    Strict loader: rows sem o SREM original ou sem o estado SPATEM-derivado
    são saltadas; dataset vazio é rejeitado pelos controllers. Isto previne
    fallback silencioso para dados sintéticos.
    """

    raw_rows = list(_read_jsonl(Path(path)))
    raw_rows.sort(key=lambda row: _float(row.get("timestamp_s") or 0.0))
    last_applied_intervention_by_tls: Dict[str, float] = {}
    intervention_actions = {"green_extension", "early_green"}

    scenarios: List[OfflineScenario] = []
    for index, row in enumerate(raw_rows):
        request_payload = row.get("request") if isinstance(row.get("request"), dict) else {}
        signal_payload = row.get("signal_state") if isinstance(row.get("signal_state"), dict) else {}
        network_state = row.get("network_state") if isinstance(row.get("network_state"), dict) else {}
        if not request_payload or not signal_payload or not network_state:
            _maybe_record_intervention(row, last_applied_intervention_by_tls, intervention_actions)
            continue
        request = _srem_from_payload(request_payload)
        signal_state = _signal_state_from_payload(signal_payload, request)
        scenario_id = str(row.get("decision_id") or row.get("request_id") or f"event_{index}")
        timestamp_s = _float(row.get("timestamp_s"))
        tls_id = str(row.get("tls_id") or request.tls_id)
        last_intervention = last_applied_intervention_by_tls.get(tls_id)
        seconds_since = None if last_intervention is None else max(0.0, timestamp_s - last_intervention)
        scenarios.append(
            OfflineScenario(
                scenario_id=f"EVENT_{scenario_id}",
                description="SUMO/TraCI event-derived TSP decision context.",
                expected_case=str(row.get("action") or "event_observation"),
                sim_time_s=timestamp_s,
                request=request,
                signal_state=signal_state,
                active_request_count=int(network_state["active_request_count"]),
                queue_vehicle_count=int(network_state["queue_vehicle_count"]),
                halted_vehicle_count=int(network_state["halted_vehicle_count"]),
                mean_speed_mps=float(network_state["mean_speed_mps"]),
                waiting_time_s=float(network_state["waiting_time_s"]),
                occupancy=float(network_state["occupancy"]),
                spillback_risk=bool(network_state["spillback_risk"]),
                seconds_since_last_intervention_s=seconds_since,
            )
        )
        _maybe_record_intervention(row, last_applied_intervention_by_tls, intervention_actions)
    return scenarios


def _maybe_record_intervention(
    row: Dict[str, Any],
    tracker: Dict[str, float],
    intervention_actions: set[str],
) -> None:
    if not bool(row.get("applied")):
        return
    action = str(row.get("action") or "")
    if action not in intervention_actions:
        return
    tls_id = str(row.get("tls_id") or "")
    if not tls_id:
        return
    ts = _float(row.get("timestamp_s") or 0.0)
    tracker[tls_id] = ts


def _read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def _latest_spatem(spatem_rows: List[Dict[str, Any]], tls_id: str, timestamp_s: float) -> Dict[str, Any]:
    candidates = [
        item
        for item in spatem_rows
        if str(item.get("tls_id")) == tls_id and _float(item.get("timestamp_s")) <= timestamp_s
    ]
    if not candidates:
        return {}
    return max(candidates, key=lambda item: _float(item.get("timestamp_s")))


def _correlation_token_from_payload(payload: Dict[str, Any]) -> str:
    """Reconstrói o `correlation_token` (station:request:seq) de um SREM JSONL."""
    station_id = payload.get("station_id")
    requests = payload.get("requests") or []
    sequence_number = payload.get("sequence_number")
    if not requests or station_id is None or sequence_number is None:
        return ""
    return f"{station_id}:{requests[0].get('request_id')}:{sequence_number}"


def _srem_from_payload(payload: Dict[str, Any]) -> SREMLike:
    """Reconstrói um SREMLike a partir do JSONL serializado.

    Espera o shape ETSI-aligned actual (`requestor.*`, `requests[].*`,
    `operator_telemetry.*`, `security.*`). JSONLs pré-v0.4 não são suportados.
    """
    requestor_payload = payload.get("requestor") or {}
    telemetry_payload = payload.get("operator_telemetry") or {}
    requests_payload = payload.get("requests") or []
    security_payload = payload.get("security") or {}

    if not requests_payload:
        raise ValueError("SREM payload missing 'requests' — log pré-v0.4 não é suportado")

    primary = requests_payload[0]
    position_payload = requestor_payload.get("position") or {}

    requestor = Requestor(
        station_id=int(requestor_payload.get("station_id", 0)),
        station_type=int(requestor_payload.get("station_type", StationType.BUS.value)),
        basic_vehicle_role=str(requestor_payload.get("basic_vehicle_role", BasicVehicleRole.PUBLIC_TRANSPORT.value)),
        position=Position3D(
            latitude_e7=int(position_payload.get("latitude_e7", 0)),
            longitude_e7=int(position_payload.get("longitude_e7", 0)),
            elevation_dm=int(position_payload.get("elevation_dm", 0)),
        ),
        heading_deg=_float(requestor_payload.get("heading_deg", 0.0)),
        speed_mps=_float(requestor_payload.get("speed_mps", 0.0)),
        route_name=requestor_payload.get("route_name"),
        operational_vehicle_id=str(requestor_payload.get("operational_vehicle_id", "")),
    )

    telemetry = OperatorTelemetry(
        schedule_delay_s=_float(telemetry_payload.get("schedule_delay_s", 0.0)),
        headway_deviation_s=_float(telemetry_payload.get("headway_deviation_s", 0.0)),
        distance_to_stopline_m=_float(telemetry_payload.get("distance_to_stopline_m", 0.0)),
        eta_to_stopline_s=_float(telemetry_payload.get("eta_to_stopline_s", 0.0)),
        operator_priority_class=str(telemetry_payload.get("operator_priority_class", OperatorPriorityClass.NOMINAL.value)),
        line_id=str(telemetry_payload.get("line_id", "")),
        route_id=str(telemetry_payload.get("route_id", "")),
        intersection_alias=str(telemetry_payload.get("intersection_alias", "")),
        tls_id=str(telemetry_payload.get("tls_id", "")),
        rsu_id=str(telemetry_payload.get("rsu_id", "")),
        priority_movement_id=str(telemetry_payload.get("priority_movement_id", "")),
        target_signal_group_id_hint=str(telemetry_payload.get("target_signal_group_id_hint", "")),
    )

    signal_request = SignalRequest(
        intersection_ref_id=int(primary.get("intersection_ref_id", 0)),
        request_id=int(primary.get("request_id", 0)),
        request_type=str(primary.get("request_type", RequestType.PRIORITY_REQUEST.value)),
        in_bound_lane_id=str(primary.get("in_bound_lane_id", "")),
        out_bound_lane_id=str(primary.get("out_bound_lane_id", "")),
        eta_min_minute=int(primary.get("eta_min_minute", 0)),
        eta_min_second_ms=int(primary.get("eta_min_second_ms", 0)),
        duration_ms=int(primary.get("duration_ms", 0)),
    )

    if security_payload:
        security = SecurityEnvelope(
            signer_id=str(security_payload.get("signer_id", "")),
            certificate_id=str(security_payload.get("certificate_id", "")),
            signature_b64=security_payload.get("signature_b64"),
            generation_time_ms=int(security_payload.get("generation_time_ms", 0)),
            valid_until_ms=int(security_payload.get("valid_until_ms", 0)),
        )
    else:
        # Reconstrução de logs sem security (não deveria acontecer no shape v0.4):
        security = build_security_envelope("", _float(payload.get("timestamp_ms", 0)) / 1000.0)

    return SREMLike(
        message_type=str(payload.get("message_type", MessageType.SREM.value)),
        station_id=int(payload.get("station_id", derive_station_id(requestor.operational_vehicle_id))),
        station_type=int(payload.get("station_type", requestor.station_type)),
        source_id=str(payload.get("source_id", "")),
        destination_id=str(payload.get("destination_id", "")),
        generation_delta_time_ms=int(payload.get("generation_delta_time_ms", 0)),
        moy=int(payload.get("moy", 0)),
        timestamp_ms=int(payload.get("timestamp_ms", 0)),
        security=security,
        message_id=str(payload.get("message_id", "")) or "",
        protocol_version=str(payload.get("protocol_version", "0.4.0")),
        correlation_id=payload.get("correlation_id"),
        sequence_number=int(payload.get("sequence_number", 0)),
        requests=[signal_request],
        requestor=requestor,
        operator_telemetry=telemetry,
        expires_at_s=_optional_float(payload.get("expires_at_s")),
    )


def _signal_state_from_payload(payload: Dict[str, Any], request: SREMLike) -> SignalState:
    controlled_lanes = payload.get("controlled_lanes")
    # O SPATEM novo tem `intersection_alias` em vez de `intersection_id`.
    intersection_alias = (
        payload.get("intersection_alias")
        or payload.get("intersection_id")
        or request.intersection_id
    )
    return SignalState(
        intersection_id=str(intersection_alias),
        tls_id=str(payload.get("tls_id") or request.tls_id),
        rsu_id=request.rsu_id,
        timestamp_s=_float(payload.get("timestamp_ms", 0)) / 1000.0
        if "timestamp_ms" in payload
        else _float(payload.get("timestamp_s", 0)),
        current_phase_index=int(payload.get("current_phase_index", 0))
        if payload.get("current_phase_index") is not None
        else None,
        current_program_id=str(payload.get("current_program_id", "")),
        red_yellow_green_state=str(payload.get("debug_sumo_state") or payload.get("red_yellow_green_state", "")),
        next_switch_s=_optional_float(payload.get("next_switch_s")),
        spent_duration_s=_optional_float(payload.get("spent_duration_s")),
        controlled_lanes=list(controlled_lanes) if isinstance(controlled_lanes, list) else [],
    )


def _network_state_from_notes(notes: Any) -> Dict[str, Any]:
    if not isinstance(notes, list):
        return {}
    prefix = "network_state="
    for note in notes:
        if not isinstance(note, str) or not note.startswith(prefix):
            continue
        payload: Dict[str, Any] = {}
        for item in note[len(prefix):].split(","):
            if ":" not in item:
                continue
            key, value = item.split(":", 1)
            payload[_network_key(key)] = _network_value(value)
        return payload
    return {}


def _network_key(key: str) -> str:
    mapping = {
        "active_requests": "active_request_count",
        "queue": "queue_vehicle_count",
        "halted": "halted_vehicle_count",
    }
    return mapping.get(key, key)


def _network_value(value: str) -> Any:
    if value == "True":
        return True
    if value == "False":
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def _float(value: Any) -> float:
    return float(value)


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)
