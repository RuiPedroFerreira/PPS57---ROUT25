#!/usr/bin/env python3
"""Build and run the PPS57 Porto/Boavista SUMO base scenario."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.build_network import build_sumo_artifacts, sumo_environment  # noqa: E402
from pps57_sumo.scenarios import apply_scenario_profile  # noqa: E402


def require(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise SystemExit(
            f"Required binary '{binary}' not found in PATH. Install SUMO and ensure SUMO binaries are available."
        )
    return path


def run(cmd: list[str]) -> None:
    print("$ " + " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True, env=sumo_environment())


def main() -> None:
    require("netconvert")
    require("sumo")
    (ROOT / "outputs").mkdir(exist_ok=True)
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "sumo/network").mkdir(parents=True, exist_ok=True)

    base_config = json.loads((ROOT / "configs/sumo_scenario_base.json").read_text(encoding="utf-8"))
    config = apply_scenario_profile(base_config, "baseline_am_peak")
    artifacts = build_sumo_artifacts(config, root=ROOT, base_dir=Path("sumo"))
    run(["sumo", "-c", str(artifacts.sumocfg_file), "--duration-log.statistics"])
    run(
        [
            sys.executable,
            "src/pps57_sumo/parse_tripinfo.py",
            "--tripinfo",
            "outputs/tripinfo.xml",
            "--out",
            "reports/baseline_kpis.json",
        ]
    )


if __name__ == "__main__":
    main()
