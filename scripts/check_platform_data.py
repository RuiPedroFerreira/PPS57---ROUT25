#!/usr/bin/env python3
"""CLI health check for the PPS57 validation platform artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_platform.data_loader import collect_snapshot, export_snapshot  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verifica artefactos da plataforma PPS57.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Raiz do repositório.")
    parser.add_argument("--config", default="configs/platform_config.json", help="Configuração da plataforma.")
    parser.add_argument("--max-records", type=int, default=5000, help="Máximo de registos JSONL a carregar.")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--strict", action="store_true", help="Falha se existirem artefactos críticos em falta.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    output_path = args.out if args.out is not None else root / "reports" / "platform_snapshot.json"
    snapshot = collect_snapshot(root, args.config, max_records=args.max_records)
    export_snapshot(snapshot, output_path)

    overview = snapshot["aggregates"]["overview"]
    print("Resumo da plataforma PPS57:")
    print(f"- root: {snapshot['root']}")
    print(f"- total_cits_messages: {overview['total_cits_messages']}")
    print(f"- total_tsp_decisions: {overview['total_tsp_decisions']}")
    print(f"- total_actuation_events: {overview['total_actuation_events']}")
    print(f"- applied_actuation_events: {overview['applied_actuation_events']}")
    print(f"- blocked_by_safety: {overview['blocked_by_safety']}")
    print(f"- policy_candidate_count: {overview['policy_candidate_count']}")
    print(f"- reward_delta: {overview['reward_delta']}")
    print(f"- snapshot: {output_path}")

    missing = snapshot.get("missing_critical_artifacts", [])
    if missing:
        print("- missing_critical_artifacts: " + ", ".join(missing))
    else:
        print("- missing_critical_artifacts: none")

    warnings = snapshot.get("artifact_warnings", [])
    if warnings:
        print("- artifact_warnings: " + "; ".join(str(item) for item in warnings))
    else:
        print("- artifact_warnings: none")

    config_error = snapshot.get("config_error")
    if config_error:
        print(f"- config_error: {config_error}")

    if args.strict and (missing or config_error or warnings):
        print(
            json.dumps(
                {
                    "missing_critical_artifacts": missing,
                    "artifact_warnings": warnings,
                    "config_error": config_error,
                },
                indent=2,
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
