#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config
from pps57_opt.config import load_policy_optimization_config
from pps57_opt.dataset import build_offline_scenarios
from pps57_opt.optimizer import OfflineOptimizationController
from pps57_opt.policy_runtime import RuntimePolicy
from pps57_opt.rl_trainer import TabularQLearningController
from pps57_tsp.engine import TSPDecisionEngine
from pps57_tsp.config import load_tsp_config


class PolicyOptimizationTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cits = load_cits_config(ROOT / "configs/cits_config.json", root=ROOT)
        cls.tsp = load_tsp_config(ROOT / "configs/tsp_config.json", root=ROOT)
        cls.opt = load_policy_optimization_config(ROOT / "configs/policy_optimization_config.json", root=ROOT)

    def _isolated_opt(self, tmp_root: Path):
        return replace(self.opt, root=tmp_root)

    def test_offline_optimization_exports_safe_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            summary = OfflineOptimizationController(self.cits, self.tsp, self._isolated_opt(tmp_root)).run()
            self.assertEqual(summary["mode"], "offline-policy-comparison")
            self.assertFalse(summary["is_reinforcement_learning"])
            self.assertTrue(summary["reward_delta_is_nonnegative_by_construction"])
            self.assertIn("optimized_action_changes_vs_baseline", summary)
            self.assertTrue(summary["safety_filter_required"])
            self.assertGreater(summary["scenario_count"], 0)
            self.assertGreater(summary["candidate_count"], summary["scenario_count"])
            self.assertGreater(summary["unsafe_candidates_filtered"], 0)
            self.assertGreaterEqual(summary["optimized_reward"], summary["baseline_reward"])
            self.assertTrue((tmp_root / "outputs/offline_policy_samples.jsonl").exists())
            self.assertTrue((tmp_root / "outputs/policy_candidates.jsonl").exists())
            self.assertTrue((tmp_root / "reports/policy_report.json").exists())
            self.assertTrue((tmp_root / "reports/policy_optimization_summary.json").exists())

    def test_exported_policy_never_selects_blocked_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            OfflineOptimizationController(self.cits, self.tsp, self._isolated_opt(tmp_root)).run()
            policy = json.loads((tmp_root / "reports/policy_report.json").read_text(encoding="utf-8"))
            self.assertTrue(policy["safety_filter_required"])
            for item in policy["selected_decisions"]:
                self.assertNotEqual(item["safety_status"], "blocked_by_safety")

    def test_stateful_safety_paths_are_exercised(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            OfflineOptimizationController(self.cits, self.tsp, self._isolated_opt(tmp_root)).run()
            policy = json.loads((tmp_root / "reports/policy_report.json").read_text(encoding="utf-8"))
            candidate_log = (tmp_root / "outputs/policy_candidates.jsonl").read_text(encoding="utf-8")
            self.assertIn("cooldown_after_priority_active", candidate_log)
            self.assertIn("max_consecutive_priority_interventions_reached", candidate_log)
            selected_by_scenario = {item["scenario_id"]: item for item in policy["selected_decisions"]}
            self.assertNotEqual(
                selected_by_scenario["OPT_COOLDOWN_ACTIVE"]["safety_status"],
                "approved",
            )
            self.assertNotEqual(
                selected_by_scenario["OPT_MAX_CONSECUTIVE_REACHED"]["safety_status"],
                "approved",
            )

    def test_exported_policy_keeps_baseline_guardrails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            OfflineOptimizationController(self.cits, self.tsp, self._isolated_opt(tmp_root)).run()
            policy = json.loads((tmp_root / "reports/policy_report.json").read_text(encoding="utf-8"))
            selected = {item["scenario_id"]: item["action"] for item in policy["selected_decisions"]}
            self.assertEqual(selected["OPT_NO_ACTION_GREEN_SUFFICIENT"], "no_action")
            self.assertEqual(selected["OPT_REEVALUATE_TOO_CLOSE"], "reevaluate_next_cycle")
            self.assertEqual(selected["OPT_REJECT_LOW_SCORE"], "reject")

    def test_runtime_policy_loads_exported_policy_for_online_inference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            OfflineOptimizationController(self.cits, self.tsp, self._isolated_opt(tmp_root)).run()
            runtime_policy = RuntimePolicy.load(self.tsp, tmp_root / "reports/policy_report.json")
            self.assertGreater(len(runtime_policy.rules), 0)

            scenario = next(
                item for item in build_offline_scenarios(self.cits)
                if item.scenario_id == "OPT_NO_ACTION_GREEN_SUFFICIENT"
            )
            baseline = TSPDecisionEngine(self.cits, self.tsp).decide(
                scenario.request,
                scenario.signal_state,
                scenario.sim_time_s,
            )
            decision = runtime_policy.decide(
                scenario.request,
                scenario.signal_state,
                scenario.sim_time_s,
                baseline,
            )
            self.assertEqual(decision.action, "no_action")
            self.assertIn("Runtime policy", " ".join(decision.notes))

    def test_tabular_q_learning_exports_rl_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            summary = TabularQLearningController(self.cits, self.tsp, self._isolated_opt(tmp_root)).run()
            self.assertEqual(summary["mode"], "tabular-q-learning")
            self.assertTrue(summary["is_reinforcement_learning"])
            self.assertFalse(summary["online_learning_in_production"])
            self.assertGreater(summary["learned_rule_count"], 0)
            self.assertTrue((tmp_root / "reports/tabular_q_policy_report.json").exists())
            self.assertTrue((tmp_root / "reports/rl_training_summary.json").exists())


if __name__ == "__main__":
    unittest.main()
