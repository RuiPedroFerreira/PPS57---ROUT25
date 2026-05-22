#!/usr/bin/env python3
"""Build and run the PPS57 Porto/Boavista SUMO base scenario."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise SystemExit(
            f"Required binary '{binary}' not found in PATH. Install SUMO and ensure SUMO binaries are available."
        )
    return path


def run(cmd: list[str]) -> None:
    print("$ " + " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> None:
    require("netconvert")
    require("sumo")
    (ROOT / "outputs").mkdir(exist_ok=True)
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "sumo/network").mkdir(parents=True, exist_ok=True)

    run([sys.executable, "src/pps57_sumo/generate_plain_corridor.py", "--config", "configs/sumo_scenario_base.json", "--output", "sumo/plain"])
    run([
        "netconvert",
        "--node-files", "sumo/plain/corredor.nod.xml",
        "--edge-files", "sumo/plain/corredor.edg.xml",
        "--output-file", "sumo/network/corredor.net.xml",
        "--no-turnarounds", "true",
        "--tls.default-type", "static",
        "--tls.cycle.time", "90",
        "--tls.yellow.time", "3",
    ])
    run(["sumo", "-c", "sumo/corredor.sumocfg", "--duration-log.statistics"])
    run([sys.executable, "src/pps57_sumo/parse_tripinfo.py", "--tripinfo", "outputs/tripinfo.xml", "--out", "reports/baseline_kpis.json"])


if __name__ == "__main__":
    main()
