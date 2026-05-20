#!/usr/bin/env python3
"""Generate deterministic demo artifacts for the PPS57 platform.

This is useful when the dashboard has to be demonstrated before running SUMO.
The generated files follow the same JSON/JSONL shape used by packages 3, 4 and 5.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable, Mapping, Any

ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Gera dados demo para a plataforma PPS57.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--overwrite", action="store_true", help="Substitui ficheiros existentes.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    (root / "outputs").mkdir(parents=True, exist_ok=True)
    (root / "reports").mkdir(parents=True, exist_ok=True)

    cits_messages = [
        {"message_type": "MAPEM_like", "timestamp_s": 0, "rsu_id": "RSU_BOAVISTA_01", "intersection_id": "BOAVISTA_01"},
        {"message_type": "SPATEM_like", "timestamp_s": 0, "rsu_id": "RSU_BOAVISTA_01", "intersection_id": "BOAVISTA_01"},
        {
            "message_type": "SREM_like",
            "timestamp_s": 12,
            "request_id": "REQ_DEMO_001",
            "vehicle_id": "bus_demo_500_01",
            "line_id": "STCP500_PROXY",
            "rsu_id": "RSU_BOAVISTA_01",
            "eta_to_stopline_s": 9.5,
            "schedule_delay_s": 95,
            "priority_level": "public_transport_high_delay",
        },
        {
            "message_type": "SSEM_like",
            "timestamp_s": 12.1,
            "request_id": "REQ_DEMO_001",
            "vehicle_id": "bus_demo_500_01",
            "rsu_id": "RSU_BOAVISTA_01",
            "status": "acknowledged",
            "action": "forward_to_decision_engine",
        },
        {
            "message_type": "SREM_like",
            "timestamp_s": 36,
            "request_id": "REQ_DEMO_002",
            "vehicle_id": "bus_demo_502_02",
            "line_id": "STCP502_PROXY",
            "rsu_id": "RSU_BOAVISTA_02",
            "eta_to_stopline_s": 22.0,
            "schedule_delay_s": 40,
            "priority_level": "public_transport_nominal",
        },
        {
            "message_type": "SSEM_like",
            "timestamp_s": 36.2,
            "request_id": "REQ_DEMO_002",
            "vehicle_id": "bus_demo_502_02",
            "rsu_id": "RSU_BOAVISTA_02",
            "status": "rejected",
            "action": "reject_with_reason",
            "reason": "cooldown_or_low_priority",
        },
    ]
    tsp_decisions = [
        {
            "timestamp_s": 12.2,
            "decision_id": "DEC_DEMO_001",
            "request_id": "REQ_DEMO_001",
            "vehicle_id": "bus_demo_500_01",
            "rsu_id": "RSU_BOAVISTA_01",
            "tls_id": "TLS_BOAVISTA_01",
            "action": "green_extension",
            "status": "approved",
            "reason": "bus_arrival_near_green_end",
            "priority_score": 0.81,
            "extension_s": 8,
        },
        {
            "timestamp_s": 36.3,
            "decision_id": "DEC_DEMO_002",
            "request_id": "REQ_DEMO_002",
            "vehicle_id": "bus_demo_502_02",
            "rsu_id": "RSU_BOAVISTA_02",
            "tls_id": "TLS_BOAVISTA_02",
            "action": "no_action",
            "status": "not_actuable",
            "reason": "request_rejected_by_rsu",
            "priority_score": 0.25,
        },
    ]
    tsp_actuation = [
        {
            "timestamp_s": 12.3,
            "decision_id": "DEC_DEMO_001",
            "tls_id": "TLS_BOAVISTA_01",
            "action": "green_extension",
            "applied": True,
            "dry_run": True,
            "command": "setPhaseDuration",
            "reason": "dry_run_actuation_logged",
            "parameters": {"extension_s": 8},
        },
        {
            "timestamp_s": 36.4,
            "decision_id": "DEC_DEMO_002",
            "tls_id": "TLS_BOAVISTA_02",
            "action": "no_action",
            "applied": False,
            "dry_run": True,
            "command": "none",
            "reason": "not_actuable",
        },
    ]
    policy_candidates = [
        {"scenario_id": "SC_DEMO_001", "action": "green_extension", "reward": 18.5, "selected": True, "safety_status": "approved"},
        {"scenario_id": "SC_DEMO_001", "action": "early_green", "reward": -1000, "selected": False, "safety_status": "blocked_by_safety"},
        {"scenario_id": "SC_DEMO_002", "action": "no_action", "reward": 4.0, "selected": True, "safety_status": "not_actuable"},
    ]
    offline_samples = [
        {"scenario_id": "SC_DEMO_001", "state_bucket": "corridor_green|eta_close|delay_high|switch_open"},
        {"scenario_id": "SC_DEMO_002", "state_bucket": "corridor_red|eta_mid|delay_low|switch_open"},
    ]

    write_jsonl(root / "outputs" / "cits_messages.jsonl", cits_messages, overwrite=args.overwrite)
    write_jsonl(root / "outputs" / "tsp_decisions.jsonl", tsp_decisions, overwrite=args.overwrite)
    write_jsonl(root / "outputs" / "tsp_actuation.jsonl", tsp_actuation, overwrite=args.overwrite)
    write_jsonl(root / "outputs" / "policy_candidates.jsonl", policy_candidates, overwrite=args.overwrite)
    write_jsonl(root / "outputs" / "offline_policy_samples.jsonl", offline_samples, overwrite=args.overwrite)

    write_json(root / "reports" / "cits_emulation_summary.json", {"total_messages": 6, "by_type": {"MAPEM_like": 1, "SPATEM_like": 1, "SREM_like": 2, "SSEM_like": 2}}, overwrite=args.overwrite)
    write_json(root / "reports" / "tsp_emulation_summary.json", {"total_decisions": 2, "by_action": {"green_extension": 1, "no_action": 1}, "by_status": {"approved": 1, "not_actuable": 1}, "actuation_events": 2, "applied_events": 1}, overwrite=args.overwrite)
    write_json(root / "reports" / "policy_optimization_summary.json", {"scenario_count": 2, "candidate_count": 3, "unsafe_candidates_filtered": 1, "baseline_reward": 14.0, "optimized_reward": 22.5, "reward_delta": 8.5, "selected_by_action": {"green_extension": 1, "no_action": 1}, "baseline_by_action": {"green_extension": 1, "reject": 1}}, overwrite=args.overwrite)
    write_json(root / "reports" / "baseline_kpis.json", {"vehicle_count": 120, "avg_duration_s": 430.0, "avg_waiting_time_s": 52.0}, overwrite=args.overwrite)

    print("Demo artifacts generated for PPS57 platform.")
    return 0


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]], *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        return
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n", encoding="utf-8")


def write_json(path: Path, payload: Mapping[str, Any], *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        return
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
