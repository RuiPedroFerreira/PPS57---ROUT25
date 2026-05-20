#!/usr/bin/env python3
"""Wrapper sobre o `tools/route/sort_routes.py` do SUMO.

Item 15 / item 6: o ficheiro de rotas é mantido à mão; este utilitário
permite reordená-lo deterministicamente (in-place) sem risco de perda de
elementos, evitando o problema do "ignore-route-errors" que descartava
silenciosamente autocarros fora de ordem temporal.

Uso:
    python -m pps57_sumo.sort_routes [--routes sumo/routes/routes.rou.xml]
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def find_sumo_sort_routes() -> Path:
    """Localiza o `sort_routes.py` do SUMO via $SUMO_HOME ou fallback no PATH."""
    sumo_home = os.environ.get("SUMO_HOME")
    if sumo_home:
        candidate = Path(sumo_home) / "tools" / "route" / "sort_routes.py"
        if candidate.exists():
            return candidate
    sumo_bin = shutil.which("sumo")
    if sumo_bin:
        candidate = Path(sumo_bin).resolve().parents[1] / "share" / "sumo" / "tools" / "route" / "sort_routes.py"
        if candidate.exists():
            return candidate
    raise SystemExit(
        "sort_routes.py não encontrado. Define SUMO_HOME ou garante que o SUMO está no PATH."
    )


def sort_in_place(routes_path: Path) -> None:
    tool = find_sumo_sort_routes()
    # O sort_routes.py do SUMO escreve para outfile; usamos um temp + move atómico.
    tmp_out = routes_path.with_suffix(routes_path.suffix + ".sorted")
    try:
        subprocess.check_call([sys.executable, str(tool), str(routes_path), "-o", str(tmp_out)])
        tmp_out.replace(routes_path)
    finally:
        if tmp_out.exists():
            tmp_out.unlink()
    print(f"Ordenado: {routes_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--routes",
        type=Path,
        default=Path("sumo/routes/routes.rou.xml"),
        help="Caminho do ficheiro de rotas a ordenar in-place.",
    )
    args = parser.parse_args()
    if not args.routes.exists():
        raise SystemExit(f"Ficheiro de rotas não encontrado: {args.routes}")
    sort_in_place(args.routes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
