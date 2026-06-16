#!/usr/bin/env python3
"""Run C-ITS/V2X emulation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config  # noqa: E402
from pps57_cits.controller import CITSEmulationController  # noqa: E402
from pps57_cits.traci_adapter import TraciUnavailableError  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="C-ITS/V2X emulation for traffic-signal priority.")
    parser.add_argument(
        "--config",
        default="configs/cits_v2x_config.json",
        help="Ficheiro JSON de configuração C-ITS.",
    )
    parser.add_argument(
        "--mode",
        choices=["sumo"],
        default="sumo",
        help="Modo de execução. Apenas SUMO/TraCI é suportado.",
    )
    parser.add_argument("--steps", type=int, default=60, help="Número máximo de passos no SUMO.")
    parser.add_argument("--sumo-binary", default="sumo", help="Binário SUMO para TraCI.")
    parser.add_argument("--gui", action="store_true", help="Usa sumo-gui em vez de sumo.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_cits_config(ROOT / args.config, root=ROOT)
    controller = CITSEmulationController(config)

    try:
        summary = controller.run_with_sumo(
            steps=args.steps, sumo_binary=args.sumo_binary, gui=args.gui
        )
    except TraciUnavailableError as exc:
        print(f"Erro TraCI/SUMO: {exc}", file=sys.stderr)
        return 2

    print("Resumo da emulação C-ITS:")
    for key, value in summary.items():
        print(f"- {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
