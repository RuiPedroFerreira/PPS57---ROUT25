#!/usr/bin/env python3
"""Audit C-ITS/TSP protocol lifecycle JSONL artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.audit import audit_protocol_lifecycle, lifecycle_violations  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cits", type=Path, default=ROOT / "outputs/cits_messages.jsonl")
    parser.add_argument("--decisions", type=Path, default=ROOT / "outputs/tsp_decisions.jsonl")
    parser.add_argument("--actuations", type=Path, default=ROOT / "outputs/tsp_actuation.jsonl")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "reports/protocol_lifecycle_audit.json"
    )
    args = parser.parse_args()

    report = audit_protocol_lifecycle(args.cits, args.decisions, args.actuations)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    # Fail-closed (B30): the report is always written for inspection, but a
    # lifecycle with missing final SSEMs, invalid state transitions, or actuation
    # errors is a protocol failure — exit non-zero so CI / `make` gates catch it
    # instead of the old unconditional "OK audit" + return 0.
    violations = lifecycle_violations(report)
    if violations:
        detail = ", ".join(f"{key}={count}" for key, count in sorted(violations.items()))
        print(f"FAIL audit: violações de ciclo de vida do protocolo: {detail} (ver {args.output})")
        return 1
    print(f"OK audit: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
