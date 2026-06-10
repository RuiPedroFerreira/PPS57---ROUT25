#!/usr/bin/env python3
"""V0 validation harness: formula and acceptance-gate tests.

Every expected value below is computed by hand from the published formula (the
mathematics is the source of truth) or from the WisDOT/DMRB/FHWA thresholds in
configs/validation_config.json. The numeric fixtures are formula-verification
vectors, NOT traffic measurements: this suite asserts the measuring instrument
is correct, it does not assert anything about Porto.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.validation import acceptance, metrics  # noqa: E402
from pps57_sumo.validation.acceptance import load_validation_config  # noqa: E402


class GehFormulaTests(unittest.TestCase):
    def test_perfect_match_is_zero(self) -> None:
        self.assertEqual(metrics.geh(100.0, 100.0), 0.0)
        self.assertEqual(metrics.geh(0.0, 0.0), 0.0)

    def test_known_values(self) -> None:
        # GEH = sqrt(2*(M-C)^2/(M+C)); computed by hand.
        self.assertAlmostEqual(metrics.geh(200.0, 100.0), 8.16497, places=4)
        self.assertAlmostEqual(metrics.geh(700.0, 650.0), 1.92450, places=4)
        # 2*40^2/200 = 16 -> sqrt = 4.0 exactly.
        self.assertAlmostEqual(metrics.geh(120.0, 80.0), 4.0, places=6)

    def test_symmetry(self) -> None:
        self.assertAlmostEqual(metrics.geh(120.0, 80.0), metrics.geh(80.0, 120.0), places=9)

    def test_negative_flow_rejected(self) -> None:
        with self.assertRaises(ValueError):
            metrics.geh(-1.0, 100.0)

    def test_bands(self) -> None:
        self.assertEqual(metrics.geh_band(4.9, good_below=5.0, investigate_below=10.0), "good")
        self.assertEqual(metrics.geh_band(5.0, good_below=5.0, investigate_below=10.0), "investigate")
        self.assertEqual(metrics.geh_band(7.0, good_below=5.0, investigate_below=10.0), "investigate")
        self.assertEqual(metrics.geh_band(10.0, good_below=5.0, investigate_below=10.0), "poor")


class FlowBandTests(unittest.TestCase):
    # WisDOT bands: <700 within 100 veh/h; 700-2700 within 15%; >=2700 within 400 veh/h.
    BANDS = [
        {"max_flow_veh_h": 700.0, "tolerance_abs_veh_h": 100.0},
        {"min_flow_veh_h": 700.0, "max_flow_veh_h": 2700.0, "tolerance_fraction": 0.15},
        {"min_flow_veh_h": 2700.0, "tolerance_abs_veh_h": 400.0},
    ]

    def test_low_band_absolute(self) -> None:
        self.assertTrue(metrics.flow_within_band(700.0, 650.0, self.BANDS))   # |50| <= 100
        self.assertFalse(metrics.flow_within_band(800.0, 650.0, self.BANDS))  # |150| > 100

    def test_mid_band_fraction(self) -> None:
        self.assertTrue(metrics.flow_within_band(1100.0, 1000.0, self.BANDS))   # |100| <= 150
        self.assertFalse(metrics.flow_within_band(1200.0, 1000.0, self.BANDS))  # |200| > 150

    def test_high_band_absolute(self) -> None:
        self.assertTrue(metrics.flow_within_band(3300.0, 3000.0, self.BANDS))   # |300| <= 400
        self.assertFalse(metrics.flow_within_band(3500.0, 3000.0, self.BANDS))  # |500| > 400

    def test_boundary_700_uses_fraction_band(self) -> None:
        # observed == 700 falls into the 700-2700 band (15% -> 105 tolerance).
        self.assertTrue(metrics.flow_within_band(800.0, 700.0, self.BANDS))   # |100| <= 105


class TravelTimeTests(unittest.TestCase):
    def test_within_15_percent(self) -> None:
        self.assertTrue(metrics.travel_time_within(540.0, 505.0, within_fraction=0.15, or_absolute_s=60.0))

    def test_one_minute_floor(self) -> None:
        # short trip: 15% of 100s = 15s, but the 60s floor applies.
        self.assertTrue(metrics.travel_time_within(150.0, 100.0, within_fraction=0.15, or_absolute_s=60.0))
        self.assertFalse(metrics.travel_time_within(170.0, 100.0, within_fraction=0.15, or_absolute_s=60.0))

    def test_negative_rejected(self) -> None:
        with self.assertRaises(ValueError):
            metrics.travel_time_within(-10.0, 100.0, within_fraction=0.15, or_absolute_s=60.0)


class ErrorStatTests(unittest.TestCase):
    def test_rmse(self) -> None:
        # diffs -2,-2,+2 -> squares 4,4,4 -> mean 4 -> sqrt 2.0
        self.assertAlmostEqual(metrics.rmse([(2, 4), (6, 8), (10, 8)]), 2.0, places=6)

    def test_rmse_pct(self) -> None:
        # rmse 2.0 over mean observed (4+8+8)/3 = 6.6667 -> 30.0%
        self.assertAlmostEqual(metrics.rmse_pct([(2, 4), (6, 8), (10, 8)]), 30.0, places=3)

    def test_pearson(self) -> None:
        self.assertAlmostEqual(metrics.pearson_r([(1, 2), (2, 4), (3, 6)]), 1.0, places=9)
        self.assertAlmostEqual(metrics.pearson_r([(1, 6), (2, 4), (3, 2)]), -1.0, places=9)

    def test_abs_pct_errors_skips_zero_observed(self) -> None:
        self.assertEqual(metrics.abs_pct_errors([(110, 100), (90, 100), (5, 0)]), [10.0, 10.0])


class LinkFlowAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_validation_config()

    def test_perfect_calibration_passes(self) -> None:
        links = [
            {"link_id": f"L{i}", "modelled_veh_h": flow, "observed_veh_h": flow}
            for i, flow in enumerate([600, 800, 1000, 1500, 2000])
        ]
        report = acceptance.evaluate_link_flow_calibration(links, self.config)
        self.assertEqual(report["verdict"], "pass")
        self.assertEqual(report["geh"]["fraction_passing"], 1.0)
        self.assertTrue(report["sum_of_flows"]["passed"])

    def test_poor_calibration_fails(self) -> None:
        links = [
            {"link_id": "L0", "modelled_veh_h": 1000, "observed_veh_h": 1000},
            {"link_id": "L1", "modelled_veh_h": 1000, "observed_veh_h": 1000},
            {"link_id": "L2", "modelled_veh_h": 1000, "observed_veh_h": 1000},
            {"link_id": "L3", "modelled_veh_h": 2000, "observed_veh_h": 100},
            {"link_id": "L4", "modelled_veh_h": 2000, "observed_veh_h": 100},
        ]
        report = acceptance.evaluate_link_flow_calibration(links, self.config)
        self.assertEqual(report["verdict"], "fail")
        self.assertEqual(report["geh"]["fraction_passing"], 0.6)  # 3/5 below GEH 5
        self.assertFalse(report["geh"]["passed"])


class TravelTimeAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_validation_config()

    def test_pass(self) -> None:
        segments = [{"segment_id": f"S{i}", "modelled_s": 505, "observed_s": 500} for i in range(4)]
        report = acceptance.evaluate_travel_times(segments, self.config)
        self.assertEqual(report["verdict"], "pass")

    def test_fail_below_85_percent(self) -> None:
        segments = [
            {"segment_id": "S0", "modelled_s": 505, "observed_s": 500},
            {"segment_id": "S1", "modelled_s": 505, "observed_s": 500},
            {"segment_id": "S2", "modelled_s": 505, "observed_s": 500},
            {"segment_id": "S3", "modelled_s": 900, "observed_s": 500},  # |400| > max(75,60)
        ]
        report = acceptance.evaluate_travel_times(segments, self.config)
        self.assertEqual(report["fraction_passing"], 0.75)
        self.assertEqual(report["verdict"], "fail")


class FaceValidityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_validation_config()

    def test_inside_envelope_is_plausible(self) -> None:
        report = acceptance.evaluate_tsp_face_validity(
            [{"metric": "bus_running_time_improvement_pct", "value_pct": 9.4}], self.config
        )
        self.assertEqual(report["verdict"], "plausible")
        self.assertTrue(report["results"][0]["inside_envelope"])

    def test_outside_envelope_is_flagged(self) -> None:
        report = acceptance.evaluate_tsp_face_validity(
            [{"metric": "bus_running_time_improvement_pct", "value_pct": 25.0}], self.config
        )
        self.assertEqual(report["verdict"], "flagged")

    def test_delay_band_is_distinct(self) -> None:
        # 60% is outside running-time [2,18] but inside delay-reduction [15,80].
        report = acceptance.evaluate_tsp_face_validity(
            [{"metric": "bus_delay_reduction_pct", "value_pct": 60.0}], self.config
        )
        self.assertTrue(report["results"][0]["inside_envelope"])

    def test_empty_is_no_measurements(self) -> None:
        report = acceptance.evaluate_tsp_face_validity([], self.config)
        self.assertEqual(report["verdict"], "no_measurements")

    def test_unknown_band_rejected(self) -> None:
        with self.assertRaises(ValueError):
            acceptance.evaluate_tsp_face_validity(
                [{"metric": "made_up_metric", "value_pct": 5.0}], self.config
            )


class ConfigSourceTraceabilityTests(unittest.TestCase):
    """Guard the core invariant: every threshold block carries a 'source'."""

    def setUp(self) -> None:
        self.config = load_validation_config()

    def test_link_flow_blocks_have_sources(self) -> None:
        cal = self.config["link_flow_calibration"]
        for key in ("geh", "network_acceptance", "flow_percentage_bands", "sum_of_flows"):
            self.assertTrue(cal[key]["source"].strip(), f"{key} missing source")

    def test_travel_time_and_face_validity_have_sources(self) -> None:
        self.assertTrue(self.config["travel_time_validation"]["source"].strip())
        fv = self.config["tsp_face_validity"]
        for key in ("bus_running_time_improvement_pct", "bus_delay_reduction_pct"):
            self.assertTrue(fv[key]["source"].strip(), f"{key} missing source")


if __name__ == "__main__":
    unittest.main()
