#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import run_ingolstadt_demo as rid  # noqa: E402


class IngolstadtRunnerTestCase(unittest.TestCase):
    def test_catalog_scenarios_are_real_slices(self) -> None:
        catalog = rid.load_ingolstadt_catalog(ROOT / "configs/scenario_catalog_ingolstadt.yaml")
        self.assertEqual(
            catalog["metadata"]["alignment_level"], "real_calibrated_thirdparty"
        )
        for scenario_id, entry in catalog["scenarios"].items():
            with self.subTest(scenario_id=scenario_id):
                self.assertTrue(entry["day"])
                self.assertLess(entry["window_s"][0], entry["window_s"][1])
                self.assertTrue(entry["description"])
                self.assertTrue(entry["realism_basis"])
                self.assertTrue(entry["kpi_focus"])

    def test_required_scenario_files_use_day_specific_tum_paths(self) -> None:
        files = rid.required_scenario_files("2023-07-04")
        self.assertIn("ingolstadt_net.net.xml", files)
        self.assertIn("Routes/routes_2023-07-04_24h_det_calib.rou.xml.gz", files)
        self.assertIn("TL/2023-07-04_tlLogics_24h.tll.xml", files)
        self.assertIn("TL/2023-07-04_WAUT.xml", files)
        self.assertIn("PT/2023-07-04_gtfs_trips.rou.xml", files)

    def test_window_seconds_convert_to_sumo_clock(self) -> None:
        self.assertEqual(rid.seconds_to_hhmmss(25200), "07:00:00")
        self.assertEqual(rid.hhmmss_to_seconds("13:00:00"), 46800)

    def test_resolve_catalog_spec_uses_window_or_steps_override(self) -> None:
        catalog = rid.load_ingolstadt_catalog(ROOT / "configs/scenario_catalog_ingolstadt.yaml")
        args = argparse.Namespace(
            all=False,
            scenario="city_am_peak",
            steps=600,
            day="2023-07-04",
            begin="07:00:00",
        )
        [spec] = rid.resolve_ingolstadt_specs(args, catalog)
        self.assertEqual(spec.scenario_id, "city_am_peak")
        self.assertEqual(spec.begin, "07:00:00")
        self.assertEqual(spec.end, "07:10:00")
        self.assertEqual(spec.steps, 600)

    def test_materialize_writes_isolated_outputs_and_seed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenario_dir = root / "scenario"
            work = root / "work"
            day = "2023-07-04"
            for src_rel in rid.required_scenario_files(day):
                path = scenario_dir / src_rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x", encoding="utf-8")

            run_output = root / "runs" / "city_am_peak" / "baseline" / "seed_57"
            sumocfg, net = rid.materialize(
                day,
                "07:00:00",
                refresh=False,
                scenario_dir=scenario_dir,
                work=work,
                run_output_dir=run_output,
                end="07:05:00",
                seed=57,
            )
            text = sumocfg.read_text(encoding="utf-8")
            self.assertEqual(net, work / "ingolstadt_net.net.xml")
            self.assertIn("<random_number>", text)
            self.assertIn('<seed value="57"/>', text)
            self.assertIn("</random_number>", text)
            self.assertIn("tripinfo-output", text)
            self.assertIn(str(run_output / "out" / "summary.xml"), text)
            self.assertIn("routes_2023-07-04_24h_det_calib.rou.xml.gz", text)

    def test_no_actuation_flag_maps_to_baseline_dry_run(self) -> None:
        # Após consolidar para dois modos, o baseline É o dry-run (controller com
        # apply_actuation=False), por isso --no-actuation resolve para baseline.
        args = argparse.Namespace(
            no_actuation=True,
            scenario="city_am_peak",
            all=False,
            run_type="pair",
        )
        self.assertEqual(rid._run_types_for(args), ["baseline"])

    def test_citywide_tsp_config_disables_corridor_debt_and_specific_mappings(self) -> None:
        raw = json.loads((ROOT / "configs/tsp_safety_config.json").read_text(encoding="utf-8"))
        resolved = rid._citywide_tsp_raw(raw)
        self.assertIsNone(resolved["corridor"]["max_corridor_recovery_debt_s"])
        self.assertTrue(resolved["corridor"]["respect_downstream_spillback"])
        self.assertEqual(resolved["controller_contracts"]["controllers"], {})
        self.assertEqual(resolved["phase_mapping"]["priority_movements"], {})
        self.assertTrue(resolved["network_profile"]["enabled"])


if __name__ == "__main__":
    unittest.main()
