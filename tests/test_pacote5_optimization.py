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
from pps57_opt.config import load_optimization_config
from pps57_opt.optimizer import OfflineOptimizationController
from pps57_tsp.config import load_tsp_config


class Package5OptimizationTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cits = load_cits_config(ROOT / "configs/cits_config.json", root=ROOT)
        cls.tsp = load_tsp_config(ROOT / "configs/tsp_config.json", root=ROOT)
        cls.opt = load_optimization_config(ROOT / "configs/optimization_config.json", root=ROOT)

    def _isolated_opt(self, tmp_root: Path):
        # Item 5: redirecionar outputs/reports do Pacote 5 para um tempdir
        # em vez de poluírem a raiz do repositório durante os testes.
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
            self.assertTrue((tmp_root / "outputs/pacote5_offline_samples.jsonl").exists())
            self.assertTrue((tmp_root / "outputs/pacote5_policy_candidates.jsonl").exists())
            self.assertTrue((tmp_root / "reports/pacote5_policy_report.json").exists())
            self.assertTrue((tmp_root / "reports/pacote5_optimization_summary.json").exists())

    def test_exported_policy_never_selects_blocked_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            OfflineOptimizationController(self.cits, self.tsp, self._isolated_opt(tmp_root)).run()
            policy = json.loads((tmp_root / "reports/pacote5_policy_report.json").read_text(encoding="utf-8"))
            self.assertTrue(policy["safety_filter_required"])
            for item in policy["selected_decisions"]:
                self.assertNotEqual(item["safety_status"], "blocked_by_safety")

    def test_stateful_safety_paths_are_exercised(self) -> None:
        # M7: o optimizer offline deve exercitar o cooldown e
        # max_consecutive_priority_interventions; cenários com estado inicial
        # forçam o bloqueio destes caminhos com estado.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            OfflineOptimizationController(self.cits, self.tsp, self._isolated_opt(tmp_root)).run()
            policy = json.loads((tmp_root / "reports/pacote5_policy_report.json").read_text(encoding="utf-8"))
            candidate_log = (tmp_root / "outputs/pacote5_policy_candidates.jsonl").read_text(encoding="utf-8")
            self.assertIn("cooldown_after_priority_active", candidate_log)
            self.assertIn("max_consecutive_priority_interventions_reached", candidate_log)
            # Nenhum candidato selecionado pode ser desses cenários bloqueados.
            selected_by_scenario = {item["scenario_id"]: item for item in policy["selected_decisions"]}
            self.assertNotEqual(
                selected_by_scenario["P5_COOLDOWN_ACTIVE"]["safety_status"],
                "approved",
            )
            self.assertNotEqual(
                selected_by_scenario["P5_MAX_CONSECUTIVE_REACHED"]["safety_status"],
                "approved",
            )

    def test_exported_policy_keeps_baseline_guardrails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            OfflineOptimizationController(self.cits, self.tsp, self._isolated_opt(tmp_root)).run()
            policy = json.loads((tmp_root / "reports/pacote5_policy_report.json").read_text(encoding="utf-8"))
            selected = {item["scenario_id"]: item["action"] for item in policy["selected_decisions"]}
            self.assertEqual(selected["P5_NO_ACTION_GREEN_SUFFICIENT"], "no_action")
            self.assertEqual(selected["P5_REEVALUATE_TOO_CLOSE"], "reevaluate_next_cycle")
            self.assertEqual(selected["P5_REJECT_LOW_SCORE"], "reject")


if __name__ == "__main__":
    unittest.main()
