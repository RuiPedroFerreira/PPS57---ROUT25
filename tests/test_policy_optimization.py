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
from pps57_cits.models import NetworkStateSnapshot
from pps57_opt.ab_compare import write_tsp_ab_comparison
from pps57_opt.config import load_policy_optimization_config
from pps57_opt.dataset import build_offline_scenarios
from pps57_opt.optimizer import OfflineOptimizationController
from pps57_opt.outcome_evaluator import write_decision_outcome_evaluation
from pps57_opt.policy_runtime import RuntimePolicy
from pps57_opt.rl_trainer import TabularQLearningController
from pps57_tsp.engine import TSPDecisionEngine
from pps57_tsp.config import load_tsp_config
from pps57_tsp.controller import TSPControlController


class PolicyOptimizationTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cits = load_cits_config(ROOT / "configs/cits_v2x_config.json", root=ROOT)
        cls.tsp = load_tsp_config(ROOT / "configs/tsp_safety_config.json", root=ROOT)
        cls.opt = load_policy_optimization_config(ROOT / "configs/policy_training_config.json", root=ROOT)

    def _isolated_opt(self, tmp_root: Path):
        return replace(self.opt, root=tmp_root)

    def _isolated_cits(self, tmp_root: Path):
        return replace(self.cits, root=tmp_root)

    def _isolated_tsp(self, tmp_root: Path):
        return replace(self.tsp, root=tmp_root)

    def _unit_scenarios(self):
        return build_offline_scenarios(self.cits)

    def test_offline_optimization_exports_safe_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            summary = OfflineOptimizationController(
                self.cits,
                self.tsp,
                self._isolated_opt(tmp_root),
                scenarios=self._unit_scenarios(),
            ).run()
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
            OfflineOptimizationController(
                self.cits,
                self.tsp,
                self._isolated_opt(tmp_root),
                scenarios=self._unit_scenarios(),
            ).run()
            policy = json.loads((tmp_root / "reports/policy_report.json").read_text(encoding="utf-8"))
            self.assertTrue(policy["safety_filter_required"])
            for item in policy["selected_decisions"]:
                self.assertNotEqual(item["safety_status"], "blocked_by_safety")

    def test_stateful_safety_paths_are_exercised(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            OfflineOptimizationController(
                self.cits,
                self.tsp,
                self._isolated_opt(tmp_root),
                scenarios=self._unit_scenarios(),
            ).run()
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
            OfflineOptimizationController(
                self.cits,
                self.tsp,
                self._isolated_opt(tmp_root),
                scenarios=self._unit_scenarios(),
            ).run()
            policy = json.loads((tmp_root / "reports/policy_report.json").read_text(encoding="utf-8"))
            selected = {item["scenario_id"]: item["action"] for item in policy["selected_decisions"]}
            self.assertEqual(selected["OPT_NO_ACTION_GREEN_SUFFICIENT"], "no_action")
            self.assertEqual(selected["OPT_REEVALUATE_TOO_CLOSE"], "reevaluate_next_cycle")
            self.assertEqual(selected["OPT_REJECT_LOW_SCORE"], "reject")

    def test_runtime_policy_loads_exported_policy_for_online_inference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            OfflineOptimizationController(
                self.cits,
                self.tsp,
                self._isolated_opt(tmp_root),
                scenarios=self._unit_scenarios(),
            ).run()
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
            summary = TabularQLearningController(
                self.cits,
                self.tsp,
                self._isolated_opt(tmp_root),
                scenarios=self._unit_scenarios(),
            ).run()
            self.assertEqual(summary["mode"], "tabular-q-learning")
            self.assertTrue(summary["is_reinforcement_learning"])
            self.assertFalse(summary["online_learning_in_production"])
            self.assertGreater(summary["learned_rule_count"], 0)
            self.assertTrue((tmp_root / "reports/tabular_q_policy_report.json").exists())
            self.assertTrue((tmp_root / "reports/rl_training_summary.json").exists())
            policy = json.loads((tmp_root / "reports/tabular_q_policy_report.json").read_text(encoding="utf-8"))
            pressure_rule = next(
                item for item in policy["rules"]
                if item["source_scenario_id"] == "OPT_HIGH_TRAFFIC_PRESSURE_REEVALUATE"
            )
            self.assertEqual(pressure_rule["action"], "reevaluate_next_cycle")
            self.assertIn("traffic_pressure_high", pressure_rule["state_bucket"])

    def test_tabular_q_learning_summary_declares_effective_algorithm_and_decayed_epsilon(self) -> None:
        # Honestidade: o "Q-learning" com gamma=0 e sem transições é, na prática,
        # um bandit contextual com epsilon-greedy. O summary deve declarar isso
        # *e* deve haver evidência de que o loop de episódios fez algo (epsilon
        # decaiu abaixo do start).
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            summary = TabularQLearningController(
                self.cits,
                self.tsp,
                self._isolated_opt(tmp_root),
                scenarios=self._unit_scenarios(),
            ).run()
            self.assertEqual(
                summary["effective_algorithm"],
                "tabular_contextual_bandit_epsilon_greedy",
            )
            self.assertLess(summary["final_epsilon"], summary["epsilon_start"])
            self.assertGreaterEqual(
                summary["final_epsilon"],
                self.opt.reinforcement_learning.get("epsilon_min", 0.02),
            )

    def test_tabular_q_learning_source_scenario_actually_produced_rule(self) -> None:
        # Antes, `_source_scenario_for_state` devolvia o PRIMEIRO scenario com
        # aquele state bucket, podendo não ser o que produziu o reward escolhido.
        # Agora o trainer regista o scenario que efectivamente contribuiu para a
        # célula (state, action). Esta verificação cruza source_scenario_id <-> bucket.
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            scenarios = self._unit_scenarios()
            controller = TabularQLearningController(
                self.cits,
                self.tsp,
                self._isolated_opt(tmp_root),
                scenarios=scenarios,
            )
            controller.run()
            policy = json.loads((tmp_root / "reports/tabular_q_policy_report.json").read_text(encoding="utf-8"))
            scenario_by_id = {s.scenario_id: s for s in scenarios}
            for rule in policy["rules"]:
                source_id = rule["source_scenario_id"]
                self.assertTrue(source_id, msg=f"rule sem source_scenario_id: {rule}")
                self.assertIn(source_id, scenario_by_id, msg=f"source_scenario_id inválido: {source_id}")
                actual_bucket = controller.optimizer._state_bucket(scenario_by_id[source_id])
                self.assertEqual(
                    actual_bucket,
                    rule["state_bucket"],
                    msg=f"source scenario {source_id} produces bucket {actual_bucket} but rule claims {rule['state_bucket']}",
                )

    def test_runtime_policy_intervention_axis_changes_state_bucket(self) -> None:
        # O eixo intervention_* estava efectivamente morto porque o controller
        # nunca passava `seconds_since_last_intervention_s` ao runtime, colapsando
        # sempre em `intervention_unknown`. Agora a inferência deve discriminar.
        from pps57_opt.policy_runtime import state_bucket_for

        scenario = next(s for s in self._unit_scenarios() if s.scenario_id == "OPT_NO_ACTION_GREEN_SUFFICIENT")
        bucket_unknown = state_bucket_for(
            self.tsp,
            scenario.request,
            scenario.signal_state,
            scenario.sim_time_s,
        )
        bucket_recent = state_bucket_for(
            self.tsp,
            scenario.request,
            scenario.signal_state,
            scenario.sim_time_s,
            seconds_since_last_intervention_s=5.0,
        )
        bucket_clear = state_bucket_for(
            self.tsp,
            scenario.request,
            scenario.signal_state,
            scenario.sim_time_s,
            seconds_since_last_intervention_s=600.0,
        )
        self.assertIn("intervention_unknown", bucket_unknown)
        self.assertIn("intervention_recent", bucket_recent)
        self.assertIn("intervention_clear", bucket_clear)
        self.assertNotEqual(bucket_unknown, bucket_recent)
        self.assertNotEqual(bucket_recent, bucket_clear)

    def test_tsp_controller_loads_exported_rl_policy_for_runtime_inference_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            TabularQLearningController(
                self.cits,
                self.tsp,
                self._isolated_opt(tmp_root),
                scenarios=self._unit_scenarios(),
            ).run()
            controller = TSPControlController(
                self.cits,
                self.tsp,
                policy_mode="rl",
                policy_report_path=str(tmp_root / "reports/tabular_q_policy_report.json"),
            )

            self.assertIsNotNone(controller.runtime_policy)
            assert controller.runtime_policy is not None
            self.assertTrue(controller.runtime_policy.is_reinforcement_learning)
            self.assertEqual(controller.runtime_policy.algorithm, "tabular_q_learning")
            self.assertEqual(controller.runtime_policy.training_environment, "event_derived_sumo_traci_scenarios")
            self.assertTrue(controller.runtime_policy.safety_filter_required)

    def test_runtime_policy_uses_sumo_network_snapshot_features(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            TabularQLearningController(
                self.cits,
                self.tsp,
                self._isolated_opt(tmp_root),
                scenarios=self._unit_scenarios(),
            ).run()
            # O guard `allow_policy_suppress_baseline_actuation` (default false)
            # impede que uma regra não-atuante (reevaluate) suprima a atuação
            # baseline. Para validar que o snapshot de rede de facto seleciona o
            # bucket traffic_pressure_high -> reevaluate, habilitamos a supressão.
            tsp_suppress = replace(
                self.tsp,
                raw={
                    **json.loads(json.dumps(self.tsp.raw)),
                    "policy_runtime": {
                        **self.tsp.raw.get("policy_runtime", {}),
                        "allow_policy_suppress_baseline_actuation": True,
                    },
                },
            )
            runtime_policy = RuntimePolicy.load(tsp_suppress, tmp_root / "reports/tabular_q_policy_report.json")
            scenario = next(
                item for item in build_offline_scenarios(self.cits)
                if item.scenario_id == "OPT_HIGH_TRAFFIC_PRESSURE_REEVALUATE"
            )
            baseline = TSPDecisionEngine(self.cits, self.tsp).decide(
                scenario.request,
                scenario.signal_state,
                scenario.sim_time_s,
            )
            self.assertEqual(baseline.action, "green_extension")

            decision = runtime_policy.decide(
                scenario.request,
                scenario.signal_state,
                scenario.sim_time_s,
                baseline,
                network_state=NetworkStateSnapshot(
                    tls_id=scenario.signal_state.tls_id,
                    timestamp_s=scenario.sim_time_s,
                    active_request_count=3,
                    lane_count=4,
                    vehicle_count=16,
                    queue_vehicle_count=14,
                    halted_vehicle_count=10,
                    mean_speed_mps=1.2,
                    waiting_time_s=180.0,
                    occupancy=0.82,
                    spillback_risk=True,
                ),
            )
            self.assertEqual(decision.action, "reevaluate_next_cycle")
            self.assertIn("traffic_pressure_high", " ".join(decision.notes))

    def test_ab_comparison_baseline_vs_rl_runtime_keeps_training_outside_engine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            baseline_root = tmp_root / "baseline"
            rl_root = tmp_root / "rl"
            baseline_summary = _summary(
                policy_mode="baseline",
                by_action={"green_extension": 2},
                by_status={"approved": 1, "blocked_by_safety": 1},
                applied_events=1,
                blocked_by_safety=1,
                runtime_policy_loaded=False,
            )
            rl_summary = _summary(
                policy_mode="rl",
                by_action={"green_extension": 1, "reevaluate_next_cycle": 1},
                by_status={"approved": 1, "not_actuable": 1},
                applied_events=1,
                blocked_by_safety=0,
                runtime_policy_loaded=True,
                runtime_policy={
                    "is_reinforcement_learning": True,
                    "algorithm": "tabular_q_learning",
                    "source_path": "reports/tabular_q_policy_report.json",
                },
            )
            _write_jsonl(
                baseline_root / "outputs/tsp_decisions.jsonl",
                [
                    _decision("d1", 10.0, "bus_a", "I5", "green_extension", "approved"),
                    _decision("d2", 15.0, "bus_b", "I5", "green_extension", "blocked_by_safety"),
                ],
            )
            _write_jsonl(
                rl_root / "outputs/tsp_decisions.jsonl",
                [
                    _decision("r1", 10.0, "bus_a", "I5", "green_extension", "approved"),
                    _decision("r2", 15.0, "bus_b", "I5", "reevaluate_next_cycle", "not_actuable"),
                ],
            )
            _write_jsonl(
                baseline_root / "outputs/tsp_actuation.jsonl",
                [_actuation("d1", True), _actuation("d2", False)],
            )
            _write_jsonl(
                rl_root / "outputs/tsp_actuation.jsonl",
                [_actuation("r1", True), _actuation("r2", False)],
            )
            baseline_decisions = _read_jsonl(baseline_root / "outputs/tsp_decisions.jsonl")
            rl_decisions = _read_jsonl(rl_root / "outputs/tsp_decisions.jsonl")
            rl_actuations = _read_jsonl(rl_root / "outputs/tsp_actuation.jsonl")

            self.assertFalse(baseline_summary["runtime_policy_loaded"])
            self.assertTrue(rl_summary["runtime_policy_loaded"])
            self.assertTrue(rl_summary["runtime_policy"]["is_reinforcement_learning"])
            self.assertEqual(rl_summary["runtime_policy"]["algorithm"], "tabular_q_learning")
            self.assertEqual(rl_summary["runtime_policy"]["source_path"], "reports/tabular_q_policy_report.json")

            self.assertGreater(baseline_summary["total_decisions"], 0)
            self.assertEqual(baseline_summary["total_decisions"], rl_summary["total_decisions"])
            self.assertEqual(len(baseline_decisions), len(rl_decisions))
            self.assertEqual(
                baseline_summary["cits_acknowledged_messages"],
                rl_summary["cits_acknowledged_messages"],
            )

            comparison = write_tsp_ab_comparison(
                baseline_summary,
                rl_summary,
                json_path=tmp_root / "ab_comparison_summary.json",
                markdown_path=tmp_root / "ab_comparison_summary.md",
            )
            comparison_metrics = {str(row["metric"]) for row in comparison["rows"]}
            comparison_by_metric = {str(row["metric"]): row for row in comparison["rows"]}
            self.assertIn("action:green_extension", comparison_metrics)
            self.assertLess(comparison_by_metric["action:green_extension"]["delta"], 0)
            self.assertGreater(comparison_by_metric["action:reevaluate_next_cycle"]["delta"], 0)
            self.assertIn("rl_algorithm", comparison_metrics)
            self.assertIn("| Metric | Baseline | RL | Delta RL-Baseline |", (tmp_root / "ab_comparison_summary.md").read_text(encoding="utf-8"))
            self.assertEqual(
                rl_summary["applied_events"],
                sum(1 for item in rl_actuations if item["applied"]),
            )

            outcome = write_decision_outcome_evaluation(
                baseline_summary=baseline_summary,
                rl_summary=rl_summary,
                baseline_decision_log=baseline_root / "outputs/tsp_decisions.jsonl",
                baseline_actuation_log=baseline_root / "outputs/tsp_actuation.jsonl",
                rl_decision_log=rl_root / "outputs/tsp_decisions.jsonl",
                rl_actuation_log=rl_root / "outputs/tsp_actuation.jsonl",
                json_path=tmp_root / "decision_outcome_evaluation.json",
                markdown_path=tmp_root / "decision_outcome_evaluation.md",
            )
            self.assertEqual(outcome["matched_decision_count"], baseline_summary["total_decisions"])
            self.assertEqual(outcome["network_impact_verdict"], "inconclusive_without_kpis")
            self.assertGreater(outcome["verdict_counts"].get("safer_or_less_intrusive", 0), 0)
            self.assertTrue(
                any(
                    row["baseline_action"] == "green_extension"
                    and row["rl_action"] == "reevaluate_next_cycle"
                    and row["verdict"] == "safer_or_less_intrusive"
                    for row in outcome["rows"]
                )
            )
            self.assertIn(
                "Decision Outcome Evaluation",
                (tmp_root / "decision_outcome_evaluation.md").read_text(encoding="utf-8"),
            )


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")


def _summary(
    *,
    policy_mode: str,
    by_action: dict[str, int],
    by_status: dict[str, int],
    applied_events: int,
    blocked_by_safety: int,
    runtime_policy_loaded: bool,
    runtime_policy: dict[str, object] | None = None,
) -> dict[str, object]:
    total = sum(by_action.values())
    return {
        "policy_mode": policy_mode,
        "total_decisions": total,
        "cits_acknowledged_messages": total,
        "actuation_events": total,
        "applied_events": applied_events,
        "blocked_by_safety": blocked_by_safety,
        "by_action": by_action,
        "by_status": by_status,
        "runtime_policy_loaded": runtime_policy_loaded,
        "runtime_policy": runtime_policy or {"loaded": runtime_policy_loaded},
    }


def _decision(
    decision_id: str,
    timestamp_s: float,
    vehicle_id: str,
    tls_id: str,
    action: str,
    status: str,
) -> dict[str, object]:
    return {
        "decision_id": decision_id,
        "timestamp_s": timestamp_s,
        "vehicle_id": vehicle_id,
        "tls_id": tls_id,
        "action": action,
        "status": status,
        "reason": status,
    }


def _actuation(decision_id: str, applied: bool) -> dict[str, object]:
    return {
        "decision_id": decision_id,
        "timestamp_s": 0.0,
        "tls_id": "I5",
        "action": "green_extension",
        "applied": applied,
        "no_actuation": False,
        "command": "trafficlight.setPhaseDuration" if applied else "none",
        "reason": "test",
    }


if __name__ == "__main__":
    unittest.main()
