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
from pps57_cits.map_spat import build_spatem_message_from_state
from pps57_cits.messages import OperatorPriorityClass, synth_srem
from pps57_cits.models import SignalState
from pps57_opt.config import load_policy_optimization_config
from pps57_opt.dataset import build_offline_scenarios
from pps57_opt.event_dataset import (
    load_event_training_dataset,
    load_event_training_scenarios,
)
from pps57_opt.optimizer import OfflineOptimizationController
from pps57_opt.outcome_evaluator import evaluate_decision_outcomes
from pps57_tsp.action_planner import decision_for_action
from pps57_tsp.config import load_tsp_config
from pps57_tsp.models import TSPAction


class RewardShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cits = load_cits_config(ROOT / "configs/cits_v2x_config.json", root=ROOT)
        cls.tsp = load_tsp_config(ROOT / "configs/tsp_safety_config.json", root=ROOT)
        cls.opt = load_policy_optimization_config(
            ROOT / "configs/policy_training_config.json", root=ROOT
        )
        cls.controller = OfflineOptimizationController(
            cls.cits,
            cls.tsp,
            cls.opt,
            scenarios=build_offline_scenarios(cls.cits),
        )

    def _scenario(self, scenario_id: str):
        return next(
            item for item in build_offline_scenarios(self.cits) if item.scenario_id == scenario_id
        )

    def test_early_green_cost_scales_with_green_actually_truncated(self) -> None:
        # O custo do early_green deve crescer com o verde conflituante removido
        # (remaining - red_truncation_to_s), não ficar constante no valor-alvo
        # da truncagem.
        scenario = self._scenario("OPT_EARLY_GREEN_SAFE_RED")
        baseline = self.controller.engine.decide(
            scenario.request, scenario.signal_state, scenario.sim_time_s
        )
        decision = decision_for_action(
            self.tsp,
            action=TSPAction.EARLY_GREEN.value,
            baseline=baseline,
            reason="test_early_green",
            notes=[],
        )
        self.assertGreaterEqual(
            decision.priority_score,
            float(self.tsp.decision_policy.get("min_priority_score", 0.35)),
        )
        near_switch = replace(
            scenario,
            signal_state=replace(scenario.signal_state, next_switch_s=scenario.sim_time_s + 8.0),
        )
        far_switch = replace(
            scenario,
            signal_state=replace(scenario.signal_state, next_switch_s=scenario.sim_time_s + 35.0),
        )
        reward_near = self.controller._reward(near_switch, decision, "approved")
        reward_far = self.controller._reward(far_switch, decision, "approved")
        self.assertLess(reward_far, reward_near)
        traffic_penalty = float(self.opt.reward.get("general_traffic_penalty_per_second", 0.35))
        # Truncar de 35s para 2s corta 33s de verde; de 8s para 2s corta 6s.
        self.assertAlmostEqual(reward_near - reward_far, (33.0 - 6.0) * traffic_penalty, places=6)

    def test_green_extension_partial_service_does_not_get_full_benefit(self) -> None:
        # Uma extensão que não cobre o défice de verde (o bus chega depois do
        # fim do verde estendido) não pode receber o benefício completo.
        scenario = self._scenario("OPT_GREEN_EXTENSION_SHORT_GREEN")
        baseline = self.controller.engine.decide(
            scenario.request, scenario.signal_state, scenario.sim_time_s
        )
        extension = decision_for_action(
            self.tsp,
            action=TSPAction.GREEN_EXTENSION.value,
            baseline=baseline,
            reason="test_green_extension",
            notes=[],
        )
        self.assertGreaterEqual(
            extension.priority_score,
            float(self.tsp.decision_policy.get("min_priority_score", 0.35)),
        )
        buffer_s = float(self.tsp.decision_policy.get("eta_arrival_buffer_s", 4))
        remaining_s = float(scenario.signal_state.next_switch_s) - scenario.sim_time_s
        needed_s = scenario.request.eta_to_stopline_s + buffer_s - remaining_s
        self.assertGreater(needed_s, 0.0)

        reward_short = self.controller._reward(
            scenario, extension.copy_with(extension_s=needed_s / 4.0), "approved"
        )
        reward_full = self.controller._reward(
            scenario, extension.copy_with(extension_s=needed_s), "approved"
        )
        reward_over = self.controller._reward(
            scenario, extension.copy_with(extension_s=2.0 * needed_s), "approved"
        )

        # Cobrir o défice tem de valer mais do que poupar custo com uma
        # extensão insuficiente (antes a extensão curta ganhava sempre).
        self.assertGreater(reward_full, reward_short)
        # O sobre-serviço continua penalizado.
        self.assertGreater(reward_full, reward_over)


class EventDatasetLoadTests(unittest.TestCase):
    def _request_payload(self) -> dict:
        return synth_srem(
            sim_time_s=10.0,
            vehicle_id="bus_dataset",
            intersection_alias="I1",
            tls_id="I1",
            rsu_id="RSU_BOAVISTA_01",
            lane_id="CITY_EAST_I1_0",
            next_edge_id="I1_I2",
            operator_priority_class=OperatorPriorityClass.HIGH_DELAY.value,
            priority_movement_id="I1_westbound_public_transport",
            target_signal_group_id_hint="I1_priority_westbound",
        ).to_dict()

    def _signal_payload(self) -> dict:
        return build_spatem_message_from_state(
            SignalState(
                intersection_id="I1",
                tls_id="I1",
                rsu_id="RSU_BOAVISTA_01",
                timestamp_s=70.0,
                current_phase_index=2,
                current_program_id="program_1",
                red_yellow_green_state="GGrr",
                next_switch_s=80.0,
                spent_duration_s=4.0,
            )
        ).to_dict()

    def _row(self, decision_id: str, network_state: dict) -> dict:
        return {
            "decision_id": decision_id,
            "timestamp_s": 70.0,
            "tls_id": "I1",
            "action": "green_extension",
            "current_phase_index": 2,
            "current_program_id": "program_1",
            "request": self._request_payload(),
            "signal_state": self._signal_payload(),
            "network_state": network_state,
        }

    def _full_network_state(self) -> dict:
        return {
            "active_request_count": 1,
            "queue_vehicle_count": 3,
            "halted_vehicle_count": 1,
            "mean_speed_mps": 5.0,
            "waiting_time_s": 12.0,
            "occupancy": 0.2,
            "spillback_risk": False,
        }

    def test_rows_with_bad_network_state_are_skipped_and_counted(self) -> None:
        good = self._row("decision_good", self._full_network_state())
        missing_key = self._full_network_state()
        missing_key.pop("occupancy")
        bad_key = self._row("decision_missing_key", missing_key)
        bad_value = self._full_network_state()
        bad_value["queue_vehicle_count"] = "not_a_number"
        bad_value_row = self._row("decision_bad_value", bad_value)
        no_context_row = {"decision_id": "decision_no_context", "timestamp_s": 71.0, "tls_id": "I1"}

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "event_rows.jsonl"
            path.write_text(
                "\n".join(
                    json.dumps(row, sort_keys=True)
                    for row in [good, bad_key, bad_value_row, no_context_row]
                )
                + "\n",
                encoding="utf-8",
            )
            scenarios, report = load_event_training_dataset(path)
            compat_scenarios = load_event_training_scenarios(path)

        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0].scenario_id, "EVENT_decision_good")
        self.assertEqual(report["row_count"], 4)
        self.assertEqual(report["scenario_count"], 1)
        self.assertEqual(report["rows_skipped"], 3)
        self.assertEqual(
            report["rows_skipped_by_reason"],
            {
                "incomplete_network_state": 2,
                "missing_request_signal_or_network_state": 1,
            },
        )
        self.assertEqual(len(compat_scenarios), 1)

    def test_undecodable_request_is_skipped_and_counted(self) -> None:
        good = self._row("decision_good", self._full_network_state())
        broken = self._row("decision_broken_request", self._full_network_state())
        broken["request"] = {"message_type": "not_a_real_message"}

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "event_rows.jsonl"
            path.write_text(
                json.dumps(good, sort_keys=True) + "\n" + json.dumps(broken, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            scenarios, report = load_event_training_dataset(path)

        self.assertEqual(len(scenarios), 1)
        self.assertEqual(report["rows_skipped"], 1)
        self.assertEqual(
            report["rows_skipped_by_reason"],
            {"undecodable_request_or_signal_state": 1},
        )


class OutcomePairingTests(unittest.TestCase):
    def _decision(
        self,
        decision_id: str,
        action: str,
        status: str = "approved",
        request_id: str | None = None,
    ) -> dict:
        decision = {
            "decision_id": decision_id,
            "timestamp_s": 10.0,
            "vehicle_id": "bus_a",
            "tls_id": "I5",
            "action": action,
            "status": status,
            "reason": status,
        }
        if request_id is not None:
            decision["request_id"] = request_id
        return decision

    def test_duplicate_pairing_keys_are_kept_and_counted(self) -> None:
        baseline = [
            self._decision("b1", "green_extension"),
            self._decision("b2", "reevaluate_next_cycle"),
        ]
        rl = [
            self._decision("r1", "green_extension"),
            self._decision("r2", "reevaluate_next_cycle"),
        ]
        payload = evaluate_decision_outcomes(
            baseline_summary={},
            rl_summary={},
            baseline_decisions=baseline,
            baseline_actuations=[],
            rl_decisions=rl,
            rl_actuations=[],
        )
        self.assertEqual(payload["decision_count"], 2)
        self.assertEqual(payload["matched_decision_count"], 2)
        self.assertEqual(payload["pairing_key_collisions"], {"baseline": 1, "rl": 1})
        pairs = {(row["baseline_decision_id"], row["rl_decision_id"]) for row in payload["rows"]}
        self.assertEqual(pairs, {("b1", "r1"), ("b2", "r2")})

    def test_unbalanced_duplicates_are_reported_as_missing(self) -> None:
        baseline = [
            self._decision("b1", "green_extension"),
            self._decision("b2", "reevaluate_next_cycle"),
        ]
        rl = [self._decision("r1", "green_extension")]
        payload = evaluate_decision_outcomes(
            baseline_summary={},
            rl_summary={},
            baseline_decisions=baseline,
            baseline_actuations=[],
            rl_decisions=rl,
            rl_actuations=[],
        )
        self.assertEqual(payload["decision_count"], 2)
        self.assertEqual(payload["matched_decision_count"], 1)
        self.assertEqual(payload["missing_rl_count"], 1)
        self.assertEqual(payload["pairing_key_collisions"], {"baseline": 1, "rl": 0})
        verdicts = sorted(str(row["verdict"]) for row in payload["rows"])
        self.assertIn("missing_rl", verdicts)

    def test_collision_pairs_by_request_id_regardless_of_log_order(self) -> None:
        # Issue #48: a mesma chave (timestamp, veículo, TLS) tem duas decisões;
        # o RL regista-as por ordem inversa do baseline. O emparelhamento tem de
        # seguir o request_id estável, não a posição no log.
        baseline = [
            self._decision("b1", "green_extension", request_id="sta:1:7"),
            self._decision("b2", "reevaluate_next_cycle", request_id="sta:2:9"),
        ]
        rl = [
            self._decision("r2", "reevaluate_next_cycle", request_id="sta:2:9"),
            self._decision("r1", "green_extension", request_id="sta:1:7"),
        ]
        payload = evaluate_decision_outcomes(
            baseline_summary={},
            rl_summary={},
            baseline_decisions=baseline,
            baseline_actuations=[],
            rl_decisions=rl,
            rl_actuations=[],
        )
        self.assertEqual(payload["matched_decision_count"], 2)
        self.assertEqual(payload["missing_baseline_count"], 0)
        self.assertEqual(payload["missing_rl_count"], 0)
        pairs = {(row["baseline_decision_id"], row["rl_decision_id"]) for row in payload["rows"]}
        # Posicional emparelharia (b1,r2)/(b2,r1) — errado. Por request_id: (b1,r1)/(b2,r2).
        self.assertEqual(pairs, {("b1", "r1"), ("b2", "r2")})

    def test_collision_with_disjoint_request_ids_is_reported_as_missing(self) -> None:
        # Decisões na mesma chave mas com request_ids diferentes são pedidos
        # lógicos distintos: não devem ser falsamente emparelhados.
        baseline = [self._decision("b1", "green_extension", request_id="sta:1:7")]
        rl = [self._decision("r1", "green_extension", request_id="sta:9:3")]
        payload = evaluate_decision_outcomes(
            baseline_summary={},
            rl_summary={},
            baseline_decisions=baseline,
            baseline_actuations=[],
            rl_decisions=rl,
            rl_actuations=[],
        )
        self.assertEqual(payload["matched_decision_count"], 0)
        self.assertEqual(payload["missing_baseline_count"], 1)
        self.assertEqual(payload["missing_rl_count"], 1)
        verdicts = sorted(str(row["verdict"]) for row in payload["rows"])
        self.assertEqual(verdicts, ["missing_baseline", "missing_rl"])


if __name__ == "__main__":
    unittest.main()
