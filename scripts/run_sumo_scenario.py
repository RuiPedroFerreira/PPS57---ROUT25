#!/usr/bin/env python3
"""Generate and optionally run configured SUMO validation scenarios."""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
from copy import deepcopy
from functools import cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config  # noqa: E402
from pps57_sumo.build_network import build_sumo_artifacts  # noqa: E402
from pps57_sumo.detector_kpis import parse_detector_kpis  # noqa: E402
from pps57_sumo.parse_emissions import parse_emissions  # noqa: E402
from pps57_sumo.parse_insertion import parse_insertion_kpis  # noqa: E402
from pps57_sumo.parse_tripinfo import parse_tripinfo  # noqa: E402
from pps57_sumo.scenarios import (  # noqa: E402
    apply_scenario_profile,
    load_catalog,
    scenario_summary,
    validate_scenario_catalog,
)
from pps57_sumo.stats import T_CRITICAL_95, mean_ci95, t_critical_95  # noqa: E402
from pps57_tsp.config import load_tsp_config  # noqa: E402
from pps57_tsp.controller import TSPControlController  # noqa: E402

# baseline = controller em dry-run (apply_actuation=False); tsp_actuation = atuação
# real. Os dois braços partilham o MESMO caminho (run_tsp), diferindo só no toggle de
# atuação, pelo que a equivalência baseline ≡ no-actuation fica garantida por construção.
RUN_TYPES = ("baseline", "tsp_actuation")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate/run SUMO scenarios from the scenario catalog."
    )
    parser.add_argument("--config", default="configs/sumo_scenario_base.json", type=Path)
    parser.add_argument("--catalog", default="configs/scenario_catalog.yaml", type=Path)
    parser.add_argument(
        "--scenario", help="Scenario id to run. Use --all to run every catalog scenario."
    )
    parser.add_argument("--all", action="store_true", help="Run every scenario in the catalog.")
    parser.add_argument("--list", action="store_true", help="List configured scenarios and exit.")
    parser.add_argument(
        "--generate-only", action="store_true", help="Generate SUMO XMLs but do not execute SUMO."
    )
    parser.add_argument(
        "--run-type",
        choices=[*RUN_TYPES, "pair", "comparison", "all"],
        default="baseline",
        help="Pipeline to run for each scenario.",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=None,
        help=(
            "Optional max simulation steps (NOT seconds). Effective horizon is "
            "steps * simulation_step_length_s. Omit to run the full configured "
            "simulation_end_s window. Passing fewer steps than the demand window "
            "is rejected unless --allow-short-horizon is set (see that flag)."
        ),
    )
    parser.add_argument(
        "--allow-short-horizon",
        action="store_true",
        help=(
            "Permit --steps to halt the run before the configured demand window "
            "ends (deliberate smoke/debug run). Without this, a truncating --steps "
            "fails fast so a partial run is never silently reported as a full one."
        ),
    )
    parser.add_argument(
        "--seeds",
        nargs="+",
        type=int,
        default=None,
        help="One or more random seeds to run as replications. Overrides scenario_profile.random_seeds.",
    )
    parser.add_argument(
        "--tsp-config",
        default="configs/tsp_safety_config.json",
        type=Path,
        help="TSP config para os braços tsp_* (permite A/B de flags, ex.: v2.2 lifecycle).",
    )
    parser.add_argument("--sumo-binary", default="sumo")
    parser.add_argument(
        "--gui", action="store_true", help="Use sumo-gui for visual scenario execution."
    )
    parser.add_argument(
        "--skip-build", action="store_true", help="Skip netconvert after generating plain files."
    )
    parser.add_argument("--outputs-dir", default=Path("outputs/scenarios"), type=Path)
    parser.add_argument("--reports-dir", default=Path("reports/scenarios"), type=Path)
    return parser.parse_args()


REQUIRED_BASE_CONFIG_KEYS = ("scenario_profiles", "demand_profiles", "active_demand_profile")


def _load_base_config(path: Path) -> dict:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise SystemExit(f"Base scenario config not found: {path}") from exc
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Base scenario config is not valid JSON ({path}): {exc}") from exc
    if not isinstance(config, dict):
        raise SystemExit(f"Base scenario config must be a JSON object: {path}")
    missing = [key for key in REQUIRED_BASE_CONFIG_KEYS if key not in config]
    if missing:
        raise SystemExit(
            f"Base scenario config {path} is missing required keys: {', '.join(missing)}"
        )
    return config


def main() -> int:
    args = parse_args()
    base_config = _load_base_config(ROOT / args.config)
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
    (reports_dir / "scenario_suite_report.md").write_text(
        render_suite_report(scenario_report), encoding="utf-8"
    )
    # RESULTS.md é o documento de resultados de topo: gerado a partir dos dados
    # (nunca escrito à mão) para garantir rastreabilidade. Só a suite completa o
    # reescreve — um run de cenário único não deve substituir o relatório global.
    if args.all and not args.generate_only:
        (ROOT / "RESULTS.md").write_text(
            render_results_doc(scenario_report), encoding="utf-8"
        )
        print("RESULTS.md regenerado a partir de reports/scenarios/scenario_suite_summary.json")
    print(json.dumps(scenario_report, indent=2, ensure_ascii=False))
    # Propaga o veredito para o exit code: qualquer cenário executado com veredito
    # != "pass" (fail/inconclusive) tem de falhar o processo, senão CI/make engolem
    # regressões com exit 0. (--list já retornou acima; --generate-only produz "pass".)
    not_passing = [
        summary.get("scenario_id", "?")
        for summary in run_summaries
        if summary.get("verdict", {}).get("status") != "pass"
    ]
    if not_passing:
        print(f"Scenario verdict not 'pass' for: {', '.join(not_passing)}", file=sys.stderr)
        return 1
    return 0


def run_scenario(
    args: argparse.Namespace, base_config: dict, catalog: dict, scenario_id: str
) -> dict:
    config = apply_scenario_profile(base_config, scenario_id)
    _assert_horizon_not_truncated(config, args, scenario_id)
    scenario_output_dir = ROOT / args.outputs_dir / scenario_id
    scenario_report_dir = ROOT / args.reports_dir / scenario_id
    scenario_output_dir.mkdir(parents=True, exist_ok=True)
    scenario_report_dir.mkdir(parents=True, exist_ok=True)

    # pair/comparison/all são aliases retidos para compatibilidade (Makefile/README):
    # após a consolidação para dois modos, todos resolvem para baseline + tsp_actuation.
    if args.run_type in {"pair", "comparison", "all"}:
        run_types = ["baseline", "tsp_actuation"]
    else:
        run_types = [args.run_type]

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

    apply_relative_insertion_gate(scenario_runs)
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
    (scenario_report_dir / "scenario_report.md").write_text(
        render_scenario_report(summary), encoding="utf-8"
    )

    if args.generate_only:
        summary["status"] = "generated"
        return summary
    summary["status"] = "completed"
    return summary


# Margem do gate relativo de inserção: o braço candidato só falha o gate de
# max_waiting_to_insert se exceder simultaneamente o limiar absoluto E o
# baseline emparelhado (mesma seed) com 10% de folga. Racional: o limiar
# absoluto protege a validade material do cenário (e mantém-se intacto para o
# baseline); quando o próprio baseline opera encostado ao limiar (ex.: 148s
# para um gate de 150s no envelope am_peak), +1-3s de perturbação do TSP não é
# uma regressão material — medir o candidato contra o baseline emparelhado é
# que distingue "margem do cenário" de "degradação causada pelo TSP".
RELATIVE_INSERTION_GATE_FACTOR = 1.1
_INSERTION_GATE_REASON = "sumo_max_waiting_to_insert_gt_threshold"


def _replications_of(run: dict) -> list[dict]:
    """Réplicas de um run agregado, ou o próprio run quando single-seed."""
    reps = run.get("replication_summaries")
    return list(reps) if reps else [run]


def apply_relative_insertion_gate(scenario_runs: dict[str, dict], *, load_kpis=None) -> None:
    """Relativiza o gate de inserção dos braços candidatos e agrega verdicts.

    1) Para cada réplica de um braço candidato (tudo o que não é baseline) cujo
       run_verdict falhou APENAS/também por max_waiting_to_insert, remove essa
       razão se candidate <= max(limiar_absoluto, baseline_mesma_seed * 1.1).
    2) Recalcula o run_verdict agregado de TODOS os braços multi-seed como o
       pior das réplicas (antes herdava o da primeira réplica, escondendo
       falhas de seeds seguintes).
    """
    loader = load_kpis if load_kpis is not None else _load_kpis
    baseline = scenario_runs.get("baseline")
    base_kpis_by_seed: dict = {}
    if baseline:
        for rep in _replications_of(baseline):
            kpis = loader(rep.get("kpis"))
            if kpis:
                base_kpis_by_seed[rep.get("seed")] = kpis

    for run_type, run in scenario_runs.items():
        if run_type != "baseline" and base_kpis_by_seed:
            for rep in _replications_of(run):
                verdict = rep.get("run_verdict") or {}
                reasons = list(verdict.get("reasons", []))
                if _INSERTION_GATE_REASON not in reasons:
                    continue
                kpis = loader(rep.get("kpis"))
                base = base_kpis_by_seed.get(rep.get("seed"))
                if not kpis or not base:
                    continue
                candidate_value = float(
                    kpis.get("insertion", {}).get("max_waiting_to_insert", 0) or 0
                )
                baseline_value = float(
                    base.get("insertion", {}).get("max_waiting_to_insert", 0) or 0
                )
                threshold = float(_sumo_quality_thresholds(kpis)["max_waiting_to_insert"])
                allowed = max(threshold, baseline_value * RELATIVE_INSERTION_GATE_FACTOR)
                if candidate_value <= allowed:
                    reasons.remove(_INSERTION_GATE_REASON)
                    rep["run_verdict"] = {
                        "status": "fail" if reasons else "pass",
                        "reasons": reasons,
                    }
                    rep["insertion_gate_note"] = (
                        f"gate relativo: candidate {candidate_value:.0f}s <= "
                        f"max(absoluto {threshold:.0f}s, baseline {baseline_value:.0f}s x "
                        f"{RELATIVE_INSERTION_GATE_FACTOR})"
                    )
        _recompute_aggregate_verdict(run)


def _recompute_aggregate_verdict(run: dict) -> None:
    """Verdict agregado multi-seed = pior das réplicas, com razões por seed."""
    reps = run.get("replication_summaries")
    if not reps:
        return
    statuses = []
    reasons = []
    for rep in reps:
        verdict = rep.get("run_verdict") or {}
        status = verdict.get("status", "pass")
        statuses.append(status)
        if status != "pass":
            seed = rep.get("seed")
            reasons.extend(f"seed_{seed}:{reason}" for reason in verdict.get("reasons", []))
    if "fail" in statuses:
        status = "fail"
    elif "inconclusive" in statuses:
        status = "inconclusive"
    else:
        status = "pass"
    run["run_verdict"] = {"status": status, "reasons": reasons}


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


# Valores críticos t de Student (bicaudal, 95%) por graus de liberdade (n-1).
# Sem scipy: tabela para n pequeno; df>30 aproxima-se do z normal (1.96). Isto
# permite reportar um intervalo de confiança honesto sobre a média de KPIs ao
# longo de réplicas (seeds), em vez de apenas um ponto de uma única corrida.
# Student-t 95% CI machinery extraída para pps57_sumo.stats (reutilizada pelo
# OPE em pps57_opt.ope). Aliases _-prefixados mantidos para call-sites internos
# e testes (test_scenario_replication_stats acede rss._mean_ci95/_t_critical_95).
_T_CRITICAL_95 = T_CRITICAL_95
_t_critical_95 = t_critical_95
_mean_ci95 = mean_ci95


def _compute_kpi_aggregate(kpis_list: list[dict]) -> dict:
    """Mean, spread (p5/p95) and 95% CI of headline KPIs across replications."""

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
        out = _mean_ci95(values)
        if not values:
            out.update({"stdev": None, "p5": None, "p95": None})
            return out
        sorted_v = sorted(values)
        p5_idx = max(0, int(round((len(sorted_v) - 1) * 0.05)))
        p95_idx = min(len(sorted_v) - 1, int(round((len(sorted_v) - 1) * 0.95)))
        # `stdev` (populacional) mantido para retrocompatibilidade; `stdev_sample`
        # e `ci95_*` são as estatísticas de inferência.
        out.update(
            {
                "stdev": round(statistics.pstdev(values), 3) if len(values) > 1 else 0.0,
                "p5": round(sorted_v[p5_idx], 3),
                "p95": round(sorted_v[p95_idx], 3),
            }
        )
        return out

    return {
        "bus_mean_time_loss_s": stat(collect(["buses", "mean_time_loss_s"])),
        "general_mean_time_loss_s": stat(collect(["general_traffic", "mean_time_loss_s"])),
        "all_vehicles_mean_duration_s": stat(collect(["all_vehicles", "mean_duration_s"])),
        "max_network_queue_vehicles": stat(
            collect(["detectors", "network_queue", "max_queue_vehicles"])
        ),
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
    config.setdefault("detectors", {})
    config["detectors"]["e1_output"] = "../../e1_detectors.xml"
    config["detectors"]["e2_output"] = "../../e2_queues.xml"

    artifacts = build_sumo_artifacts(
        config,
        root=ROOT,
        base_dir=run_output_dir / "sumo",
        output_dir=run_output_dir,
        build_net=not args.skip_build,
    )
    (run_output_dir / "resolved_config.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = scenario_summary(config)
    summary["run_type"] = run_type
    summary["catalog"] = catalog["scenarios"][scenario_id]
    summary["outputs_dir"] = str(run_output_dir.relative_to(ROOT))
    summary["reports_dir"] = str(run_report_dir.relative_to(ROOT))
    summary["max_steps"] = args.steps
    summary["requested_steps"] = args.steps
    # Sem isto, _replication_kpis_by_seed não consegue emparelhar réplicas
    # (rep.get("seed") era sempre None) e o teste de significância t-Student
    # nunca aparecia nos sumários multi-seed.
    summary["seed"] = int(seed)
    summary["step_length_s"] = float(config.get("simulation_step_length_s", 1.0))
    summary["configured_end_s"] = float(config.get("simulation_end_s", 7200))
    summary["sumo_quality_thresholds"] = dict(config.get("sumo_quality_thresholds", {}))
    summary["effective_end_s"] = _effective_end_s(config, args.steps)
    summary["sumocfg"] = str(artifacts.sumocfg_file.relative_to(ROOT))
    summary["network"] = str(artifacts.network_file.relative_to(ROOT))

    if args.generate_only:
        summary["status"] = "generated"
        return summary

    if run_type == "baseline":
        # Baseline = o controller real em dry-run: decide mas não atua. Mesmo
        # caminho que tsp_actuation, só com o toggle de atuação a falso.
        run_tsp(args, scenario_id, run_output_dir, run_report_dir, artifacts, apply_actuation=False)
    elif run_type == "tsp_actuation":
        run_tsp(args, scenario_id, run_output_dir, run_report_dir, artifacts, apply_actuation=True)
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


def _effective_end_s(config: dict, requested_steps: int | None) -> float:
    configured_begin = float(config.get("simulation_begin_s", 0))
    configured_end = float(config.get("simulation_end_s", 7200))
    if requested_steps is None:
        return configured_end
    step_length = float(config.get("simulation_step_length_s", 1.0))
    if step_length <= 0:
        raise SystemExit("simulation_step_length_s must be > 0.")
    requested_end = configured_begin + max(0, int(requested_steps)) * step_length
    return min(configured_end, requested_end)


def _assert_horizon_not_truncated(
    config: dict, args: argparse.Namespace, scenario_id: str
) -> None:
    """Fail fast when ``--steps`` would halt the run before the demand window ends.

    ``--steps`` counts TraCI *steps*, not seconds: at 0.5 s/step, ``--steps 7200``
    stops the sim at 3600 s, i.e. half of the configured 7200 s (2 h) window. That
    is exactly the defect that made 7 of 8 scenarios run 1 h instead of 2 h — the
    demand keeps emitting past the halt, so ~half the vehicles never depart and
    every KPI reflects a partial run while still looking "clean". Reject it unless
    the caller explicitly opts into a short run.
    """
    if args.steps is None or getattr(args, "allow_short_horizon", False):
        return
    step_length = float(config.get("simulation_step_length_s", 1.0))
    configured_end = float(config.get("simulation_end_s", 7200))
    effective_end = _effective_end_s(config, args.steps)
    if effective_end < configured_end:
        full_steps = int(round(configured_end / step_length)) if step_length else 0
        raise SystemExit(
            f"[{scenario_id}] --steps {args.steps} @ {step_length}s/step = "
            f"{effective_end:.0f}s horizon, truncating the configured "
            f"{configured_end:.0f}s demand window (~{configured_end - effective_end:.0f}s "
            f"of demand would never depart, biasing every KPI). Omit --steps to run the "
            f"full window, pass --steps {full_steps} for the full {configured_end:.0f}s, "
            f"or --allow-short-horizon for a deliberate smoke run."
        )


def run_tsp(
    args: argparse.Namespace,
    scenario_id: str,
    run_output_dir: Path,
    run_report_dir: Path,
    artifacts,
    *,
    apply_actuation: bool,
) -> None:
    clear_global_sumo_outputs()
    cits_config_path = write_cits_config(scenario_id, run_output_dir, run_report_dir, artifacts)
    tsp_config_path = write_tsp_config(
        scenario_id, run_output_dir, run_report_dir, source=ROOT / args.tsp_config
    )
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
    kpis = (
        parse_tripinfo(tripinfo)
        if tripinfo.exists()
        else {"source": str(tripinfo), "missing_tripinfo": True}
    )
    kpis["detectors"] = parse_detector_kpis(
        run_output_dir / "e1_detectors.xml", run_output_dir / "e2_queues.xml"
    )
    kpis["insertion"] = parse_insertion_kpis(
        run_output_dir / "summary.xml", run_output_dir / "statistics.xml"
    )
    # Emissions are carried as per-vehicle trip totals inside tripinfo.xml (via
    # the emissions device), not a per-step emission-output dump — see cfg.
    kpis["emissions"] = parse_emissions(run_output_dir / "tripinfo.xml")
    return kpis


def write_cits_config(
    scenario_id: str, run_output_dir: Path, run_report_dir: Path, artifacts
) -> Path:
    raw = json.loads((ROOT / "configs/cits_v2x_config.json").read_text(encoding="utf-8"))
    raw["scenario_id"] = f"{scenario_id}_cits"
    raw.setdefault("sumo", {}).update(
        {
            "sumocfg": str(artifacts.sumocfg_file.relative_to(ROOT)),
            "network": str(artifacts.network_file.relative_to(ROOT)),
        }
    )
    raw.setdefault("logging", {}).update(
        {
            "message_log": str((run_output_dir / "cits_messages.jsonl").relative_to(ROOT)),
            "summary_report": str(
                (run_report_dir / "cits_emulation_summary.json").relative_to(ROOT)
            ),
            "mapem_snapshot": str((run_output_dir / "cits_mapem_snapshot.json").relative_to(ROOT)),
            "spatem_snapshot": str(
                (run_output_dir / "cits_spatem_snapshot.json").relative_to(ROOT)
            ),
        }
    )
    config_path = run_output_dir / "cits_v2x_config.json"
    config_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    return config_path


def write_tsp_config(
    scenario_id: str,
    run_output_dir: Path,
    run_report_dir: Path,
    source: Path | None = None,
) -> Path:
    raw = json.loads(
        (source or ROOT / "configs/tsp_safety_config.json").read_text(encoding="utf-8")
    )
    raw["scenario_id"] = f"{scenario_id}_tsp"
    raw.setdefault("logging", {}).update(
        {
            "decision_log": str((run_output_dir / "tsp_decisions.jsonl").relative_to(ROOT)),
            "actuation_log": str((run_output_dir / "tsp_actuation.jsonl").relative_to(ROOT)),
            "summary_report": str(
                (run_report_dir / "tsp_emulation_summary.json").relative_to(ROOT)
            ),
        }
    )
    config_path = run_output_dir / "tsp_safety_config.json"
    config_path.write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")
    return config_path


GLOBAL_SUMO_OUTPUTS = ("tripinfo.xml", "summary.xml", "statistics.xml")


def clear_global_sumo_outputs() -> None:
    # SUMO writes these files at fixed paths declared in corredor.sumocfg, so
    # only one scenario at a time can own them. The CLI runs scenarios
    # sequentially (--all is a serial loop); do NOT parallelise scenario
    # execution without first moving these outputs into per-run paths via
    # SUMO's --tripinfo-output/--summary flags.
    for name in GLOBAL_SUMO_OUTPUTS:
        path = ROOT / "outputs" / name
        if path.exists():
            path.unlink()


def copy_global_sumo_outputs(run_output_dir: Path) -> None:
    # Always overwrite the per-run copy when SUMO has produced a fresh global
    # output. Previously this skipped the copy if the per-run file already existed,
    # so re-running a scenario/seed dir without `make clean` kept the PREVIOUS run's
    # tripinfo/summary/statistics and collect_run_kpis parsed stale data as fresh.
    for name in GLOBAL_SUMO_OUTPUTS:
        source = ROOT / "outputs" / name
        target = run_output_dir / name
        if source.exists():
            shutil.copy2(source, target)


def _replication_kpis_by_seed(run: dict) -> dict[int, dict]:
    """Mapeia seed -> KPIs carregados, para as réplicas de um run_type."""
    out: dict[int, dict] = {}
    for rep in run.get("replication_summaries", []) or []:
        seed = rep.get("seed")
        kpis = _load_kpis(rep.get("kpis"))
        if seed is not None and kpis:
            out[int(seed)] = kpis
    return out


def _paired_significance(
    baseline_run: dict,
    candidate_run: dict,
    group: str,
    metric: str,
    *,
    lower_is_better: bool,
) -> dict | None:
    """Teste de significância emparelhado por seed sobre um KPI.

    Para cada seed comum a baseline e candidato, calcula a melhoria
    (redução do KPI quando ``lower_is_better``) e devolve a média com IC95
    t-Student. Sem >=2 seeds emparelhados não há base estatística -> None.
    """
    base_by_seed = _replication_kpis_by_seed(baseline_run)
    cand_by_seed = _replication_kpis_by_seed(candidate_run)
    common = sorted(set(base_by_seed) & set(cand_by_seed))
    deltas: list[float] = []
    for seed in common:
        base_value = base_by_seed[seed].get(group, {}).get(metric)
        cand_value = cand_by_seed[seed].get(group, {}).get(metric)
        if isinstance(base_value, (int, float)) and isinstance(cand_value, (int, float)):
            improvement = (
                (base_value - cand_value) if lower_is_better else (cand_value - base_value)
            )
            deltas.append(float(improvement))
    if len(deltas) < 2:
        return None
    ci = _mean_ci95(deltas)
    ci_low, ci_high = ci["ci95_low"], ci["ci95_high"]
    if ci_low is not None and ci_low > 0:
        verdict = "significant_improvement"
    elif ci_high is not None and ci_high < 0:
        verdict = "significant_regression"
    else:
        verdict = "inconclusive_ci_includes_zero"
    return {
        "metric": f"{group}.{metric}",
        "paired_seeds": common,
        "n": ci["n"],
        "mean_improvement": ci["mean"],
        "ci95_low": ci_low,
        "ci95_high": ci_high,
        "verdict": verdict,
        "note": (
            "Melhoria = redução do KPI (lower_is_better=True); IC95 t-Student "
            "emparelhado por seed. Significativo só quando o IC95 exclui zero."
        ),
    }


def compare_scenario_runs(runs: dict[str, dict]) -> dict:
    baseline = _load_kpis(runs.get("baseline", {}).get("kpis"))
    baseline_run = runs.get("baseline", {})
    comparisons: dict[str, dict] = {}
    for run_type in ("tsp_actuation",):
        candidate = _load_kpis(runs.get(run_type, {}).get("kpis"))
        if not baseline or not candidate:
            continue
        comparison = compare_kpis(baseline, candidate)
        # Quando há réplicas multi-seed em ambos os braços, acrescenta o teste
        # de significância emparelhado — a comparação ponto-a-ponto sozinha não
        # suporta qualquer alegação de efeito TSP estatisticamente significativo.
        significance = _paired_significance(
            baseline_run, runs.get(run_type, {}), "buses", "mean_time_loss_s", lower_is_better=True
        )
        if significance is not None:
            comparison["bus_time_loss_replication_significance"] = significance
        # v2.1: o trade-off precisa de IC nos dois lados — o custo no tráfego
        # geral merece a mesma honestidade estatística que o ganho do TP.
        # (lower_is_better=True: "melhoria" positiva = redução do time loss;
        # um custo TSP real aparece como significant_regression.)
        general_significance = _paired_significance(
            baseline_run,
            runs.get(run_type, {}),
            "general_traffic",
            "mean_time_loss_s",
            lower_is_better=True,
        )
        if general_significance is not None:
            comparison["general_traffic_time_loss_replication_significance"] = general_significance
        comparisons[f"baseline_vs_{run_type}"] = comparison
    return comparisons


def compare_kpis(baseline: dict, candidate: dict) -> dict:
    bus_delta = _metric_delta(
        baseline, candidate, "buses", "mean_time_loss_s", lower_is_better=True
    )
    general_delta = _metric_delta(
        baseline, candidate, "general_traffic", "mean_time_loss_s", lower_is_better=True
    )
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


def _metric_delta(
    baseline: dict, candidate: dict, group: str, metric: str, *, lower_is_better: bool
) -> dict:
    base_value = baseline.get(group, {}).get(metric)
    candidate_value = candidate.get(group, {}).get(metric)
    if base_value is None or candidate_value is None:
        return {
            "baseline": base_value,
            "candidate": candidate_value,
            "delta": None,
            "regression_pct": None,
        }
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
    thresholds = _sumo_quality_thresholds(kpis)
    insertion = kpis.get("insertion", {})

    if kpis.get("all_vehicles", {}).get("vehicles", 0) <= 0:
        reasons.append("no_completed_vehicles")
    if kpis.get("buses", {}).get("vehicles", 0) <= 0:
        scenario = kpis.get("scenario", {})
        max_steps = scenario.get("max_steps")
        if max_steps is not None and float(max_steps) < 1800:
            inconclusive.append("no_completed_buses_in_short_smoke_run")
        else:
            reasons.append("no_completed_buses")
    if int(insertion.get("collisions", 0) or 0) > int(thresholds["max_collisions"]):
        reasons.append("sumo_collisions_gt_threshold")
    if int(insertion.get("teleports_total", 0) or 0) > int(thresholds["max_teleports_total"]):
        reasons.append("sumo_teleports_gt_threshold")
    # Jam-type teleports indicate gridlock and are always a hard fail, even
    # when the total-teleports gate is relaxed to absorb yield-type teleports
    # at give-way junctions (I6 roundabout, I7 lane 3 turn).
    if int(insertion.get("teleports_jam", 0) or 0) > int(thresholds["max_teleports_jam"]):
        reasons.append("sumo_jam_teleports_gt_threshold")
    emergency_braking = int(insertion.get("emergency_braking", 0) or 0)
    completed_vehicles = int(kpis.get("all_vehicles", {}).get("vehicles", 0) or 0)
    emergency_braking_rate = (
        emergency_braking / completed_vehicles * 1000.0
        if completed_vehicles > 0
        else float(emergency_braking)
    )
    min_completed_for_rate = int(thresholds["min_completed_vehicles_for_rate_gates"])
    rate_gate_applies = completed_vehicles >= min_completed_for_rate
    if emergency_braking > int(thresholds["max_emergency_braking"]) or (
        rate_gate_applies
        and emergency_braking_rate > float(thresholds["max_emergency_braking_per_1000_vehicles"])
    ):
        reasons.append("sumo_emergency_braking_gt_threshold")
    if int(insertion.get("vehicles_waiting", 0) or 0) > int(
        thresholds["max_vehicles_waiting_at_end"]
    ):
        reasons.append("sumo_waiting_to_insert_at_end_gt_threshold")
    if int(insertion.get("insertion_gap_at_end", 0) or 0) > int(
        thresholds["max_insertion_gap_at_end"]
    ):
        reasons.append("sumo_insertion_gap_at_end_gt_threshold")
    if int(insertion.get("max_waiting_to_insert", 0) or 0) > int(
        thresholds["max_waiting_to_insert"]
    ):
        reasons.append("sumo_max_waiting_to_insert_gt_threshold")
    steps = int(insertion.get("steps", 0) or 0)
    if steps > 0:
        backlog_ratio = float(insertion.get("backlog_step_count", 0) or 0) / float(steps)
        if backlog_ratio > float(thresholds["max_backlog_step_ratio"]):
            reasons.append("sumo_backlog_step_ratio_gt_threshold")
    if inconclusive and not reasons:
        return {"status": "inconclusive", "reasons": inconclusive}
    return {"status": "fail" if reasons else "pass", "reasons": reasons}


def _sumo_quality_thresholds(kpis: dict) -> dict[str, float | int]:
    scenario_thresholds = kpis.get("scenario", {}).get("sumo_quality_thresholds", {})
    # Fallback only: every pipeline run carries its own thresholds from
    # configs/sumo_scenario_base.json (merged in via apply_scenario_profile and
    # stored under kpis["scenario"]). These defaults MIRROR that file — the source
    # of truth — and must be kept in sync with it if it is recalibrated. (8 teleports
    # and 150 insertion-gap are deliberate: 3 was too tight, 0 unsatisfiable — see
    # the `note` field there.)
    defaults: dict[str, float | int] = {
        "max_collisions": 0,
        "max_teleports_total": 8,
        "max_teleports_jam": 0,
        "max_emergency_braking": 150,
        "max_emergency_braking_per_1000_vehicles": 30,
        "min_completed_vehicles_for_rate_gates": 500,
        "max_waiting_to_insert": 150,
        "max_vehicles_waiting_at_end": 150,
        "max_insertion_gap_at_end": 150,
        "max_backlog_step_ratio": 0.75,
    }
    if isinstance(scenario_thresholds, dict):
        defaults.update(
            {key: scenario_thresholds[key] for key in defaults if key in scenario_thresholds}
        )
    return defaults


def scenario_verdict(summary: dict) -> dict:
    # Status is derived from structured flags, not by substring-matching the reason
    # strings: a hard fail in any run or comparison dominates; an inconclusive run
    # only downgrades the scenario to "inconclusive" when nothing actually failed.
    # The reason strings keep their ":inconclusive:" marker purely for readability.
    reasons = []
    has_fail = False
    has_inconclusive = False
    for run_type, run in summary.get("runs", {}).items():
        verdict = run.get("run_verdict", {})
        status = verdict.get("status")
        if status == "fail":
            has_fail = True
            reasons.append(f"{run_type}:{','.join(verdict.get('reasons', []))}")
        elif status == "inconclusive":
            has_inconclusive = True
            reasons.append(f"{run_type}:inconclusive:{','.join(verdict.get('reasons', []))}")
    for key, comparison in summary.get("comparisons", {}).items():
        if comparison.get("verdict") == "fail":
            has_fail = True
            reasons.append(f"{key}:{','.join(comparison.get('fail_reasons', []))}")
    if has_fail:
        return {"status": "fail", "reasons": reasons}
    if has_inconclusive:
        return {"status": "inconclusive", "reasons": reasons}
    return {"status": "pass", "reasons": reasons}


@cache
def _load_kpis_cached(path_str: str, mtime: float) -> dict | None:
    # kpis.json is written once per run then read many times (paired significance
    # ×2, the point comparison, and the scenario/suite/RESULTS reports). Caching on
    # (path, mtime) collapses ~10 reads+parses of each file into one, while the
    # mtime key stays correct if a file is regenerated within the same process.
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


def _load_kpis(rel: str | None) -> dict | None:
    # Returns a fresh deepcopy of the (path, mtime)-cached parse, so callers may treat
    # the result as owned/mutable without corrupting the shared cache entry. The cache
    # still collapses the ~10 reads+parses of each kpis.json into one disk read.
    if not rel:
        return None
    path = ROOT / rel
    if not path.exists():
        return None
    cached = _load_kpis_cached(str(path), path.stat().st_mtime)
    return deepcopy(cached) if cached is not None else None


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
        emissions_totals = (
            kpis.get("emissions", {}).get("totals_mg", {})
            if isinstance(kpis.get("emissions"), dict)
            else {}
        )
        lines.append(
            "| {run} | {status} | {veh} | {bus} | {bus_loss} | {gen_loss} | {queue} | {co2} | {fuel} |".format(
                run=run_type,
                status=run.get("run_verdict", {}).get("status", run.get("status")),
                veh=kpis.get("all_vehicles", {}).get("vehicles", ""),
                bus=kpis.get("buses", {}).get("vehicles", ""),
                bus_loss=kpis.get("buses", {}).get("mean_time_loss_s", ""),
                gen_loss=kpis.get("general_traffic", {}).get("mean_time_loss_s", ""),
                queue=kpis.get("detectors", {})
                .get("network_queue", {})
                .get("max_queue_vehicles", ""),
                co2=emissions_totals.get("CO2", ""),
                fuel=emissions_totals.get("fuel", ""),
            )
        )

    significance_rows = [
        (key, comparison[sig_key])
        for key, comparison in summary.get("comparisons", {}).items()
        if isinstance(comparison, dict)
        for sig_key in (
            "bus_time_loss_replication_significance",
            "general_traffic_time_loss_replication_significance",
        )
        if sig_key in comparison
    ]
    if significance_rows:
        lines += [
            "",
            f"Seeds (réplicas): {summary.get('seeds', [])}",
            "",
            "## timeLoss — significância emparelhada por seed (IC95 t-Student)",
            "",
            "Melhoria = redução vs baseline; custo TSP real no tráfego geral",
            "aparece como significant_regression.",
            "",
            "| Comparação | Métrica | n | Melhoria média (s) | IC95 baixo | IC95 alto | Veredito |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
        for key, sig in significance_rows:
            lines.append(
                f"| {key} | {sig.get('metric', '')} | {sig.get('n', '')} | "
                f"{sig.get('mean_improvement', '')} | "
                f"{sig.get('ci95_low', '')} | {sig.get('ci95_high', '')} | {sig.get('verdict', '')} |"
            )
    return "\n".join(lines) + "\n"


def _scenario_horizon_s(scenario: dict) -> float | None:
    """Effective simulated horizon (s) for a scenario, read back from its runs."""
    for run in scenario.get("runs", {}).values():
        end = run.get("effective_end_s")
        if isinstance(end, (int, float)):
            return float(end)
    return None


def _bus_significance(scenario: dict) -> dict | None:
    cmp = scenario.get("comparisons", {}).get("baseline_vs_tsp_actuation", {})
    return cmp.get("bus_time_loss_replication_significance") if isinstance(cmp, dict) else None


def render_suite_report(report: dict) -> str:
    lines = [
        "# Scenario Suite Report",
        "",
        "| Scenario | Verdict | Seeds | Horizonte (s) | Comparisons | Bus timeLoss (sig.) |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for scenario in report.get("scenarios", []):
        seeds = scenario.get("seeds", [])
        horizon = _scenario_horizon_s(scenario)
        horizon_cell = f"{horizon:.0f}" if horizon is not None else "—"
        sig = _bus_significance(scenario)
        sig_cell = f"{sig.get('verdict', '')} ({sig.get('mean_improvement', '')}s)" if sig else "—"
        lines.append(
            f"| {scenario['scenario_id']} | "
            f"{scenario.get('verdict', {}).get('status', 'unknown')} | "
            f"{len(seeds)} | {horizon_cell} | "
            f"{len(scenario.get('comparisons', {}))} | {sig_cell} |"
        )
    return "\n".join(lines) + "\n"


# Preâmbulo estático de metodologia para o RESULTS.md gerado. É raciocínio (não
# uma alegação numérica), por isso é seguro mantê-lo fixo; todos os NÚMEROS abaixo
# dele saem de reports/scenarios/scenario_suite_summary.json.
_RESULTS_METHODOLOGY = """\
_Documento gerado automaticamente por `scripts/run_sumo_scenario.py` a partir de
`reports/scenarios/scenario_suite_summary.json`. **Não editar à mão** — corre
`make scenario-suite` (opcionalmente após `make clean`) para o regenerar a partir
dos dados._

## Desenho da avaliação

Cada cenário é uma **comparação emparelhada** entre o braço `baseline` (o controlador
TSP em dry-run: decide mas nunca atua) e o braço `tsp_actuation` (idêntico, mas a
aplicar os comandos aprovados), sobre o conjunto de seeds configurado em
`scenario_profiles[*].random_seeds`. Como ambos os braços partilham procura, geometria
e seed, qualquer diferença medida é atribuível à atuação e não à máquina de execução.

Cada run cobre a janela completa de procura (`simulation_end_s`); a guarda
`--allow-short-horizon` impede que um `--steps` curto trunque a janela sem aviso.
Os ganhos por seed são emparelhados e reportados como média com intervalo de
confiança a 95 % (t-Student); o efeito só é **significativo quando o IC95 exclui
zero**. Com poucas seeds os intervalos são largos — uma direção consistente não é
o mesmo que um tamanho de efeito provado.
"""


def _arm_aggregate_mean(run: dict, agg_key: str) -> float | None:
    agg = run.get("kpi_aggregate", {})
    stat = agg.get(agg_key) if isinstance(agg, dict) else None
    if isinstance(stat, dict) and isinstance(stat.get("mean"), (int, float)):
        return float(stat["mean"])
    return None


def _arm_emission_total(run: dict, gas: str, agg_key: str) -> float | None:
    """Mean total emission across seeds; falls back to the (single-seed) KPI file."""
    mean = _arm_aggregate_mean(run, agg_key)
    if mean is not None:
        return mean
    kpis = _load_kpis(run.get("kpis"))
    if kpis:
        value = kpis.get("emissions", {}).get("totals_mg", {}).get(gas)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _pct_delta(baseline: float | None, candidate: float | None) -> str:
    if baseline is None or candidate is None or baseline == 0:
        return "—"
    return f"{100 * (candidate - baseline) / baseline:+.1f}%"


def _ci_cell(sig: dict | None) -> str:
    if not sig or sig.get("ci95_low") is None or sig.get("ci95_high") is None:
        return "—"
    return f"[{sig['ci95_low']:+.1f}, {sig['ci95_high']:+.1f}]"


def render_results_doc(report: dict) -> str:
    """Build the repo-root RESULTS.md entirely from the suite summary (no hand-typed
    numbers). Scenarios without a completed paired comparison render as 'pendente'."""
    scenarios = report.get("scenarios", [])
    horizons = {h for s in scenarios if (h := _scenario_horizon_s(s)) is not None}
    steps = {
        run.get("step_length_s")
        for s in scenarios
        for run in s.get("runs", {}).values()
        if isinstance(run.get("step_length_s"), (int, float))
    }
    total_runs = sum(len(s.get("seeds", [])) * len(s.get("runs", {})) for s in scenarios)
    horizon_txt = (
        f"{next(iter(horizons)):.0f}s (~{next(iter(horizons)) / 3600:.1f}h)"
        if len(horizons) == 1
        else "variável entre cenários (ver tabela de qualidade)"
        if horizons
        else "—"
    )
    step_txt = f"{next(iter(steps))}s" if len(steps) == 1 else "—"

    lines = [
        "# Results",
        "",
        _RESULTS_METHODOLOGY,
        "",
        f"**Cobertura:** {len(scenarios)} cenários · {total_runs} runs SUMO · "
        f"janela {horizon_txt} · passo {step_txt}.",
        "",
        "## Impacto no transporte público (atraso dos autocarros)",
        "",
        "Melhoria = redução do `buses.mean_time_loss_s` vs baseline (emparelhada por seed).",
        "",
        "| Cenário | seeds (n) | Melhoria média (s) | IC95 (s) | Veredito estatístico | Δ ponto seed-base (%) |",
        "|---|---:|---:|---:|---|---:|",
    ]
    for s in scenarios:
        cmp = s.get("comparisons", {}).get("baseline_vs_tsp_actuation", {})
        if not cmp:
            lines.append(f"| {s['scenario_id']} | {len(s.get('seeds', []))} | pendente | — | — | — |")
            continue
        sig = cmp.get("bus_time_loss_replication_significance")
        point = cmp.get("bus_time_loss", {})
        n = sig.get("n") if sig else "1"
        mean_imp = f"{sig['mean_improvement']:+.1f}" if sig else "—"
        verdict = sig.get("verdict", "single_seed_no_ci") if sig else "single_seed_no_ci"
        reg_pct = point.get("regression_pct")
        # regression_pct é positivo quando o TSP piora; mostramos como melhoria (-)
        pt = f"{-reg_pct:+.1f}%" if isinstance(reg_pct, (int, float)) else "—"
        lines.append(
            f"| {s['scenario_id']} | {n} | {mean_imp} | {_ci_cell(sig)} | {verdict} | {pt} |"
        )

    lines += [
        "",
        "## Impacto no tráfego geral",
        "",
        "Mesma convenção: melhoria = redução do `general_traffic.mean_time_loss_s`. "
        "Um custo real do TSP aparece como `significant_regression`.",
        "",
        "| Cenário | Melhoria média (s) | IC95 (s) | Veredito estatístico |",
        "|---|---:|---:|---|",
    ]
    for s in scenarios:
        cmp = s.get("comparisons", {}).get("baseline_vs_tsp_actuation", {})
        if not cmp:
            lines.append(f"| {s['scenario_id']} | pendente | — | — |")
            continue
        sig = cmp.get("general_traffic_time_loss_replication_significance")
        mean_imp = f"{sig['mean_improvement']:+.1f}" if sig else "—"
        verdict = sig.get("verdict", "single_seed_no_ci") if sig else "single_seed_no_ci"
        lines.append(f"| {s['scenario_id']} | {mean_imp} | {_ci_cell(sig)} | {verdict} |")

    lines += [
        "",
        "## Emissões (TSP vs baseline)",
        "",
        "Total de frota por run; média entre seeds quando disponível. Negativo = redução.",
        "",
        "| Cenário | CO2 Δ | Combustível Δ |",
        "|---|---:|---:|",
    ]
    for s in scenarios:
        runs = s.get("runs", {})
        base, tsp = runs.get("baseline", {}), runs.get("tsp_actuation", {})
        if not base or not tsp:
            lines.append(f"| {s['scenario_id']} | pendente | pendente |")
            continue
        co2 = _pct_delta(
            _arm_emission_total(base, "CO2", "total_co2_mg"),
            _arm_emission_total(tsp, "CO2", "total_co2_mg"),
        )
        fuel = _pct_delta(
            _arm_emission_total(base, "fuel", "total_fuel_mg"),
            _arm_emission_total(tsp, "fuel", "total_fuel_mg"),
        )
        lines.append(f"| {s['scenario_id']} | {co2} | {fuel} |")

    lines += [
        "",
        "## Qualidade e viabilidade da simulação",
        "",
        "| Cenário | Veredito | Horizonte (s) | seeds | Comparações |",
        "|---|---|---:|---:|---:|",
    ]
    for s in scenarios:
        horizon = _scenario_horizon_s(s)
        horizon_cell = f"{horizon:.0f}" if horizon is not None else "—"
        lines.append(
            f"| {s['scenario_id']} | {s.get('verdict', {}).get('status', 'unknown')} | "
            f"{horizon_cell} | {len(s.get('seeds', []))} | {len(s.get('comparisons', {}))} |"
        )

    lines += [
        "",
        "## Reprodução",
        "",
        "```bash",
        "make clean          # limpa outputs/reports de cenário (preserva os .md versionados)",
        "make scenario-suite # corre os 8 cenários, ambos os braços, todas as seeds, janela completa",
        "```",
        "",
        "Os números acima são regenerados no fim de `make scenario-suite`. Para mais seeds "
        "(IC95 mais apertado) edita `scenario_profiles[*].random_seeds` em "
        "`configs/sumo_scenario_base.json` ou corre `make scenario-suite SUITE_SEEDS=\"17 42 57 …\"`.",
    ]
    return "\n".join(lines) + "\n"


def _require(binary: str) -> None:
    if shutil.which(binary) is None:
        raise SystemExit(f"Required binary not found in PATH: {binary}")


if __name__ == "__main__":
    raise SystemExit(main())
