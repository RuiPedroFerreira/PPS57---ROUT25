#!/usr/bin/env python3
"""P4 off-policy evaluation substrate + shared stats parity."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_opt.models import OfflineScenario
from pps57_opt.ope import (
    ESTIMATED,
    INCONCLUSIVE_NO_BEHAVIOR,
    INCONCLUSIVE_NO_OUTCOMES,
    LIMITED_SUPPORT,
    evaluate_policy,
)
from pps57_sumo.stats import mean_ci95, t_critical_95


def scenario(sid: str, *, behavior=None, outcome=None) -> OfflineScenario:
    return OfflineScenario(
        scenario_id=sid,
        description="",
        expected_case="",
        sim_time_s=0.0,
        request=None,
        signal_state=None,
        behavior_policy_action=behavior,
        realized_outcome=outcome,
    )


class OPEVerdictTestCase(unittest.TestCase):
    def test_inconclusive_without_outcomes(self) -> None:
        scenarios = [scenario("a", behavior="green_extension"), scenario("b", behavior="no_action")]
        report = evaluate_policy(scenarios, lambda s: "green_extension")
        self.assertEqual(report.verdict, INCONCLUSIVE_NO_OUTCOMES)
        self.assertIsNone(report.estimate)
        self.assertIsNone(report.confidence_interval)
        self.assertEqual(report.n_scenarios, 2)

    def test_inconclusive_without_behavior_actions(self) -> None:
        scenarios = [scenario("a", outcome=10.0), scenario("b", outcome=5.0)]
        report = evaluate_policy(scenarios, lambda s: "green_extension")
        self.assertEqual(report.verdict, INCONCLUSIVE_NO_BEHAVIOR)
        self.assertIsNone(report.estimate)

    def test_known_ips_estimate(self) -> None:
        scenarios = [
            scenario("s1", behavior="green_extension", outcome=10.0),
            scenario("s2", behavior="green_extension", outcome=20.0),
            scenario("s3", behavior="no_action", outcome=5.0),
            scenario("s4", behavior="no_action", outcome=7.0),
        ]
        report = evaluate_policy(scenarios, lambda s: "green_extension")
        self.assertEqual(report.verdict, ESTIMATED)
        # IPS terms [10,20,0,0] -> mean 7.5; matched outcomes [10,20] -> mean 15.0.
        self.assertAlmostEqual(report.estimate, 7.5, places=3)
        self.assertAlmostEqual(report.matched_mean_outcome, 15.0, places=3)
        self.assertEqual(report.n_eligible, 4)
        self.assertEqual(report.n_matched, 2)
        self.assertAlmostEqual(report.coverage, 0.5, places=3)
        self.assertTrue(report.assumed_deterministic_behavior_propensity)
        self.assertIsNotNone(report.confidence_interval)
        self.assertEqual(len(report.to_dict()["confidence_interval"]), 2)

    def test_zero_support_is_not_a_measured_zero(self) -> None:
        # Target never matches behavior -> no support overlap: must NOT report a
        # measured estimate of 0 with a zero-width CI (that would be false precision).
        scenarios = [scenario(f"s{i}", behavior="no_action", outcome=float(i)) for i in range(4)]
        report = evaluate_policy(scenarios, lambda s: "green_extension")
        self.assertEqual(report.verdict, LIMITED_SUPPORT)
        self.assertEqual(report.n_matched, 0)
        self.assertIsNone(report.estimate)
        self.assertIsNone(report.confidence_interval)
        self.assertIsNone(report.matched_mean_outcome)

    def test_single_eligible_sample_has_no_confidence_interval(self) -> None:
        report = evaluate_policy([scenario("s1", behavior="g", outcome=10.0)], lambda s: "g")
        self.assertEqual(report.verdict, LIMITED_SUPPORT)
        self.assertEqual(report.estimate, 10.0)
        self.assertIsNone(report.confidence_interval)  # n=1 cannot bound variance

    def test_limited_support_when_coverage_low(self) -> None:
        scenarios = [scenario(f"n{i}", behavior="no_action", outcome=1.0) for i in range(5)]
        scenarios.append(scenario("g", behavior="green_extension", outcome=9.0))
        report = evaluate_policy(scenarios, lambda s: "green_extension", min_coverage=0.2)
        self.assertEqual(report.verdict, LIMITED_SUPPORT)  # coverage 1/6 < 0.2
        self.assertEqual(report.n_matched, 1)


class StatsParityTestCase(unittest.TestCase):
    """The OPE CI reuses the same Student-t machinery as the replication stats."""

    def test_mean_ci95_matches_run_sumo_scenario_alias(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("rss_stats", ROOT / "scripts" / "run_sumo_scenario.py")
        rss = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rss)
        values = [10.0, 12.0, 11.0, 9.0, 13.0]
        self.assertEqual(rss._mean_ci95(values), mean_ci95(values))
        self.assertEqual(rss._t_critical_95(4), t_critical_95(4))
        self.assertEqual(rss._t_critical_95(200), t_critical_95(200))


if __name__ == "__main__":
    unittest.main()
