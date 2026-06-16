#!/usr/bin/env python3
"""Run tabular Q-learning policy training on simulated TSP scenarios."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config  # noqa: E402
from pps57_opt.config import load_policy_optimization_config  # noqa: E402
from pps57_opt.rl_trainer import TabularQLearningController  # noqa: E402
from pps57_tsp.config import load_tsp_config  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Tabular Q-learning training for safe TSP policy selection."
    )
    parser.add_argument(
        "--config", default="configs/cits_v2x_config.json", help="Base C-ITS configuration."
    )
    parser.add_argument(
        "--tsp-config",
        default="configs/tsp_safety_config.json",
        help="TSP/Safety Layer configuration.",
    )
    parser.add_argument(
        "--policy-config",
        default="configs/policy_training_config.json",
        help="Policy training configuration.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cits_config = load_cits_config(ROOT / args.config, root=ROOT)
    tsp_config = load_tsp_config(ROOT / args.tsp_config, root=ROOT)
    optimization_config = load_policy_optimization_config(ROOT / args.policy_config, root=ROOT)
    summary = TabularQLearningController(cits_config, tsp_config, optimization_config).run()

    print("RL training summary:")
    for key, value in summary.items():
        print(f"- {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
