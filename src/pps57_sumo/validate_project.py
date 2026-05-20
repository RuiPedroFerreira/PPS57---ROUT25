#!/usr/bin/env python3
"""Static validation for the PPS57 SUMO realistic scenario.

This does not replace validation with SUMO. It checks the file structure and XML well-formedness.
"""
from __future__ import annotations

import argparse
from pathlib import Path
# M4: defusedxml em vez do stdlib para validação de XML do projeto.
from defusedxml import ElementTree as ET  # type: ignore[import-untyped]

REQUIRED_FILES = [
    "configs/corridor_config.json",
    "configs/corridor_config_porto_boavista_realistic.json",
    "configs/scenarios.yaml",
    "configs/cits_config.json",
    "configs/tsp_config.json",
    "sumo/plain/corredor.nod.xml",
    "sumo/plain/corredor.edg.xml",
    "sumo/routes/routes.rou.xml",
    "sumo/additional/bus_stops.add.xml",
    "sumo/additional/detectors.add.xml",
    "sumo/corredor.sumocfg",
    "docs/CENARIO_REALISTA_BOAVISTA.md",
]

XML_FILES = [
    "sumo/plain/corredor.nod.xml",
    "sumo/plain/corredor.edg.xml",
    "sumo/routes/routes.rou.xml",
    "sumo/additional/bus_stops.add.xml",
    "sumo/additional/detectors.add.xml",
    "sumo/corredor.sumocfg",
]


def validate_routes_sorted(routes_path: Path) -> None:
    """Falha cedo se o ficheiro de rotas estiver fora de ordem temporal.

    Com `<ignore-route-errors value="true"/>` no sumocfg, o SUMO degrada o erro
    "should be sorted" para warning e **descarta** silenciosamente os veículos
    fora de ordem (incluindo autocarros). Esta verificação estática evita a
    regressão silenciosa.
    """
    tree = ET.parse(routes_path)
    root_el = tree.getroot()
    timeline: list[tuple[float, str, str]] = []
    for child in root_el:
        if child.tag in {"vehicle", "person"}:
            time_attr = child.attrib.get("depart")
        elif child.tag == "flow":
            time_attr = child.attrib.get("begin")
        else:
            continue
        if time_attr is None:
            continue
        try:
            timeline.append((float(time_attr), child.tag, child.attrib.get("id", "?")))
        except ValueError:
            # Valores não-numéricos (ex.: depart="triggered") são ignorados.
            continue
    for i in range(1, len(timeline)):
        if timeline[i][0] < timeline[i - 1][0]:
            prev_t, prev_tag, prev_id = timeline[i - 1]
            cur_t, cur_tag, cur_id = timeline[i]
            raise SystemExit(
                f"Route file {routes_path} not sorted by departure: "
                f"<{cur_tag} id='{cur_id}' time={cur_t}> appears after "
                f"<{prev_tag} id='{prev_id}' time={prev_t}>. "
                f"Fix with: python $SUMO_HOME/tools/route/sort_routes.py "
                f"{routes_path} -o {routes_path}"
            )
    print(f"OK sorted: {routes_path.name} ({len(timeline)} time-dependent elements)")


def validate(root: Path) -> None:
    missing = [path for path in REQUIRED_FILES if not (root / path).exists()]
    if missing:
        raise SystemExit("Missing required files:\n" + "\n".join(missing))
    for rel in XML_FILES:
        ET.parse(root / rel)
        print(f"OK XML: {rel}")
    validate_routes_sorted(root / "sumo/routes/routes.rou.xml")
    print("Static validation completed. Runtime validation with netconvert/sumo is still required.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    validate(args.root)


if __name__ == "__main__":
    main()
