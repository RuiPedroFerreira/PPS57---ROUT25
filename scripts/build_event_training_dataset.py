#!/usr/bin/env python3
"""Build an offline learning JSONL dataset from generated event logs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_opt.event_dataset import write_event_training_dataset  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create event-based learning rows from C-ITS/TSP logs."
    )
    parser.add_argument("--cits-log", default="outputs/cits_messages.jsonl")
    parser.add_argument("--decision-log", default="outputs/tsp_decisions.jsonl")
    parser.add_argument("--actuation-log", default="outputs/tsp_actuation.jsonl")
    parser.add_argument("--out", default="outputs/event_training_dataset.jsonl")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = write_event_training_dataset(
        cits_log=ROOT / args.cits_log,
        decision_log=ROOT / args.decision_log,
        actuation_log=ROOT / args.actuation_log,
        output_path=ROOT / args.out,
    )
    print("Event training dataset summary:")
    for key, value in summary.items():
        print(f"- {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
