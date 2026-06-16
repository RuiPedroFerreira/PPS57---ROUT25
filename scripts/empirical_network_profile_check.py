#!/usr/bin/env python3
"""Empirically check a SUMO net.xml against NetworkProfile and TSP safety.

This script starts SUMO through TraCI, compares the extracted NetworkProfile
against the traffic-light programs actually loaded by SUMO, then runs one
auto-discovered TSP request through the decision engine and safety layer. With
``--apply-actuation`` it also applies the approved setPhaseDuration command and
records the real SUMO phase sequence that follows.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for entry in (str(SRC), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from _evidence_common import auto_discovery_cits_config, auto_tsp_config  # noqa: E402

from pps57_cits.messages import OperatorPriorityClass, synth_srem  # noqa: E402
from pps57_cits.models import SignalState  # noqa: E402
from pps57_sumo.environment import apply_sumo_environment  # noqa: E402
from pps57_sumo.network_profile import (
    MovementProfile,
    NetworkProfile,
    TLSProfile,
    load_network_profile,
)  # noqa: E402
from pps57_tsp.config import TSPConfig  # noqa: E402
from pps57_tsp.engine import TSPDecisionEngine  # noqa: E402
from pps57_tsp.safety import TSPSafetyLayer  # noqa: E402
from pps57_tsp.signal_control import build_controller_contract  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--tls-id", default="")
    parser.add_argument("--from-edge", default="")
    parser.add_argument("--to-edge", default="")
    parser.add_argument("--sim-time", type=float, default=10.0)
    parser.add_argument(
        "--traci-port",
        type=int,
        default=None,
        help="TraCI port; default: a free port via the shared resolver "
        "(TRACI_PORT, else an OS-assigned free port, else a fixed fallback port).",
    )
    parser.add_argument("--sumo-binary", default="sumo")
    parser.add_argument("--apply-actuation", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    traci_port = args.traci_port
    if traci_port is None:
        # Reuse the shared adapter resolver: it tries getFreeSocketPort() and,
        # when ephemeral-port probing is unavailable (restricted environments),
        # falls back to scanning fixed ports instead of returning None/raising.
        from pps57_cits.traci_adapter import _resolve_traci_port

        traci_port = _resolve_traci_port()

    network = args.network if args.network.is_absolute() else ROOT / args.network
    report = run_check(
        network=network,
        tls_id=args.tls_id,
        from_edge=args.from_edge,
        to_edge=args.to_edge,
        sim_time_s=args.sim_time,
        traci_port=traci_port,
        sumo_binary=args.sumo_binary,
        apply_actuation=args.apply_actuation,
    )
    text = json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    if report["traci_profile_mismatch_count"] or not report.get("safety", {}).get("approved"):
        raise SystemExit(1)


def run_check(
    *,
    network: Path,
    tls_id: str,
    from_edge: str,
    to_edge: str,
    sim_time_s: float,
    traci_port: int,
    sumo_binary: str,
    apply_actuation: bool,
) -> dict[str, Any]:
    apply_sumo_environment()
    import traci  # imported lazily so static imports work without SUMO tools

    profile = load_network_profile(network)
    cits = auto_discovery_cits_config(network)
    tsp = auto_tsp_config(cits.root)

    traci.start(
        [sumo_binary, "-n", str(network), "--no-step-log", "true", "--no-warnings", "true"],
        port=traci_port,
        numRetries=20,
    )
    try:
        traci.simulationStep(sim_time_s)
        comparison = _compare_profile_to_traci(profile, traci)
        selected_tls, movement = _select_movement(
            profile,
            traci,
            tls_id=tls_id,
            from_edge=from_edge,
            to_edge=to_edge,
        )
        decision_report = _run_tsp_probe(
            cits=cits,
            tsp=tsp,
            profile=profile,
            traci=traci,
            tls=selected_tls,
            movement=movement,
            sim_time_s=sim_time_s,
            apply_actuation=apply_actuation,
        )
        return {
            "network": str(network),
            "tls_count_profile": len(profile.tls_profiles),
            "tls_count_traci": len(traci.trafficlight.getIDList()),
            "traci_profile_mismatch_count": len(comparison["mismatches"]),
            "traci_profile_mismatches": comparison["mismatches"],
            "checked_tls": comparison["checked_tls"],
            **decision_report,
        }
    finally:
        traci.close()


def _compare_profile_to_traci(profile: NetworkProfile, traci_module: Any) -> dict[str, Any]:
    mismatches: list[str] = []
    checked: list[dict[str, Any]] = []
    for tls_id in sorted(traci_module.trafficlight.getIDList()):
        tls = profile.tls_profile(tls_id)
        if tls is None:
            mismatches.append(f"{tls_id}: missing from profile")
            continue
        logics = traci_module.trafficlight.getAllProgramLogics(tls_id)
        if not logics:
            mismatches.append(f"{tls_id}: no TraCI program logic")
            continue
        current_program = str(traci_module.trafficlight.getProgram(tls_id))
        logic = next(
            (item for item in logics if str(getattr(item, "programID", "")) == current_program),
            logics[0],
        )
        traci_states = [str(phase.state) for phase in logic.phases]
        traci_durations = [float(phase.duration) for phase in logic.phases]
        profile_states = [phase.state for phase in tls.phases]
        profile_durations = [float(phase.duration_s) for phase in tls.phases]
        if traci_states != profile_states:
            mismatches.append(f"{tls_id}: phase states mismatch")
        if len(traci_durations) != len(profile_durations) or any(
            abs(a - b) > 1e-6 for a, b in zip(traci_durations, profile_durations)
        ):
            mismatches.append(f"{tls_id}: phase durations mismatch")
        controlled_links = traci_module.trafficlight.getControlledLinks(tls_id)
        if len(controlled_links) != len(tls.connections):
            mismatches.append(f"{tls_id}: controlled link count mismatch")
        checked.append(
            {
                "tls_id": tls_id,
                "phase_count": len(tls.phases),
                "controlled_links": len(tls.connections),
                "movements": len(tls.movements),
                "service_green_phase_indices": tls.service_green_phase_indices,
                "intergreen_phase_indices": tls.intergreen_phase_indices,
            }
        )
    return {"mismatches": mismatches, "checked_tls": checked}


def _select_movement(
    profile: NetworkProfile,
    traci_module: Any,
    *,
    tls_id: str,
    from_edge: str,
    to_edge: str,
) -> tuple[TLSProfile, MovementProfile]:
    tls_candidates = (
        [profile.tls_profile(tls_id)] if tls_id else list(profile.tls_profiles.values())
    )
    for tls in tls_candidates:
        if tls is None:
            continue
        current_phase = int(traci_module.trafficlight.getPhase(tls.tls_id))
        movements = list(tls.movements)
        if from_edge:
            movements = [movement for movement in movements if movement.from_edge == from_edge]
        if to_edge:
            movements = [movement for movement in movements if movement.to_edge == to_edge]
        for movement in movements:
            if movement.target_phase_index is None or movement.target_phase_index == current_phase:
                continue
            if _has_intergreen_between(tls, current_phase, movement.target_phase_index):
                return tls, movement
    raise SystemExit(
        "No suitable movement found with target phase reachable through an intergreen phase."
    )


def _run_tsp_probe(
    *,
    cits: Any,
    tsp: TSPConfig,
    profile: NetworkProfile,
    traci: Any,
    tls: TLSProfile,
    movement: MovementProfile,
    sim_time_s: float,
    apply_actuation: bool,
) -> dict[str, Any]:
    intersection = cits.tls_to_intersection[tls.tls_id]
    cits_movement = next(
        item
        for item in intersection.priority_movements
        if item.approach_edges == [movement.from_edge] and item.egress_edges == [movement.to_edge]
    )
    signal_state = _signal_state_from_traci(traci, tls.tls_id, intersection.rsu_id, sim_time_s)
    request = synth_srem(
        sim_time_s=sim_time_s,
        vehicle_id="bus_empirical_probe",
        intersection_alias=tls.tls_id,
        tls_id=tls.tls_id,
        rsu_id=intersection.rsu_id,
        lane_id=movement.controlled_lanes[0],
        next_edge_id=movement.to_edge,
        eta_to_stopline_s=15.0,
        distance_to_stopline_m=80.0,
        schedule_delay_s=120.0,
        operator_priority_class=OperatorPriorityClass.HIGH_DELAY.value,
        priority_movement_id=cits_movement.movement_id,
        target_signal_group_id_hint=cits_movement.target_signal_group_id,
    )
    contract = build_controller_contract(cits, tsp, tls.tls_id)
    decision = TSPDecisionEngine(cits, tsp).decide(request, signal_state, sim_time_s)
    safety = TSPSafetyLayer(cits, tsp)
    safety.set_signal_program_verified(True)
    validation = safety.validate(decision, signal_state, sim_time_s)
    phase_trace = [_phase_sample(traci, tls.tls_id, sim_time_s)]
    if apply_actuation and validation.approved:
        duration = float(validation.safe_decision.phase_duration_s or 0.0)
        traci.trafficlight.setPhaseDuration(tls.tls_id, duration)
        for target_time in (
            sim_time_s + duration,
            sim_time_s + duration + 1.0,
            sim_time_s + duration + 4.0,
        ):
            traci.simulationStep(target_time)
            phase_trace.append(_phase_sample(traci, tls.tls_id, target_time))
    return {
        "selected_tls": tls.tls_id,
        "selected_movement": {
            "movement_id": cits_movement.movement_id,
            "target_signal_group_id": cits_movement.target_signal_group_id,
            "from_edge": movement.from_edge,
            "to_edge": movement.to_edge,
            "target_phase_index": movement.target_phase_index,
            "conflict_count": len(movement.conflicts_with),
        },
        "contract": {
            "phase_sequence": contract.phase_sequence,
            "service_green_phase_indices": contract.service_green_phase_indices,
            "intergreen_phase_indices": contract.intergreen_phase_indices,
            "signal_group_count": len(contract.signal_groups),
        },
        "initial_signal_state": {
            "phase": signal_state.current_phase_index,
            "state": signal_state.red_yellow_green_state,
            "spent_duration_s": signal_state.spent_duration_s,
            "next_switch_s": signal_state.next_switch_s,
        },
        "decision": {
            "action": decision.action,
            "reason": decision.reason,
            "target_phase_index": decision.target_phase_index,
            "phase_duration_s": decision.phase_duration_s,
            "priority_score": decision.priority_score,
        },
        "safety": {
            "approved": validation.approved,
            "status": validation.status,
            "reason": validation.reason,
            "safe_action": validation.safe_decision.action,
            "safe_phase_duration_s": validation.safe_decision.phase_duration_s,
        },
        "phase_trace_after_setPhaseDuration": phase_trace,
    }


def _signal_state_from_traci(
    traci_module: Any, tls_id: str, rsu_id: str, sim_time_s: float
) -> SignalState:
    return SignalState(
        intersection_id=tls_id,
        tls_id=tls_id,
        rsu_id=rsu_id,
        timestamp_s=sim_time_s,
        current_phase_index=int(traci_module.trafficlight.getPhase(tls_id)),
        current_program_id=str(traci_module.trafficlight.getProgram(tls_id)),
        red_yellow_green_state=str(traci_module.trafficlight.getRedYellowGreenState(tls_id)),
        next_switch_s=float(traci_module.trafficlight.getNextSwitch(tls_id)),
        spent_duration_s=float(traci_module.trafficlight.getSpentDuration(tls_id)),
        controlled_lanes=list(traci_module.trafficlight.getControlledLanes(tls_id)),
        controlled_links=list(traci_module.trafficlight.getControlledLinks(tls_id)),
    )


def _phase_sample(traci_module: Any, tls_id: str, sim_time_s: float) -> dict[str, Any]:
    return {
        "time_s": round(sim_time_s, 3),
        "phase": int(traci_module.trafficlight.getPhase(tls_id)),
        "state": str(traci_module.trafficlight.getRedYellowGreenState(tls_id)),
    }


def _has_intergreen_between(tls: TLSProfile, current: int, target: int) -> bool:
    sequence = tls.phase_sequence
    if current not in sequence or target not in sequence:
        return False
    pos = sequence.index(current)
    between: list[int] = []
    for _ in range(1, len(sequence) + 1):
        pos = (pos + 1) % len(sequence)
        phase = sequence[pos]
        if phase == target:
            break
        between.append(phase)
    return any(phase in tls.intergreen_phase_indices for phase in between)


if __name__ == "__main__":
    main()
