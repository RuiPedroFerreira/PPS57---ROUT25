#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_dashboard.results import (  # noqa: E402
    catalog_label_map,
    default_scenario_dataset,
    discover_scenario_report_roots,
    load_scenario_kpi_rows,
    scenario_catalog_path,
)


class DashboardResultsTestCase(unittest.TestCase):
    def test_prefers_ingolstadt_report_root_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp) / "reports"
            (reports / "ingolstadt").mkdir(parents=True)
            (reports / "scenarios").mkdir(parents=True)
            roots = discover_scenario_report_roots(reports)
            self.assertEqual(default_scenario_dataset(reports), "ingolstadt")
            self.assertEqual(roots["ingolstadt"], reports / "ingolstadt")
            self.assertEqual(roots["synthetic"], reports / "scenarios")

    def test_loads_ingolstadt_kpi_rows_from_reference_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = (
                Path(tmp)
                / "reports"
                / "ingolstadt"
                / "city_am_peak"
                / "tsp_actuation"
                / "seed_57"
            )
            root.mkdir(parents=True)
            (root / "kpis.json").write_text(
                json.dumps(
                    {
                        "buses": {
                            "vehicles": 2,
                            "mean_time_loss_s": 12.5,
                        }
                    }
                ),
                encoding="utf-8",
            )
            rows = load_scenario_kpi_rows(
                Path(tmp) / "reports" / "ingolstadt",
                "buses",
                {"mean_time_loss_s": ("Perda", "s", "desc")},
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["Cenário"], "city_am_peak")
            self.assertEqual(rows[0]["Run type"], "tsp_actuation")
            self.assertEqual(rows[0]["Valor"], 12.5)

    def test_catalog_helpers_point_to_ingolstadt_reference(self) -> None:
        self.assertEqual(
            scenario_catalog_path(ROOT, "ingolstadt").name,
            "scenario_catalog_ingolstadt.yaml",
        )
        labels = catalog_label_map({"scenarios": {"city_am_peak": {"description": "AM"}}})
        self.assertEqual(labels["city_am_peak"], "AM")


if __name__ == "__main__":
    unittest.main()
