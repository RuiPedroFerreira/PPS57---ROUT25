#!/usr/bin/env python3
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config
from pps57_cits.messages import PriorityLevel, RequestStatus, RequestedManeuver, SREMLike, SSEMLike
from pps57_cits.models import SignalState
from pps57_tsp.actuator import DryRunTSPActuator, TraciTSPActuator
from pps57_tsp.config import TSPConfig, load_tsp_config
from pps57_tsp.controller import TSPControlController
from pps57_tsp.engine import TSPDecisionEngine
from pps57_tsp.models import DecisionStatus, TSPAction
from pps57_tsp.safety import TSPSafetyLayer


class Package4TSPTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cits = load_cits_config(ROOT / "configs/cits_config.json", root=ROOT)
        cls.tsp = load_tsp_config(ROOT / "configs/tsp_config.json", root=ROOT)
        cls.engine = TSPDecisionEngine(cls.cits, cls.tsp)

    def _request(self, **overrides):
        payload = dict(
            source_id="OBU_bus_1",
            destination_id="RSU_BOAVISTA_02",
            timestamp_s=100.0,
            vehicle_id="bus_1",
            vehicle_class="bus",
            line_id="STCP500_PROXY_W",
            route_id="route_boavista_east_to_west",
            intersection_id="I2",
            tls_id="I2",
            rsu_id="RSU_BOAVISTA_02",
            current_edge_id="I1_I2",
            current_lane_id="I1_I2_0",
            speed_mps=10.0,
            distance_to_stopline_m=160.0,
            eta_to_stopline_s=16.0,
            schedule_delay_s=120.0,
            headway_deviation_s=0.0,
            requested_maneuver=RequestedManeuver.GREEN_EXTENSION.value,
            priority_level=PriorityLevel.PUBLIC_TRANSPORT_HIGH_DELAY.value,
            expires_at_s=130.0,
        )
        payload.update(overrides)
        return SREMLike(**payload)

    def _state(self, **overrides):
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

    def test_engine_proposes_green_extension_when_green_is_short(self) -> None:
        request = self._request()
        decision = self.engine.decide(request, self._state(), sim_time_s=100.0)
        self.assertEqual(decision.action, TSPAction.GREEN_EXTENSION.value)
        self.assertGreater(decision.extension_s, 0)

    def test_engine_proposes_no_action_when_green_is_sufficient(self) -> None:
        request = self._request(eta_to_stopline_s=10.0)
        decision = self.engine.decide(request, self._state(next_switch_s=140.0), sim_time_s=100.0)
        self.assertEqual(decision.action, TSPAction.NO_ACTION.value)

    def test_engine_rejects_low_score_request(self) -> None:
        request = self._request(
            schedule_delay_s=0.0,
            headway_deviation_s=0.0,
            distance_to_stopline_m=250.0,
            priority_level=PriorityLevel.PUBLIC_TRANSPORT_NOMINAL.value,
        )
        decision = self.engine.decide(request, self._state(), sim_time_s=100.0)
        self.assertEqual(decision.action, TSPAction.REJECT.value)
        self.assertIn("priority_score_below_threshold", decision.reason)

    def test_engine_rejects_expired_request(self) -> None:
        request = self._request(expires_at_s=99.0)
        decision = self.engine.decide(request, self._state(), sim_time_s=100.0)
        self.assertEqual(decision.action, TSPAction.REJECT.value)
        self.assertEqual(decision.reason, "request_expired_before_tsp_decision")

    def test_engine_rejects_request_expired_at_zero_timestamp(self) -> None:
        request = self._request(expires_at_s=0.0)
        decision = self.engine.decide(request, self._state(), sim_time_s=1.0)
        self.assertEqual(decision.action, TSPAction.REJECT.value)
        self.assertEqual(decision.reason, "request_expired_before_tsp_decision")

    def test_engine_proposes_early_green_when_priority_movement_is_red(self) -> None:
        request = self._request(
            destination_id="RSU_BOAVISTA_06",
            intersection_id="I6",
            tls_id="I6",
            rsu_id="RSU_BOAVISTA_06",
            current_edge_id="I7_I6",
            current_lane_id="I7_I6_0",
            requested_maneuver=RequestedManeuver.EARLY_GREEN.value,
        )
        state = SignalState(
            intersection_id="I6",
            tls_id="I6",
            rsu_id="RSU_BOAVISTA_06",
            timestamp_s=100.0,
            current_phase_index=2,
            current_program_id="test",
            red_yellow_green_state="rrGG",
            next_switch_s=125.0,
            spent_duration_s=20.0,
            controlled_lanes=["I5_I6_0", "I7_I6_0", "N_I6_I6_0", "S_I6_I6_0"],
        )
        decision = self.engine.decide(request, state, sim_time_s=100.0)
        self.assertEqual(decision.action, TSPAction.EARLY_GREEN.value)
        self.assertEqual(decision.phase_duration_s, 2.0)

    def test_engine_reevaluates_when_bus_is_too_close_for_early_green(self) -> None:
        request = self._request(
            current_edge_id="I3_I2",
            current_lane_id="I3_I2_0",
            eta_to_stopline_s=9.0,
            requested_maneuver=RequestedManeuver.EARLY_GREEN.value,
        )
        state = self._state(
            current_phase_index=2,
            red_yellow_green_state="rrGG",
            controlled_lanes=["I1_I2_0", "I3_I2_0", "N_I2_I2_0", "S_I2_I2_0"],
        )
        decision = self.engine.decide(request, state, sim_time_s=100.0)
        self.assertEqual(decision.action, TSPAction.REEVALUATE_NEXT_CYCLE.value)

    def test_safety_clips_green_extension_to_maximum(self) -> None:
        safety = TSPSafetyLayer(self.cits, self.tsp)
        request = self._request(eta_to_stopline_s=40.0)
        decision = self.engine.decide(request, self._state(next_switch_s=101.0), sim_time_s=100.0)
        result = safety.validate(decision, self._state(next_switch_s=101.0), sim_time_s=100.0)
        self.assertTrue(result.approved)
        self.assertLessEqual(result.safe_decision.extension_s, self.cits.safety_constraints["max_green_extension_s"])

    def test_safety_blocks_early_green_before_min_green(self) -> None:
        safety = TSPSafetyLayer(self.cits, self.tsp)
        request = self._request(
            intersection_id="I6",
            tls_id="I6",
            rsu_id="RSU_BOAVISTA_06",
            current_edge_id="I7_I6",
            current_lane_id="I7_I6_0",
            requested_maneuver=RequestedManeuver.EARLY_GREEN.value,
        )
        state = SignalState(
            intersection_id="I6",
            tls_id="I6",
            rsu_id="RSU_BOAVISTA_06",
            timestamp_s=100.0,
            current_phase_index=2,
            current_program_id="test",
            red_yellow_green_state="rrGG",
            next_switch_s=125.0,
            spent_duration_s=3.0,
            controlled_lanes=["I5_I6_0", "I7_I6_0", "N_I6_I6_0", "S_I6_I6_0"],
        )
        decision = self.engine.decide(request, state, sim_time_s=100.0)
        result = safety.validate(decision, state, sim_time_s=100.0)
        self.assertFalse(result.approved)
        self.assertEqual(result.safe_decision.status, DecisionStatus.BLOCKED_BY_SAFETY.value)

    def test_safety_blocks_green_extension_during_yellow_transition(self) -> None:
        safety = TSPSafetyLayer(self.cits, self.tsp)
        decision = self.engine.decide(self._request(), self._state(), sim_time_s=100.0)
        result = safety.validate(decision, self._state(red_yellow_green_state="yyrr"), sim_time_s=100.0)
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "current_phase_is_yellow_wait_for_next_cycle")

    def test_safety_yellow_check_is_per_movement_not_global(self) -> None:
        # L1: amarelo numa aproximação SECUNDÁRIA com o corredor verde estável
        # não deve bloquear a extensão do corredor. Posições 0,1 = corredor
        # (I1_I2_0, I3_I2_0); posições 2,3 = secundário (N_I2_I2_0, S_I2_I2_0).
        safety = TSPSafetyLayer(self.cits, self.tsp)
        decision = self.engine.decide(self._request(), self._state(), sim_time_s=100.0)
        state = self._state(red_yellow_green_state="GGyy")  # corredor verde, secundário amarelo
        result = safety.validate(decision, state, sim_time_s=100.0)
        self.assertTrue(result.approved, msg=f"reason={result.reason}")

    def test_safety_blocks_green_extension_outside_corridor_green_phase(self) -> None:
        safety = TSPSafetyLayer(self.cits, self.tsp)
        decision = self.engine.decide(self._request(), self._state(), sim_time_s=100.0)
        state = self._state(current_phase_index=2, red_yellow_green_state="rrGG")
        result = safety.validate(decision, state, sim_time_s=100.0)
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "green_extension_requires_corridor_green_phase")

    def test_safety_blocks_early_green_when_phase_sequence_does_not_reach_target(self) -> None:
        raw = deepcopy(self.tsp.raw)
        raw["phase_mapping"]["I6"] = {
            "corridor_green_phase_index": 0,
            "minor_green_phase_index": 2,
            "phase_sequence": [0, 2, 1, 3],
        }
        tsp = TSPConfig(root=self.tsp.root, raw=raw)
        engine = TSPDecisionEngine(self.cits, tsp)
        safety = TSPSafetyLayer(self.cits, tsp)
        request = self._request(
            destination_id="RSU_BOAVISTA_06",
            intersection_id="I6",
            tls_id="I6",
            rsu_id="RSU_BOAVISTA_06",
            current_edge_id="I7_I6",
            current_lane_id="I7_I6_0",
            requested_maneuver=RequestedManeuver.EARLY_GREEN.value,
        )
        state = SignalState(
            intersection_id="I6",
            tls_id="I6",
            rsu_id="RSU_BOAVISTA_06",
            timestamp_s=100.0,
            current_phase_index=2,
            current_program_id="test",
            red_yellow_green_state="rrGG",
            next_switch_s=125.0,
            spent_duration_s=20.0,
            controlled_lanes=["I5_I6_0", "I7_I6_0", "N_I6_I6_0", "S_I6_I6_0"],
        )
        decision = engine.decide(request, state, sim_time_s=100.0)
        result = safety.validate(decision, state, sim_time_s=100.0)
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "early_green_target_phase_not_next_after_transition")

    def test_safety_fails_closed_when_remaining_phase_time_unknown(self) -> None:
        # C2: sem next_switch não é possível provar max_total_green -> bloquear.
        safety = TSPSafetyLayer(self.cits, self.tsp)
        decision = self.engine.decide(self._request(), self._state(), sim_time_s=100.0)
        result = safety.validate(decision, self._state(next_switch_s=None), sim_time_s=100.0)
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "green_extension_unknown_remaining_phase_time")

    def test_safety_fails_closed_when_max_total_green_missing(self) -> None:
        # C2: bound de segurança em falta nunca é substituído por default.
        raw = deepcopy(self.cits.raw)
        del raw["safety_constraints"]["max_total_green_s"]
        cits = replace(self.cits, raw=raw)
        safety = TSPSafetyLayer(cits, self.tsp)
        decision = self.engine.decide(self._request(), self._state(), sim_time_s=100.0)
        result = safety.validate(decision, self._state(), sim_time_s=100.0)
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "safety_constraint_missing:max_total_green_s")

    def test_safety_blocks_early_green_that_would_skip_clearance_phase(self) -> None:
        # C1: never_skip_yellow_or_all_red é agora efetivamente verificado.
        raw = deepcopy(self.tsp.raw)
        raw["phase_mapping"]["I6"] = {
            "corridor_green_phase_index": 0,
            "minor_green_phase_index": 2,
            "phase_sequence": [2, 0, 1, 3],
        }
        tsp = TSPConfig(root=self.tsp.root, raw=raw)
        engine = TSPDecisionEngine(self.cits, tsp)
        safety = TSPSafetyLayer(self.cits, tsp)
        request = self._request(
            destination_id="RSU_BOAVISTA_06",
            intersection_id="I6",
            tls_id="I6",
            rsu_id="RSU_BOAVISTA_06",
            current_edge_id="I7_I6",
            current_lane_id="I7_I6_0",
            requested_maneuver=RequestedManeuver.EARLY_GREEN.value,
        )
        state = SignalState(
            intersection_id="I6",
            tls_id="I6",
            rsu_id="RSU_BOAVISTA_06",
            timestamp_s=100.0,
            current_phase_index=2,
            current_program_id="test",
            red_yellow_green_state="rrGG",
            next_switch_s=125.0,
            spent_duration_s=20.0,
            controlled_lanes=["I5_I6_0", "I7_I6_0", "N_I6_I6_0", "S_I6_I6_0"],
        )
        decision = engine.decide(request, state, sim_time_s=100.0)
        result = safety.validate(decision, state, sim_time_s=100.0)
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "early_green_would_skip_clearance_phase")

    def test_request_without_expiry_is_not_treated_as_expired(self) -> None:
        # H2: expires_at_s=None significa "sem expiração".
        request = self._request(expires_at_s=None)
        decision = self.engine.decide(request, self._state(), sim_time_s=100.0)
        self.assertNotEqual(decision.reason, "request_expired_before_tsp_decision")

    def test_safety_resets_consecutive_interventions_after_cooldown(self) -> None:
        safety = TSPSafetyLayer(self.cits, self.tsp)
        decision = self.engine.decide(self._request(), self._state(), sim_time_s=100.0)
        safety.last_intervention_time_by_tls["I2"] = 0.0
        safety.consecutive_interventions_by_tls["I2"] = self.cits.safety_constraints["max_consecutive_priority_interventions_per_tls"]
        result = safety.validate(decision, self._state(), sim_time_s=1000.0)
        self.assertTrue(result.approved)
        self.assertEqual(safety.consecutive_interventions_by_tls["I2"], 0)

    def test_engine_lane_match_is_not_fooled_by_edge_prefix_collision(self) -> None:
        # M1: "I1_I20_0" não pode ser tratada como pertencente à edge "I1_I2".
        request = self._request()  # current_edge_id=I1_I2, current_lane_id=I1_I2_0
        state = self._state(
            controlled_lanes=["I1_I20_0", "I1_I2_0"],
            red_yellow_green_state="rG",
            current_phase_index=0,
        )
        self.assertTrue(self.engine.is_priority_movement_green(request, state))

    def _ack(self, request) -> SSEMLike:
        return SSEMLike(
            source_id=request.rsu_id,
            destination_id=request.source_id,
            timestamp_s=100.0,
            request_id=request.request_id,
            vehicle_id=request.vehicle_id,
            intersection_id=request.intersection_id,
            tls_id=request.tls_id,
            rsu_id=request.rsu_id,
            status=RequestStatus.ACKNOWLEDGED.value,
            action="forward_to_decision_engine",
            reason="ok",
            valid_until_s=120.0,
        )

    def test_safety_counters_advance_in_dry_run_across_steps(self) -> None:
        # H5: contadores de safety devem avançar com base no "would-apply" para
        # o cooldown bloquear pedidos subsequentes em modos dry-run / no-actuation.
        controller = TSPControlController(self.cits, self.tsp)

        class _NullLogger:
            def write(self, item) -> None:  # noqa: ANN001
                pass

        actuator = TraciTSPActuator(adapter=None, apply_actuation=False)  # type: ignore[arg-type]
        req1 = self._request(vehicle_id="bus_a")
        decisions: list = []
        actuations: list = []
        controller._process_acknowledged_requests(
            responses=[self._ack(req1)],
            requests_by_id={req1.request_id: req1},
            signal_states={"I2": self._state()},
            actuator=actuator,
            sim_time_s=100.0,
            decision_logger=_NullLogger(),
            actuation_logger=_NullLogger(),
            decisions=decisions,
            actuations=actuations,
        )
        # Apesar de applied=False (no-actuation), o cooldown deve estar marcado.
        self.assertIn("I2", controller.safety.last_intervention_time_by_tls)
        self.assertEqual(controller.safety.last_intervention_time_by_tls["I2"], 100.0)

        # Pedido subsequente no mesmo TLS antes do cooldown expirar -> bloqueado.
        req2 = self._request(vehicle_id="bus_b")
        controller._process_acknowledged_requests(
            responses=[self._ack(req2)],
            requests_by_id={req2.request_id: req2},
            signal_states={"I2": self._state()},
            actuator=actuator,
            sim_time_s=110.0,  # 10s < 90s cooldown
            decision_logger=_NullLogger(),
            actuation_logger=_NullLogger(),
            decisions=decisions,
            actuations=actuations,
        )
        blocked = [d for d in decisions if d.reason == "cooldown_after_priority_active"]
        self.assertEqual(len(blocked), 1)

    def test_same_tls_intervention_deduplicated_within_step_even_without_actuation(self) -> None:
        # M6: dois pedidos para o mesmo TLS no mesmo passo -> no máximo 1
        # intervenção, mesmo em modo no-actuation (onde o cooldown não corre).
        controller = TSPControlController(self.cits, self.tsp)

        class _NullLogger:
            def write(self, item) -> None:  # noqa: ANN001
                pass

        req1 = self._request(vehicle_id="bus_a")
        req2 = self._request(vehicle_id="bus_b")
        responses = [self._ack(req1), self._ack(req2)]
        requests_by_id = {req1.request_id: req1, req2.request_id: req2}
        decisions: list = []
        actuations: list = []
        controller._process_acknowledged_requests(
            responses=responses,
            requests_by_id=requests_by_id,
            signal_states={"I2": self._state()},
            actuator=TraciTSPActuator(adapter=None, apply_actuation=False),  # type: ignore[arg-type]
            sim_time_s=100.0,
            decision_logger=_NullLogger(),
            actuation_logger=_NullLogger(),
            decisions=decisions,
            actuations=actuations,
        )
        self.assertEqual(len(decisions), 2)
        approved = [d for d in decisions if d.status == DecisionStatus.APPROVED.value]
        superseded = [d for d in decisions if d.reason == "superseded_by_earlier_intervention_same_step"]
        self.assertEqual(len(approved), 1)
        self.assertEqual(len(superseded), 1)
        self.assertEqual(superseded[0].status, DecisionStatus.NOT_ACTUABLE.value)
        self.assertFalse(any(a.applied for a in actuations))

    def test_signal_program_verification_detects_mismatch_and_actuated(self) -> None:
        # C3/C4: reconciliação do phase_mapping e deteção de TLS atuado.
        controller = TSPControlController(self.cits, self.tsp)

        class _CleanAdapter:
            def read_program_phase_count(self, tls_id: str) -> int:
                return 4

            def read_program_type(self, tls_id: str) -> str:
                return "0"

            def read_program_is_fixed_time(self, tls_id: str) -> bool:
                return True

            def read_program_phase_states(self, tls_id: str):
                # 0=corridor green, 1=yellow clearance, 2=minor green, 3=yellow clearance
                return ["GGgrrrr", "yyyrrrr", "rrrGGgr", "rrryyyy"]

        class _BadAdapter:
            def read_program_phase_count(self, tls_id: str):
                return 2 if tls_id == "I1" else None

            def read_program_type(self, tls_id: str) -> str:
                return "3"

            def read_program_is_fixed_time(self, tls_id: str):
                if self.read_program_phase_count(tls_id) is None:
                    return None  # unreadable -> fail-closed at phase_count gate
                return False  # behaviourally actuated (minDur < maxDur)

            def read_program_phase_states(self, tls_id: str):
                return None

        self.assertEqual(controller._verify_signal_programs(_CleanAdapter()), [])

        problems = controller._verify_signal_programs(_BadAdapter())
        self.assertTrue(problems)
        joined = " ".join(problems)
        self.assertIn("fora do programa", joined)  # phase_sequence 2,3 > 2 phases
        self.assertIn("atuado/adaptativo", joined)  # actuated detected by behaviour
        self.assertIn("ilegível", joined)           # unreadable program (fail-closed)

    def test_signal_program_verification_flags_intermediate_phase_without_clearance(self) -> None:
        # Item 2 (closes C1 residual): fase intermédia da sequência tem de ser
        # intergreen — qualquer 'g'/'G' fora dos índices de verde configurados
        # significa que a clearance não está garantida.
        controller = TSPControlController(self.cits, self.tsp)

        class _NoClearanceAdapter:
            def read_program_phase_count(self, tls_id: str) -> int:
                return 4

            def read_program_type(self, tls_id: str) -> str:
                return "0"

            def read_program_is_fixed_time(self, tls_id: str) -> bool:
                return True

            def read_program_phase_states(self, tls_id: str):
                # Fase 1 (intermédia) ainda tem 'G' — green->green sem clearance.
                return ["GGgrrrr", "GGgrrrr", "rrrGGgr", "rrryyyy"]

        problems = controller._verify_signal_programs(_NoClearanceAdapter())
        self.assertTrue(problems)
        self.assertTrue(any("fase 1" in p and "clearance" in p for p in problems))

    def test_traci_no_actuation_does_not_report_applied(self) -> None:
        decision = self.engine.decide(self._request(), self._state(), sim_time_s=100.0)
        safe = decision.copy_with(status=DecisionStatus.APPROVED.value)
        actuator = TraciTSPActuator(adapter=None, apply_actuation=False)  # type: ignore[arg-type]
        result = actuator.apply(safe, self._state(), sim_time_s=100.0)
        self.assertFalse(result.applied)
        self.assertTrue(result.dry_run)
        self.assertEqual(result.reason, "sumo_no_actuation_flag_would_apply")

    def test_dry_run_generates_tsp_summary_and_logs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            cits = replace(self.cits, root=tmp_root)
            tsp = replace(self.tsp, root=tmp_root)
            controller = TSPControlController(cits, tsp)
            summary = controller.run_dry_run(steps=30)
            self.assertGreater(summary["total_decisions"], 0)
            self.assertIn(TSPAction.GREEN_EXTENSION.value, summary["by_action"])
            self.assertIn(TSPAction.EARLY_GREEN.value, summary["by_action"])
            self.assertTrue((tmp_root / "outputs/tsp_decisions.jsonl").exists())
            self.assertTrue((tmp_root / "outputs/tsp_actuation.jsonl").exists())
            self.assertTrue((tmp_root / "reports/tsp_emulation_summary.json").exists())


if __name__ == "__main__":
    unittest.main()
