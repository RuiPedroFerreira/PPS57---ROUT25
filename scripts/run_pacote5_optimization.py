#!/usr/bin/env python3
"""Executa o Pacote 5 - otimização offline e política segura."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config  # noqa: E402
from pps57_opt.config import load_optimization_config  # noqa: E402
from pps57_opt.optimizer import OfflineOptimizationController  # noqa: E402
from pps57_tsp.config import load_tsp_config  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pacote 5 - otimização offline/RL com Safety Layer obrigatória.")
    parser.add_argument("--config", default="configs/cits_config.json", help="Configuração C-ITS base.")
    parser.add_argument("--tsp-config", default="configs/tsp_config.json", help="Configuração TSP/Safety Layer.")
    parser.add_argument("--optimization-config", default="configs/optimization_config.json", help="Configuração do Pacote 5.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cits_config = load_cits_config(ROOT / args.config, root=ROOT)
    tsp_config = load_tsp_config(ROOT / args.tsp_config, root=ROOT)
    optimization_config = load_optimization_config(ROOT / args.optimization_config, root=ROOT)
    summary = OfflineOptimizationController(cits_config, tsp_config, optimization_config).run()

    print("Resumo do Pacote 5:")
    for key, value in summary.items():
        print(f"- {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
