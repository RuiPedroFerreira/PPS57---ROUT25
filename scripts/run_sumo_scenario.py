#!/usr/bin/env python3
"""Generate and optionally run configured SUMO validation scenarios."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
from functools import lru_cache
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
    rematerialize_stochastic_incidents,
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
        "--jobs",
        type=int,
        default=1,
        help=(
            "Worker processes for parallel leaf execution (scenario × arm × seed). "
            "1 = serial (default; behaviour unchanged). 0 = auto = min(cpu, leaves). "
            "Each leaf runs in its own process with a dedicated TraCI port and per-run "
            "output dir; the suite summary / RESULTS.md are still written once by the "
            "parent, so there is no shared-state race."
        ),
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

    for scenario_id in scenario_ids:
        assert scenario_id is not None
    run_summaries = _execute_scenarios(args, base_config, catalog, scenario_ids)

    scenario_report = {
        "scenario_count": len(run_summaries),
        "scenarios": run_summaries,
    }
    reports_dir = ROOT / args.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    # Bugbot: a --generate-only run builds SUMO artifacts WITHOUT running, so it has no
    # real verdicts (scenario_verdict defaults them to "pass"). It must NOT touch the
    # persisted result summaries — neither overwrite the JSON suite (which would clobber
    # real verdicts, including via the `--all` wholesale write) nor make the exit code
    # gate on the stored suite. The dashboard's result data only ever comes from real runs.
    if not args.generate_only:
        summary_path = reports_dir / "scenario_suite_summary.json"
        # B2: a single-scenario run (make scenario-run) must NOT drop the other scenarios
        # already in the suite summary. Merge by scenario_id, overriding only the ones
        # just (re)run. A full `--all` run intentionally replaces everything.
        if not args.all and summary_path.exists():
            try:
                existing = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                # An unreadable/corrupt summary must NOT be silently overwritten with this
                # partial run (that would drop every other stored scenario) — fail closed.
                raise SystemExit(
                    f"{summary_path} corrupto/ilegível ({exc}); recuso-me a sobrescrevê-lo "
                    "com um run parcial e perder os outros cenários. Corrige/apaga o ficheiro "
                    "ou corre `make scenario-suite` (--all)."
                ) from exc
            # Tolerate a valid-JSON-but-malformed structure ("scenarios": null / non-dict
            # root): `.get(..., [])` returns null when the key exists -> TypeError on iter.
            existing_scenarios = existing.get("scenarios") if isinstance(existing, dict) else None
            merged = {
                s.get("scenario_id"): s
                for s in (existing_scenarios if isinstance(existing_scenarios, list) else [])
                if isinstance(s, dict) and s.get("scenario_id")
            }
            for summary in run_summaries:
                merged[summary.get("scenario_id")] = summary
            scenario_report = {"scenario_count": len(merged), "scenarios": list(merged.values())}
        summary_path.write_text(
            json.dumps(scenario_report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (reports_dir / "scenario_suite_report.md").write_text(
            render_suite_report(scenario_report), encoding="utf-8"
        )
        # RESULTS.md é o documento de resultados de topo, gerado a partir dos dados
        # (nunca escrito à mão). Só a suite completa (--all) o reescreve.
        if args.all:
            (ROOT / "RESULTS.md").write_text(render_results_doc(scenario_report), encoding="utf-8")
            print("RESULTS.md regenerado a partir de reports/scenarios/scenario_suite_summary.json")
    print(json.dumps(scenario_report, indent=2, ensure_ascii=False))
    # Propaga o veredito para o exit code a partir da SUITE PERSISTIDA (merged), não
    # só dos cenários desta invocação: senão um `scenario-run` que passa sai 0 mesmo
    # que o suite summary guardado ainda liste outros cenários a falhar (Bugbot) —
    # CI/make engoliam regressões. (--list já retornou acima; --generate-only => pass.)
    not_passing = [
        summary.get("scenario_id", "?")
        for summary in scenario_report["scenarios"]
        if summary.get("verdict", {}).get("status") != "pass"
    ]
    if not_passing:
        print(f"Scenario verdict not 'pass' for: {', '.join(not_passing)}", file=sys.stderr)
        return 1
    return 0


def run_scenario(
    args: argparse.Namespace, base_config: dict, catalog: dict, scenario_id: str
) -> dict:
    """Serial single-scenario run: execute every leaf (run_type × seed) then finalize.

    The parallel path (`--jobs`) reaches the same result via _execute_scenarios; both
    funnel through _scenario_runs_from_leaves + finalize_scenario so the aggregation,
    verdict and reports are byte-for-byte identical regardless of scheduling.
    """
    config, run_types, seeds = _scenario_run_plan(args, base_config, scenario_id)
    leaves: dict[str, dict[int, dict]] = {}
    for run_type in run_types:
        leaves[run_type] = {}
        for seed in seeds:
            leaves[run_type][seed] = run_scenario_type(
                args=args,
                base_config=config,
                catalog=catalog,
                scenario_id=scenario_id,
                run_type=run_type,
                seed=seed,
            )
    scenario_runs = _scenario_runs_from_leaves(run_types, seeds, leaves)
    return finalize_scenario(args, config, catalog, scenario_id, scenario_runs, seeds)


def _scenario_run_plan(
    args: argparse.Namespace, base_config: dict, scenario_id: str
) -> tuple[dict, list[str], list[int]]:
    """Resolve the (config, run_types, seeds) plan for a scenario and create its dirs.

    Cheap and side-effect-light (apply profile, horizon guard, mkdir), so it is safe
    to call up front for every scenario before fanning the heavy leaf runs out.
    """
    config = apply_scenario_profile(base_config, scenario_id)
    _assert_horizon_not_truncated(config, args, scenario_id)
    (ROOT / args.outputs_dir / scenario_id).mkdir(parents=True, exist_ok=True)
    (ROOT / args.reports_dir / scenario_id).mkdir(parents=True, exist_ok=True)
    # pair/comparison/all são aliases retidos para compatibilidade (Makefile/README):
    # após a consolidação para dois modos, todos resolvem para baseline + tsp_actuation.
    if args.run_type in {"pair", "comparison", "all"}:
        run_types = ["baseline", "tsp_actuation"]
    else:
        run_types = [args.run_type]
    seeds = _resolve_seeds(args, config)
    return config, run_types, seeds


def _scenario_runs_from_leaves(
    run_types: list[str], seeds: list[int], leaves: dict[str, dict[int, dict]]
) -> dict[str, dict]:
    """Assemble per-run_type summaries from individual leaf results.

    Single seed -> the leaf summary as-is; multiple seeds -> the replication
    aggregate. Seeds are consumed in the requested order so the "first" replication
    (the one _aggregate_replications copies headline fields from) is identical whether
    the leaves ran serially or in parallel.
    """
    scenario_runs: dict[str, dict] = {}
    for run_type in run_types:
        per_seed = [leaves[run_type][seed] for seed in seeds]
        scenario_runs[run_type] = (
            per_seed[0] if len(per_seed) == 1 else _aggregate_replications(per_seed)
        )
    return scenario_runs


def finalize_scenario(
    args: argparse.Namespace,
    config: dict,
    catalog: dict,
    scenario_id: str,
    scenario_runs: dict[str, dict],
    seeds: list[int],
) -> dict:
    """Post-leaf aggregation: insertion gate, paired comparisons, verdict, reports.

    Pure bookkeeping over the kpis.json files the leaves already wrote (no SUMO), so
    it runs in the parent process once all leaves — however they were scheduled — have
    finished. Single source of truth for the per-scenario summary/report.
    """
    scenario_output_dir = ROOT / args.outputs_dir / scenario_id
    scenario_report_dir = ROOT / args.reports_dir / scenario_id
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
    summary["status"] = "generated" if args.generate_only else "completed"
    return summary


# Porta base TraCI para a execução paralela: cada leaf (scenario×arm×seed) recebe uma
# porta dedicada e DISTINTA (base + índice) em vez de sondar uma porta livre, eliminando
# a janela TOCTOU do getFreeSocketPort quando dezenas de SUMO arrancam em simultâneo.
# Overridable por env para ambientes onde a gama 8900+ esteja ocupada.
_TRACI_PORT_BASE = int(os.environ.get("PPS57_TRACI_PORT_BASE", "8900"))


def _resolve_jobs(requested: int | None, total_leaves: int) -> int:
    """Number of worker processes for leaf execution.

    1 (default) keeps the original serial behaviour. 0/negative => auto:
    min(cpu_count, total_leaves). Never more workers than there are leaves.
    """
    if total_leaves <= 0:
        return 1
    if requested is None or requested <= 0:
        requested = min(os.cpu_count() or 1, total_leaves)
    return max(1, min(requested, total_leaves))


# Quantas tentativas por leaf antes de desistir. Sob alta concorrência o arranque do
# SUMO/TraCI falha ocasionalmente de forma transitória ("Connection closed by SUMO"),
# e numa suite de centenas de leaves uma única falha não deve desperdiçar a run inteira.
_LEAF_MAX_ATTEMPTS = int(os.environ.get("PPS57_LEAF_MAX_ATTEMPTS", "3"))


def _run_leaf_task(
    args: argparse.Namespace,
    config: dict,
    catalog: dict,
    scenario_id: str,
    run_type: str,
    seed: int,
    port: int,
) -> dict:
    """Worker entry point: pin a dedicated TraCI port, run one leaf, return its summary.

    Retries transient SUMO/TraCI startup failures (e.g. "Connection closed by SUMO"
    under heavy concurrency) on a fresh, disjoint port with backoff before giving up,
    so one flaky leaf does not abort a multi-hundred-leaf suite. Runs in a fresh child
    per task (max_tasks_per_child=1) so no TraCI state leaks between leaves.
    """
    import time

    last_exc: Exception | None = None
    for attempt in range(_LEAF_MAX_ATTEMPTS):
        # Disjoint port per attempt so a half-open socket from a failed start can't
        # poison the retry (+20000 stays well inside the TCP range for any leaf index).
        os.environ["TRACI_PORT"] = str(port + attempt * 20000)
        try:
            return run_scenario_type(
                args=args,
                base_config=config,
                catalog=catalog,
                scenario_id=scenario_id,
                run_type=run_type,
                seed=seed,
            )
        except Exception as exc:  # noqa: BLE001 - transient TraCI/SUMO startup flakiness
            last_exc = exc
            # A failed start can leave traci's module-global 'default' connection
            # half-open (the controller starts the adapter outside its try/finally),
            # so without resetting it the next attempt dies with
            # "Connection 'default' is already active." Close it best-effort so the
            # retry — on a fresh port — starts from a clean client state.
            try:
                import traci

                if traci.isLoaded():
                    traci.close()
            except Exception:  # noqa: BLE001 - cleanup must never mask the real error
                pass
            if attempt + 1 < _LEAF_MAX_ATTEMPTS:
                time.sleep(2.0 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def _execute_scenarios(
    args: argparse.Namespace, base_config: dict, catalog: dict, scenario_ids: list[str]
) -> list[dict]:
    """Run every scenario, fanning the leaf SUMO runs across worker processes.

    Each (scenario, run_type, seed) SUMO run is fully independent — per-run output
    dirs (the sumocfg writes tripinfo/summary/statistics locally) plus a dedicated
    TraCI port — so the expensive leaves run in a process pool. The cheap aggregation
    (insertion gate, paired CI, verdict, per-scenario reports) then runs serially in
    the parent, and main() writes the suite summary / RESULTS.md exactly once, so there
    is no shared-state race. --jobs 1 (default) preserves the original serial path.
    """
    plans = {sid: _scenario_run_plan(args, base_config, sid) for sid in scenario_ids}
    total_leaves = sum(len(run_types) * len(seeds) for _c, run_types, seeds in plans.values())
    jobs = _resolve_jobs(getattr(args, "jobs", 1), total_leaves)

    # generate-only builds artifacts without running SUMO; keep it serial — the pool
    # would add latency without any real per-leaf work to overlap.
    if jobs <= 1 or args.generate_only:
        return [run_scenario(args, base_config, catalog, sid) for sid in scenario_ids]

    tasks: list[tuple[str, str, int, dict]] = []
    for sid in scenario_ids:
        config, run_types, seeds = plans[sid]
        for run_type in run_types:
            for seed in seeds:
                tasks.append((sid, run_type, seed, config))

    if _TRACI_PORT_BASE + len(tasks) > 65000:
        raise SystemExit(
            f"Parallel run needs {len(tasks)} distinct TraCI ports from base "
            f"{_TRACI_PORT_BASE}, which exceeds the TCP range. Lower PPS57_TRACI_PORT_BASE."
        )

    print(
        f"[parallel] {len(tasks)} leaves (scenario×arm×seed) across {jobs} workers "
        f"(cpu={os.cpu_count()}); TraCI ports {_TRACI_PORT_BASE}..{_TRACI_PORT_BASE + len(tasks) - 1}",
        file=sys.stderr,
    )

    leaves: dict[str, dict[str, dict[int, dict]]] = {}
    # max_tasks_per_child=1: a fresh process per leaf, so TraCI/SUMO socket state never
    # carries over between runs in a reused worker.
    with ProcessPoolExecutor(max_workers=jobs, max_tasks_per_child=1) as executor:
        future_to_leaf = {
            executor.submit(
                _run_leaf_task, args, config, catalog, sid, run_type, seed, _TRACI_PORT_BASE + idx
            ): (sid, run_type, seed)
            for idx, (sid, run_type, seed, config) in enumerate(tasks)
        }
        failed_leaves: list[tuple[str, str, int, str]] = []
        for done, future in enumerate(as_completed(future_to_leaf), start=1):
            sid, run_type, seed = future_to_leaf[future]
            try:
                summary = future.result()
            except Exception as exc:  # noqa: BLE001 - one flaky leaf must not nuke the suite
                # Already retried _LEAF_MAX_ATTEMPTS times in the worker; record and drop
                # this leaf rather than aborting the whole (possibly hours-long) run.
                failed_leaves.append((sid, run_type, seed, repr(exc)))
                print(
                    f"[parallel] {done}/{len(tasks)} {sid}/{run_type}/seed_{seed} "
                    f"-> FAILED after {_LEAF_MAX_ATTEMPTS} attempts: {exc!r}",
                    file=sys.stderr,
                )
                continue
            leaves.setdefault(sid, {}).setdefault(run_type, {})[seed] = summary
            status = summary.get("run_verdict", {}).get("status", summary.get("status"))
            print(
                f"[parallel] {done}/{len(tasks)} {sid}/{run_type}/seed_{seed} -> {status}",
                file=sys.stderr,
            )
        if failed_leaves:
            print(
                f"[parallel] WARNING: {len(failed_leaves)} leaf(s) failed after retries: "
                + ", ".join(f"{s}/{r}/seed_{sd}" for s, r, sd, _ in failed_leaves),
                file=sys.stderr,
            )

    run_summaries = []
    for sid in scenario_ids:
        config, run_types, seeds = plans[sid]
        # Pairing needs the seed in EVERY arm; a leaf dropped after retry-exhaustion
        # removes just that seed from that scenario (reduced n, recorded in the summary),
        # instead of aborting the suite or silently unbalancing the paired comparison.
        effective_seeds = [
            seed
            for seed in seeds
            if all(seed in leaves.get(sid, {}).get(rt, {}) for rt in run_types)
        ]
        if not effective_seeds:
            print(
                f"[parallel] WARNING: scenario {sid} has no seed complete across all arms; "
                "skipped from the suite.",
                file=sys.stderr,
            )
            continue
        if len(effective_seeds) < len(seeds):
            dropped = sorted(set(seeds) - set(effective_seeds))
            print(
                f"[parallel] WARNING: scenario {sid} reduced to n={len(effective_seeds)} "
                f"(dropped seeds {dropped} after leaf failure).",
                file=sys.stderr,
            )
        scenario_runs = _scenario_runs_from_leaves(run_types, effective_seeds, leaves[sid])
        run_summaries.append(
            finalize_scenario(args, config, catalog, sid, scenario_runs, effective_seeds)
        )
    return run_summaries


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
                    if reasons:
                        status, out_reasons = "fail", reasons
                    else:
                        # Bugbot: removing the last hard reason must not bury an
                        # inconclusive verdict (e.g. safety telemetry unavailable, B4)
                        # as a pass — re-derive the inconclusive list from the KPIs.
                        _, inconclusive = _verdict_reason_lists(kpis)
                        status = "inconclusive" if inconclusive else "pass"
                        out_reasons = inconclusive if inconclusive else []
                    rep["run_verdict"] = {"status": status, "reasons": out_reasons}
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
    loaded = [s for s in summaries if s]
    if loaded:
        aggregate["kpi_aggregate"] = _compute_kpi_aggregate(loaded)
    # B8: surface how many seeds actually fed the aggregate vs how many were
    # expected, so a silently-dropped (missing/unreadable) kpis.json is visible
    # instead of the mean/CI quietly shrinking to the loaded subset.
    aggregate["kpi_aggregate_n"] = len(loaded)
    aggregate["kpi_aggregate_dropped"] = len(kpi_paths) - len(loaded)
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
            out.update({"stdev": None, "p5": None, "p95": None, "min": None, "max": None})
            return out
        sorted_v = sorted(values)
        if len(sorted_v) >= 2:
            # B10: interpolated percentiles (statistics.quantiles) instead of a
            # nearest-rank index that collapsed p5/p95 to min/max for small n.
            cuts = statistics.quantiles(sorted_v, n=100, method="inclusive")
            p5 = round(cuts[4], 3)
            p95 = round(cuts[94], 3)
        else:
            p5 = p95 = round(sorted_v[0], 3)
        # B9: `stdev` now uses the SAMPLE stdev (statistics.stdev), consistent with
        # the sample-based ci95_* (was populacional pstdev — a different basis in
        # the same aggregate, so a consumer re-deriving a CI from it disagreed).
        out.update(
            {
                "stdev": round(statistics.stdev(values), 3) if len(values) > 1 else 0.0,
                "p5": p5,
                "p95": p95,
                # min/max across seeds — worst-case gates (e.g. max queue) must use the
                # worst seed, not the mean, so a single >threshold seed isn't averaged away.
                "min": round(sorted_v[0], 3),
                "max": round(sorted_v[-1], 3),
            }
        )
        return out

    return {
        "bus_mean_time_loss_s": stat(collect(["buses", "mean_time_loss_s"])),
        "general_mean_time_loss_s": stat(collect(["general_traffic", "mean_time_loss_s"])),
        "all_vehicles_mean_duration_s": stat(collect(["all_vehicles", "mean_duration_s"])),
        # Bugbot: aggregate the completed-vehicle/bus COUNTS too, so the report row
        # doesn't mix first-seed counts with across-seed aggregated metrics.
        "all_vehicles_count": stat(collect(["all_vehicles", "vehicles"])),
        "buses_count": stat(collect(["buses", "vehicles"])),
        "max_network_queue_vehicles": stat(
            collect(["detectors", "network_queue", "max_queue_vehicles"])
        ),
        "total_co2_mg": stat(collect(["emissions", "totals_mg", "CO2"])),
        "total_fuel_mg": stat(collect(["emissions", "totals_mg", "fuel"])),
        # Métricas-foco por cenário, agregadas pela MESMA máquina (média/IC95/p5-p95/min-max),
        # mantendo o pipeline uniforme. Ficam com mean=None onde o grupo não existe:
        # emergency_* só popula no emergency_vehicle_conflict; buses_*bound só onde há
        # autocarros nesse sentido. O destaque por-cenário é depois feito no display.
        "emergency_mean_time_loss_s": stat(collect(["emergency_vehicles", "mean_time_loss_s"])),
        "emergency_mean_duration_s": stat(collect(["emergency_vehicles", "mean_duration_s"])),
        "bus_westbound_mean_time_loss_s": stat(collect(["buses_westbound", "mean_time_loss_s"])),
        "bus_eastbound_mean_time_loss_s": stat(collect(["buses_eastbound", "mean_time_loss_s"])),
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
    # B40: re-draw stochastic incidents for THIS replication's seed (the base config
    # carries the base-seed draw; without this every seed shares the same incidents).
    rematerialize_stochastic_incidents(config)
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


def _assert_horizon_not_truncated(config: dict, args: argparse.Namespace, scenario_id: str) -> None:
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
    # The PER-SCENARIO sumocfg (write_sumocfg) declares tripinfo/summary/statistics
    # under each run's OWN output_dir, so a scenario leaf never writes to this legacy
    # global outputs/ path — which is exactly why --jobs can run leaves in parallel
    # safely (each owns a private output dir + TraCI port). These unlinks only clear a
    # stale global dump left by the non-scenario flows (make run/tsp-sumo); use
    # missing_ok=True so concurrent leaves don't race on a TOCTOU unlink.
    for name in GLOBAL_SUMO_OUTPUTS:
        (ROOT / "outputs" / name).unlink(missing_ok=True)


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


def _mean_kpis_for_compare(run: dict) -> dict | None:
    """Build a KPI dict from the multi-seed means (kpi_aggregate) for compare_kpis.

    B5: the point-delta comparison and the absolute gates must use the mean KPIs
    across seeds — not the first seed's kpis.json — so they agree with the paired
    CI95 instead of riding on a single replication. Returns None when there is no
    aggregate (caller falls back to the first-seed kpis.json).
    """
    aggregate = run.get("kpi_aggregate")
    if not isinstance(aggregate, dict):
        return None

    def _stat(key: str, field: str) -> float | None:
        entry = aggregate.get(key)
        return entry.get(field) if isinstance(entry, dict) else None

    return {
        "buses": {"mean_time_loss_s": _stat("bus_mean_time_loss_s", "mean")},
        "general_traffic": {"mean_time_loss_s": _stat("general_mean_time_loss_s", "mean")},
        "detectors": {
            # Bugbot: the network_queue_gt_30 gate is a worst-case check — use the MAX
            # across seeds, not the mean, so one seed's >30 peak isn't averaged away.
            "network_queue": {"max_queue_vehicles": _stat("max_network_queue_vehicles", "max")}
        },
    }


# Métricas-foco por cenário: chave de significância → (rótulo PT, grupo no kpis.json,
# métrica). Fonte única de verdade para o produtor (compare_scenario_runs) e para os
# relatórios (render_scenario_report / render_results_doc); acrescentar uma métrica aqui
# propaga-a a todos os sítios sem editar listas paralelas.
FOCUS_SIG_METRICS = [
    (
        "emergency_time_loss_replication_significance",
        "Emergência · timeLoss",
        "emergency_vehicles",
        "mean_time_loss_s",
    ),
    (
        "bus_westbound_time_loss_replication_significance",
        "Autocarro westbound · timeLoss",
        "buses_westbound",
        "mean_time_loss_s",
    ),
    (
        "bus_eastbound_time_loss_replication_significance",
        "Autocarro eastbound · timeLoss",
        "buses_eastbound",
        "mean_time_loss_s",
    ),
]


def compare_scenario_runs(runs: dict[str, dict]) -> dict:
    baseline_run = runs.get("baseline", {})
    baseline = _mean_kpis_for_compare(baseline_run) or _load_kpis(baseline_run.get("kpis"))
    comparisons: dict[str, dict] = {}
    for run_type in ("tsp_actuation",):
        candidate_run = runs.get(run_type, {})
        candidate = _mean_kpis_for_compare(candidate_run) or _load_kpis(candidate_run.get("kpis"))
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
        # Métricas-foco: significância emparelhada onde o grupo existe. _paired_significance
        # devolve None (e é ignorada) sem >=2 seeds com valor numérico — i.e. cenários sem
        # emergência, ou sem autocarros num dado sentido. Mesma régua para todos os cenários.
        focus_significance = {
            key: (group, metric) for key, _label, group, metric in FOCUS_SIG_METRICS
        }
        for sig_key, (sig_group, sig_metric) in focus_significance.items():
            sig = _paired_significance(
                baseline_run, candidate_run, sig_group, sig_metric, lower_is_better=True
            )
            if sig is not None:
                comparison[sig_key] = sig
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
    regression_pct = round((regression / base_value) * 100, 3) if base_value != 0 else None
    return {
        "baseline": base_value,
        "candidate": candidate_value,
        "delta": delta,
        "regression_pct": regression_pct,
    }


def _verdict_reason_lists(kpis: dict) -> tuple[list[str], list[str]]:
    """Build (hard reasons, inconclusive reasons) for a run's KPIs.

    Shared by run_verdict and apply_relative_insertion_gate so that relaxing the
    insertion reason can still surface an inconclusive verdict (e.g. missing safety
    telemetry, B4) rather than collapsing to a bogus pass.
    """
    reasons: list[str] = []
    inconclusive: list[str] = []
    thresholds = _sumo_quality_thresholds(kpis)
    insertion = kpis.get("insertion", {})

    # Fail-closed safety gate (B4): the collision / teleport / emergency-braking /
    # vehicles-waiting counters all come from statistics.xml. Keying on file
    # existence alone is not enough — a present-but-empty statistics.xml (aborted or
    # short TraCI run) parses cleanly yet carries none of those counters, and the
    # gates below would then read them as 0 via `or 0`, passing every safety gate
    # with no evidence. parse_insertion sets safety_statistics_complete only when the
    # <vehicles>/<teleports>/<safety> blocks were all actually read, so gate on that.
    # Mark the run inconclusive when telemetry is incomplete; a real hard failure
    # elsewhere still dominates (inconclusive is only returned when no reasons fire).
    if not insertion.get("safety_statistics_complete"):
        inconclusive.append("sumo_safety_statistics_unavailable")

    # Bugbot: the insertion gates below (max_waiting_to_insert / insertion_gap_at_end /
    # backlog ratio) all come from summary.xml. After B18 a corrupt summary sets
    # parse_error and leaves those counters unset, so they read 0 via `or 0` and the
    # gates pass with no evidence. Mark inconclusive instead of silently passing.
    if insertion.get("parse_error"):
        inconclusive.append("sumo_insertion_summary_unavailable")

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
    return reasons, inconclusive


def run_verdict(kpis: dict) -> dict:
    if kpis.get("missing_tripinfo"):
        return {"status": "fail", "reasons": ["missing_tripinfo"]}
    # B12: a tripinfo that exists but failed to parse yields a mutilated dict (no
    # all_vehicles block). Fail with the right reason instead of the misleading
    # "no_completed_vehicles" the empty block would otherwise trigger.
    if kpis.get("tripinfo_parse_error"):
        return {"status": "fail", "reasons": ["tripinfo_parse_error"]}
    reasons, inconclusive = _verdict_reason_lists(kpis)
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


@lru_cache(maxsize=512)
def _load_kpis_cached(path_str: str, mtime: float) -> dict | None:
    # kpis.json is written once per run then read many times (paired significance
    # ×2, the point comparison, and the scenario/suite/RESULTS reports). Caching on
    # (path, mtime) collapses ~10 reads+parses of each file into one, while the
    # mtime key stays correct if a file is regenerated within the same process.
    # Bounded (vs functools.cache) so long-lived processes don't grow without limit;
    # 512 comfortably covers a full suite's scenarios×seeds×run_types kpis.json set.
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
        "| Run | Seeds | Status | Vehicles | Buses | Bus timeLoss | General timeLoss | Max queue | Total CO2 (mg) | Total fuel (mg) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for run_type, run in summary.get("runs", {}).items():
        kpis = _load_kpis(run.get("kpis")) or {}
        aggregate = run.get("kpi_aggregate") if isinstance(run.get("kpi_aggregate"), dict) else {}

        # B6: when there are multiple seeds, show the across-seed MEAN (kpi_aggregate)
        # rather than the first seed's value unsignalled; the Seeds column makes the
        # replication count explicit. Falls back to the first-seed value otherwise.
        # aggregate=aggregate liga a variável de loop como default (avaliado no def, por
        # iteração) — _val é chamado dentro da própria iteração, mas o bind explícito
        # satisfaz o B023 e blinda contra uso diferido futuro.
        def _val(
            agg_key: str,
            fallback: Any,
            field: str = "mean",
            as_int: bool = False,
            aggregate: dict = aggregate,
        ) -> Any:
            entry = aggregate.get(agg_key)
            if isinstance(entry, dict) and entry.get(field) is not None:
                value = entry[field]
                # Counts are integers — render the across-seed mean rounded to a whole
                # number instead of an odd-looking fractional count (e.g. 101.333).
                return int(round(value)) if as_int else round(value, 3)
            return fallback

        emissions_totals = (
            kpis.get("emissions", {}).get("totals_mg", {})
            if isinstance(kpis.get("emissions"), dict)
            else {}
        )
        lines.append(
            "| {run} | {seeds} | {status} | {veh} | {bus} | {bus_loss} | {gen_loss} | {queue} | {co2} | {fuel} |".format(
                run=run_type,
                seeds=run.get("replication_count", 1),
                status=run.get("run_verdict", {}).get("status", run.get("status")),
                veh=_val(
                    "all_vehicles_count",
                    kpis.get("all_vehicles", {}).get("vehicles", ""),
                    as_int=True,
                ),
                bus=_val("buses_count", kpis.get("buses", {}).get("vehicles", ""), as_int=True),
                bus_loss=_val(
                    "bus_mean_time_loss_s", kpis.get("buses", {}).get("mean_time_loss_s", "")
                ),
                gen_loss=_val(
                    "general_mean_time_loss_s",
                    kpis.get("general_traffic", {}).get("mean_time_loss_s", ""),
                ),
                queue=_val(
                    "max_network_queue_vehicles",
                    kpis.get("detectors", {})
                    .get("network_queue", {})
                    .get("max_queue_vehicles", ""),
                    field="max",  # Bugbot: report the worst seed, consistent with the gate
                ),
                co2=_val("total_co2_mg", emissions_totals.get("CO2", "")),
                fuel=_val("total_fuel_mg", emissions_totals.get("fuel", "")),
            )
        )

    significance_rows = [
        (key, comparison[sig_key])
        for key, comparison in summary.get("comparisons", {}).items()
        if isinstance(comparison, dict)
        for sig_key in (
            "bus_time_loss_replication_significance",
            "general_traffic_time_loss_replication_significance",
            *(key for key, *_ in FOCUS_SIG_METRICS),
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

## Veredito de viabilidade vs. estimativa de efeito

Os gates de viabilidade (`max_collisions=0`, `max_teleports_jam=0`,
`max_waiting_to_insert`, …) são **fail-closed estritos**. Numa amostragem larga
(muitas seeds) dos cenários mais carregados, podem assinalar **eventos de cauda
raros por-seed** — uma colisão de seguimento denso ou um teleport de gridlock numa
seed isolada — que são reportados de forma **transparente** como `fail` de
viabilidade, em vez de mascarados. Esses são micro-eventos ao nível da simulação
numa seed específica e **não** invalidam o IC95 emparelhado do **efeito** do TSP
nesse cenário (a estimativa de efeito é a diferença baseline↔atuação na mesma
seed; um artefacto que ocorre numa seed afeta ambos os braços). Ler as duas
tabelas em conjunto: a de **impacto** mede o efeito; a de **viabilidade** sinaliza
seeds com micro-eventos a inspecionar, não uma falha do efeito medido.
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
            lines.append(
                f"| {s['scenario_id']} | {len(s.get('seeds', []))} | pendente | — | — | — |"
            )
            continue
        sig = cmp.get("bus_time_loss_replication_significance")
        point = cmp.get("bus_time_loss", {})
        # B7: show the real seed count instead of a hard-coded "1" when several
        # seeds ran but no paired CI was computed; the verdict states so explicitly.
        seed_count = len(s.get("seeds", [])) or 1
        n = sig.get("n") if sig else seed_count
        mean_imp = f"{sig['mean_improvement']:+.1f}" if sig else "—"
        verdict = (
            sig.get("verdict", "single_seed_no_ci")
            if sig
            else ("no_paired_ci" if seed_count > 1 else "single_seed_no_ci")
        )
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
        seed_count = len(s.get("seeds", [])) or 1
        mean_imp = f"{sig['mean_improvement']:+.1f}" if sig else "—"
        verdict = (
            sig.get("verdict", "single_seed_no_ci")
            if sig
            else ("no_paired_ci" if seed_count > 1 else "single_seed_no_ci")
        )
        lines.append(f"| {s['scenario_id']} | {mean_imp} | {_ci_cell(sig)} | {verdict} |")

    focus_rows = [
        (s["scenario_id"], label, sig)
        for s in scenarios
        for key_name, label, *_ in FOCUS_SIG_METRICS
        for sig in [s.get("comparisons", {}).get("baseline_vs_tsp_actuation", {}).get(key_name)]
        if sig is not None
    ]
    if focus_rows:
        lines += [
            "",
            "## Métricas-foco por cenário (IC95 emparelhado)",
            "",
            "Métricas específicas do cenário: veículo de emergência ou autocarro direcional.",
            "",
            "| Cenário | Métrica | n | Melhoria média (s) | IC95 (s) | Veredito |",
            "|---|---|---:|---:|---:|---|",
        ]
        for scen_id, label, sig in focus_rows:
            mean_imp = sig.get("mean_improvement")
            mean_cell = f"{mean_imp:+.1f}" if isinstance(mean_imp, (int, float)) else "—"
            lines.append(
                f"| {scen_id} | {label} | {sig.get('n', '')} | "
                f"{mean_cell} | {_ci_cell(sig)} | {sig.get('verdict', '')} |"
            )

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
        '`configs/sumo_scenario_base.json` ou corre `make scenario-suite SUITE_SEEDS="17 42 57 …"`.',
    ]
    return "\n".join(lines) + "\n"


def _require(binary: str) -> None:
    if shutil.which(binary) is None:
        raise SystemExit(f"Required binary not found in PATH: {binary}")


if __name__ == "__main__":
    raise SystemExit(main())
