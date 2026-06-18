"""Result discovery helpers shared by the dashboard and tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATASET_INGOLSTADT = "ingolstadt"
DATASET_SYNTHETIC = "synthetic"


def discover_scenario_report_roots(reports_root: Path) -> dict[str, Path]:
    """Return available scenario result roots, preferring Ingolstadt as reference."""
    roots: dict[str, Path] = {}
    ingolstadt = reports_root / "ingolstadt"
    synthetic = reports_root / "scenarios"
    if _has_scenario_reports(ingolstadt):
        roots[DATASET_INGOLSTADT] = ingolstadt
    if _has_scenario_reports(synthetic):
        roots[DATASET_SYNTHETIC] = synthetic
    return roots


def _has_scenario_reports(report_root: Path) -> bool:
    if (report_root / "scenario_suite_summary.json").exists():
        return True
    if not report_root.exists():
        return False
    return any(report_root.glob("*/*/seed_*/kpis.json"))


def default_scenario_dataset(reports_root: Path) -> str:
    roots = discover_scenario_report_roots(reports_root)
    if DATASET_INGOLSTADT in roots:
        return DATASET_INGOLSTADT
    if DATASET_SYNTHETIC in roots:
        return DATASET_SYNTHETIC
    return DATASET_INGOLSTADT


def scenario_catalog_path(root: Path, dataset: str) -> Path:
    if dataset == DATASET_INGOLSTADT:
        return root / "configs" / "scenario_catalog_ingolstadt.yaml"
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

                        def _append(metric_key: str, value: Any) -> None:
                            if value is None:
                                return
                            value_f = _to_float(value)
                            if value_f is None:
                                return
                            rows.append(
                                {
                                    "Cenário": scenario_dir.name,
                                    "Run type": run_dir.name,
                                    "Seed": seed_dir.name,
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
                            route_len_f = _to_float(data.get("mean_route_length_m"))
                            if vehicle_count_f and vehicle_count_f > 0:
                                if co2_total is not None:
                                    co2_f = _to_float(co2_total)
                                    if co2_f is not None:
                                        _append("total_co2_mg_per_vehicle", co2_f / vehicle_count_f)

                                if fuel_total is not None:
                                    fuel_f = _to_float(fuel_total)
                                    if fuel_f is not None:
                                        _append("total_fuel_mg_per_vehicle", fuel_f / vehicle_count_f)

                            if route_len_f and route_len_f > 0 and vehicle_count_f and vehicle_count_f > 0:
                                total_distance_m = route_len_f * vehicle_count_f
                                if total_distance_m > 0:
                                    dist_km = total_distance_m / 1000
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
                    label = meta[0] if isinstance(meta, (tuple, list)) and meta else metric_key
                    rows.append(
                        {
                            "Cenário": scenario_dir.name,
                            "Run type": run_dir.name,
                            "Seed": seed_dir.name,
                            "metric_key": metric_key,
                            "Métrica": label,
                            "Valor": value,
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
