#!/usr/bin/env python3
"""Reference runner for the real calibrated Ingolstadt SUMO scenario.

The Ingolstadt branch is the reference evidence path: scenarios come from
``configs/scenario_catalog_ingolstadt.yaml`` and each run writes isolated SUMO
outputs, KPIs and reports under ``reports/ingolstadt``. The old no-actuation mode
is kept as a TSP dry-run, but the baseline arm is now plain SUMO without the TSP
runtime.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from pps57_cits.config import load_cits_config  # noqa: E402
from pps57_sumo.build_network import sumo_environment  # noqa: E402
from pps57_sumo.scenarios import ScenarioConfigError, load_catalog  # noqa: E402
from pps57_tsp.config import load_tsp_config  # noqa: E402
from pps57_tsp.controller import TSPControlController  # noqa: E402
from run_sumo_scenario import (  # noqa: E402
    _aggregate_replications,
    _require,
    apply_relative_insertion_gate,
    collect_run_kpis,
    compare_scenario_runs,
    render_scenario_report,
    render_suite_report,
    run_verdict,
    scenario_verdict,
)

SCENARIO_DIR = ROOT / ".tools" / "ingolstadt" / "simulation" / "Ingolstadt SUMO 365"
WORK = ROOT / ".tools" / "ingol_run"
RUN_TYPES = ("baseline", "tsp_no_actuation", "tsp_actuation")
DEFAULT_SEED = 57
REFERENCE = "ingolstadt_citywide"
SCENARIO_SET = "pps57_ingolstadt_citywide_tsp_v1"
CATALOG_REQUIRED_FIELDS = ("day", "window_s", "description", "realism_basis", "kpi_focus")


@dataclass(frozen=True)
class IngolstadtScenarioSpec:
    scenario_id: str
    day: str
    begin_s: int
    end_s: int
    begin: str
    end: str
    steps: int
    catalog_entry: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--catalog",
        default=Path("configs/scenario_catalog_ingolstadt.yaml"),
        type=Path,
    )
    parser.add_argument("--scenario", help="Scenario id from the Ingolstadt catalog.")
    parser.add_argument("--all", action="store_true", help="Run every Ingolstadt catalog scenario.")
    parser.add_argument("--list", action="store_true", help="List Ingolstadt catalog scenarios.")
    parser.add_argument(
        "--run-type",
        choices=[*RUN_TYPES, "pair", "comparison", "all"],
        default="pair",
        help="baseline=plain SUMO; tsp_no_actuation=TSP dry-run; tsp_actuation=real actuation.",
    )
    parser.add_argument("--seeds", nargs="+", type=int, default=None)
    parser.add_argument(
        "--steps",
        type=int,
        default=None,
        help="Optional horizon in seconds. Defaults to the catalog window for catalog runs.",
    )
    parser.add_argument(
        "--day",
        default="2023-07-04",
        help="Ad-hoc day when --scenario is omitted.",
    )
    parser.add_argument(
        "--begin",
        default="07:00:00",
        help="Ad-hoc begin time when --scenario is omitted.",
    )
    parser.add_argument(
        "--no-actuation",
        action="store_true",
        help="Legacy shortcut for an ad-hoc TSP dry-run; not a plain SUMO baseline.",
    )
    parser.add_argument("--generate-only", action="store_true")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Recopy TUM files into .tools/ingol_run.",
    )
    parser.add_argument("--config", default=Path("configs/cits_ingolstadt_config.json"), type=Path)
    parser.add_argument("--tsp-config", default=Path("configs/tsp_safety_config.json"), type=Path)
    parser.add_argument("--outputs-dir", default=Path(".tools/ingol_run/runs"), type=Path)
    parser.add_argument("--reports-dir", default=Path("reports/ingolstadt"), type=Path)
    parser.add_argument("--sumo-binary", default="sumo")
    parser.add_argument("--gui", action="store_true", help="Use sumo-gui for TSP TraCI arms.")
    return parser.parse_args()


def hhmmss_to_seconds(value: str) -> int:
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError(f"Expected HH:MM:SS, got {value!r}")
    hours, minutes, seconds = (int(part) for part in parts)
    return hours * 3600 + minutes * 60 + seconds


def seconds_to_hhmmss(value: int) -> str:
    if value < 0:
        raise ValueError("SUMO clock seconds must be non-negative")
    hours, remainder = divmod(int(value), 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def required_scenario_files(day: str) -> dict[str, str]:
    route = f"Routes/routes_{day}_24h_det_calib.rou.xml.gz"
    tl_logic = f"TL/{day}_tlLogics_24h.tll.xml"
    waut = f"TL/{day}_WAUT.xml"
    gtfs = f"PT/{day}_gtfs_trips.rou.xml"
    return {
        "ingolstadt_net.net.xml": "ingolstadt_net.net.xml",
        route: route,
        tl_logic: tl_logic,
        waut: waut,
        "PT/pt_stops.add.xml": "PT/pt_stops.add.xml",
        gtfs: gtfs,
    }


def load_ingolstadt_catalog(path: Path) -> dict[str, Any]:
    catalog = load_catalog(path)
    for scenario_id, entry in catalog["scenarios"].items():
        if not isinstance(entry, dict):
            raise ScenarioConfigError(f"Ingolstadt scenario {scenario_id!r} must be a mapping.")
        missing = [key for key in CATALOG_REQUIRED_FIELDS if not entry.get(key)]
        if missing:
            raise ScenarioConfigError(
                f"Ingolstadt scenario {scenario_id!r} missing fields: {', '.join(missing)}"
            )
        if not _valid_window(entry.get("window_s")):
            raise ScenarioConfigError(
                f"Ingolstadt scenario {scenario_id!r} must define an increasing window_s."
            )
    return catalog


def _valid_window(raw: object) -> bool:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return False
    try:
        begin_s, end_s = (int(value) for value in raw)
    except (TypeError, ValueError):
        return False
    return begin_s < end_s


def resolve_ingolstadt_specs(
    args: argparse.Namespace, catalog: dict[str, Any]
) -> list[IngolstadtScenarioSpec]:
    if args.all:
        scenario_ids = list(catalog["scenarios"])
    elif args.scenario:
        scenario_ids = [args.scenario]
    else:
        begin_s = hhmmss_to_seconds(args.begin)
        steps = int(args.steps if args.steps is not None else 300)
        return [
            _build_spec(
                scenario_id="ad_hoc_ingolstadt",
                day=args.day,
                begin_s=begin_s,
                steps=steps,
                catalog_entry={
                    "description": "Ad-hoc Ingolstadt city-wide smoke window",
                    "day": args.day,
                    "window_s": [begin_s, begin_s + steps],
                    "realism_basis": "Real TUM-VT Ingolstadt slice selected by CLI.",
                    "kpi_focus": ["bus_time_loss_citywide", "general_traffic_delay"],
                },
            )
        ]

    specs: list[IngolstadtScenarioSpec] = []
    for scenario_id in scenario_ids:
        if scenario_id not in catalog["scenarios"]:
            raise SystemExit(f"Unknown Ingolstadt scenario: {scenario_id}")
        entry = catalog["scenarios"][scenario_id]
        begin_s, catalog_end_s = (int(value) for value in entry["window_s"])
        steps = int(args.steps if args.steps is not None else catalog_end_s - begin_s)
        specs.append(
            _build_spec(
                scenario_id=scenario_id,
                day=str(entry["day"]),
                begin_s=begin_s,
                steps=steps,
                catalog_entry=dict(entry),
            )
        )
    return specs


def _build_spec(
    *,
    scenario_id: str,
    day: str,
    begin_s: int,
    steps: int,
    catalog_entry: dict[str, Any],
) -> IngolstadtScenarioSpec:
    end_s = begin_s + steps
    return IngolstadtScenarioSpec(
        scenario_id=scenario_id,
        day=day,
        begin_s=begin_s,
        end_s=end_s,
        begin=seconds_to_hhmmss(begin_s),
        end=seconds_to_hhmmss(end_s),
        steps=steps,
        catalog_entry=catalog_entry,
    )


def materialize(
    day: str,
    begin: str,
    refresh: bool,
    *,
    scenario_dir: Path = SCENARIO_DIR,
    work: Path = WORK,
    run_output_dir: Path | None = None,
    end: str | None = None,
    seed: int | None = None,
) -> tuple[Path, Path]:
    """Copy TUM files into a clean path and write a SUMO config for one run."""
    if not scenario_dir.exists():
        raise SystemExit(
            f"Cenário não encontrado em {scenario_dir}.\n"
            "Clona o TUM-VT primeiro: git clone --depth 1 "
            "https://github.com/TUM-VT/sumo_ingolstadt.git .tools/ingolstadt"
        )

    files = required_scenario_files(day)
    for dst_rel in files.values():
        (work / dst_rel).parent.mkdir(parents=True, exist_ok=True)
    out_dir = (run_output_dir / "out") if run_output_dir is not None else work / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    for src_rel, dst_rel in files.items():
        src, dst = scenario_dir / src_rel, work / dst_rel
        if not src.exists():
            raise SystemExit(f"Ficheiro do cenário em falta para o dia {day}: {src}")
        if refresh or not dst.exists():
            shutil.copy2(src, dst)

    sumocfg = (
        run_output_dir / "demo.sumocfg"
        if run_output_dir is not None
        else work / "demo.sumocfg"
    )
    end_value = end or "24:00:00"
    seed_block = (
        f"\n  <random>\n    <seed value=\"{int(seed)}\"/>\n  </random>"
        if seed is not None
        else ""
    )
    sumocfg.parent.mkdir(parents=True, exist_ok=True)
    sumocfg.write_text(
        _sumocfg_xml(
            day=day,
            begin=begin,
            end=end_value,
            work=work,
            out_dir=out_dir,
            seed_block=seed_block,
        ),
        encoding="utf-8",
    )
    return sumocfg, work / "ingolstadt_net.net.xml"


def _sumocfg_xml(
    *,
    day: str,
    begin: str,
    end: str,
    work: Path,
    out_dir: Path,
    seed_block: str,
) -> str:
    additional_files = ", ".join(
        str(path)
        for path in (
            work / "TL" / f"{day}_tlLogics_24h.tll.xml",
            work / "TL" / f"{day}_WAUT.xml",
            work / "PT" / "pt_stops.add.xml",
            work / "PT" / f"{day}_gtfs_trips.rou.xml",
        )
    )
    return f"""<configuration>
  <input>
    <net-file value="{work / 'ingolstadt_net.net.xml'}"/>
    <route-files value="{work / 'Routes' / f'routes_{day}_24h_det_calib.rou.xml.gz'}"/>
    <additional-files value="{additional_files}"/>
  </input>
  <time>
    <begin value="{begin}"/>
    <end value="{end}"/>
  </time>
  <processing>
    <step-length value="1"/>
    <ignore-junction-blocker value="15"/>
    <time-to-teleport value="240"/>
    <max-depart-delay value="100"/>
    <device.rerouting.probability value="0.7"/>
  </processing>{seed_block}
  <output>
    <tripinfo-output value="{out_dir / 'tripinfo.xml'}"/>
    <summary-output value="{out_dir / 'summary.xml'}"/>
    <statistic-output value="{out_dir / 'statistics.xml'}"/>
    <emission-output value="{out_dir / 'emissions.xml'}"/>
  </output>
</configuration>
"""


def _relative(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _run_output_dir(
    args: argparse.Namespace, spec: IngolstadtScenarioSpec, run_type: str, seed: int
) -> Path:
    return ROOT / args.outputs_dir / spec.scenario_id / run_type / f"seed_{seed}"


def _run_report_dir(
    args: argparse.Namespace, spec: IngolstadtScenarioSpec, run_type: str, seed: int
) -> Path:
    return ROOT / args.reports_dir / spec.scenario_id / run_type / f"seed_{seed}"


def _run_types_for(args: argparse.Namespace) -> list[str]:
    if args.no_actuation and not args.scenario and not args.all:
        return ["tsp_no_actuation"]
    if args.run_type == "pair":
        return ["baseline", "tsp_actuation"]
    if args.run_type in {"comparison", "all"}:
        return ["baseline", "tsp_no_actuation", "tsp_actuation"]
    return [args.run_type]


def _seeds_for(args: argparse.Namespace) -> list[int]:
    return [int(seed) for seed in (args.seeds or [DEFAULT_SEED])]


def _generated_verdict() -> dict[str, Any]:
    return {"status": "generated", "reasons": []}


def _run_verdict_for(args: argparse.Namespace, kpis: dict[str, Any]) -> dict[str, Any]:
    return _generated_verdict() if args.generate_only else run_verdict(kpis)


def _scenario_verdict_for(args: argparse.Namespace, summary: dict[str, Any]) -> dict[str, Any]:
    return _generated_verdict() if args.generate_only else scenario_verdict(summary)


def _comparisons_for(args: argparse.Namespace, scenario_runs: dict[str, dict]) -> dict[str, Any]:
    return {} if args.generate_only else compare_scenario_runs(scenario_runs)


def write_cits_config(
    args: argparse.Namespace,
    spec: IngolstadtScenarioSpec,
    run_output_dir: Path,
    sumocfg: Path,
    net: Path,
) -> Path:
    raw = _read_json(ROOT / args.config)
    raw["scenario_id"] = f"ingolstadt_{spec.scenario_id}_cits"
    raw.setdefault("sumo", {}).update({"sumocfg": _relative(sumocfg), "network": _relative(net)})
    schedule_plan = raw.get("schedule_plan", {})
    if isinstance(schedule_plan, dict) and str(schedule_plan.get("mode", "")).lower() == "gtfs":
        schedule_plan["gtfs_trips"] = _relative(WORK / "PT" / f"{spec.day}_gtfs_trips.rou.xml")
        schedule_plan["pt_stops"] = _relative(WORK / "PT" / "pt_stops.add.xml")
    out = run_output_dir / "out"
    raw["logging"] = {
        "message_log": _relative(out / "cits_messages.jsonl"),
        "summary_report": _relative(out / "cits_summary.json"),
        "mapem_snapshot": _relative(out / "mapem.json"),
        "spatem_snapshot": _relative(out / "spatem.json"),
    }
    config_path = run_output_dir / "cits_resolved.json"
    _write_json(config_path, raw)
    return config_path


def _citywide_tsp_raw(raw: dict[str, Any]) -> dict[str, Any]:
    resolved = deepcopy(raw)
    corridor = dict(resolved.get("corridor", {}))
    corridor.update(
        {
            "max_corridor_recovery_debt_s": None,
            "respect_downstream_spillback": True,
            "flag_green_wave": False,
        }
    )
    resolved["corridor"] = corridor
    network_profile = dict(resolved.get("network_profile", {}))
    network_profile.update({"enabled": True, "prefer_generated_contracts_for_unknown_tls": True})
    resolved["network_profile"] = network_profile
    contracts = dict(resolved.get("controller_contracts", {}))
    if isinstance(contracts.get("controllers"), dict):
        contracts["controllers"] = {}
    resolved["controller_contracts"] = contracts
    phase_mapping = dict(resolved.get("phase_mapping", {}))
    if isinstance(phase_mapping.get("priority_movements"), dict):
        phase_mapping["priority_movements"] = {}
    resolved["phase_mapping"] = phase_mapping
    return resolved


def write_tsp_config(
    args: argparse.Namespace,
    spec: IngolstadtScenarioSpec,
    run_type: str,
    run_output_dir: Path,
) -> Path:
    raw = _read_json(ROOT / args.tsp_config)
    raw = _citywide_tsp_raw(raw)
    raw["scenario_id"] = f"ingolstadt_{spec.scenario_id}_{run_type}"
    out = run_output_dir / "out"
    raw.setdefault("logging", {}).update(
        {
            "decision_log": _relative(out / "tsp_decisions.jsonl"),
            "actuation_log": _relative(out / "tsp_actuation.jsonl"),
            "summary_report": _relative(out / "tsp_summary.json"),
        }
    )
    config_path = run_output_dir / "tsp_resolved.json"
    _write_json(config_path, raw)
    return config_path


def run_baseline(args: argparse.Namespace, sumocfg: Path) -> None:
    _require(args.sumo_binary)
    cmd = [args.sumo_binary, "-c", str(sumocfg), "--duration-log.statistics"]
    print("$ " + " ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True, env=sumo_environment())


def run_tsp(
    args: argparse.Namespace,
    spec: IngolstadtScenarioSpec,
    run_type: str,
    run_output_dir: Path,
    sumocfg: Path,
    net: Path,
) -> dict:
    cits_path = write_cits_config(args, spec, run_output_dir, sumocfg, net)
    tsp_path = write_tsp_config(args, spec, run_type, run_output_dir)
    cits_config = load_cits_config(cits_path, root=ROOT)
    tsp_config = load_tsp_config(tsp_path, root=ROOT)
    controller = TSPControlController(cits_config, tsp_config)
    return controller.run_with_sumo(
        steps=spec.steps,
        sumo_binary=args.sumo_binary,
        gui=args.gui,
        apply_actuation=run_type == "tsp_actuation",
    )


def run_scenario_type(
    args: argparse.Namespace,
    spec: IngolstadtScenarioSpec,
    run_type: str,
    seed: int,
) -> dict[str, Any]:
    run_output_dir = _run_output_dir(args, spec, run_type, seed)
    run_report_dir = _run_report_dir(args, spec, run_type, seed)
    run_output_dir.mkdir(parents=True, exist_ok=True)
    run_report_dir.mkdir(parents=True, exist_ok=True)
    sumocfg, net = materialize(
        spec.day,
        spec.begin,
        args.refresh,
        run_output_dir=run_output_dir,
        end=spec.end,
        seed=seed,
    )

    controller_summary: dict[str, Any] = {}
    if not args.generate_only:
        if run_type == "baseline":
            run_baseline(args, sumocfg)
        else:
            controller_summary = run_tsp(args, spec, run_type, run_output_dir, sumocfg, net)

    out_dir = run_output_dir / "out"
    kpis = collect_run_kpis(out_dir)
    kpis["scenario"] = {
        "scenario_id": spec.scenario_id,
        "run_type": run_type,
        "seed": seed,
        "day": spec.day,
        "begin_s": spec.begin_s,
        "end_s": spec.end_s,
        "max_steps": spec.steps,
        "reference": REFERENCE,
    }
    kpi_path = run_report_dir / "kpis.json"
    _write_json(kpi_path, kpis)
    return {
        "run_type": run_type,
        "seed": seed,
        "status": "generated" if args.generate_only else "completed",
        "outputs_dir": _relative(out_dir),
        "reports_dir": _relative(run_report_dir),
        "kpis": _relative(kpi_path),
        "controller_summary": controller_summary,
        "run_verdict": _run_verdict_for(args, kpis),
    }


def run_scenario(args: argparse.Namespace, spec: IngolstadtScenarioSpec) -> dict[str, Any]:
    scenario_runs: dict[str, dict] = {}
    seeds = _seeds_for(args)
    for run_type in _run_types_for(args):
        runs = [run_scenario_type(args, spec, run_type, seed) for seed in seeds]
        scenario_runs[run_type] = runs[0] if len(runs) == 1 else _aggregate_replications(runs)

    if not args.generate_only:
        apply_relative_insertion_gate(scenario_runs)
    summary = {
        "scenario_id": spec.scenario_id,
        "scenario_set": SCENARIO_SET,
        "city": "Ingolstadt",
        "reference": REFERENCE,
        "day": spec.day,
        "begin_s": spec.begin_s,
        "end_s": spec.end_s,
        "steps": spec.steps,
        "catalog": spec.catalog_entry,
        "outputs_dir": _relative(ROOT / args.outputs_dir / spec.scenario_id),
        "reports_dir": _relative(ROOT / args.reports_dir / spec.scenario_id),
        "runs": scenario_runs,
        "seeds": seeds,
        "comparisons": _comparisons_for(args, scenario_runs),
    }
    summary["verdict"] = _scenario_verdict_for(args, summary)
    scenario_report_dir = ROOT / args.reports_dir / spec.scenario_id
    scenario_report_dir.mkdir(parents=True, exist_ok=True)
    _write_json(scenario_report_dir / "scenario_summary.json", summary)
    (scenario_report_dir / "scenario_report.md").write_text(
        render_scenario_report(summary), encoding="utf-8"
    )
    return summary


def main() -> int:
    args = parse_args()
    catalog = load_ingolstadt_catalog(ROOT / args.catalog)
    if args.list:
        for scenario_id, entry in catalog["scenarios"].items():
            begin, end = (seconds_to_hhmmss(int(value)) for value in entry["window_s"])
            focus = ",".join(entry["kpi_focus"])
            print(f"{scenario_id}: day={entry['day']} window={begin}-{end} focus={focus}")
        return 0

    specs = resolve_ingolstadt_specs(args, catalog)
    summaries = [run_scenario(args, spec) for spec in specs]
    suite = {
        "scenario_count": len(summaries),
        "scenario_set": SCENARIO_SET,
        "reference": REFERENCE,
        "scenarios": summaries,
    }
    reports_dir = ROOT / args.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    _write_json(reports_dir / "scenario_suite_summary.json", suite)
    (reports_dir / "scenario_suite_report.md").write_text(
        render_suite_report(suite), encoding="utf-8"
    )
    print(json.dumps(suite, indent=2, ensure_ascii=False))
    if args.generate_only:
        return 0
    failed = [
        item["scenario_id"]
        for item in summaries
        if item.get("verdict", {}).get("status") not in {"pass", "generated"}
    ]
    if failed:
        print(f"Ingolstadt scenario verdict not 'pass' for: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
