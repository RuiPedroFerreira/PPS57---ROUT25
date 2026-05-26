#!/usr/bin/env python3
"""Runtime smoke validation for the generated SUMO scenario."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.build_network import build_sumo_artifacts  # noqa: E402
from pps57_sumo.build_network import sumo_environment  # noqa: E402
from pps57_sumo.parse_tripinfo import parse_tripinfo  # noqa: E402


STRUCTURAL_WARNING_PATTERNS = (
    "has no connection",
    "emergency stop",
    "teleporting vehicle",
    "collision with",
    "collision between",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=Path("configs/sumo_scenario_base.json"), type=Path)
    parser.add_argument("--steps", type=int, default=1800)
    parser.add_argument("--sumo-binary", default="sumo")
    args = parser.parse_args()

    for binary in ("netconvert", args.sumo_binary):
        if shutil.which(binary) is None:
            raise SystemExit(f"Required SUMO binary not found in PATH: {binary}")

    smoke_dir = ROOT / "outputs/sumo_smoke"
    config = json.loads((ROOT / args.config).read_text(encoding="utf-8"))
    config["random_seed"] = int(config.get("random_seed", 57))
    config.setdefault("detectors", {})
    config["detectors"]["e1_output"] = "../../e1_detectors.xml"
    config["detectors"]["e2_output"] = "../../e2_queues.xml"
    artifacts = build_sumo_artifacts(
        config,
        root=ROOT,
        base_dir=smoke_dir / "sumo",
        output_dir=smoke_dir,
        build_net=True,
    )
    cmd = [
        args.sumo_binary,
        "-c",
        str(artifacts.sumocfg_file),
        "--duration-log.statistics",
        "--end",
        str(args.steps),
        "--pedestrian.model",
        "striping",
    ]
    completed = subprocess.run(
        cmd,
        cwd=ROOT,
        check=True,
        env=sumo_environment(),
        text=True,
        capture_output=True,
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)

    runtime_log = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    structural_warnings = [
        line
        for line in runtime_log.splitlines()
        if any(pattern in line.lower() for pattern in STRUCTURAL_WARNING_PATTERNS)
    ]
    if structural_warnings:
        raise SystemExit(
            "SUMO smoke failed; structural runtime warnings found:\n"
            + "\n".join(structural_warnings)
        )

    tripinfo = smoke_dir / "tripinfo.xml"
    e1 = smoke_dir / "e1_detectors.xml"
    e2 = smoke_dir / "e2_queues.xml"
    missing = [str(path.relative_to(ROOT)) for path in (tripinfo, e1, e2) if not path.exists()]
    if missing:
        raise SystemExit("SUMO smoke failed; missing outputs: " + ", ".join(missing))
    kpis = parse_tripinfo(tripinfo)
    if kpis.get("all_vehicles", {}).get("vehicles", 0) <= 0:
        raise SystemExit("SUMO smoke failed; no completed vehicles in tripinfo.")
    print(json.dumps({"status": "pass", "tripinfo": kpis.get("all_vehicles", {})}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
