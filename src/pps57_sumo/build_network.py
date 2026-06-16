#!/usr/bin/env python3
"""Build SUMO artifacts through one shared path.

The project has several entry points that need a SUMO network: baseline runs,
scenario-suite runs, C-ITS/TSP runs and GUI inspection. Keeping netconvert flags
and post-build TLS edits in one module prevents those paths from drifting.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable, Sequence

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.apply_tls_offsets import apply_tls_offsets
from pps57_sumo.environment import ensure_sumo_environment
from pps57_sumo.generate_plain_corridor import generate
from pps57_sumo.scenarios import apply_scenario_profile


Runner = Callable[[Sequence[str], Path], None]


@dataclass(frozen=True)
class SumoArtifacts:
    base_dir: Path
    plain_dir: Path
    network_file: Path
    routes_file: Path
    bus_stops_file: Path
    detectors_file: Path
    parking_file: Path
    calibrators_file: Path
    tls_offsets_file: Path
    sumocfg_file: Path


def artifact_paths(base_dir: Path) -> SumoArtifacts:
    base_dir = Path(base_dir)
    return SumoArtifacts(
        base_dir=base_dir,
        plain_dir=base_dir / "plain",
        network_file=base_dir / "network" / "corredor.net.xml",
        routes_file=base_dir / "routes" / "routes.rou.xml",
        bus_stops_file=base_dir / "additional" / "bus_stops.add.xml",
        detectors_file=base_dir / "additional" / "detectors.add.xml",
        parking_file=base_dir / "additional" / "parking.add.xml",
        calibrators_file=base_dir / "additional" / "calibrators.add.xml",
        tls_offsets_file=base_dir / "additional" / "tls_offsets.add.xml",
        sumocfg_file=base_dir / "corredor.sumocfg",
    )


def build_sumo_artifacts(
    config: dict,
    *,
    root: Path,
    base_dir: Path,
    output_dir: Path | None = None,
    build_net: bool = True,
    netconvert_binary: str = "netconvert",
    runner: Runner | None = None,
) -> SumoArtifacts:
    """Generate plain/routes/additional files and optionally compile the net.xml."""
    root = Path(root)
    artifacts = artifact_paths(root / base_dir if not base_dir.is_absolute() else base_dir)
    artifacts.network_file.parent.mkdir(parents=True, exist_ok=True)
    generate(
        config,
        artifacts.plain_dir,
        routes_output=artifacts.routes_file,
        bus_stops_output=artifacts.bus_stops_file,
        detectors_output=artifacts.detectors_file,
        parking_output=artifacts.parking_file,
        calibrators_output=artifacts.calibrators_file,
        tls_offsets_output=artifacts.tls_offsets_file,
    )
    _ensure_detector_output_dirs(config, artifacts)
    resolved_output_dir = (
        root / "outputs"
        if output_dir is None
        else (root / output_dir if not output_dir.is_absolute() else output_dir)
    )
    write_sumocfg(config, artifacts, output_dir=resolved_output_dir)
    if build_net:
        command = netconvert_command(config, artifacts, netconvert_binary=netconvert_binary)
        run = runner or _run_subprocess
        run(command, root)
        modified = apply_tls_offsets(artifacts.network_file, artifacts.tls_offsets_file)
        if modified:
            print(f"applied {modified} tls offsets")
    return artifacts


def netconvert_command(
    config: dict,
    artifacts: SumoArtifacts,
    *,
    netconvert_binary: str = "netconvert",
) -> list[str]:
    network_cfg = config.get("network", {}) if isinstance(config.get("network"), dict) else {}
    cycle_time = "90"
    intersections = (
        network_cfg.get("intersections", [])
        if isinstance(network_cfg.get("intersections"), list)
        else []
    )
    cycles = {int(i.get("tls_cycle_s", 90)) for i in intersections if "tls_cycle_s" in i}
    if cycles and len(cycles) == 1:
        cycle_time = str(cycles.pop())

    cmd = [
        netconvert_binary,
        "--node-files",
        str(artifacts.plain_dir / "corredor.nod.xml"),
        "--edge-files",
        str(artifacts.plain_dir / "corredor.edg.xml"),
        "--output-file",
        str(artifacts.network_file),
        "--no-turnarounds",
        "true",
        "--tls.default-type",
        "static",
        "--tls.cycle.time",
        cycle_time,
        "--tls.yellow.time",
        "3",
    ]
    if network_cfg.get("enable_sidewalks"):
        cmd.extend(["--sidewalks.guess", "true"])
    if network_cfg.get("enable_pedestrian_crossings"):
        ped_cfg = (
            network_cfg.get("pedestrian_crossings", {})
            if isinstance(network_cfg.get("pedestrian_crossings"), dict)
            else {}
        )
        cmd.extend(["--crossings.guess", "true", "--walkingareas", "true"])
        # Override netconvert defaults so the guessed crossing phase has a
        # realistic clearance (WALK + flashing-DW). Default green is ~5s,
        # default clearance is ~3s — too tight for a 8m crossing at 1.2 m/s.
        cmd.extend(
            [
                "--tls.crossing-min.time",
                str(int(ped_cfg.get("crossing_min_s", 6))),
                "--tls.crossing-clearance.time",
                str(int(ped_cfg.get("crossing_clearance_s", 5))),
            ]
        )
    return cmd


def _ensure_detector_output_dirs(config: dict, artifacts: SumoArtifacts) -> None:
    """Create parent dirs for E1/E2 detector output files.

    Detector ``file=`` attributes in additional/detectors.add.xml are resolved
    by SUMO relative to that additional file. If the parent directory does not
    exist, SUMO aborts with 'Could not build output file'. Materialise it here
    so the bundle is self-contained regardless of where it was generated.
    """
    detector_cfg = config.get("detectors", {}) if isinstance(config.get("detectors"), dict) else {}
    additional_dir = artifacts.detectors_file.parent
    for key in ("e1_output", "e2_output"):
        rel_value = detector_cfg.get(key)
        if not rel_value:
            continue
        target = (additional_dir / str(rel_value)).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)


def write_sumocfg(config: dict, artifacts: SumoArtifacts, *, output_dir: Path) -> None:
    """Write a sumocfg local to the artifact bundle."""
    artifacts.sumocfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_dir = artifacts.sumocfg_file.parent

    def rel(path: Path) -> str:
        return os.path.relpath(path, cfg_dir)

    output_dir.mkdir(parents=True, exist_ok=True)

    additional_files = [
        rel(artifacts.bus_stops_file),
        rel(artifacts.detectors_file),
        rel(artifacts.parking_file),
    ]
    begin = int(float(config.get("simulation_begin_s", 0)))
    end = int(float(config.get("simulation_end_s", 7200)))
    step_length = float(config.get("simulation_step_length_s", 1.0))
    seed = int(config.get("random_seed", 57))
    # Tune actuated TLS defaults when any intersection requests actuated control.
    # `tls.actuated.jam-threshold` is a sumo-runtime CLI option and lives in the
    # <processing> block. The per-TLS `detector-gap` is NOT a CLI option in
    # SUMO 1.26 (only jam-threshold and detector-length are global flags); it
    # must be set via `<param key="detector-gap" value="..."/>` inside each
    # actuated `<tlLogic>` if a non-default value is required. We accept the
    # SUMO default (3.0s) here; tighten via additional-files override if a
    # SCATS-style sub-3s gap becomes necessary.
    network_cfg = config.get("network", {}) if isinstance(config.get("network"), dict) else {}
    intersections = (
        network_cfg.get("intersections", [])
        if isinstance(network_cfg.get("intersections"), list)
        else []
    )
    actuated_lines = ""
    if any(str(i.get("tls_type", "")) == "actuated" for i in intersections):
        actuated_cfg = (
            network_cfg.get("actuated_tls", {})
            if isinstance(network_cfg.get("actuated_tls"), dict)
            else {}
        )
        jam_threshold = float(actuated_cfg.get("jam_threshold_s", 30.0))
        actuated_lines = f'    <tls.actuated.jam-threshold value="{jam_threshold:g}"/>\n'
    text = f"""<?xml version="1.0" ?>
<configuration>
  <input>
    <net-file value="{rel(artifacts.network_file)}"/>
    <route-files value="{rel(artifacts.routes_file)}"/>
    <additional-files value="{",".join(additional_files)}"/>
    <!-- Calibrators are generated as scaffolding but intentionally not loaded.
         SUMO calibrators enforce synthetic targets by inserting/removing
         vehicles, so they should only be activated with real count data. -->
  </input>
  <time>
    <begin value="{begin}"/>
    <end value="{end}"/>
    <step-length value="{step_length:g}"/>
  </time>
  <output>
    <tripinfo-output value="{rel(output_dir / "tripinfo.xml")}"/>
    <summary-output value="{rel(output_dir / "summary.xml")}"/>
    <statistic-output value="{rel(output_dir / "statistics.xml")}"/>
    <emission-output value="{rel(output_dir / "emissions.xml")}"/>
  </output>
  <report>
    <duration-log.statistics value="true"/>
  </report>
  <processing>
    <time-to-teleport value="300"/>
{actuated_lines}    <!-- Do not enable ignore-route-errors here: sorted route validation should
         fail loudly instead of silently dropping out-of-order vehicles. -->
  </processing>
  <random_number>
    <seed value="{seed}"/>
  </random_number>
</configuration>
"""
    artifacts.sumocfg_file.write_text(text, encoding="utf-8")


def sumo_environment() -> dict[str, str]:
    """Return an environment with a usable SUMO_HOME."""
    return ensure_sumo_environment()


def _run_subprocess(cmd: Sequence[str], cwd: Path) -> None:
    print("$ " + " ".join(str(part) for part in cmd))
    subprocess.run(list(cmd), cwd=cwd, check=True, env=sumo_environment())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/sumo_scenario_base.json"))
    parser.add_argument("--scenario", default=None)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--base-dir", type=Path, default=Path("sumo"))
    parser.add_argument("--skip-netconvert", action="store_true")
    parser.add_argument("--netconvert-binary", default="netconvert")
    args = parser.parse_args()

    root = args.root.resolve()
    config = json.loads((root / args.config).read_text(encoding="utf-8"))
    config = apply_scenario_profile(config, args.scenario)
    build_sumo_artifacts(
        config,
        root=root,
        base_dir=args.base_dir,
        build_net=not args.skip_netconvert,
        netconvert_binary=args.netconvert_binary,
    )
    print(f"Built SUMO artifacts in {root / args.base_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
