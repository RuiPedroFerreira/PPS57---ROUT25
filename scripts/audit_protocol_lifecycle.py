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

from pps57_cits.audit import audit_protocol_lifecycle  # noqa: E402


def main() -> None:
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
    print(f"OK audit: {args.output}")


if __name__ == "__main__":
    main()
