#!/usr/bin/env python3
"""Build learning rows and scenarios from C-ITS/TSP event logs."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import json

from pps57_cits.messages import PriorityLevel, RequestedManeuver, SREMLike
from pps57_cits.models import SignalState

from .models import OfflineScenario


def build_event_training_rows(
    *,
    cits_log: str | Path,
    decision_log: str | Path,
    actuation_log: str | Path,
) -> List[Dict[str, Any]]:
    cits_rows = list(_read_jsonl(Path(cits_log)))
    requests = {
        str(item.get("request_id")): item
        for item in cits_rows
        if item.get("message_type") == "SREM_like" and item.get("request_id")
    }
    spatem_rows = [item for item in cits_rows if item.get("message_type") == "SPATEM_like"]
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

    The loader is intentionally strict: rows without the original SREM and
    SPATEM-derived signal state are skipped, and an empty dataset is rejected by
    the controllers. This prevents silent fallback to fixture/default data.

    `seconds_since_last_intervention_s` é derivado da sequência de decisões:
    para cada linha, procura a anterior decisão *aplicada* no MESMO `tls_id`
    e regista o delta. Sem isto, o estado `intervention_*` colapsava sempre
    para `intervention_unknown` e o eixo era efectivamente morto.
    """

    raw_rows = list(_read_jsonl(Path(path)))
    # Ordena por timestamp para que a varredura "última intervenção" seja válida.
    raw_rows.sort(key=lambda row: _float(row.get("timestamp_s") or 0.0))
    last_applied_intervention_by_tls: Dict[str, float] = {}
    intervention_actions = {"green_extension", "early_green"}

    scenarios: List[OfflineScenario] = []
    for index, row in enumerate(raw_rows):
        request_payload = row.get("request") if isinstance(row.get("request"), dict) else {}
        signal_payload = row.get("signal_state") if isinstance(row.get("signal_state"), dict) else {}
        network_state = row.get("network_state") if isinstance(row.get("network_state"), dict) else {}
        if not request_payload or not signal_payload or not network_state:
            # Atualiza o tracker mesmo para linhas saltadas (consistência temporal).
            _maybe_record_intervention(
                row, last_applied_intervention_by_tls, intervention_actions
            )
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
    """Regista intervenções aplicadas (action=GE/EG AND applied=True) para o
    cálculo de `seconds_since_last_intervention_s` em linhas subsequentes."""
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


def _srem_from_payload(payload: Dict[str, Any]) -> SREMLike:
    return SREMLike(
        source_id=str(payload["source_id"]),
        destination_id=str(payload["destination_id"]),
        timestamp_s=_float(payload["timestamp_s"]),
        vehicle_id=str(payload["vehicle_id"]),
        vehicle_class=str(payload.get("vehicle_class", "bus")),
        line_id=str(payload["line_id"]),
        route_id=str(payload["route_id"]),
        intersection_id=str(payload["intersection_id"]),
        tls_id=str(payload["tls_id"]),
        rsu_id=str(payload["rsu_id"]),
        current_edge_id=str(payload["current_edge_id"]),
        current_lane_id=str(payload["current_lane_id"]),
        speed_mps=_float(payload["speed_mps"]),
        distance_to_stopline_m=_float(payload["distance_to_stopline_m"]),
        eta_to_stopline_s=_float(payload["eta_to_stopline_s"]),
        schedule_delay_s=_float(payload["schedule_delay_s"]),
        headway_deviation_s=_float(payload["headway_deviation_s"]),
        requested_maneuver=str(payload.get("requested_maneuver", RequestedManeuver.PRIORITY_CANDIDATE.value)),
        priority_level=str(payload.get("priority_level", PriorityLevel.PUBLIC_TRANSPORT_NOMINAL.value)),
        expires_at_s=_optional_float(payload.get("expires_at_s")),
        request_id=str(payload["request_id"]),
        message_id=str(payload.get("message_id") or payload["request_id"]),
        correlation_id=payload.get("correlation_id"),
    )


def _signal_state_from_payload(payload: Dict[str, Any], request: SREMLike) -> SignalState:
    controlled_lanes = payload.get("controlled_lanes")
    return SignalState(
        intersection_id=str(payload.get("intersection_id") or request.intersection_id),
        tls_id=str(payload.get("tls_id") or request.tls_id),
        rsu_id=request.rsu_id,
        timestamp_s=_float(payload["timestamp_s"]),
        current_phase_index=int(payload["current_phase_index"]),
        current_program_id=str(payload["current_program_id"]),
        red_yellow_green_state=str(payload["red_yellow_green_state"]),
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
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _float(value: Any) -> float:
    return float(value)


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)
