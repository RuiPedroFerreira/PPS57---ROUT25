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
