"""Result discovery helpers shared by the dashboard and tests."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

DATASET_SYNTHETIC = "synthetic"

# Single headline dataset: the synthetic Boavista corridor (e1/e2 detectors and
# GTFS-derived bus headways). The earlier city-wide scenario pipeline was removed,
# so the synthetic corridor is the only scenario dataset. DATASET_ENV_VAR is kept for
# backward compatibility but no longer selects an alternative.
DATASET_ENV_VAR = "PPS57_DASHBOARD_DATASET"
PREFERRED_DATASET = DATASET_SYNTHETIC


def discover_scenario_report_roots(reports_root: Path) -> dict[str, Path]:
    """Return available scenario result roots (the synthetic corridor)."""
    roots: dict[str, Path] = {}
    synthetic = reports_root / "scenarios"
    if _has_scenario_reports(synthetic):
        roots[DATASET_SYNTHETIC] = synthetic
    return roots


def _has_scenario_reports(report_root: Path) -> bool:
    if not report_root.exists():
        return False
    # B29: a bare scenario_suite_summary.json (no per-seed kpis.json) is an empty
    # dataset — requiring at least one kpis.json stops the dashboard pinning a blank
    # root (e.g. after a partial run that wrote only the summary, cf. B2).
    return any(report_root.glob("*/*/seed_*/kpis.json"))


def default_scenario_dataset(reports_root: Path) -> str:
    """Headline dataset the dashboard defaults to (the synthetic corridor)."""
    return DATASET_SYNTHETIC


def scenario_catalog_path(root: Path, dataset: str = DATASET_SYNTHETIC) -> Path:
    return root / "configs" / "scenario_catalog.yaml"


def load_scenario_kpi_rows(
    report_root: Path, vehicle_cls: str, kpi_meta: dict[str, Any]
) -> list[dict]:
    """Load per-scenario/run/seed KPI rows from a scenario report root."""
    rows: list[dict] = []

    if not report_root.exists():
        return rows

    emission_supported = vehicle_cls in {"all_vehicles", "buses"}

    def _to_float(val: Any) -> float | None:
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    for scenario_dir in sorted(path for path in report_root.iterdir() if path.is_dir()):
        for run_dir in sorted(path for path in scenario_dir.iterdir() if path.is_dir()):
            for seed_dir in sorted(path for path in run_dir.iterdir() if path.is_dir()):
                kpi_path = seed_dir / "kpis.json"
                if not kpi_path.exists():
                    continue
                try:
                    kpis = json.loads(kpi_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                data = kpis.get(vehicle_cls, {}) if vehicle_cls else kpis
                if not isinstance(data, dict):
                    continue

                # emissions are stored at the root level in each kpis.json file.
                # For this dashboard we expose total + normalized CO2/fuel
                # metrics, but only for classes where these are interpretable
                # (all vehicles or buses with bus-specific breakdowns).
                if emission_supported:
                    emissions = kpis.get("emissions")
                    if isinstance(emissions, dict):
                        totals = emissions.get("totals_mg")
                        if not isinstance(totals, dict):
                            totals = None

                        if vehicle_cls == "buses":
                            bus_totals = emissions.get("bus_totals_mg")
                            if isinstance(bus_totals, dict):
                                totals = bus_totals
                            bus_count = emissions.get("bus_count")
                        else:
                            bus_count = None

                        # Loop-derived names bound as keyword-only defaults so the
                        # closure captures the current iteration (avoids late
                        # binding); callers only ever pass metric_key + value.
                        def _append(
                            metric_key: str,
                            value: Any,
                            *,
                            _scen: str = scenario_dir.name,
                            _run: str = run_dir.name,
                            _seed: str = seed_dir.name,
                        ) -> None:
                            if value is None:
                                return
                            value_f = _to_float(value)
                            if value_f is None:
                                return
                            rows.append(
                                {
                                    "Cenário": _scen,
                                    "Run type": _run,
                                    "Seed": _seed,
                                    "metric_key": metric_key,
                                    "Métrica": kpi_meta.get(metric_key, (metric_key, "", ""))[0],
                                    "Valor": value_f,
                                }
                            )

                        if isinstance(totals, dict):
                            co2_total = totals.get("CO2")
                            fuel_total = totals.get("fuel")
                            _append("total_co2_mg", co2_total)
                            _append("total_fuel_mg", fuel_total)

                            vehicle_count = data.get("vehicles")
                            if vehicle_cls == "buses" and bus_count is not None:
                                vehicle_count = bus_count

                            vehicle_count_f = _to_float(vehicle_count)
                            if vehicle_count_f and vehicle_count_f > 0:
                                if co2_total is not None:
                                    co2_f = _to_float(co2_total)
                                    if co2_f is not None:
                                        _append("total_co2_mg_per_vehicle", co2_f / vehicle_count_f)

                                if fuel_total is not None:
                                    fuel_f = _to_float(fuel_total)
                                    if fuel_f is not None:
                                        _append("total_fuel_mg_per_vehicle", fuel_f / vehicle_count_f)

                            # B27: normalise per-vehicle-km against the SUM of route
                            # lengths (total_route_length_m), not mean_route_length_m ×
                            # vehicles, which only matches when all routes are equal.
                            total_route_m = _to_float(data.get("total_route_length_m"))
                            if total_route_m and total_route_m > 0:
                                dist_km = total_route_m / 1000
                                if co2_total is not None:
                                    co2_f = _to_float(co2_total)
                                    if co2_f is not None:
                                        _append("total_co2_mg_per_vehicle_km", co2_f / dist_km)
                                if fuel_total is not None:
                                    fuel_f = _to_float(fuel_total)
                                    if fuel_f is not None:
                                        _append("total_fuel_mg_per_vehicle_km", fuel_f / dist_km)

                for metric_key, meta in kpi_meta.items():
                    value = data.get(metric_key)
                    if value is None:
                        continue
                    # B28: coerce to float like the emissions branch and
                    # load_scenario_run_table do, so "Valor" never mixes str/int/float
                    # dtypes (which breaks pandas/plotly aggregations downstream).
                    value_f = _to_float(value)
                    if value_f is None:
                        continue
                    label = meta[0] if isinstance(meta, (tuple, list)) and meta else metric_key
                    rows.append(
                        {
                            "Cenário": scenario_dir.name,
                            "Run type": run_dir.name,
                            "Seed": seed_dir.name,
                            "metric_key": metric_key,
                            "Métrica": label,
                            "Valor": value_f,
                        }
                    )
    return rows


def catalog_label_map(catalog: dict[str, Any]) -> dict[str, str]:
    scenarios = catalog.get("scenarios") if isinstance(catalog, dict) else {}
    if not isinstance(scenarios, dict):
        return {}
    return {
        scenario_id: str(entry.get("description", scenario_id))
        for scenario_id, entry in scenarios.items()
        if isinstance(entry, dict)
    }


# ── rich per-run table ──────────────────────────────────────────────────────────
# `load_scenario_kpi_rows` above serves the legacy single-class views: it filters to
# one vehicle class and the KPI_META metric set. The richer KPIs tab instead needs
# to juxtapose bus vs general traffic, queues, safety counters and air-quality
# pollutants from the SAME run, so it reads the whole kpis.json once per run via the
# loader below. Every value traces straight back to a field in kpis.json — the only
# arithmetic is documented normalisation (per-vehicle-km, kept identical to the
# legacy loader) and headway amplitude (= max − min of the per-line headways).

# Vehicle-class blocks whose scalar metrics we surface, keyed by the kpis.json block.
CLASS_SCOPES = (
    "all_vehicles",
    "buses",
    # Split por sentido (parse_tripinfo): permite destacar o foco direcional (ex.
    # delayed_bus_westbound) que a média dos dois sentidos do grupo `buses` dilui.
    "buses_westbound",
    "buses_eastbound",
    "general_traffic",
    "priority_vehicles",
    "emergency_vehicles",
)
CLASS_METRICS = (
    "vehicles",
    "mean_time_loss_s",
    "mean_waiting_time_s",
    "mean_duration_s",
    "mean_depart_delay_s",
    "mean_speed_mps",
    "mean_stop_count",
    "p95_time_loss_s",
    "p95_duration_s",
    "mean_route_length_m",
)
NETWORK_METRICS = (
    "max_queue_vehicles",
    "mean_queue_vehicles",
    "mean_occupancy_pct",
    # Sum of edge-intervals with a ≥8-veh queue (scales with network size). Renamed
    # from the ambiguous `intervals_above_8_veh`; legacy reports are mapped below.
    "edge_intervals_above_8_veh",
)
SAFETY_METRICS = (
    "teleports_total",
    "teleports_jam",
    "collisions",
    "emergency_braking",
    "max_waiting_to_insert",
    "final_waiting",
    "backlog_step_count",
)
# Order = display priority. CO2/fuel are the headline pair; NOx/PMx are the urban
# air-quality pollutants; CO/HC are kept for completeness.
EMISSION_SPECIES = ("CO2", "fuel", "NOx", "PMx", "CO", "HC")


def load_scenario_focus_significance(report_root: Path) -> dict[str, dict]:
    """Map ``scenario_id -> baseline_vs_tsp_actuation`` comparison dict from the suite
    summary — the paired-significance blocks (bus / general / emergency / directional).

    Empty when the summary is absent or malformed, so callers degrade gracefully to the
    point delta without a CI verdict. Source: ``scenario_suite_summary.json`` (the one
    file that already carries the per-scenario ``comparisons``).
    """
    summary_path = report_root / "scenario_suite_summary.json"
    if not summary_path.exists():
        return {}
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    scenarios = data.get("scenarios") if isinstance(data, dict) else None
    out: dict[str, dict] = {}
    if isinstance(scenarios, list):
        for entry in scenarios:
            if not isinstance(entry, dict) or not entry.get("scenario_id"):
                continue
            comps = entry.get("comparisons")
            pair = comps.get("baseline_vs_tsp_actuation") if isinstance(comps, dict) else None
            if isinstance(pair, dict):
                out[str(entry["scenario_id"])] = pair
    return out


def load_scenario_run_table(report_root: Path) -> list[dict]:
    """Return tidy per-scenario/run/seed KPI rows across *all* scopes.

    Each row is ``{Cenário, Run type, Seed, scope, metric_key, Valor}`` (headway
    rows also carry ``Linha``). ``scope`` groups the metric family: a vehicle-class
    id (e.g. ``"buses"``), ``"network"``, ``"safety"``, ``"emissions"``,
    ``"emissions_bus"`` or ``"headway"``. Multi-seed roots emit one row per seed;
    callers aggregate by mean over seeds (same pattern as the legacy loader).
    """
    rows: list[dict] = []
    if not report_root.exists():
        return rows

    def _f(val: Any) -> float | None:
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    def _add(
        ctx: tuple[str, str, str],
        scope: str,
        metric_key: str,
        value: Any,
        line: str | None = None,
    ) -> None:
        v = _f(value)
        if v is None:
            return
        row = {
            "Cenário": ctx[0],
            "Run type": ctx[1],
            "Seed": ctx[2],
            "scope": scope,
            "metric_key": metric_key,
            "Valor": v,
        }
        if line is not None:
            row["Linha"] = line
        rows.append(row)

    for scenario_dir in sorted(p for p in report_root.iterdir() if p.is_dir()):
        for run_dir in sorted(p for p in scenario_dir.iterdir() if p.is_dir()):
            for seed_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
                kpi_path = seed_dir / "kpis.json"
                if not kpi_path.exists():
                    continue
                try:
                    kpis = json.loads(kpi_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if not isinstance(kpis, dict):
                    continue
                ctx = (scenario_dir.name, run_dir.name, seed_dir.name)

                # per vehicle class -------------------------------------------------
                for scope in CLASS_SCOPES:
                    block = kpis.get(scope)
                    if isinstance(block, dict):
                        for metric_key in CLASS_METRICS:
                            _add(ctx, scope, metric_key, block.get(metric_key))

                # network / queues --------------------------------------------------
                detectors = kpis.get("detectors")
                if isinstance(detectors, dict):
                    nq = detectors.get("network_queue")
                    if isinstance(nq, dict):
                        # Back-compat: older reports stored the network-level count
                        # under `intervals_above_8_veh`. Surface it under the corrected
                        # canonical key so existing reports keep showing the metric
                        # without regenerating the suite.
                        if (
                            "edge_intervals_above_8_veh" not in nq
                            and "intervals_above_8_veh" in nq
                        ):
                            nq = {**nq, "edge_intervals_above_8_veh": nq["intervals_above_8_veh"]}
                        for metric_key in NETWORK_METRICS:
                            _add(ctx, "network", metric_key, nq.get(metric_key))

                # safety / viability ------------------------------------------------
                insertion = kpis.get("insertion")
                if isinstance(insertion, dict):
                    for metric_key in SAFETY_METRICS:
                        _add(ctx, "safety", metric_key, insertion.get(metric_key))

                # emissions: fleet totals + per-vehicle-km --------------------------
                emissions = kpis.get("emissions")
                if isinstance(emissions, dict):
                    all_block = kpis.get("all_vehicles") or {}
                    # B27: per-vehicle-km denominator is the SUM of route lengths
                    # (total_route_length_m), identical to load_scenario_kpi_rows, so
                    # the values stay consistent and correct for heterogeneous routes.
                    total_route = _f(all_block.get("total_route_length_m"))
                    dist_km = (total_route / 1000) if total_route else None
                    totals = emissions.get("totals_mg")
                    if isinstance(totals, dict):
                        for sp in EMISSION_SPECIES:
                            tot = _f(totals.get(sp))
                            if tot is None:
                                continue
                            key = sp.lower()
                            _add(ctx, "emissions", f"total_{key}_mg", tot)
                            if dist_km and dist_km > 0:
                                _add(
                                    ctx,
                                    "emissions",
                                    f"total_{key}_mg_per_vehicle_km",
                                    tot / dist_km,
                                )
                    bus_totals = emissions.get("bus_totals_mg")
                    if isinstance(bus_totals, dict):
                        for sp in EMISSION_SPECIES:
                            tot = _f(bus_totals.get(sp))
                            if tot is not None:
                                _add(ctx, "emissions_bus", f"total_{sp.lower()}_mg", tot)

                # bus headways (per line:direction) ---------------------------------
                headways = kpis.get("bus_headways")
                if isinstance(headways, dict):
                    for line_id, hv in headways.items():
                        if not isinstance(hv, dict):
                            continue
                        _add(ctx, "headway", "mean_headway_s", hv.get("mean_headway_s"), line_id)
                        _add(ctx, "headway", "departures", hv.get("departures"), line_id)
                        min_h = _f(hv.get("min_headway_s"))
                        max_h = _f(hv.get("max_headway_s"))
                        if min_h is not None and max_h is not None:
                            _add(ctx, "headway", "headway_amplitude_s", max_h - min_h, line_id)
    return rows


def scenario_scoreboard(rows: list[dict]) -> dict[str, Any]:
    """Cross-scenario summary of the TSP effect from ``load_scenario_run_table`` rows.

    Pure aggregation feeding the Resumo and KPIs headlines. For each scenario it
    compares the TSP arm to the baseline arm and tallies:

    - ``bus_improved`` / ``bus_delta_median_pct`` — scenarios where bus time loss
      drops, and the median Δ%.
    - ``general_cost_over_90s`` — scenarios whose general-traffic time loss rises
      beyond the pipeline's 90 s gate.
    - ``safety_clean`` — scenarios with no collisions and no gridlock teleports.
    - ``queue_worsened`` / ``nox_improved`` — congestion and air-quality direction.

    Empty or single-arm data yields zeros and a ``None`` median (callers guard on
    ``n_scenarios``). Mirrors the verdict logic in run_sumo_scenario, so nothing is
    invented beyond the documented thresholds.
    """
    out: dict[str, Any] = {
        "n_scenarios": 0,
        "bus_improved": 0,
        "bus_delta_median_pct": None,
        "general_cost_over_90s": 0,
        "safety_clean": 0,
        "queue_worsened": 0,
        "nox_improved": 0,
    }
    if not rows:
        return out

    run_types = sorted({r["Run type"] for r in rows})
    # Prefer the exact arm names; fall back to substring only if the pipeline ever
    # renames them (and never let the tsp pick collide with the baseline arm).
    baseline_rt = (
        "baseline" if "baseline" in run_types else next((r for r in run_types if "baseline" in r), None)
    )
    tsp_rt = (
        "tsp_actuation"
        if "tsp_actuation" in run_types
        else next((r for r in run_types if r != baseline_rt and "tsp" in r), None)
    )
    scenarios = sorted({r["Cenário"] for r in rows})
    out["n_scenarios"] = len(scenarios)
    if not (baseline_rt and tsp_rt):
        return out

    acc: dict[tuple[str, str, str, str], list[float]] = defaultdict(list)
    for r in rows:
        acc[(r["Cenário"], r["Run type"], r["scope"], r["metric_key"])].append(r["Valor"])

    def mean(scen: str, rt: str, scope: str, metric_key: str) -> float | None:
        vals = acc.get((scen, rt, scope, metric_key))
        return (sum(vals) / len(vals)) if vals else None

    bus_deltas: list[float] = []
    for scen in scenarios:
        bus_b = mean(scen, baseline_rt, "buses", "mean_time_loss_s")
        bus_t = mean(scen, tsp_rt, "buses", "mean_time_loss_s")
        if bus_b is not None and bus_t is not None and bus_b != 0:
            delta = (bus_t - bus_b) / abs(bus_b) * 100
            bus_deltas.append(delta)
            if delta < 0:
                out["bus_improved"] += 1

        gen_b = mean(scen, baseline_rt, "general_traffic", "mean_time_loss_s")
        gen_t = mean(scen, tsp_rt, "general_traffic", "mean_time_loss_s")
        if gen_b is not None and gen_t is not None and (gen_t - gen_b) > 90:
            out["general_cost_over_90s"] += 1

        # Fail-closed (B26): only count a scenario as safety-clean when we actually
        # have telemetry showing zero. The old `not mean(...)` read a missing row
        # (mean() -> None) as 0, so scenarios with no safety data were tallied as
        # "clean" and inflated the headline. Require both counters present and zero.
        collisions = mean(scen, tsp_rt, "safety", "collisions")
        teleports_jam = mean(scen, tsp_rt, "safety", "teleports_jam")
        if (
            collisions is not None
            and teleports_jam is not None
            and collisions == 0
            and teleports_jam == 0
        ):
            out["safety_clean"] += 1

        q_b = mean(scen, baseline_rt, "network", "max_queue_vehicles")
        q_t = mean(scen, tsp_rt, "network", "max_queue_vehicles")
        if q_b is not None and q_t is not None and q_t > q_b:
            out["queue_worsened"] += 1

        nox_b = mean(scen, baseline_rt, "emissions", "total_nox_mg_per_vehicle_km")
        nox_t = mean(scen, tsp_rt, "emissions", "total_nox_mg_per_vehicle_km")
        if nox_b is not None and nox_t is not None and nox_t < nox_b:
            out["nox_improved"] += 1

    if bus_deltas:
        out["bus_delta_median_pct"] = round(statistics.median(bus_deltas), 1)
    return out
