#!/usr/bin/env python3
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config
from pps57_cits.messages import (
    GrantedStrategy,
    MessageType,
    OperatorPriorityClass,
    PrioritizationResponse,
    ResponseStatus,
    SSEMAudit,
    SSEMLike,
    StationType,
    build_security_envelope,
    derive_station_id,
    parse_intersection_ref_id,
    sim_time_to_cdd,
    synth_srem,
)
from pps57_cits.models import SignalState
from pps57_tsp.actuator import TraciTSPActuator
from pps57_tsp.config import TSPConfig, load_tsp_config
from pps57_tsp.controller import TSPControlController
from pps57_tsp.engine import TSPDecisionEngine
from pps57_tsp.models import DecisionStatus, TSPAction
from pps57_tsp.safety import TSPSafetyLayer
from pps57_tsp.signal_control import SimulatedControllerAdapter, build_controller_contracts


class Package4TSPTestCase(unittest.TestCase):
    # Fixtures alinhadas com o programa SUMO realmente gerado (build_network):
    # 7 fases, ciclo 90s, com fase pedonal exclusiva (Barnes Dance, 12s) anexada
    # no índice 6. Verdes de serviço em [0, 3], intergreens em [1, 2, 4, 5]; a
    # fase pedonal (6) fica fora do contrato (service_green/intergreen). I1 e I2
    # têm a mesma topologia de 7 fases, diferindo no split e nas máscaras RYG.
    _STATIC_PHASE_STATES = [
        "rrrrgGggGGgrrrrrgGGGgrrrr",
        "rrrryyyyyyyrrrrryyyyyrrrr",
        "rrrrrrrrrrrrrrrrrrrrrrrrr",
        "gGGgrrrrrrrggGGgrrrrrrrrr",
        "yyyyrrrrrrryyyyyrrrrrrrrr",
        "rrrrrrrrrrrrrrrrrrrrrrrrr",
        "rrrrrrrrrrrrrrrrrrrrrGGGG",
    ]
    _STATIC_PHASE_DURATIONS = [31, 3, 1, 39, 3, 1, 12]
    _FDW_PHASE_STATES = [
        "rrrrGgGGgrrrrGgGGgrrrr",
        "rrrryyyyyrrrryyyyyrrrr",
        "rrrrrrrrrrrrrrrrrrrrrr",
        "ggGgrrrrrggGgrrrrrrrrr",
        "yyyyrrrrryyyyrrrrrrrrr",
        "rrrrrrrrrrrrrrrrrrrrrr",
        "rrrrrrrrrrrrrrrrrrGGGG",
    ]
    _FDW_PHASE_DURATIONS = [39, 3, 1, 31, 3, 1, 12]
    _FDW_TLS = {"I2", "I4", "I5"}

    @classmethod
    def setUpClass(cls) -> None:
        cls.cits = load_cits_config(ROOT / "configs/cits_v2x_config.json", root=ROOT)
        cls.tsp = load_tsp_config(ROOT / "configs/tsp_safety_config.json", root=ROOT)
        cls.engine = TSPDecisionEngine(cls.cits, cls.tsp)

    def _request(self, **overrides):
        """Constrói um SREM ETSI-aligned para fixtures de teste.

        Aceita nomes de override do v0.3 (intersection_id, current_lane_id,
        target_signal_group_id, priority_level, requested_maneuver, ...) e
        traduz para os parâmetros de `synth_srem`. `requested_maneuver` é
        silenciosamente descartado — a OBU não decide manobra no v0.4.
        """
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
        # Tradução de aliases v0.3 -> v0.4.
        alias_map = {
            "intersection_id": "intersection_alias",
            "current_lane_id": "lane_id",
            "target_signal_group_id": "target_signal_group_id_hint",
            "priority_level": "operator_priority_class",
        }
        # Drops silenciosos: campos que deixaram de fazer sentido.
        ignored = {"requested_maneuver", "current_edge_id", "destination_id", "source_id", "timestamp_s"}
        for key, value in overrides.items():
            if key in ignored:
                continue
            defaults[alias_map.get(key, key)] = value
        return synth_srem(**defaults)

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
            priority_level=OperatorPriorityClass.NOMINAL.value,
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
            destination_id="RSU_BOAVISTA_07",
            intersection_id="I7",
            tls_id="I7",
            rsu_id="RSU_BOAVISTA_07",
            current_edge_id="ATLANTIC_WEST_I7",
            current_lane_id="ATLANTIC_WEST_I7_0",
            priority_movement_id="I7_eastbound_public_transport",
            target_signal_group_id="I7_priority_eastbound",
        )
        state = SignalState(
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
        decision = self.engine.decide(request, state, sim_time_s=100.0)
        self.assertEqual(decision.action, TSPAction.EARLY_GREEN.value)
        self.assertEqual(decision.phase_duration_s, 2.0)

    def test_engine_reevaluates_when_bus_is_too_close_for_early_green(self) -> None:
        request = self._request(
            current_edge_id="I3_I2",
            current_lane_id="I3_I2_0",
            priority_movement_id="I2_eastbound_public_transport",
            target_signal_group_id="I2_priority_eastbound",
            eta_to_stopline_s=9.0,
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
            intersection_id="I7",
            tls_id="I7",
            rsu_id="RSU_BOAVISTA_07",
            current_edge_id="ATLANTIC_WEST_I7",
            current_lane_id="ATLANTIC_WEST_I7_0",
            priority_movement_id="I7_eastbound_public_transport",
            target_signal_group_id="I7_priority_eastbound",
        )
        state = SignalState(
            intersection_id="I7",
            tls_id="I7",
            rsu_id="RSU_BOAVISTA_07",
            timestamp_s=100.0,
            current_phase_index=2,
            current_program_id="test",
            red_yellow_green_state="rrGG",
            next_switch_s=125.0,
            spent_duration_s=3.0,
            controlled_lanes=["I6_I7_0", "ATLANTIC_WEST_I7_0", "N_I7_I7_0", "S_I7_I7_0"],
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
        # L1: amarelo numa aproximação secundária não deve bloquear a extensão
        # quando o movimento prioritário alvo está em verde estável.
        safety = TSPSafetyLayer(self.cits, self.tsp)
        decision = self.engine.decide(self._request(), self._state(), sim_time_s=100.0)
        state = self._state(red_yellow_green_state="GGyy")
        result = safety.validate(decision, state, sim_time_s=100.0)
        self.assertTrue(result.approved, msg=f"reason={result.reason}")

    def test_safety_blocks_green_extension_outside_priority_movement_green_phase(self) -> None:
        safety = TSPSafetyLayer(self.cits, self.tsp)
        decision = self.engine.decide(self._request(), self._state(), sim_time_s=100.0)
        state = self._state(current_phase_index=2, red_yellow_green_state="rrGG")
        result = safety.validate(decision, state, sim_time_s=100.0)
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "green_extension_requires_priority_movement_green_phase")

    def test_safety_blocks_early_green_when_phase_sequence_does_not_reach_target(self) -> None:
        raw = deepcopy(self.tsp.raw)
        raw["controller_contracts"]["controllers"] = {
            "I7": {"phase_sequence": [2, 3, 1]}
        }
        tsp = TSPConfig(root=self.tsp.root, raw=raw)
        engine = TSPDecisionEngine(self.cits, tsp)
        safety = TSPSafetyLayer(self.cits, tsp)
        request = self._request(
            destination_id="RSU_BOAVISTA_07",
            intersection_id="I7",
            tls_id="I7",
            rsu_id="RSU_BOAVISTA_07",
            current_edge_id="ATLANTIC_WEST_I7",
            current_lane_id="ATLANTIC_WEST_I7_0",
            priority_movement_id="I7_eastbound_public_transport",
            target_signal_group_id="I7_priority_eastbound",
        )
        state = SignalState(
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
        decision = engine.decide(request, state, sim_time_s=100.0)
        result = safety.validate(decision, state, sim_time_s=100.0)
        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "early_green_phase_not_in_configured_sequence")

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
        raw["controller_contracts"]["controllers"] = {
            "I7": {"phase_sequence": [2, 0, 1, 3]}
        }
        tsp = TSPConfig(root=self.tsp.root, raw=raw)
        engine = TSPDecisionEngine(self.cits, tsp)
        safety = TSPSafetyLayer(self.cits, tsp)
        request = self._request(
            destination_id="RSU_BOAVISTA_07",
            intersection_id="I7",
            tls_id="I7",
            rsu_id="RSU_BOAVISTA_07",
            current_edge_id="ATLANTIC_WEST_I7",
            current_lane_id="ATLANTIC_WEST_I7_0",
            priority_movement_id="I7_eastbound_public_transport",
            target_signal_group_id="I7_priority_eastbound",
        )
        state = SignalState(
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

    def _ack(self, request, sim_time_s: float = 100.0) -> SSEMLike:
        """Constrói um SSEM com `responseStatus=processing` (v0.3: ACKNOWLEDGED)."""
        primary = request.requests[0]
        moy, timestamp_ms, generation_delta = sim_time_to_cdd(sim_time_s)
        return SSEMLike(
            message_type=MessageType.SSEM.value,
            station_id=derive_station_id(request.rsu_id),
            station_type=StationType.ROAD_SIDE_UNIT.value,
            source_id=request.rsu_id,
            destination_id=request.source_id,
            generation_delta_time_ms=generation_delta,
            moy=moy,
            timestamp_ms=timestamp_ms,
            security=build_security_envelope(request.rsu_id, sim_time_s),
            intersection_ref_id=parse_intersection_ref_id(request.intersection_id),
            intersection_alias=request.intersection_id,
            tls_id=request.tls_id,
            rsu_id=request.rsu_id,
            response=PrioritizationResponse(
                request_id=primary.request_id,
                sequence_number=request.sequence_number,
                requestor_station_id=request.station_id,
                response_status=ResponseStatus.PROCESSING.value,
                granted_signal_group=None,
                valid_until_ms=int(round(sim_time_s * 1000)) + 15_000,
            ),
            audit=SSEMAudit(
                granted_strategy=GrantedStrategy.NONE.value,
                rejection_reason=None,
                confidence=0.95,
                notes=[],
            ),
            correlation_id=request.message_id,
        )

    def test_controller_emits_final_ssem_after_tsp_decision(self) -> None:
        controller = TSPControlController(self.cits, self.tsp)

        class _NullLogger:
            def write(self, item) -> None:  # noqa: ANN001
                pass

        req = self._request()
        decisions: list = []
        actuations: list = []
        final_responses = controller._process_acknowledged_requests(
            responses=[self._ack(req)],
            requests_by_id={req.request_id: req},
            signal_states={"I2": self._state()},
            actuator=TraciTSPActuator(adapter=None, apply_actuation=False),  # type: ignore[arg-type]
            sim_time_s=100.0,
            decision_logger=_NullLogger(),
            actuation_logger=_NullLogger(),
            decisions=decisions,
            actuations=actuations,
        )
        self.assertEqual(len(final_responses), 1)
        final = final_responses[0]
        # Em modo no-actuation a decisão é aprovada pela Safety Layer mas NÃO é
        # fisicamente aplicada; o SSEM final só promete GRANTED quando o sinal
        # muda mesmo. Aqui o estado é rejected, com evidência de "would grant".
        self.assertEqual(final.status, ResponseStatus.REJECTED.value)
        self.assertEqual(final.audit.rejection_reason, "sumo_no_actuation_flag_would_apply")
        self.assertIn("tsp_would_grant_not_applied=true", final.audit.notes)
        self.assertIn("tsp_status=approved", final.audit.notes)
        self.assertEqual(final.correlation_id, req.message_id)
        self.assertIn("tsp_decision_id=", final.audit.notes[1])

    def test_controller_final_ssem_rejects_low_score_decision(self) -> None:
        controller = TSPControlController(self.cits, self.tsp)

        class _NullLogger:
            def write(self, item) -> None:  # noqa: ANN001
                pass

        req = self._request(
            schedule_delay_s=0.0,
            headway_deviation_s=0.0,
            distance_to_stopline_m=250.0,
            priority_level=OperatorPriorityClass.NOMINAL.value,
        )
        decisions: list = []
        actuations: list = []
        final_responses = controller._process_acknowledged_requests(
            responses=[self._ack(req)],
            requests_by_id={req.request_id: req},
            signal_states={"I2": self._state()},
            actuator=TraciTSPActuator(adapter=None, apply_actuation=False),  # type: ignore[arg-type]
            sim_time_s=100.0,
            decision_logger=_NullLogger(),
            actuation_logger=_NullLogger(),
            decisions=decisions,
            actuations=actuations,
        )
        self.assertEqual(len(final_responses), 1)
        self.assertEqual(final_responses[0].status, ResponseStatus.REJECTED.value)
        self.assertIn("priority_score_below_threshold", final_responses[0].reason)

    def test_safety_counters_advance_in_no_actuation_across_steps(self) -> None:
        # H5: contadores de safety devem avançar com base no "would-apply" para
        # o cooldown bloquear pedidos subsequentes em modo SUMO no-actuation.
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

    def test_corridor_arbiter_defer_is_downgrade_only_in_controller_loop(self) -> None:
        # P6: a corridor defer (pre-Safety) must downgrade to NOT_ACTUABLE, never
        # reach APPROVED, never actuate, and NOT mark the TLS intervened (so a
        # same-step follow-up is corridor-deferred, not superseded).
        tsp = replace(self.tsp, raw={**self.tsp.raw, "corridor": {"max_corridor_recovery_debt_s": 1.0}})
        controller = TSPControlController(self.cits, tsp)
        controller.safety.recovery_debt_by_tls["I2"] = 5.0  # over the 1.0 corridor cap

        class _NullLogger:
            def write(self, item) -> None:  # noqa: ANN001
                pass

        req1 = self._request(vehicle_id="bus_a")
        req2 = self._request(vehicle_id="bus_b")
        decisions: list = []
        actuations: list = []
        controller._process_acknowledged_requests(
            responses=[self._ack(req1), self._ack(req2)],
            requests_by_id={req1.request_id: req1, req2.request_id: req2},
            signal_states={"I2": self._state()},
            actuator=TraciTSPActuator(adapter=None, apply_actuation=False),  # type: ignore[arg-type]
            sim_time_s=100.0,
            decision_logger=_NullLogger(),
            actuation_logger=_NullLogger(),
            decisions=decisions,
            actuations=actuations,
        )
        self.assertEqual(len(decisions), 2)
        for d in decisions:
            self.assertEqual(d.status, DecisionStatus.NOT_ACTUABLE.value)
            self.assertNotEqual(d.status, DecisionStatus.APPROVED.value)
            # Both corridor-deferred (NOT "superseded") => defer never marked I2 intervened.
            self.assertEqual(d.reason, "deferred_corridor_recovery_debt_exhausted")
        self.assertFalse(any(a.applied for a in actuations))

    def test_signal_program_verification_detects_mismatch_and_actuated(self) -> None:
        # C3/C4: reconciliação do phase_mapping e deteção de TLS atuado.
        controller = TSPControlController(self.cits, self.tsp)

        class _CleanAdapter:
            def read_program_phase_count(self, tls_id: str) -> int:
                # Topologia atual: 7 fases para todos os TLS (verde main, inter,
                # all-red, verde minor, inter, all-red, fase pedonal exclusiva).
                return 7

            def read_program_type(self, tls_id: str) -> str:
                return "0"

            def read_program_is_fixed_time(self, tls_id: str) -> bool:
                return True

            def read_program_phase_states(self, tls_id: str):
                return (
                    Package4TSPTestCase._FDW_PHASE_STATES
                    if tls_id in Package4TSPTestCase._FDW_TLS
                    else Package4TSPTestCase._STATIC_PHASE_STATES
                )

            def read_program_phase_durations(self, tls_id: str):
                return (
                    Package4TSPTestCase._FDW_PHASE_DURATIONS
                    if tls_id in Package4TSPTestCase._FDW_TLS
                    else Package4TSPTestCase._STATIC_PHASE_DURATIONS
                )

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
        self.assertNotIn("I6:", joined)             # I6 is a SUMO priority roundabout, not a TLS

    def test_signal_program_verification_flags_intermediate_phase_without_clearance(self) -> None:
        # Item 2 (closes C1 residual): fase intermédia da sequência tem de ser
        # intergreen — qualquer 'g'/'G' fora dos índices de verde configurados
        # significa que a clearance não está garantida.
        controller = TSPControlController(self.cits, self.tsp)

        class _NoClearanceAdapter:
            def read_program_phase_count(self, tls_id: str) -> int:
                return 6

            def read_program_type(self, tls_id: str) -> str:
                return "0"

            def read_program_is_fixed_time(self, tls_id: str) -> bool:
                return True

            def read_program_phase_states(self, tls_id: str):
                # Fase 1 (intermédia) ainda tem 'G' — green->green sem clearance.
                return ["GGgrrrr", "GGgrrrr", "rrrrrrr", "rrrGGgr", "rrryyyy", "rrrrrrr"]

            def read_program_phase_durations(self, tls_id: str):
                return [42, 3, 1, 42, 3, 1]

        problems = controller._verify_signal_programs(_NoClearanceAdapter())
        self.assertTrue(problems)
        self.assertTrue(any("fase 1" in p and "clearance" in p for p in problems))

    def test_controller_contract_requires_conflict_matrix(self) -> None:
        raw = deepcopy(self.tsp.raw)
        raw["controller_contracts"]["controllers"]["I2"]["signal_groups"]["I2_priority_westbound"]["conflicts_with"] = []
        controller = TSPControlController(self.cits, TSPConfig(root=self.tsp.root, raw=raw))

        class _CleanAdapter:
            def read_program_phase_count(self, tls_id: str) -> int:
                # Topologia atual: 7 fases para todos os TLS (verde main, inter,
                # all-red, verde minor, inter, all-red, fase pedonal exclusiva).
                return 7

            def read_program_type(self, tls_id: str) -> str:
                return "0"

            def read_program_is_fixed_time(self, tls_id: str) -> bool:
                return True

            def read_program_phase_states(self, tls_id: str):
                return (
                    Package4TSPTestCase._FDW_PHASE_STATES
                    if tls_id in Package4TSPTestCase._FDW_TLS
                    else Package4TSPTestCase._STATIC_PHASE_STATES
                )

            def read_program_phase_durations(self, tls_id: str):
                return (
                    Package4TSPTestCase._FDW_PHASE_DURATIONS
                    if tls_id in Package4TSPTestCase._FDW_TLS
                    else Package4TSPTestCase._STATIC_PHASE_DURATIONS
                )

        problems = controller._verify_signal_programs(_CleanAdapter())
        self.assertTrue(any("sem matriz de conflitos" in item for item in problems))

    def test_controller_contract_requires_signal_group_green_phase(self) -> None:
        controller = TSPControlController(self.cits, self.tsp)

        class _NoGreenAdapter:
            def read_program_phase_count(self, tls_id: str) -> int:
                return 6

            def read_program_type(self, tls_id: str) -> str:
                return "0"

            def read_program_is_fixed_time(self, tls_id: str) -> bool:
                return True

            def read_program_phase_states(self, tls_id: str):
                return ["rrrrrrr", "yyyrrrr", "rrrrrrr", "rrrGGgr", "rrryyyy", "rrrrrrr"]

            def read_program_phase_durations(self, tls_id: str):
                return [42, 3, 1, 42, 3, 1]

        problems = controller._verify_signal_programs(_NoGreenAdapter())
        self.assertTrue(any("sem verde" in item for item in problems))

    def test_traci_adapter_selects_current_program_logic_for_verification(self) -> None:
        from types import SimpleNamespace
        from pps57_cits.traci_adapter import TraciSimulationAdapter

        class _TrafficLight:
            def getProgram(self, tls_id: str) -> str:
                return "active"

            def getAllProgramLogics(self, tls_id: str):
                return [
                    SimpleNamespace(programID="stale", phases=[SimpleNamespace(state="G")]),
                    SimpleNamespace(programID="active", phases=[SimpleNamespace(state="G"), SimpleNamespace(state="r")]),
                ]

        adapter = TraciSimulationAdapter(self.cits)
        adapter.traci = SimpleNamespace(trafficlight=_TrafficLight())
        self.assertEqual(adapter.read_program_phase_count("I2"), 2)

    def test_safety_blocks_early_green_when_signal_program_not_verified(self) -> None:
        # Pedestrian clearance enforcement: sem `_verify_signal_programs` ter
        # corrido com sucesso não há prova de que as fases intermédias são
        # intergreen genuínas -> fail-closed.
        safety = TSPSafetyLayer(self.cits, self.tsp)
        # signal_program_verified defaults to False
        request = self._request(
            destination_id="RSU_BOAVISTA_07",
            intersection_id="I7",
            tls_id="I7",
            rsu_id="RSU_BOAVISTA_07",
            current_edge_id="ATLANTIC_WEST_I7",
            current_lane_id="ATLANTIC_WEST_I7_0",
            priority_movement_id="I7_eastbound_public_transport",
            target_signal_group_id="I7_priority_eastbound",
        )
        state = SignalState(
            intersection_id="I7",
            tls_id="I7",
            rsu_id="RSU_BOAVISTA_07",
            timestamp_s=100.0,
            current_phase_index=3,  # fase do verde conflituante (secundárias N/S) a truncar
            current_program_id="test",
            red_yellow_green_state="rrGG",
            next_switch_s=125.0,
            spent_duration_s=20.0,  # > min_green_s=8
            controlled_lanes=["I6_I7_0", "ATLANTIC_WEST_I7_0", "N_I7_I7_0", "S_I7_I7_0"],
        )
        decision = self.engine.decide(request, state, sim_time_s=100.0)
        result = safety.validate(decision, state, sim_time_s=100.0)
        self.assertFalse(result.approved)
        self.assertEqual(
            result.reason,
            "pedestrian_clearance_unverifiable_signal_program_not_validated",
        )

    def test_safety_approves_early_green_when_signal_program_verified(self) -> None:
        safety = TSPSafetyLayer(self.cits, self.tsp)
        safety.set_signal_program_verified(True)
        request = self._request(
            destination_id="RSU_BOAVISTA_07",
            intersection_id="I7",
            tls_id="I7",
            rsu_id="RSU_BOAVISTA_07",
            current_edge_id="ATLANTIC_WEST_I7",
            current_lane_id="ATLANTIC_WEST_I7_0",
            priority_movement_id="I7_eastbound_public_transport",
            target_signal_group_id="I7_priority_eastbound",
        )
        state = SignalState(
            intersection_id="I7",
            tls_id="I7",
            rsu_id="RSU_BOAVISTA_07",
            timestamp_s=100.0,
            current_phase_index=3,  # fase do verde conflituante (secundárias N/S) a truncar
            current_program_id="test",
            red_yellow_green_state="rrGG",
            next_switch_s=125.0,
            spent_duration_s=20.0,
            controlled_lanes=["I6_I7_0", "ATLANTIC_WEST_I7_0", "N_I7_I7_0", "S_I7_I7_0"],
        )
        decision = self.engine.decide(request, state, sim_time_s=100.0)
        result = safety.validate(decision, state, sim_time_s=100.0)
        self.assertTrue(result.approved, msg=f"reason={result.reason}")
        self.assertEqual(result.reason, "approved_red_truncation")

    def test_copy_with_does_not_alias_notes_list(self) -> None:
        # copy_with passou a usar dataclasses.replace; sem a cópia explícita
        # de `notes`, o objeto copiado partilharia a lista do original.
        decision = self.engine.decide(self._request(), self._state(), sim_time_s=100.0)
        clone = decision.copy_with(status=DecisionStatus.APPROVED.value)
        self.assertIsNot(clone.notes, decision.notes)
        clone.notes.append("nota só do clone")
        self.assertNotIn("nota só do clone", decision.notes)

    def test_traci_no_actuation_does_not_report_applied(self) -> None:
        decision = self.engine.decide(self._request(), self._state(), sim_time_s=100.0)
        safe = decision.copy_with(status=DecisionStatus.APPROVED.value)
        actuator = TraciTSPActuator(adapter=None, apply_actuation=False)  # type: ignore[arg-type]
        result = actuator.apply(safe, self._state(), sim_time_s=100.0)
        self.assertFalse(result.applied)
        self.assertTrue(result.no_actuation)
        self.assertEqual(result.reason, "sumo_no_actuation_flag_would_apply")
        self.assertEqual(result.severity, "info")

    def test_simulated_controller_rejects_manual_mode_before_traci(self) -> None:
        class _BaseAdapter:
            called = False

            def set_phase_duration(self, tls_id: str, duration_s: float) -> None:
                self.called = True

        base = _BaseAdapter()
        simulated = SimulatedControllerAdapter(
            base=base,  # type: ignore[arg-type]
            contracts=build_controller_contracts(self.cits, self.tsp),
            config={"default_mode": "manual"},
        )
        decision = self.engine.decide(self._request(), self._state(), sim_time_s=100.0)
        safe = decision.copy_with(status=DecisionStatus.APPROVED.value)
        result = TraciTSPActuator(adapter=simulated, apply_actuation=True).apply(safe, self._state(), sim_time_s=100.0)
        self.assertFalse(result.applied)
        self.assertFalse(base.called)
        self.assertEqual(result.reason, "controller_locked_manual_mode")
        self.assertFalse(result.controller_response["accepted"])

    def test_simulated_controller_accepts_and_logs_effective_time(self) -> None:
        class _BaseAdapter:
            duration_s = None

            def set_phase_duration(self, tls_id: str, duration_s: float) -> None:
                self.duration_s = duration_s

        base = _BaseAdapter()
        simulated = SimulatedControllerAdapter(
            base=base,  # type: ignore[arg-type]
            contracts=build_controller_contracts(self.cits, self.tsp),
            config={"default_mode": "automatic", "command_latency_s": 0.0, "pending_lock_s": 0.5},
        )
        decision = self.engine.decide(self._request(), self._state(), sim_time_s=100.0)
        safe = decision.copy_with(status=DecisionStatus.APPROVED.value)
        result = TraciTSPActuator(adapter=simulated, apply_actuation=True).apply(safe, self._state(), sim_time_s=100.0)
        self.assertTrue(result.applied)
        self.assertIsNotNone(base.duration_s)
        self.assertEqual(result.controller_response["reason"], "controller_command_accepted")
        self.assertEqual(result.controller_response["effective_at_s"], 100.0)
        self.assertEqual(result.parameters["controller_adapter"], "simulated_real_controller")

    def test_simulated_controller_nacks_nonzero_latency_pending_scheduler(self) -> None:
        # Limitação conhecida (roadmap P2): o adaptador simulado ainda não tem
        # um command scheduler, por isso qualquer command_latency_s > 0 é
        # rejeitado (NACK) em vez de agendado. Este teste ancora o comportamento
        # atual; quando o scheduler for implementado, deve passar a ACK diferido.
        class _BaseAdapter:
            duration_s = None

            def set_phase_duration(self, tls_id: str, duration_s: float) -> None:
                self.duration_s = duration_s

        base = _BaseAdapter()
        simulated = SimulatedControllerAdapter(
            base=base,  # type: ignore[arg-type]
            contracts=build_controller_contracts(self.cits, self.tsp),
            config={"default_mode": "automatic", "command_latency_s": 0.5, "pending_lock_s": 0.5},
        )
        decision = self.engine.decide(self._request(), self._state(), sim_time_s=100.0)
        safe = decision.copy_with(status=DecisionStatus.APPROVED.value)
        result = TraciTSPActuator(adapter=simulated, apply_actuation=True).apply(safe, self._state(), sim_time_s=100.0)
        self.assertFalse(result.applied)
        self.assertIsNone(base.duration_s)
        self.assertEqual(result.reason, "controller_latency_requires_command_scheduler")
        self.assertFalse(result.controller_response["accepted"])

    def test_simulated_controller_rejects_active_pedestrian_call(self) -> None:
        class _BaseAdapter:
            def set_phase_duration(self, tls_id: str, duration_s: float) -> None:
                raise AssertionError("TraCI must not be called when controller rejects")

        request = self._request(
            destination_id="RSU_BOAVISTA_07",
            intersection_id="I7",
            tls_id="I7",
            rsu_id="RSU_BOAVISTA_07",
            current_edge_id="ATLANTIC_WEST_I7",
            current_lane_id="ATLANTIC_WEST_I7_0",
            priority_movement_id="I7_eastbound_public_transport",
            target_signal_group_id="I7_priority_eastbound",
        )
        state = SignalState(
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
        decision = self.engine.decide(request, state, sim_time_s=100.0).copy_with(status=DecisionStatus.APPROVED.value)
        simulated = SimulatedControllerAdapter(
            base=_BaseAdapter(),  # type: ignore[arg-type]
            contracts=build_controller_contracts(self.cits, self.tsp),
            config={"default_mode": "automatic", "active_pedestrian_calls_by_tls": ["I7"]},
        )
        result = TraciTSPActuator(adapter=simulated, apply_actuation=True).apply(decision, state, sim_time_s=100.0)
        self.assertFalse(result.applied)
        self.assertEqual(result.reason, "controller_rejected_pedestrian_call_active")

    def test_actuation_error_sets_severity_error_and_forces_cooldown(self) -> None:
        # Auditoria deve detectar falhas via severity=error, e o TLS num estado
        # potencialmente intermédio entra em cooldown para não receber retries.
        class _RaisingAdapter:
            def set_phase_duration(self, tls_id: str, duration_s: float) -> None:
                raise RuntimeError("traci socket reset")

        decision = self.engine.decide(self._request(), self._state(), sim_time_s=100.0)
        safe = decision.copy_with(status=DecisionStatus.APPROVED.value)
        actuator = TraciTSPActuator(adapter=_RaisingAdapter(), apply_actuation=True)  # type: ignore[arg-type]
        result = actuator.apply(safe, self._state(), sim_time_s=100.0)
        self.assertFalse(result.applied)
        self.assertFalse(result.no_actuation)
        self.assertEqual(result.severity, "error")
        self.assertTrue(result.reason.startswith("traci_actuation_error:"))

        # Verifica que o controller responde a severity=error forçando cooldown.
        controller = TSPControlController(self.cits, self.tsp)

        class _NullLogger:
            def write(self, item) -> None:  # noqa: ANN001
                pass

        req = self._request(vehicle_id="bus_x")
        decisions: list = []
        actuations: list = []
        controller._process_acknowledged_requests(
            responses=[self._ack(req)],
            requests_by_id={req.request_id: req},
            signal_states={"I2": self._state()},
            actuator=actuator,
            sim_time_s=100.0,
            decision_logger=_NullLogger(),
            actuation_logger=_NullLogger(),
            decisions=decisions,
            actuations=actuations,
        )
        self.assertIn("I2", controller.safety.last_intervention_time_by_tls)
        self.assertEqual(controller.safety.last_intervention_time_by_tls["I2"], 100.0)
        error_actuations = [a for a in actuations if a.severity == "error"]
        self.assertEqual(len(error_actuations), 1)

if __name__ == "__main__":
    unittest.main()
