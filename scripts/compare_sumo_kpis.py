#!/usr/bin/env python3
"""Compare SUMO KPI JSON files for baseline vs RL runs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_opt.ab_compare import write_kpi_comparison  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare baseline and RL SUMO KPI reports.")
    parser.add_argument("--baseline-kpis", required=True, type=Path)
    parser.add_argument("--rl-kpis", required=True, type=Path)
    parser.add_argument("--json-out", default="reports/sumo_baseline_vs_rl_kpi_comparison.json")
    parser.add_argument("--md-out", default="reports/sumo_baseline_vs_rl_kpi_comparison.md")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    baseline = json.loads(args.baseline_kpis.read_text(encoding="utf-8"))
    rl = json.loads(args.rl_kpis.read_text(encoding="utf-8"))
    payload = write_kpi_comparison(
        baseline,
        rl,
        json_path=ROOT / args.json_out,
        markdown_path=ROOT / args.md_out,
    )
    print("SUMO KPI baseline vs RL comparison:")
    print(f"- rows: {len(payload['rows'])}")
    print(f"- json: {ROOT / args.json_out}")
    print(f"- markdown: {ROOT / args.md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
