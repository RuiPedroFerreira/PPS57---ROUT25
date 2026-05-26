#!/usr/bin/env python3
"""Generate and optionally run configured SUMO validation scenarios."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.generate_plain_corridor import generate  # noqa: E402
from pps57_sumo.detector_kpis import parse_detector_kpis  # noqa: E402
from pps57_sumo.parse_tripinfo import parse_tripinfo  # noqa: E402
from pps57_sumo.parse_insertion import parse_insertion_kpis  # noqa: E402
from pps57_sumo.parse_emissions import parse_emissions  # noqa: E402
from pps57_sumo.apply_tls_offsets import apply_tls_offsets  # noqa: E402
from pps57_sumo.scenarios import (  # noqa: E402
    apply_scenario_profile,
    load_catalog,
    scenario_summary,
    validate_scenario_catalog,
)
from pps57_cits.config import load_cits_config  # noqa: E402
from pps57_cits.controller import CITSEmulationController  # noqa: E402
from pps57_tsp.config import load_tsp_config  # noqa: E402
from pps57_tsp.controller import TSPControlController  # noqa: E402

RUN_TYPES = ("baseline", "cits", "tsp_no_actuation", "tsp_actuation")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate/run SUMO scenarios from the scenario catalog.")
    parser.add_argument("--config", default="configs/sumo_scenario_base.json", type=Path)
    parser.add_argument("--catalog", default="configs/scenario_catalog.yaml", type=Path)
    parser.add_argument("--scenario", help="Scenario id to run. Use --all to run every catalog scenario.")
    parser.add_argument("--all", action="store_true", help="Run every scenario in the catalog.")
    parser.add_argument("--list", action="store_true", help="List configured scenarios and exit.")
    parser.add_argument("--generate-only", action="store_true", help="Generate SUMO XMLs but do not execute SUMO.")
    parser.add_argument(
        "--run-type",
        choices=[*RUN_TYPES, "comparison", "all"],
        default="baseline",
        help="Pipeline to run for each scenario.",
    )
    parser.add_argument("--steps", type=int, default=None, help="Optional max simulation steps for C-ITS/TSP runs.")
    parser.add_argument(
        "--seeds",
        nargs="+",
        type=int,
        default=None,
        help="One or more random seeds to run as replications. Overrides scenario_profile.random_seeds.",
    )
    parser.add_argument("--sumo-binary", default="sumo")
    parser.add_argument("--gui", action="store_true", help="Use sumo-gui for visual scenario execution.")
    parser.add_argument("--skip-build", action="store_true", help="Skip netconvert after generating plain files.")
    parser.add_argument("--outputs-dir", default=Path("outputs/scenarios"), type=Path)
    parser.add_argument("--reports-dir", default=Path("reports/scenarios"), type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_config = json.loads((ROOT / args.config).read_text(encoding="utf-8"))
    catalog = load_catalog(ROOT / args.catalog)
    summaries = validate_scenario_catalog(base_config, catalog)
    if args.list:
        for summary in summaries:
            print(
                f"{summary['scenario_id']}: cars~{summary['estimated_car_departures']} "
                f"buses~{summary['estimated_bus_departures']} events={summary['event_count']}"
            )
        return 0

    scenario_ids = list(catalog["scenarios"].keys()) if args.all else [args.scenario]
    if not scenario_ids or scenario_ids == [None]:
        raise SystemExit("Use --scenario <id>, --all, or --list.")

    if not args.generate_only:
        _require(args.sumo_binary)
    if not args.skip_build:
        _require("netconvert")

    run_summaries = []
    for scenario_id in scenario_ids:
        assert scenario_id is not None
        run_summaries.append(run_scenario(args, base_config, catalog, scenario_id))

    scenario_report = {
        "scenario_count": len(run_summaries),
        "scenarios": run_summaries,
    }
    reports_dir = ROOT / args.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "scenario_suite_summary.json").write_text(
        json.dumps(scenario_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (reports_dir / "scenario_suite_report.md").write_text(render_suite_report(scenario_report), encoding="utf-8")
    print(json.dumps(scenario_report, indent=2, ensure_ascii=False))
    return 0


def run_scenario(args: argparse.Namespace, base_config: dict, catalog: dict, scenario_id: str) -> dict:
    config = apply_scenario_profile(base_config, scenario_id)
    scenario_output_dir = ROOT / args.outputs_dir / scenario_id
    scenario_report_dir = ROOT / args.reports_dir / scenario_id
    scenario_output_dir.mkdir(parents=True, exist_ok=True)
    scenario_report_dir.mkdir(parents=True, exist_ok=True)

    if args.run_type == "comparison":
        run_types = ["baseline", "tsp_no_actuation", "tsp_actuation"]
    else:
        run_types = list(RUN_TYPES) if args.run_type == "all" else [args.run_type]

    seeds = _resolve_seeds(args, config)
    scenario_runs: dict[str, dict] = {}
    for run_type in run_types:
        if len(seeds) == 1:
            scenario_runs[run_type] = run_scenario_type(
                args=args,
                base_config=config,
                catalog=catalog,
                scenario_id=scenario_id,
                run_type=run_type,
                seed=seeds[0],
            )
        else:
            per_seed_runs: list[dict] = []
            for seed in seeds:
                per_seed_runs.append(
                    run_scenario_type(
                        args=args,
                        base_config=config,
                        catalog=catalog,
                        scenario_id=scenario_id,
                        run_type=run_type,
                        seed=seed,
                    )
                )
            scenario_runs[run_type] = _aggregate_replications(per_seed_runs)

    summary = scenario_summary(config)
    summary["catalog"] = catalog["scenarios"][scenario_id]
    summary["outputs_dir"] = str(scenario_output_dir.relative_to(ROOT))
    summary["reports_dir"] = str(scenario_report_dir.relative_to(ROOT))
    summary["runs"] = scenario_runs
    summary["seeds"] = seeds
    summary["comparisons"] = compare_scenario_runs(scenario_runs)
    summary["verdict"] = scenario_verdict(summary)
    (scenario_report_dir / "scenario_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (scenario_report_dir / "scenario_report.md").write_text(render_scenario_report(summary), encoding="utf-8")

    if args.generate_only:
        summary["status"] = "generated"
        return summary
    summary["status"] = "completed"
    return summary


def _resolve_seeds(args: argparse.Namespace, config: dict) -> list[int]:
    """Pick the list of seeds for replications.

    Priority: CLI `--seeds` > scenario_profile.random_seeds > config.random_seed.
    """
    if getattr(args, "seeds", None):
        return [int(s) for s in args.seeds]
    profile_seeds = config.get("scenario_profile", {}).get("random_seeds")
    if isinstance(profile_seeds, list) and profile_seeds:
        return [int(s) for s in profile_seeds]
    base_seeds = config.get("random_seeds")
    if isinstance(base_seeds, list) and base_seeds:
        return [int(s) for s in base_seeds]
    return [int(config.get("random_seed", 57))]


def _aggregate_replications(runs: list[dict]) -> dict:
    """Roll multiple seed replications of a single run_type into one summary."""
    if not runs:
        return {}
    first = runs[0]
    aggregate = dict(first)
    aggregate["replication_count"] = len(runs)
    aggregate["replication_summaries"] = [dict(run) for run in runs]
    kpi_paths = [run.get("kpis") for run in runs if run.get("kpis")]
    aggregate["kpi_paths"] = kpi_paths
    summaries = [_load_kpis(p) for p in kpi_paths if p]
    summaries = [s for s in summaries if s]
    if summaries:
        aggregate["kpi_aggregate"] = _compute_kpi_aggregate(summaries)
    return aggregate


def _compute_kpi_aggregate(kpis_list: list[dict]) -> dict:
    """Mean and spread (p5/p95) of headline KPIs across replications."""
    import statistics

    def collect(path_keys: list[str]) -> list[float]:
        values = []
        for k in kpis_list:
            current: Any = k
            for key in path_keys:
                if isinstance(current, dict):
                    current = current.get(key)
                else:
                    current = None
                    break
            if isinstance(current, (int, float)):
                values.append(float(current))
        return values

    def stat(values: list[float]) -> dict[str, float | None]:
        if not values:
            return {"mean": None, "p5": None, "p95": None, "n": 0}
        sorted_v = sorted(values)
        p5_idx = max(0, int(round((len(sorted_v) - 1) * 0.05)))
        p95_idx = min(len(sorted_v) - 1, int(round((len(sorted_v) - 1) * 0.95)))
        return {
            "mean": round(statistics.fmean(values), 3),
            "stdev": round(statistics.pstdev(values), 3) if len(values) > 1 else 0.0,
            "p5": round(sorted_v[p5_idx], 3),
            "p95": round(sorted_v[p95_idx], 3),
            "n": len(values),
        }

    return {
        "bus_mean_time_loss_s": stat(collect(["buses", "mean_time_loss_s"])),
        "general_mean_time_loss_s": stat(collect(["general_traffic", "mean_time_loss_s"])),
        "all_vehicles_mean_duration_s": stat(collect(["all_vehicles", "mean_duration_s"])),
        "max_network_queue_vehicles": stat(collect(["detectors", "network_queue", "max_queue_vehicles"])),
        "total_co2_mg": stat(collect(["emissions", "totals_mg", "CO2"])),
        "total_fuel_mg": stat(collect(["emissions", "totals_mg", "fuel"])),
    }


def run_scenario_type(
    *,
    args: argparse.Namespace,
    base_config: dict,
    catalog: dict,
    scenario_id: str,
    run_type: str,
    seed: int | None = None,
) -> dict:
    if seed is None:
        seed = int(base_config.get("random_seed", 57))
    suffix = f"seed_{seed}"
    run_output_dir = ROOT / args.outputs_dir / scenario_id / run_type / suffix
    run_report_dir = ROOT / args.reports_dir / scenario_id / run_type / suffix
    run_output_dir.mkdir(parents=True, exist_ok=True)
    run_report_dir.mkdir(parents=True, exist_ok=True)

    config = deepcopy(base_config)
    config["random_seed"] = int(seed)
    rel_run_output = run_output_dir.relative_to(ROOT)
    config.setdefault("detectors", {})
    config["detectors"]["e1_output"] = f"../../{rel_run_output}/e1_detectors.xml"
    config["detectors"]["e2_output"] = f"../../{rel_run_output}/e2_queues.xml"

    generate(
        config,
        ROOT / "sumo/plain",
        routes_output=ROOT / "sumo/routes/routes.rou.xml",
        bus_stops_output=ROOT / "sumo/additional/bus_stops.add.xml",
        detectors_output=ROOT / "sumo/additional/detectors.add.xml",
        parking_output=ROOT / "sumo/additional/parking.add.xml",
        calibrators_output=ROOT / "sumo/additional/calibrators.add.xml",
        tls_offsets_output=ROOT / "sumo/additional/tls_offsets.add.xml",
    )
    (run_output_dir / "resolved_config.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    if not args.skip_build:
        build_network(config)

    summary = scenario_summary(config)
    summary["run_type"] = run_type
    summary["catalog"] = catalog["scenarios"][scenario_id]
    summary["outputs_dir"] = str(run_output_dir.relative_to(ROOT))
    summary["reports_dir"] = str(run_report_dir.relative_to(ROOT))
    summary["max_steps"] = args.steps

    if args.generate_only:
        summary["status"] = "generated"
        return summary

    if run_type == "baseline":
        run_baseline_sumo(args, config, run_output_dir)
    elif run_type == "cits":
        run_cits(args, scenario_id, run_output_dir, run_report_dir)
    elif run_type == "tsp_no_actuation":
        run_tsp(args, scenario_id, run_output_dir, run_report_dir, apply_actuation=False)
    elif run_type == "tsp_actuation":
        run_tsp(args, scenario_id, run_output_dir, run_report_dir, apply_actuation=True)
    else:  # pragma: no cover - argparse prevents this.
        raise SystemExit(f"Unknown run type: {run_type}")

    copy_global_sumo_outputs(run_output_dir)
    kpis = collect_run_kpis(run_output_dir)
    kpis["scenario"] = summary
    kpi_path = run_report_dir / "kpis.json"
    kpi_path.write_text(json.dumps(kpis, indent=2, ensure_ascii=False), encoding="utf-8")
    summary["status"] = "completed"
    summary["kpis"] = str(kpi_path.relative_to(ROOT))
    summary["run_verdict"] = run_verdict(kpis)
    return summary


def build_network(config: dict | None = None) -> None:
    network_cfg = (config or {}).get("network", {}) if config else {}
    cycle_time = "90"
    intersections = network_cfg.get("intersections", []) if network_cfg else []
    if intersections:
        cycles = {int(i.get("tls_cycle_s", 90)) for i in intersections if "tls_cycle_s" in i}
        if cycles and len(cycles) == 1:
            cycle_time = str(cycles.pop())
    cmd = [
        "netconvert",
        "--node-files", "sumo/plain/corredor.nod.xml",
        "--edge-files", "sumo/plain/corredor.edg.xml",
        "--output-file", "sumo/network/corredor.net.xml",
        "--no-turnarounds", "true",
        "--tls.default-type", "static",
        "--tls.cycle.time", cycle_time,
        "--tls.yellow.time", "3",
    ]
    if network_cfg.get("enable_sidewalks"):
        cmd.extend(["--sidewalks.guess", "true"])
    if network_cfg.get("enable_pedestrian_crossings"):
        cmd.extend(["--crossings.guess", "true", "--walkingareas", "true"])
    _run(cmd)
    overrides_path = ROOT / "sumo/additional/tls_offsets.add.xml"
    if overrides_path.exists():
        modified = apply_tls_offsets(ROOT / "sumo/network/corredor.net.xml", overrides_path)
        if modified:
            print(f"applied {modified} tls offsets")


def run_baseline_sumo(args: argparse.Namespace, config: dict, run_output_dir: Path) -> None:
    binary = config.get("sumo", {}).get("default_gui_binary", "sumo-gui") if args.gui else args.sumo_binary
    cmd = [
        binary,
        "-c", "sumo/corredor.sumocfg",
        "--duration-log.statistics",
        "--tripinfo-output", str(run_output_dir / "tripinfo.xml"),
        "--summary-output", str(run_output_dir / "summary.xml"),
        "--statistic-output", str(run_output_dir / "statistics.xml"),
        "--emission-output", str(run_output_dir / "emissions.xml"),
        "--seed", str(config.get("random_seed", 57)),
        "--end", str(config.get("simulation_end_s", 7200)),
    ]
    if config.get("pedestrian_flows"):
        cmd.extend(["--pedestrian.model", "striping"])
    if args.gui:
        cmd.extend(["--start", "--quit-on-end"])
    _run(cmd)


def run_cits(args: argparse.Namespace, scenario_id: str, run_output_dir: Path, run_report_dir: Path) -> None:
    clear_global_sumo_outputs()
    config_path = write_cits_config(scenario_id, run_output_dir, run_report_dir)
    config = load_cits_config(config_path, root=ROOT)
    controller = CITSEmulationController(config)
    controller.run_with_sumo(steps=args.steps, sumo_binary=args.sumo_binary, gui=args.gui)


def run_tsp(
    args: argparse.Namespace,
    scenario_id: str,
    run_output_dir: Path,
    run_report_dir: Path,
    *,
    apply_actuation: bool,
) -> None:
    clear_global_sumo_outputs()
    cits_config_path = write_cits_config(scenario_id, run_output_dir, run_report_dir)
    tsp_config_path = write_tsp_config(scenario_id, run_output_dir, run_report_dir)
    cits_config = load_cits_config(cits_config_path, root=ROOT)
    tsp_config = load_tsp_config(tsp_config_path, root=ROOT)
    controller = TSPControlController(cits_config, tsp_config)
    controller.run_with_sumo(
        steps=args.steps,
        sumo_binary=args.sumo_binary,
        gui=args.gui,
        apply_actuation=apply_actuation,
    )


def collect_run_kpis(run_output_dir: Path) -> dict:
    tripinfo = run_output_dir / "tripinfo.xml"
    kpis = parse_tripinfo(tripinfo) if tripinfo.exists() else {"source": str(tripinfo), "missing_tripinfo": True}
    kpis["detectors"] = parse_detector_kpis(run_output_dir / "e1_detectors.xml", run_output_dir / "e2_queues.xml")
    kpis["insertion"] = parse_insertion_kpis(run_output_dir / "summary.xml", run_output_dir / "statistics.xml")
    kpis["emissions"] = parse_emissions(run_output_dir / "emissions.xml")
    return kpis


def write_cits_config(scenario_id: str, run_output_dir: Path, run_report_dir: Path) -> Path:
    raw = json.loads((ROOT / "configs/cits_v2x_config.json").read_text(encoding="utf-8"))
    raw["scenario_id"] = f"{scenario_id}_cits"
    raw.setdefault("logging", {}).update(
        {
            "message_log": str((run_output_dir / "cits_messages.jsonl").relative_to(ROOT)),
            "summary_report": str((run_report_dir / "cits_emulation_summary.json").relative_to(ROOT)),
            "mapem_snapshot": str((run_output_dir / "cits_mapem_snapshot.json").relative_to(ROOT)),
            "spatem_snapshot": str((run_output_dir / "cits_spatem_snapshot.json").relative_to(ROOT)),
        }
    )
    config_path = run_output_dir / "cits_v2x_config.json"
    config_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    return config_path


def write_tsp_config(scenario_id: str, run_output_dir: Path, run_report_dir: Path) -> Path:
    raw = json.loads((ROOT / "configs/tsp_safety_config.json").read_text(encoding="utf-8"))
    raw["scenario_id"] = f"{scenario_id}_tsp"
    raw.setdefault("logging", {}).update(
        {
            "decision_log": str((run_output_dir / "tsp_decisions.jsonl").relative_to(ROOT)),
            "actuation_log": str((run_output_dir / "tsp_actuation.jsonl").relative_to(ROOT)),
            "summary_report": str((run_report_dir / "tsp_emulation_summary.json").relative_to(ROOT)),
        }
    )
    config_path = run_output_dir / "tsp_safety_config.json"
    config_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    return config_path


def clear_global_sumo_outputs() -> None:
    for rel in ("outputs/tripinfo.xml", "outputs/summary.xml", "outputs/statistics.xml", "outputs/emissions.xml"):
        path = ROOT / rel
        if path.exists():
            path.unlink()


def copy_global_sumo_outputs(run_output_dir: Path) -> None:
    for name in ("tripinfo.xml", "summary.xml", "statistics.xml", "emissions.xml"):
        source = ROOT / "outputs" / name
        target = run_output_dir / name
        if source.exists() and not target.exists():
            shutil.copy2(source, target)


def compare_scenario_runs(runs: dict[str, dict]) -> dict:
    baseline = _load_kpis(runs.get("baseline", {}).get("kpis"))
    comparisons: dict[str, dict] = {}
    for run_type in ("tsp_no_actuation", "tsp_actuation"):
        candidate = _load_kpis(runs.get(run_type, {}).get("kpis"))
        if not baseline or not candidate:
            continue
        comparisons[f"baseline_vs_{run_type}"] = compare_kpis(baseline, candidate)
    return comparisons


def compare_kpis(baseline: dict, candidate: dict) -> dict:
    bus_delta = _metric_delta(baseline, candidate, "buses", "mean_time_loss_s", lower_is_better=True)
    general_delta = _metric_delta(baseline, candidate, "general_traffic", "mean_time_loss_s", lower_is_better=True)
    max_queue = candidate.get("detectors", {}).get("network_queue", {}).get("max_queue_vehicles")
    fail_reasons = []
    if bus_delta.get("regression_pct") is not None and bus_delta["regression_pct"] > 10:
        fail_reasons.append("bus_time_loss_regression_gt_10pct")
    if general_delta.get("delta") is not None and general_delta["delta"] > 90:
        fail_reasons.append("general_traffic_time_loss_penalty_gt_90s")
    if max_queue is not None and max_queue > 30:
        fail_reasons.append("network_queue_gt_30_vehicles")
    return {
        "bus_time_loss": bus_delta,
        "general_traffic_time_loss": general_delta,
        "candidate_max_queue_vehicles": max_queue,
        "verdict": "fail" if fail_reasons else "pass",
        "fail_reasons": fail_reasons,
    }


def _metric_delta(baseline: dict, candidate: dict, group: str, metric: str, *, lower_is_better: bool) -> dict:
    base_value = baseline.get(group, {}).get(metric)
    candidate_value = candidate.get(group, {}).get(metric)
    if base_value is None or candidate_value is None:
        return {"baseline": base_value, "candidate": candidate_value, "delta": None, "regression_pct": None}
    delta = round(candidate_value - base_value, 3)
    regression = delta if lower_is_better else -delta
    regression_pct = round((regression / base_value) * 100, 3) if base_value else None
    return {
        "baseline": base_value,
        "candidate": candidate_value,
        "delta": delta,
        "regression_pct": regression_pct,
    }


def run_verdict(kpis: dict) -> dict:
    if kpis.get("missing_tripinfo"):
        return {"status": "fail", "reasons": ["missing_tripinfo"]}
    reasons = []
    inconclusive = []
    if kpis.get("all_vehicles", {}).get("vehicles", 0) <= 0:
        reasons.append("no_completed_vehicles")
    if kpis.get("buses", {}).get("vehicles", 0) <= 0:
        scenario = kpis.get("scenario", {})
        max_steps = scenario.get("max_steps")
        if max_steps is not None and float(max_steps) < 1800:
            inconclusive.append("no_completed_buses_in_short_smoke_run")
        else:
            reasons.append("no_completed_buses")
    if inconclusive and not reasons:
        return {"status": "inconclusive", "reasons": inconclusive}
    return {"status": "fail" if reasons else "pass", "reasons": reasons}


def scenario_verdict(summary: dict) -> dict:
    reasons = []
    for run_type, run in summary.get("runs", {}).items():
        verdict = run.get("run_verdict", {})
        if verdict.get("status") == "fail":
            reasons.append(f"{run_type}:{','.join(verdict.get('reasons', []))}")
        elif verdict.get("status") == "inconclusive":
            reasons.append(f"{run_type}:inconclusive:{','.join(verdict.get('reasons', []))}")
    for key, comparison in summary.get("comparisons", {}).items():
        if comparison.get("verdict") == "fail":
            reasons.append(f"{key}:{','.join(comparison.get('fail_reasons', []))}")
    if any(":inconclusive:" not in reason for reason in reasons):
        return {"status": "fail", "reasons": reasons}
    return {"status": "inconclusive" if reasons else "pass", "reasons": reasons}


def _load_kpis(rel: str | None) -> dict | None:
    if not rel:
        return None
    path = ROOT / rel
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def render_scenario_report(summary: dict) -> str:
    lines = [
        f"# {summary['scenario_id']}",
        "",
        f"Verdict: **{summary.get('verdict', {}).get('status', 'unknown')}**",
        "",
        "| Run | Status | Vehicles | Buses | Bus timeLoss | General timeLoss | Max queue | Total CO2 (mg) | Total fuel (mg) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for run_type, run in summary.get("runs", {}).items():
        kpis = _load_kpis(run.get("kpis")) or {}
        emissions_totals = kpis.get("emissions", {}).get("totals_mg", {}) if isinstance(kpis.get("emissions"), dict) else {}
        lines.append(
            "| {run} | {status} | {veh} | {bus} | {bus_loss} | {gen_loss} | {queue} | {co2} | {fuel} |".format(
                run=run_type,
                status=run.get("run_verdict", {}).get("status", run.get("status")),
                veh=kpis.get("all_vehicles", {}).get("vehicles", ""),
                bus=kpis.get("buses", {}).get("vehicles", ""),
                bus_loss=kpis.get("buses", {}).get("mean_time_loss_s", ""),
                gen_loss=kpis.get("general_traffic", {}).get("mean_time_loss_s", ""),
                queue=kpis.get("detectors", {}).get("network_queue", {}).get("max_queue_vehicles", ""),
                co2=emissions_totals.get("CO2", ""),
                fuel=emissions_totals.get("fuel", ""),
            )
        )
    return "\n".join(lines) + "\n"


def render_suite_report(report: dict) -> str:
    lines = [
        "# Scenario Suite Report",
        "",
        "| Scenario | Verdict | Runs | Comparisons |",
        "|---|---:|---:|---:|",
    ]
    for scenario in report.get("scenarios", []):
        lines.append(
            f"| {scenario['scenario_id']} | {scenario.get('verdict', {}).get('status', 'unknown')} | "
            f"{len(scenario.get('runs', {}))} | {len(scenario.get('comparisons', {}))} |"
        )
    return "\n".join(lines) + "\n"


def _require(binary: str) -> None:
    if shutil.which(binary) is None:
        raise SystemExit(f"Required binary not found in PATH: {binary}")


def _run(cmd: list[str]) -> None:
    print("$ " + " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)


if __name__ == "__main__":
    raise SystemExit(main())
