#!/usr/bin/env python3
"""Run TSP control with mandatory safety validation."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config  # noqa: E402
from pps57_cits.traci_adapter import TraciUnavailableError  # noqa: E402
from pps57_tsp.config import load_tsp_config  # noqa: E402
from pps57_tsp.controller import TSPControlController  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TSP Decision Engine + Safety Layer.")
    parser.add_argument("--config", default="configs/cits_config.json", help="Configuração C-ITS base.")
    parser.add_argument("--tsp-config", default="configs/tsp_config.json", help="Configuração do motor TSP.")
    parser.add_argument("--mode", choices=["dry-run", "sumo"], default="dry-run", help="Modo de execução.")
    parser.add_argument("--steps", type=int, default=None, help="Número máximo de passos.")
    parser.add_argument("--sumo-binary", default="sumo", help="Binário SUMO para TraCI.")
    parser.add_argument("--gui", action="store_true", help="Usa sumo-gui em vez de sumo.")
    parser.add_argument("--no-actuation", action="store_true", help="No modo SUMO, calcula decisões mas não aplica comandos TraCI.")
    parser.add_argument("--policy-mode", choices=["baseline", "optimized"], default="baseline", help="Runtime policy mode.")
    parser.add_argument("--policy-report", default=None, help="Path to exported runtime policy report.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cits_config = load_cits_config(ROOT / args.config, root=ROOT)
    tsp_config = load_tsp_config(ROOT / args.tsp_config, root=ROOT)
    controller = TSPControlController(
        cits_config,
        tsp_config,
        policy_mode=args.policy_mode,
        policy_report_path=args.policy_report,
    )

    try:
        if args.mode == "dry-run":
            summary = controller.run_dry_run(steps=args.steps)
        else:
            summary = controller.run_with_sumo(
                steps=args.steps,
                sumo_binary=args.sumo_binary,
                gui=args.gui,
                apply_actuation=not args.no_actuation,
            )
    except TraciUnavailableError as exc:
        print(f"Erro TraCI/SUMO: {exc}", file=sys.stderr)
        return 2

    print("TSP control summary:")
    for key, value in summary.items():
        print(f"- {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
