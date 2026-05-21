#!/usr/bin/env python3
"""Run and report the PPS57 TSP demonstrator.

The demonstrator compares:
- SUMO baseline without TSP intervention;
- TSP direct TraCI actuation;
- TSP through the simulated controller contract.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config  # noqa: E402
from pps57_cits.traci_adapter import TraciUnavailableError  # noqa: E402
from pps57_opt.demonstrator import (  # noqa: E402
    build_demonstrator_report,
    load_demonstrator_run,
    write_demonstrator_report,
)
from pps57_sumo.parse_tripinfo import parse_tripinfo  # noqa: E402
from pps57_tsp.config import TSPConfig, load_tsp_config  # noqa: E402
from pps57_tsp.controller import TSPControlController  # noqa: E402


SNAPSHOT_PATHS = (
    "outputs/tripinfo.xml",
    "outputs/summary.xml",
    "outputs/statistics.xml",
    "outputs/tsp_decisions.jsonl",
    "outputs/tsp_actuation.jsonl",
    "outputs/cits_messages.jsonl",
    "reports/tsp_emulation_summary.json",
    "reports/cits_emulation_summary.json",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the SUMO/TraCI TSP demonstrator and write evidence reports.")
    parser.add_argument("--config", default="configs/cits_config.json", help="Base C-ITS configuration.")
    parser.add_argument("--tsp-config", default="configs/tsp_config.json", help="TSP/Safety Layer configuration.")
    parser.add_argument("--policy-config", default=None, help="Accepted for platform command compatibility; not used.")
    parser.add_argument("--steps", type=int, default=7200, help="SUMO/TraCI steps for TSP runs.")
    parser.add_argument("--sumo-binary", default="sumo", help="SUMO binary for TraCI.")
    parser.add_argument("--no-actuation", action="store_true", help="Calculate TSP decisions without applying commands.")
    parser.add_argument("--baseline-root", default=None, help="Existing SUMO baseline snapshot root.")
    parser.add_argument("--tsp-root", default=None, help="Existing TSP direct snapshot root.")
    parser.add_argument("--controller-root", default=None, help="Existing TSP controller snapshot root.")
    parser.add_argument("--report-only", action="store_true", help="Do not run SUMO; require the three snapshot roots.")
    parser.add_argument(
        "--snapshot-root",
        default=None,
        help="Root for generated snapshots. Defaults to outputs/demonstrator/run-YYYYmmdd-HHMMSS.",
    )
    parser.add_argument("--json-out", default="reports/tsp_demonstrator_report.json", help="JSON report output.")
    parser.add_argument("--md-out", default="reports/tsp_demonstrator_report.md", help="Markdown report output.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cits_config = load_cits_config(ROOT / args.config, root=ROOT)
    tsp_config = load_tsp_config(ROOT / args.tsp_config, root=ROOT)
    snapshot_root = _snapshot_root(args.snapshot_root)

    if args.report_only and not (args.baseline_root and args.tsp_root and args.controller_root):
        raise SystemExit("--report-only requires --baseline-root, --tsp-root, and --controller-root.")

    baseline_root = _path_from_root(args.baseline_root) if args.baseline_root else None
    tsp_root = _path_from_root(args.tsp_root) if args.tsp_root else None
    controller_root = _path_from_root(args.controller_root) if args.controller_root else None

    try:
        if baseline_root is None:
            print("[DEMO] Running SUMO baseline without TSP.")
            _run_baseline(args.steps)
            _write_current_kpis("sumo_baseline")
            baseline_root = _snapshot_artifacts(snapshot_root / "sumo_baseline", "sumo_baseline")

        if tsp_root is None:
            print("[DEMO] Rebuilding static signal program for direct TSP.")
            _build_static_network()
            print("[DEMO] Running TSP direct TraCI actuation.")
            _run_tsp(cits_config, _with_controller_simulation(tsp_config, enabled=False), args)
            _write_current_kpis("tsp")
            tsp_root = _snapshot_artifacts(snapshot_root / "tsp", "tsp")

        if controller_root is None:
            print("[DEMO] Rebuilding static signal program for simulated controller run.")
            _build_static_network()
            print("[DEMO] Running TSP through simulated controller contract.")
            _run_tsp(cits_config, _with_controller_simulation(tsp_config, enabled=True), args)
            _write_current_kpis("tsp_controller")
            controller_root = _snapshot_artifacts(snapshot_root / "tsp_controller", "tsp_controller")
    except TraciUnavailableError as exc:
        print(f"Erro TraCI/SUMO: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"Command failed with exit code {exc.returncode}: {' '.join(exc.cmd)}", file=sys.stderr)
        return exc.returncode

    report = build_demonstrator_report(
        baseline=load_demonstrator_run(baseline_root, "sumo_baseline"),
        tsp=load_demonstrator_run(tsp_root, "tsp"),
        tsp_controller=load_demonstrator_run(controller_root, "tsp_controller"),
    )
    write_demonstrator_report(
        report,
        json_path=ROOT / args.json_out,
        markdown_path=ROOT / args.md_out,
    )

    print("TSP demonstrator report:")
    print(f"- json: {ROOT / args.json_out}")
    print(f"- markdown: {ROOT / args.md_out}")
    print(f"- baseline snapshot: {baseline_root}")
    print(f"- tsp snapshot: {tsp_root}")
    print(f"- tsp_controller snapshot: {controller_root}")
    print(f"- verdict: {report['verdict']['status']}")
    return 0


def _snapshot_root(raw: str | None) -> Path:
    if raw:
        return _path_from_root(raw)
    run_id = datetime.now().strftime("run-%Y%m%d-%H%M%S")
    return ROOT / "outputs/demonstrator" / run_id


def _path_from_root(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def _run_baseline(steps: int | None) -> None:
    _build_network(tls_type="static")
    _require("sumo")
    cmd = ["sumo", "-c", "sumo/corredor.sumocfg", "--duration-log.statistics"]
    if steps is not None:
        cmd.extend(["--end", str(int(steps))])
    subprocess.run(cmd, cwd=ROOT, check=True)


def _run_tsp(cits_config, tsp_config: TSPConfig, args: argparse.Namespace) -> None:  # type: ignore[no-untyped-def]
    TSPControlController(
        cits_config,
        tsp_config,
        policy_mode="baseline",
    ).run_with_sumo(
        steps=args.steps,
        sumo_binary=args.sumo_binary,
        apply_actuation=not args.no_actuation,
    )


def _with_controller_simulation(tsp_config: TSPConfig, *, enabled: bool) -> TSPConfig:
    raw = deepcopy(tsp_config.raw)
    controller_simulation = dict(raw.get("controller_simulation", {}))
    controller_simulation["enabled"] = enabled
    raw["controller_simulation"] = controller_simulation
    return TSPConfig(root=tsp_config.root, raw=raw)


def _build_static_network() -> None:
    _build_network(tls_type="static")


def _build_network(*, tls_type: str) -> None:
    _require("netconvert")
    (ROOT / "outputs").mkdir(exist_ok=True)
    (ROOT / "reports").mkdir(exist_ok=True)
    (ROOT / "sumo/network").mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "src/pps57_sumo/generate_plain_corridor.py",
            "--config",
            "configs/corridor_config.json",
            "--output",
            "sumo/plain",
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [
            "netconvert",
            "--node-files",
            "sumo/plain/corredor.nod.xml",
            "--edge-files",
            "sumo/plain/corredor.edg.xml",
            "--output-file",
            "sumo/network/corredor.net.xml",
            "--no-turnarounds",
            "true",
            "--tls.default-type",
            tls_type,
            "--tls.cycle.time",
            "90",
            "--tls.yellow.time",
            "3",
        ],
        cwd=ROOT,
        check=True,
    )


def _write_current_kpis(label: str) -> None:
    tripinfo = ROOT / "outputs/tripinfo.xml"
    if not tripinfo.exists():
        return
    kpis = parse_tripinfo(tripinfo)
    out = ROOT / f"reports/{label}_kpis.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(kpis, indent=2, ensure_ascii=False), encoding="utf-8")


def _snapshot_artifacts(dest_root: Path, label: str) -> Path:
    dest_root.mkdir(parents=True, exist_ok=True)
    paths = (
        ("outputs/tripinfo.xml", "outputs/summary.xml", "outputs/statistics.xml")
        if label == "sumo_baseline"
        else SNAPSHOT_PATHS
    )
    for rel in [*paths, f"reports/{label}_kpis.json"]:
        src = ROOT / rel
        if not src.exists():
            continue
        dst = dest_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return dest_root


def _require(binary: str) -> None:
    if not shutil.which(binary):
        raise SystemExit(
            f"Required binary '{binary}' not found in PATH. Install SUMO and ensure SUMO binaries are available."
        )


if __name__ == "__main__":
    raise SystemExit(main())
