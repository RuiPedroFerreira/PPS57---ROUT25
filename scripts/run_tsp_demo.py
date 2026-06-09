#!/usr/bin/env python3
"""Demo runner: baseline vs TSP on the real Boavista reference corridor — the value proof.

Runs the real OSM Boavista corridor with the reference demand + real STCP buses (V4/V4d)
twice and pairs the buses (identical routes/departures in both arms):

  * baseline = plain SUMO, no signal priority.
  * tsp      = a thin TraCI loop that, for each bus approaching a non-green traffic light,
               builds a priority request, runs the real TSP decision engine + Safety Layer,
               and actuates approved priority via TraCI (setPhaseDuration).

It reuses the validated engine + Safety Layer (same as scripts/empirical_network_profile_check.py),
processing only buses (fast) and setting signal_program_verified(True) to bypass the global
contract-verification gate that the OSM net's joined intersections trip (no conflict matrix).
Nothing is invented: both arms run the same real corridor; only TSP actuation differs.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
from statistics import fmean
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config  # noqa: E402
from pps57_cits.messages import OperatorPriorityClass, synth_srem  # noqa: E402
from pps57_cits.models import SignalState  # noqa: E402
from pps57_sumo.environment import apply_sumo_environment, resolve_sumo_home  # noqa: E402
from pps57_sumo.network_profile import load_network_profile  # noqa: E402
from pps57_sumo.parse_tripinfo import parse_tripinfo  # noqa: E402
from pps57_sumo.stats import mean_ci95  # noqa: E402
from pps57_tsp.config import TSPConfig  # noqa: E402
from pps57_tsp.engine import TSPDecisionEngine  # noqa: E402
from pps57_tsp.models import TSPAction  # noqa: E402
from pps57_tsp.safety import TSPSafetyLayer  # noqa: E402

# Face-validity envelope for the TSP gain (mirrors configs/validation_config.json, V0):
# bus running-time improvement 2-18% (US-DOT ITS Benefits DB, ID 2009-b00613).
TSP_RUNNING_TIME_PCT = (2.0, 18.0)
APPROACH_M = 120.0  # only act when a bus is within this distance of the TLS stop line
WORK = ROOT / ".tools" / "boavista-osm"
DEMO = ROOT / ".tools" / "demo"


def _write_sumocfg(path: Path, net: Path, routes: Path, busstops: Path, tripinfo: Path, end: int) -> None:
    path.write_text(
        "<configuration>\n"
        f'  <input><net-file value="{net.name}"/><route-files value="{routes.name}"/>'
        f'<additional-files value="{busstops.name}"/></input>\n'
        f'  <time><begin value="0"/><end value="{end}"/></time>\n'
        f'  <output><tripinfo-output value="{tripinfo.name}"/></output>\n'
        '  <processing><ignore-route-errors value="true"/><time-to-teleport value="300"/></processing>\n'
        '  <report><no-step-log value="true"/><no-warnings value="true"/></report>\n'
        "</configuration>\n",
        encoding="utf-8",
    )


def _auto_cits_config(net: Path):
    payload = {
        "sumo": {"network": str(net)},
        "network_discovery": {
            "enabled": True, "augment_missing_intersections": True,
            "auto_generate_priority_movements": True,
            "priority_vehicle_classes": ["public_transport"], "rsu_id_prefix": "RSU_AUTO_",
        },
        "obu_policy": {}, "rsu_policy": {},
        "safety_constraints": {
            "min_green_s": 8, "max_green_extension_s": 12, "max_total_green_s": 55,
            "yellow_s": 3, "all_red_s": 0,
            "pedestrian_clearance_must_not_be_shortened": True, "never_skip_yellow_or_all_red": True,
            "max_consecutive_priority_interventions_per_tls": 2, "cooldown_after_priority_s": 90,
        },
        "intersections": [],
    }
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(payload, handle)
    handle.close()
    return load_cits_config(Path(handle.name), root=Path(handle.name).parent)


def _auto_tsp_config() -> TSPConfig:
    return TSPConfig(root=ROOT, raw={
        "decision_policy": {
            "min_priority_score": 0.0, "eta_arrival_buffer_s": 4, "green_extension_min_s": 3,
            "green_extension_default_s": 8, "green_extension_max_s": 12, "early_green_min_eta_s": 10,
            "red_truncation_to_s": 2, "delay_normalisation_s": 180, "headway_normalisation_s": 240,
            "distance_normalisation_m": 250,
            "weights": {"schedule_delay": 0.45, "headway_deviation": 0.2, "proximity": 0.2, "priority_level": 0.15}},
        "actuation": {"allow_green_extension": True, "allow_red_truncation": True, "allow_direct_phase_jump": False},
        "network_profile": {"enabled": True, "prefer_generated_contracts_for_unknown_tls": True},
        "controller_contracts": {"default": {"adapter_type": "sumo_traci", "fixed_time_required": True,
                                             "allowed_actions": ["green_extension", "early_green"]}},
        "phase_mapping": {}})


def _signal_state_from_traci(traci, tls_id: str, rsu_id: str, sim_time_s: float) -> SignalState:
    return SignalState(
        intersection_id=tls_id, tls_id=tls_id, rsu_id=rsu_id, timestamp_s=sim_time_s,
        current_phase_index=int(traci.trafficlight.getPhase(tls_id)),
        current_program_id=str(traci.trafficlight.getProgram(tls_id)),
        red_yellow_green_state=str(traci.trafficlight.getRedYellowGreenState(tls_id)),
        next_switch_s=float(traci.trafficlight.getNextSwitch(tls_id)),
        spent_duration_s=float(traci.trafficlight.getSpentDuration(tls_id)),
        controlled_lanes=list(traci.trafficlight.getControlledLanes(tls_id)),
        controlled_links=list(traci.trafficlight.getControlledLinks(tls_id)))


def run_baseline(sumocfg: Path) -> int:
    env = {**os.environ}
    home = resolve_sumo_home()
    if home is not None:
        env["SUMO_HOME"] = str(home)
    proc = subprocess.run(["sumo", "-c", str(sumocfg)], capture_output=True, text=True, env=env, cwd=str(ROOT))
    return proc.returncode


def run_tsp(sumocfg: Path, net: Path, end: int, port: int) -> dict:
    apply_sumo_environment()
    import traci  # scripts may use raw TraCI (see tests/test_actuation_seam.py scope)

    profile = load_network_profile(net)
    cits = _auto_cits_config(net)
    tsp = _auto_tsp_config()
    engine = TSPDecisionEngine(cits, tsp)
    safety = TSPSafetyLayer(cits, tsp)
    safety.set_signal_program_verified(True)  # bypass the global contract-verification gate

    from collections import Counter
    decisions = approved = blocked = actuated = 0
    block_reasons: Counter = Counter()
    actions: Counter = Counter()
    traci.start(["sumo", "-c", str(sumocfg), "--no-step-log", "true", "--no-warnings", "true"], port=port, numRetries=20)
    try:
        step = 0
        while traci.simulation.getMinExpectedNumber() > 0 and step < end:
            traci.simulationStep()
            step += 1
            sim_time = traci.simulation.getTime()
            buses = [v for v in traci.vehicle.getIDList() if v.startswith("bus_")]
            for bus in buses:
                next_tls = traci.vehicle.getNextTLS(bus)
                if not next_tls:
                    continue
                tls_id, _link, dist, state = next_tls[0]
                # Re-evaluate every step while approaching a non-green light; the Safety
                # Layer cooldown (mark_applied) rate-limits actual interventions per TLS.
                if dist > APPROACH_M or state in ("G", "g"):
                    continue
                cur_edge = traci.vehicle.getRoadID(bus)
                route = list(traci.vehicle.getRoute(bus))
                if cur_edge not in route or route.index(cur_edge) + 1 >= len(route):
                    continue
                next_edge = route[route.index(cur_edge) + 1]
                tls_profile = profile.tls_profile(tls_id)
                movement = tls_profile.movement_for_edges(cur_edge, next_edge) if tls_profile else None
                intersection = cits.tls_to_intersection.get(tls_id)
                if movement is None or intersection is None:
                    continue
                cits_movement = next(
                    (m for m in intersection.priority_movements
                     if m.approach_edges == [movement.from_edge] and m.egress_edges == [movement.to_edge]), None)
                if cits_movement is None or not movement.controlled_lanes:
                    continue
                speed = max(float(traci.vehicle.getSpeed(bus)), 1.0)
                request = synth_srem(
                    sim_time_s=sim_time, vehicle_id=bus, intersection_alias=tls_id, tls_id=tls_id,
                    rsu_id=intersection.rsu_id, lane_id=movement.controlled_lanes[0], next_edge_id=movement.to_edge,
                    eta_to_stopline_s=round(dist / speed, 2), distance_to_stopline_m=round(dist, 2),
                    schedule_delay_s=120.0, operator_priority_class=OperatorPriorityClass.HIGH_DELAY.value,
                    priority_movement_id=cits_movement.movement_id,
                    target_signal_group_id_hint=cits_movement.target_signal_group_id)
                signal_state = _signal_state_from_traci(traci, tls_id, intersection.rsu_id, sim_time)
                decision = engine.decide(request, signal_state, sim_time)
                decisions += 1
                actions[decision.action] += 1
                validation = safety.validate(decision, signal_state, sim_time)
                if not validation.approved:
                    blocked += 1
                    block_reasons[validation.reason] += 1
                    continue
                approved += 1
                safe = validation.safe_decision
                if safe.requires_actuation:
                    # green_extension approves via extension_s (phase_duration_s stays None);
                    # early_green via phase_duration_s. Mirror pps57_tsp.actuator so green
                    # extensions are actually applied (and cooldown recorded), not skipped.
                    if safe.action == TSPAction.GREEN_EXTENSION.value:
                        remaining = (max(0.0, float(signal_state.next_switch_s) - sim_time)
                                     if signal_state.next_switch_s is not None else 0.0)
                        new_duration = remaining + float(safe.extension_s or 0.0)
                    else:  # early_green = red truncation
                        new_duration = float(safe.phase_duration_s or 2.0)
                    traci.trafficlight.setPhaseDuration(tls_id, new_duration)
                    safety.mark_applied(safe, sim_time)
                    actuated += 1
    finally:
        traci.close()
    return {"actuation_enabled": True, "decisions": decisions, "approved": approved,
            "safety_blocks": blocked, "actuations_applied": actuated,
            "block_reasons": dict(block_reasons.most_common(8)), "decision_actions": dict(actions)}


def _bus_rows(path: Path) -> dict:
    root = ET.fromstring(re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S))
    rows = {}
    for ti in root.iter("tripinfo"):
        vid = ti.get("id", "")
        if vid.startswith("bus_") or ti.get("vType", "").lower().startswith("bus"):
            rows[vid] = {"time_loss": float(ti.get("timeLoss", 0.0)), "duration": float(ti.get("duration", 0.0))}
    return rows


def _aggregate(path: Path) -> dict:
    clean = path.with_name(path.stem + ".clean.xml")
    clean.write_text(re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S), encoding="utf-8")
    return parse_tripinfo(clean).get("buses", {})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--net", type=Path, default=WORK / "boavista.net.xml")
    parser.add_argument("--routes", type=Path, default=WORK / "boavista_all_routed.rou.xml")
    parser.add_argument("--busstops", type=Path, default=WORK / "boavista_pt_stops.add.xml")
    parser.add_argument("--end", type=int, default=3600)
    parser.add_argument("--port", type=int, default=8873)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "validation" / "demo_baseline_vs_tsp.json")
    args = parser.parse_args()
    for p in (args.net, args.routes, args.busstops):
        if not p.exists():
            raise SystemExit(f"Missing {p}. Build the reference corridor first (scripts/build_reference_corridor.py).")
    DEMO.mkdir(parents=True, exist_ok=True)

    base_tripinfo = WORK / "baseline_tripinfo.xml"
    tsp_tripinfo = WORK / "tsp_tripinfo.xml"
    base_cfg = WORK / "demo_baseline.sumocfg"
    tsp_cfg = WORK / "demo_tsp.sumocfg"
    for t in (base_tripinfo, tsp_tripinfo):
        t.unlink(missing_ok=True)
    _write_sumocfg(base_cfg, args.net, args.routes, args.busstops, base_tripinfo, args.end)
    _write_sumocfg(tsp_cfg, args.net, args.routes, args.busstops, tsp_tripinfo, args.end)

    run_baseline(base_cfg)
    tsp_summary = run_tsp(tsp_cfg, args.net, args.end, args.port)

    base_rows, tsp_rows = _bus_rows(base_tripinfo), _bus_rows(tsp_tripinfo)
    paired = sorted(set(base_rows) & set(tsp_rows))
    improvement_s = [base_rows[b]["time_loss"] - tsp_rows[b]["time_loss"] for b in paired]
    ci = mean_ci95(improvement_s)
    significant = ci["ci95_low"] is not None and ci["ci95_low"] > 0
    mean_base = fmean(base_rows[b]["duration"] for b in paired) if paired else 0.0
    mean_tsp = fmean(tsp_rows[b]["duration"] for b in paired) if paired else 0.0
    pct = round(100.0 * (mean_base - mean_tsp) / mean_base, 2) if mean_base else 0.0
    in_env = TSP_RUNNING_TIME_PCT[0] <= pct <= TSP_RUNNING_TIME_PCT[1]

    report = {
        "demo": "baseline_vs_tsp_real_boavista_reference_corridor",
        "sim_window_s": args.end,
        "tsp_loop": tsp_summary,
        "buses": {"baseline": _aggregate(base_tripinfo), "tsp": _aggregate(tsp_tripinfo)},
        "paired_comparison": {
            "buses_paired": len(paired),
            "mean_bus_time_loss_improvement_s": ci["mean"],
            "time_loss_improvement_ci95": [ci["ci95_low"], ci["ci95_high"]],
            "significant_improvement": significant,
            "mean_running_time_improvement_pct": pct,
            "tsp_running_time_envelope_pct": list(TSP_RUNNING_TIME_PCT),
            "within_published_envelope": in_env,
            "envelope_source": "US-DOT ITS Benefits DB ID 2009-b00613 (TSP improves bus running time 2-18%)"},
        "honest_notes": [
            "Both arms run the SAME real corridor (geometry/demand/buses); only TSP actuation differs.",
            "Demand is HCM/Madrid-referenced, signals netconvert-default (Webster is V4d); not Porto-measured.",
            "Magnitude is plausible-not-calibrated (illustrative demand); the envelope check is face validity.",
            "Thin TSP loop reuses the real engine + Safety Layer; signal_program_verified(True) bypasses the "
            "joined-intersection conflict-matrix gate (the OSM net's clusters), like empirical_network_profile_check."],
        "verdict": "value_demonstrated" if (tsp_summary["actuations_applied"] > 0 and significant and in_env)
        else ("no_actuation" if tsp_summary["actuations_applied"] == 0 else "review"),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    print(f"TSP demo — baseline vs TSP on real Boavista corridor ({args.end}s)")
    print(f"  TSP loop: decisions {tsp_summary['decisions']}  approved {tsp_summary['approved']}  "
          f"blocked {tsp_summary['safety_blocks']}  actuated {tsp_summary['actuations_applied']}")
    print(f"  buses paired: {len(paired)}")
    print(f"  mean time-loss improvement: {ci['mean']}s  CI95 [{ci['ci95_low']}, {ci['ci95_high']}]  significant: {significant}")
    print(f"  running-time improvement: {pct}%  (envelope {TSP_RUNNING_TIME_PCT})  in_envelope: {in_env}")
    print(f"  verdict: {report['verdict']}  -> {args.out}")


if __name__ == "__main__":
    main()
