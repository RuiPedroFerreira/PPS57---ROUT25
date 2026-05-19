#!/usr/bin/env python3
"""Static validation for the PPS57 SUMO realistic scenario.

This does not replace validation with SUMO. It checks the file structure and XML well-formedness.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from xml.etree import ElementTree as ET

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


def validate(root: Path) -> None:
    missing = [path for path in REQUIRED_FILES if not (root / path).exists()]
    if missing:
        raise SystemExit("Missing required files:\n" + "\n".join(missing))
    for rel in XML_FILES:
        ET.parse(root / rel)
        print(f"OK XML: {rel}")
    print("Static validation completed. Runtime validation with netconvert/sumo is still required.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    validate(args.root)


if __name__ == "__main__":
    main()
