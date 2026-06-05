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
    MessageType,
    SREMLike,
    SPATEMLike,
)
from pps57_cits.models import SignalState
from pps57_cits.protocol_codec import JsonSimulationCodec, ProtocolCodecError
from pps57_tsp.models import DEFAULT_ACTUATING_ACTIONS

from .models import OfflineScenario


_CITS_CODEC = JsonSimulationCodec()


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
                "next_switch_s": decision.get("current_next_switch_s"),
                "spent_duration_s": decision.get("current_spent_duration_s"),
                "controlled_lanes": decision.get("controlled_lanes", []),
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


def load_event_training_scenarios(
    path: str | Path,
    intervention_actions: Optional[Iterable[str]] = None,
) -> List[OfflineScenario]:
    """Load optimization/RL scenarios from SUMO/TraCI event-derived rows.

    Strict loader: rows sem o SREM original ou sem o estado SPATEM-derivado
    são saltadas; dataset vazio é rejeitado pelos controllers. Isto previne
    fallback silencioso para dados sintéticos.

    `intervention_actions` é a fonte de verdade única do conjunto de ações que
    atuam o semáforo (ver TSPConfig.actuating_actions); quando None recai em
    DEFAULT_ACTUATING_ACTIONS, mantendo o comportamento anterior.
    """

    raw_rows = list(_read_jsonl(Path(path)))
    raw_rows.sort(key=lambda row: _float(row.get("timestamp_s") or 0.0))
    last_applied_intervention_by_tls: Dict[str, float] = {}
    intervention_actions = (
        set(intervention_actions)
        if intervention_actions is not None
        else set(DEFAULT_ACTUATING_ACTIONS)
    )

    scenarios: List[OfflineScenario] = []
    for index, row in enumerate(raw_rows):
        request_payload = row.get("request") if isinstance(row.get("request"), dict) else {}
        signal_payload = row.get("signal_state") if isinstance(row.get("signal_state"), dict) else {}
        network_state = row.get("network_state") if isinstance(row.get("network_state"), dict) else {}
        if not request_payload or not signal_payload or not network_state:
            _maybe_record_intervention(row, last_applied_intervention_by_tls, intervention_actions)
            continue
        request = _srem_from_payload(request_payload)
        signal_payload = _merge_signal_context(signal_payload, row)
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
                behavior_policy_action=(str(row["action"]) if row.get("action") is not None else None),
                realized_outcome=_optional_float(row.get("realized_outcome")),
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
        if str(item.get("tls_id")) == tls_id and _message_time_s(item) <= timestamp_s
    ]
    if not candidates:
        return {}
    return max(candidates, key=_message_time_s)


def _correlation_token_from_payload(payload: Dict[str, Any]) -> str:
    """Reconstrói o `correlation_token` (station:request:seq) de um SREM JSONL."""
    station_id = payload.get("station_id")
    requests = payload.get("requests") or []
    sequence_number = payload.get("sequence_number")
    if not requests or station_id is None or sequence_number is None:
        return ""
    return f"{station_id}:{requests[0].get('request_id')}:{sequence_number}"


def _srem_from_payload(payload: Dict[str, Any]) -> SREMLike:
    """Reconstrói um SREMLike validado a partir do JSONL serializado."""
    message = _CITS_CODEC.decode(payload)
    if not isinstance(message, SREMLike):
        raise ProtocolCodecError(f"Expected SREM payload, got {message.message_type!r}")
    return message


def _signal_state_from_payload(payload: Dict[str, Any], request: SREMLike) -> SignalState:
    spatem = _spatem_from_payload(payload)
    controlled_lanes = payload.get("controlled_lanes")
    # O SPATEM novo tem `intersection_alias` em vez de `intersection_id`.
    intersection_alias = (
        spatem.intersection_alias
        or payload.get("intersection_id")
        or request.intersection_id
    )
    return SignalState(
        intersection_id=str(intersection_alias),
        tls_id=str(spatem.tls_id or request.tls_id),
        rsu_id=request.rsu_id,
        timestamp_s=_message_time_s(payload),
        current_phase_index=int(payload.get("current_phase_index", 0))
        if payload.get("current_phase_index") is not None
        else None,
        current_program_id=_optional_text(payload.get("current_program_id")),
        red_yellow_green_state=_optional_text(spatem.debug_sumo_state or payload.get("red_yellow_green_state")),
        next_switch_s=_optional_float(payload.get("next_switch_s")),
        spent_duration_s=_optional_float(payload.get("spent_duration_s")),
        controlled_lanes=list(controlled_lanes) if isinstance(controlled_lanes, list) else [],
    )


def _spatem_from_payload(payload: Dict[str, Any]) -> SPATEMLike:
    message = _CITS_CODEC.decode(payload)
    if not isinstance(message, SPATEMLike):
        raise ProtocolCodecError(f"Expected SPATEM payload, got {message.message_type!r}")
    return message


def _merge_signal_context(signal_payload: Dict[str, Any], row: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(signal_payload)
    for key in (
        "current_phase_index",
        "current_program_id",
        "red_yellow_green_state",
        "next_switch_s",
        "spent_duration_s",
        "controlled_lanes",
    ):
        if key not in merged and key in row:
            merged[key] = row.get(key)
    if "red_yellow_green_state" not in merged and "current_signal_state" in row:
        merged["red_yellow_green_state"] = row.get("current_signal_state")
    return merged


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


def _message_time_s(payload: Dict[str, Any]) -> float:
    if payload.get("timestamp_s") is not None:
        return _float(payload.get("timestamp_s"))
    if payload.get("moy") is not None and payload.get("timestamp_ms") is not None:
        return int(payload.get("moy", 0)) * 60.0 + _float(payload.get("timestamp_ms", 0)) / 1000.0
    return 0.0


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value)
