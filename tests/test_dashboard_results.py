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
    load_scenario_focus_significance,
    load_scenario_kpi_rows,
    load_scenario_run_table,
    scenario_catalog_path,
    scenario_scoreboard,
)


class DashboardResultsTestCase(unittest.TestCase):
    def test_discovers_synthetic_scenario_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp) / "reports"
            (reports / "scenarios").mkdir(parents=True)
            (reports / "scenarios" / "scenario_suite_summary.json").write_text(
                "{}", encoding="utf-8"
            )
            # B29: a root needs at least one seed kpis.json to count as available.
            seed = reports / "scenarios" / "city_am_peak" / "tsp_actuation" / "seed_57"
            seed.mkdir(parents=True)
            (seed / "kpis.json").write_text("{}", encoding="utf-8")
            roots = discover_scenario_report_roots(reports)
            self.assertEqual(default_scenario_dataset(reports), "synthetic")
            self.assertEqual(roots["synthetic"], reports / "scenarios")

    def test_summary_only_root_is_not_available(self) -> None:
        # B29: a scenarios root with only the suite summary (no seed kpis.json) is an
        # empty dataset and must not be discovered.
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp) / "reports"
            (reports / "scenarios" / "baseline_am_peak").mkdir(parents=True)
            (reports / "scenarios" / "scenario_suite_summary.json").write_text(
                "{}", encoding="utf-8"
            )
            roots = discover_scenario_report_roots(reports)
            self.assertNotIn("synthetic", roots)
            self.assertEqual(default_scenario_dataset(reports), "synthetic")

    def test_loads_kpi_rows_from_reference_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = (
                Path(tmp)
                / "reports"
                / "scenarios"
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
                Path(tmp) / "reports" / "scenarios",
                "buses",
                {"mean_time_loss_s": ("Perda", "s", "desc")},
            )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["Cenário"], "city_am_peak")
            self.assertEqual(rows[0]["Run type"], "tsp_actuation")
            self.assertEqual(rows[0]["Valor"], 12.5)

    def test_run_table_extracts_all_scopes_with_derived_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "reports" / "scenarios"
            seed = root / "delayed_bus_westbound" / "tsp_actuation" / "seed_57"
            seed.mkdir(parents=True)
            (seed / "kpis.json").write_text(
                json.dumps(
                    {
                        "all_vehicles": {
                            "vehicles": 100,
                            "mean_time_loss_s": 50.0,
                            "mean_route_length_m": 2000.0,
                            # B27: per-vehicle-km now normalises against the SUM of
                            # route lengths (here 100 vehicles × 2000 m = 200 000 m).
                            "total_route_length_m": 200000.0,
                        },
                        "buses": {
                            "vehicles": 10,
                            "mean_time_loss_s": 80.0,
                            "p95_duration_s": 300.0,
                        },
                        "general_traffic": {"vehicles": 90, "mean_time_loss_s": 40.0},
                        "detectors": {
                            "network_queue": {
                                "max_queue_vehicles": 12.0,
                                "intervals_above_8_veh": 5,
                            }
                        },
                        "insertion": {
                            "teleports_total": 0,
                            "teleports_jam": 0,
                            "collisions": 0,
                            "emergency_braking": 7,
                        },
                        "emissions": {
                            "totals_mg": {"CO2": 1000.0, "fuel": 400.0, "NOx": 20.0, "PMx": 2.0},
                            "bus_totals_mg": {"CO2": 100.0, "NOx": 5.0},
                        },
                        "bus_headways": {
                            "L1:E": {
                                "departures": 4,
                                "mean_headway_s": 600.0,
                                "min_headway_s": 550.0,
                                "max_headway_s": 650.0,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            rows = load_scenario_run_table(root)
            self.assertTrue(rows)
            # every row carries the run coordinates
            for row in rows:
                self.assertEqual(row["Cenário"], "delayed_bus_westbound")
                self.assertEqual(row["Run type"], "tsp_actuation")
                self.assertEqual(row["Seed"], "seed_57")

            flat = {(r["scope"], r["metric_key"]): r["Valor"] for r in rows}
            self.assertEqual(flat[("buses", "mean_time_loss_s")], 80.0)
            self.assertEqual(flat[("buses", "p95_duration_s")], 300.0)
            self.assertEqual(flat[("general_traffic", "mean_time_loss_s")], 40.0)
            self.assertEqual(flat[("network", "max_queue_vehicles")], 12.0)
            self.assertEqual(flat[("safety", "collisions")], 0.0)
            self.assertEqual(flat[("safety", "emergency_braking")], 7.0)
            self.assertEqual(flat[("emissions", "total_nox_mg")], 20.0)
            # per-vehicle-km = total_co2 / (total_route_length_m / 1000) = 1000 / 200
            self.assertEqual(flat[("emissions", "total_co2_mg_per_vehicle_km")], 5.0)
            # legacy `intervals_above_8_veh` is surfaced under the corrected canonical key
            self.assertEqual(flat[("network", "edge_intervals_above_8_veh")], 5.0)
            self.assertEqual(flat[("emissions_bus", "total_co2_mg")], 100.0)

            # headway amplitude = max - min, carried with the line id
            amp = [
                r
                for r in rows
                if r["scope"] == "headway" and r["metric_key"] == "headway_amplitude_s"
            ]
            self.assertEqual(len(amp), 1)
            self.assertEqual(amp[0]["Valor"], 100.0)
            self.assertEqual(amp[0]["Linha"], "L1:E")

    def test_run_table_surfaces_directional_bus_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "reports" / "scenarios"
            seed = root / "delayed_bus_westbound" / "tsp_actuation" / "seed_57"
            seed.mkdir(parents=True)
            (seed / "kpis.json").write_text(
                json.dumps(
                    {
                        "buses": {"vehicles": 4, "mean_time_loss_s": 30.0},
                        "buses_westbound": {"vehicles": 2, "mean_time_loss_s": 44.0},
                        "buses_eastbound": {"vehicles": 2, "mean_time_loss_s": 16.0},
                    }
                ),
                encoding="utf-8",
            )
            rows = load_scenario_run_table(root)
            flat = {(r["scope"], r["metric_key"]): r["Valor"] for r in rows}
            self.assertEqual(flat[("buses_westbound", "mean_time_loss_s")], 44.0)
            self.assertEqual(flat[("buses_eastbound", "mean_time_loss_s")], 16.0)
            # the headline two-way bus scope stays intact
            self.assertEqual(flat[("buses", "mean_time_loss_s")], 30.0)

    def test_focus_significance_loader_reads_suite_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "reports" / "scenarios"
            root.mkdir(parents=True)
            (root / "scenario_suite_summary.json").write_text(
                json.dumps(
                    {
                        "scenarios": [
                            {
                                "scenario_id": "delayed_bus_westbound",
                                "comparisons": {
                                    "baseline_vs_tsp_actuation": {
                                        "bus_westbound_time_loss_replication_significance": {
                                            "verdict": "significant_improvement",
                                            "ci95_low": 2.0,
                                            "ci95_high": 9.0,
                                        }
                                    }
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            sig = load_scenario_focus_significance(root)
            block = sig["delayed_bus_westbound"][
                "bus_westbound_time_loss_replication_significance"
            ]
            self.assertEqual(block["verdict"], "significant_improvement")
            self.assertEqual(block["ci95_low"], 2.0)
            # absent / malformed summary -> empty map, not a crash
            self.assertEqual(load_scenario_focus_significance(Path(tmp) / "nope"), {})

    def test_scenario_scoreboard_tallies_tsp_effect(self) -> None:
        def row(scen, rt, scope, mk, val):
            return {
                "Cenário": scen,
                "Run type": rt,
                "scope": scope,
                "metric_key": mk,
                "Valor": val,
            }

        rows = [
            # "good": bus improves -20%, general +5s (ok), safe, queue down, NOx down
            row("good", "baseline", "buses", "mean_time_loss_s", 100.0),
            row("good", "tsp_actuation", "buses", "mean_time_loss_s", 80.0),
            row("good", "baseline", "general_traffic", "mean_time_loss_s", 50.0),
            row("good", "tsp_actuation", "general_traffic", "mean_time_loss_s", 55.0),
            row("good", "tsp_actuation", "safety", "collisions", 0.0),
            row("good", "tsp_actuation", "safety", "teleports_jam", 0.0),
            row("good", "baseline", "network", "max_queue_vehicles", 10.0),
            row("good", "tsp_actuation", "network", "max_queue_vehicles", 9.0),
            row("good", "baseline", "emissions", "total_nox_mg_per_vehicle_km", 20.0),
            row("good", "tsp_actuation", "emissions", "total_nox_mg_per_vehicle_km", 18.0),
            # "bad": bus +10%, general +150s (cost), collision, queue up, NOx up
            row("bad", "baseline", "buses", "mean_time_loss_s", 100.0),
            row("bad", "tsp_actuation", "buses", "mean_time_loss_s", 110.0),
            row("bad", "baseline", "general_traffic", "mean_time_loss_s", 50.0),
            row("bad", "tsp_actuation", "general_traffic", "mean_time_loss_s", 200.0),
            row("bad", "tsp_actuation", "safety", "collisions", 1.0),
            row("bad", "tsp_actuation", "safety", "teleports_jam", 0.0),
            row("bad", "baseline", "network", "max_queue_vehicles", 10.0),
            row("bad", "tsp_actuation", "network", "max_queue_vehicles", 12.0),
            row("bad", "baseline", "emissions", "total_nox_mg_per_vehicle_km", 20.0),
            row("bad", "tsp_actuation", "emissions", "total_nox_mg_per_vehicle_km", 25.0),
        ]
        sb = scenario_scoreboard(rows)
        self.assertEqual(sb["n_scenarios"], 2)
        self.assertEqual(sb["bus_improved"], 1)
        self.assertEqual(sb["bus_delta_median_pct"], -5.0)  # median(-20, +10)
        self.assertEqual(sb["general_cost_over_90s"], 1)
        self.assertEqual(sb["safety_clean"], 1)
        self.assertEqual(sb["queue_worsened"], 1)
        self.assertEqual(sb["nox_improved"], 1)

    def test_scenario_scoreboard_handles_empty_and_single_arm(self) -> None:
        self.assertEqual(scenario_scoreboard([])["n_scenarios"], 0)
        single = [
            {
                "Cenário": "x",
                "Run type": "baseline",
                "scope": "buses",
                "metric_key": "mean_time_loss_s",
                "Valor": 10.0,
            }
        ]
        sb = scenario_scoreboard(single)
        self.assertEqual(sb["n_scenarios"], 1)
        self.assertEqual(sb["bus_improved"], 0)
        self.assertIsNone(sb["bus_delta_median_pct"])

    def test_catalog_helper_points_to_synthetic_catalog(self) -> None:
        self.assertEqual(
            scenario_catalog_path(ROOT).name,
            "scenario_catalog.yaml",
        )
        labels = catalog_label_map({"scenarios": {"city_am_peak": {"description": "AM"}}})
        self.assertEqual(labels["city_am_peak"], "AM")


if __name__ == "__main__":
    unittest.main()
