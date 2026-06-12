#!/usr/bin/env python3
"""Testes do engine v2: prioridade condicional e decisão cost-aware.

O portão de necessidade básico (bus a horas -> reject) vive em
test_tsp_safety_layer; aqui cobrem-se os caminhos que dependem do
NetworkStateSnapshot e da truncagem proporcional/recuperabilidade.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config
from pps57_cits.messages import OperatorPriorityClass, synth_srem
from pps57_cits.models import NetworkStateSnapshot, SignalState
from pps57_tsp.config import load_tsp_config
from pps57_tsp.engine import TSPDecisionEngine
from pps57_tsp.models import TSPAction


class EngineV2CostAwareTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cits = load_cits_config(ROOT / "configs/cits_v2x_config.json", root=ROOT)
        cls.tsp = load_tsp_config(ROOT / "configs/tsp_safety_config.json", root=ROOT)
        cls.engine = TSPDecisionEngine(cls.cits, cls.tsp)

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------
    def _request_i2(self, **overrides):
        defaults = dict(
            sim_time_s=100.0,
            vehicle_id="bus_1",
            intersection_alias="I2",
            tls_id="I2",
            rsu_id="RSU_BOAVISTA_02",
            lane_id="I1_I2_0",
            line_id="STCP500_PROXY_W",
            route_id="route_boavista_east_to_west",
            speed_mps=10.0,
            distance_to_stopline_m=160.0,
            eta_to_stopline_s=16.0,
            schedule_delay_s=120.0,
            headway_deviation_s=0.0,
            operator_priority_class=OperatorPriorityClass.HIGH_DELAY.value,
            priority_movement_id="I2_westbound_public_transport",
            target_signal_group_id_hint="I2_priority_westbound",
            expires_at_s=130.0,
            ttl_s=30.0,
        )
        defaults.update(overrides)
        return synth_srem(**defaults)

    def _request_i7(self, **overrides):
        return self._request_i2(
            intersection_alias="I7",
            tls_id="I7",
            rsu_id="RSU_BOAVISTA_07",
            lane_id="ATLANTIC_WEST_I7_0",
            priority_movement_id="I7_eastbound_public_transport",
            target_signal_group_id_hint="I7_priority_eastbound",
            **overrides,
        )

    def _state_i2_green(self, **overrides):
        payload = dict(
            intersection_id="I2",
            tls_id="I2",
            rsu_id="RSU_BOAVISTA_02",
            timestamp_s=100.0,
            current_phase_index=0,
            current_program_id="test",
            red_yellow_green_state="GGrr",
            next_switch_s=102.0,
            spent_duration_s=33.0,
            controlled_lanes=["I1_I2_0", "I3_I2_0", "N_I2_I2_0", "S_I2_I2_0"],
        )
        payload.update(overrides)
        return SignalState(**payload)

    def _state_i7_red(self, **overrides):
        payload = dict(
            intersection_id="I7",
            tls_id="I7",
            rsu_id="RSU_BOAVISTA_07",
            timestamp_s=100.0,
            current_phase_index=2,
            current_program_id="test",
            red_yellow_green_state="rrGG",
            next_switch_s=125.0,
            spent_duration_s=20.0,
            controlled_lanes=["I6_I7_0", "ATLANTIC_WEST_I7_0", "N_I7_I7_0", "S_I7_I7_0"],
        )
        payload.update(overrides)
        return SignalState(**payload)

    def _snapshot(self, tls_id="I7", **overrides):
        payload = dict(
            tls_id=tls_id,
            timestamp_s=100.0,
            occupancy=0.1,
            spillback_risk=False,
            halted_by_lane={},
        )
        payload.update(overrides)
        return NetworkStateSnapshot(**payload)

    # ------------------------------------------------------------------
    # Limiar de score sensível a congestão
    # ------------------------------------------------------------------
    def test_congested_occupancy_raises_min_score(self) -> None:
        # delay=60 -> score ~0.35: passa o limiar base (0.2) mas não o limiar
        # congestionado (0.5).
        request = self._request_i2(schedule_delay_s=60.0)
        congested = self._snapshot(tls_id="I2", occupancy=0.6)
        decision = self.engine.decide(
            request, self._state_i2_green(), sim_time_s=100.0, network_state=congested
        )
        self.assertEqual(decision.action, TSPAction.REJECT.value)
        self.assertIn("priority_score_below_threshold", decision.reason)

        free_flow = self._snapshot(tls_id="I2", occupancy=0.1)
        decision = self.engine.decide(
            request, self._state_i2_green(), sim_time_s=100.0, network_state=free_flow
        )
        self.assertNotEqual(decision.action, TSPAction.REJECT.value)

    # ------------------------------------------------------------------
    # Pressão de rede -> diferir early green
    # ------------------------------------------------------------------
    def test_spillback_risk_defers_early_green(self) -> None:
        snapshot = self._snapshot(spillback_risk=True)
        decision = self.engine.decide(
            self._request_i7(), self._state_i7_red(), sim_time_s=100.0, network_state=snapshot
        )
        self.assertEqual(decision.action, TSPAction.REEVALUATE_NEXT_CYCLE.value)
        self.assertIn("network_pressure_defer_intervention", decision.reason)
        self.assertIn("spillback_risk", decision.reason)

    def test_cross_pressure_defers_early_green(self) -> None:
        snapshot = self._snapshot(halted_by_lane={"N_I7_I7_0": 9})
        decision = self.engine.decide(
            self._request_i7(), self._state_i7_red(), sim_time_s=100.0, network_state=snapshot
        )
        self.assertEqual(decision.action, TSPAction.REEVALUATE_NEXT_CYCLE.value)
        self.assertIn("cross_halted_9>=8", decision.reason)

    def test_cross_pressure_ignores_bus_approach_and_internal_lanes(self) -> None:
        # 5 parados na própria aproximação do bus + 7 em lane interna de junção
        # não contam; só os 3 da transversal -> abaixo do limiar 8 -> early green.
        snapshot = self._snapshot(
            halted_by_lane={"ATLANTIC_WEST_I7_0": 5, ":I7_w0_0": 7, "N_I7_I7_0": 3}
        )
        decision = self.engine.decide(
            self._request_i7(), self._state_i7_red(), sim_time_s=100.0, network_state=snapshot
        )
        self.assertEqual(decision.action, TSPAction.EARLY_GREEN.value)

    # ------------------------------------------------------------------
    # Extensão de verde: tecto reduzido sob pressão transversal
    # ------------------------------------------------------------------
    def test_extension_capped_under_cross_pressure(self) -> None:
        request = self._request_i2(eta_to_stopline_s=40.0)
        snapshot = self._snapshot(tls_id="I2", halted_by_lane={"N_I2_I2_0": 9})
        decision = self.engine.decide(
            request,
            self._state_i2_green(next_switch_s=101.0),
            sim_time_s=100.0,
            network_state=snapshot,
        )
        self.assertEqual(decision.action, TSPAction.GREEN_EXTENSION.value)
        self.assertLessEqual(decision.extension_s, 6.0)

        decision_free = self.engine.decide(
            request, self._state_i2_green(next_switch_s=101.0), sim_time_s=100.0
        )
        self.assertEqual(decision_free.action, TSPAction.GREEN_EXTENSION.value)
        self.assertGreater(decision_free.extension_s, 6.0)

    # ------------------------------------------------------------------
    # v2.1: dial congestionado (necessidade endurecida, score alcançável)
    # ------------------------------------------------------------------
    def test_congested_need_gate_requires_more_delay(self) -> None:
        # delay=25 passa a necessidade base (20s) mas não a congestionada (35s).
        request = self._request_i2(schedule_delay_s=25.0)
        congested = self._snapshot(tls_id="I2", occupancy=0.6)
        decision = self.engine.decide(
            request, self._state_i2_green(), sim_time_s=100.0, network_state=congested
        )
        self.assertEqual(decision.action, TSPAction.REJECT.value)
        self.assertIn("priority_need_not_met", decision.reason)

        free_flow = self._snapshot(tls_id="I2", occupancy=0.1)
        decision = self.engine.decide(
            request, self._state_i2_green(), sim_time_s=100.0, network_state=free_flow
        )
        self.assertNotIn("priority_need_not_met", decision.reason)

    def test_congested_dial_is_reachable_with_material_delay(self) -> None:
        # Ao contrário do score-cliff 0.5 (inalcançável no regime proxy), o
        # dial 0.4 deixa passar um autocarro com atraso material (100s).
        request = self._request_i2(schedule_delay_s=100.0)
        congested = self._snapshot(tls_id="I2", occupancy=0.6)
        decision = self.engine.decide(
            request, self._state_i2_green(), sim_time_s=100.0, network_state=congested
        )
        self.assertNotEqual(decision.action, TSPAction.REJECT.value)

    # ------------------------------------------------------------------
    # v2.1: vítimas por máscara RYG
    # ------------------------------------------------------------------
    def test_extension_ignores_pressure_on_green_sharing_lanes(self) -> None:
        # I3_I2_0 partilha o verde (RYG "GGrr") — congestão aí não paga a
        # extensão, por isso o tecto não encolhe.
        request = self._request_i2(eta_to_stopline_s=40.0)
        snapshot = self._snapshot(tls_id="I2", halted_by_lane={"I3_I2_0": 9})
        decision = self.engine.decide(
            request,
            self._state_i2_green(next_switch_s=101.0),
            sim_time_s=100.0,
            network_state=snapshot,
        )
        self.assertEqual(decision.action, TSPAction.GREEN_EXTENSION.value)
        self.assertGreater(decision.extension_s, 6.0)

    def test_early_green_ignores_pressure_on_red_lanes(self) -> None:
        # I6_I7_0 está em vermelho (RYG "rrGG") — a sua fila não é vítima da
        # truncagem da fase verde corrente, por isso não defere o early green.
        snapshot = self._snapshot(halted_by_lane={"I6_I7_0": 9})
        decision = self.engine.decide(
            self._request_i7(), self._state_i7_red(), sim_time_s=100.0, network_state=snapshot
        )
        self.assertEqual(decision.action, TSPAction.EARLY_GREEN.value)

    # ------------------------------------------------------------------
    # Recuperabilidade: truncagem com poupança marginal não vale o custo
    # ------------------------------------------------------------------
    def test_marginal_saving_defers_early_green(self) -> None:
        # remaining 6s: truncar para max(2, 6-10)=2 só devolve 4s (<5) ao TP.
        decision = self.engine.decide(
            self._request_i7(), self._state_i7_red(next_switch_s=106.0), sim_time_s=100.0
        )
        self.assertEqual(decision.action, TSPAction.REEVALUATE_NEXT_CYCLE.value)
        self.assertIn("intervention_benefit_too_small", decision.reason)

    # ------------------------------------------------------------------
    # Emergência: hierarquia própria, sem portões de necessidade/pressão
    # ------------------------------------------------------------------
    def test_emergency_bypasses_need_and_network_pressure_gates(self) -> None:
        request = self._request_i7(
            vehicle_id="ev_1",
            schedule_delay_s=0.0,
            operator_priority_class=OperatorPriorityClass.EMERGENCY.value,
        )
        snapshot = self._snapshot(spillback_risk=True, occupancy=0.9)
        decision = self.engine.decide(
            request, self._state_i7_red(), sim_time_s=100.0, network_state=snapshot
        )
        self.assertNotIn("priority_need_not_met", decision.reason)
        self.assertNotIn("network_pressure_defer_intervention", decision.reason)
        self.assertEqual(decision.action, TSPAction.EARLY_GREEN.value)


if __name__ == "__main__":
    unittest.main()
