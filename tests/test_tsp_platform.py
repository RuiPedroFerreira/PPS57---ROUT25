#!/usr/bin/env python3
from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config
from pps57_cits.messages import (
    MessageType,
    OperatorPriorityClass,
    PrioritizationResponse,
    ResponseStatus,
    SSEMAudit,
    SSEMLike,
    StationType,
    build_security_envelope,
    derive_station_id,
    sim_time_to_cdd,
    synth_srem,
)
from pps57_cits.models import NetworkStateSnapshot, SignalState
from pps57_tsp.actuator import TraciTSPActuator
from pps57_tsp.config import TSPConfig, load_tsp_config
from pps57_tsp.controller import TSPControlController
from pps57_tsp.engine import TSPDecisionEngine
from pps57_tsp.models import DecisionStatus, TSPAction, TSPDecision
from pps57_tsp.safety import TSPSafetyLayer
from pps57_tsp.signal_control import (
    ControllerContract,
    SignalGroupContract,
    TraciSignalControlAdapter,
)
from pps57_opt.policy_runtime import RuntimePolicy, RuntimePolicyRule


class MemoryLogger:
    def __init__(self) -> None:
        self.items = []

    def write(self, item) -> None:
        self.items.append(item)


class FakeProgramAdapter:
    def __init__(self, states: list[str], durations: list[float]) -> None:
        self.states = states
        self.durations = durations

    def read_program_phase_count(self, tls_id: str):
        return len(self.states)

    def read_program_phase_states(self, tls_id: str):
        return list(self.states)

    def read_program_phase_durations(self, tls_id: str):
        return list(self.durations)

    def read_program_is_fixed_time(self, tls_id: str):
        return True

    def read_program_type(self, tls_id: str):
        return "static"

    def set_phase_duration(self, tls_id: str, duration_s: float) -> None:
        raise AssertionError("not used")


class RejectThenNoActuation:
    apply_actuation = True

    def __init__(self) -> None:
        self.calls = []

    def apply(self, decision, signal_state, sim_time_s):
        self.calls.append(decision)
        if len(self.calls) == 1:
            from pps57_tsp.models import ActuationResult

            return ActuationResult(
                decision_id=decision.decision_id,
                timestamp_s=sim_time_s,
                tls_id=decision.tls_id,
                action=decision.action,
                applied=False,
                no_actuation=False,
                command="trafficlight.setPhaseDuration",
                reason="controller_min_command_interval_active",
                severity="warning",
            )
        from pps57_tsp.models import ActuationResult

        return ActuationResult(
            decision_id=decision.decision_id,
            timestamp_s=sim_time_s,
            tls_id=decision.tls_id,
            action=decision.action,
            applied=False,
            no_actuation=True,
            command="trafficlight.setPhaseDuration",
            reason="sumo_no_actuation_flag_would_apply",
        )


class NoActuationRecorder:
    apply_actuation = False

    def __init__(self) -> None:
        self.calls = []

    def apply(self, decision, signal_state, sim_time_s):
        self.calls.append(decision)
        from pps57_tsp.models import ActuationResult

        return ActuationResult(
            decision_id=decision.decision_id,
            timestamp_s=sim_time_s,
            tls_id=decision.tls_id,
            action=decision.action,
            applied=False,
            no_actuation=True,
            command="trafficlight.setPhaseDuration",
            reason="sumo_no_actuation_flag_would_apply",
        )


def configs():
    cits = load_cits_config(ROOT / "configs" / "cits_v2x_config.json", root=ROOT)
    tsp = load_tsp_config(ROOT / "configs" / "tsp_safety_config.json", root=ROOT)
    return cits, tsp


def signal_state(
    *,
    phase: int = 0,
    state: str = "G",
    lane: str = "CITY_EAST_I1_0",
    spent: float = 10.0,
    next_switch: float = 12.0,
) -> SignalState:
    return SignalState(
        intersection_id="I1",
        tls_id="I1",
        rsu_id="RSU_BOAVISTA_01",
        timestamp_s=0.0,
        current_phase_index=phase,
        current_program_id="static",
        red_yellow_green_state=state,
        next_switch_s=next_switch,
        spent_duration_s=spent,
        controlled_lanes=[lane],
        controlled_links=[],
    )


def request(**overrides):
    params = {
        "sim_time_s": 0.0,
        "vehicle_id": "bus_1",
        "intersection_alias": "I1",
        "tls_id": "I1",
        "rsu_id": "RSU_BOAVISTA_01",
        "lane_id": "CITY_EAST_I1_0",
        "next_edge_id": "I1_I2",
        "eta_to_stopline_s": 15.0,
        "distance_to_stopline_m": 80.0,
        "schedule_delay_s": 120.0,
        "operator_priority_class": OperatorPriorityClass.HIGH_DELAY.value,
        "priority_movement_id": "I1_westbound_public_transport",
        "target_signal_group_id_hint": "I1_priority_westbound",
    }
    params.update(overrides)
    return synth_srem(**params)


def early_green_decision(*, priority_level: str, current_phase_index: int = 3) -> TSPDecision:
    """EARLY_GREEN proposto, parametrizado pela classe de prioridade.

    Usado pelos testes que caracterizam a divisão racionamento-vs-clearance da
    preempção de emergência na Safety layer.
    """
    return TSPDecision(
        timestamp_s=10.0,
        request_id="req",
        vehicle_id="ev",
        intersection_id="I1",
        tls_id="I1",
        rsu_id="RSU_BOAVISTA_01",
        action=TSPAction.EARLY_GREEN.value,
        status=DecisionStatus.PROPOSED.value,
        reason="test",
        priority_score=1.0,
        eta_to_stopline_s=5.0,
        schedule_delay_s=0.0,
        headway_deviation_s=0.0,
        priority_level=priority_level,
        current_edge_id="N_I1_I1",
        current_lane_id="N_I1_I1_0",
        next_edge_id="I1_I2",
        priority_movement_id="I1_westbound_public_transport",
        target_signal_group_id="I1_priority_westbound",
        phase_duration_s=2.0,
        target_phase_index=0,
        current_phase_index=current_phase_index,
    )


def processing_ssem(srem) -> SSEMLike:
    primary = srem.requests[0]
    moy, timestamp_ms, generation_delta = sim_time_to_cdd(0.0)
    return SSEMLike(
        message_type=MessageType.SSEM.value,
        station_id=derive_station_id(srem.rsu_id),
        station_type=StationType.ROAD_SIDE_UNIT.value,
        source_id=srem.rsu_id,
        destination_id=srem.source_id,
        generation_delta_time_ms=generation_delta,
        moy=moy,
        timestamp_ms=timestamp_ms,
        security=build_security_envelope(srem.rsu_id, 0.0),
        intersection_alias=srem.intersection_id,
        tls_id=srem.tls_id,
        rsu_id=srem.rsu_id,
        response=PrioritizationResponse(
            request_id=primary.request_id,
            sequence_number=srem.sequence_number,
            requestor_station_id=srem.station_id,
            response_status=ResponseStatus.PROCESSING.value,
        ),
        audit=SSEMAudit(),
    )


class TSPPlatformTests(unittest.TestCase):
    def test_emergency_request_bypasses_normal_score_threshold(self) -> None:
        cits, tsp = configs()
        engine = TSPDecisionEngine(cits, tsp)
        srem = request(
            vehicle_id="ev_1",
            operator_priority_class=OperatorPriorityClass.EMERGENCY.value,
            schedule_delay_s=0.0,
            headway_deviation_s=0.0,
            distance_to_stopline_m=400.0,
            eta_to_stopline_s=20.0,
        )

        decision = engine.decide(srem, signal_state(phase=3, state="r"), 0.0)

        self.assertNotEqual(decision.action, TSPAction.REJECT.value)
        self.assertNotIn("priority_score_below_threshold", decision.reason)

    def test_exact_lane_state_is_used_before_same_edge_fallback(self) -> None:
        cits, tsp = configs()
        engine = TSPDecisionEngine(cits, tsp)
        srem = request(lane_id="CITY_EAST_I1_1")
        state = signal_state(
            state="rG",
            lane="CITY_EAST_I1_0",
        )
        state = state.__class__(
            **{
                **state.__dict__,
                "controlled_lanes": ["CITY_EAST_I1_0", "CITY_EAST_I1_1"],
            }
        )

        self.assertTrue(engine.is_priority_movement_green(srem, state))

    def test_same_edge_fallback_is_disabled_by_default(self) -> None:
        cits, tsp = configs()
        engine = TSPDecisionEngine(cits, tsp)
        srem = request(lane_id="CITY_EAST_I1_1")
        state = signal_state(state="G", lane="CITY_EAST_I1_0")

        self.assertFalse(engine.is_priority_movement_green(srem, state))

    def test_early_green_blocks_when_target_phase_is_already_active(self) -> None:
        cits, tsp = configs()
        safety = TSPSafetyLayer(cits, tsp)
        safety.set_signal_program_verified(True)
        decision = TSPDecision(
            timestamp_s=0.0,
            request_id="req",
            vehicle_id="bus",
            intersection_id="I1",
            tls_id="I1",
            rsu_id="RSU_BOAVISTA_01",
            action=TSPAction.EARLY_GREEN.value,
            status=DecisionStatus.PROPOSED.value,
            reason="test",
            priority_score=1.0,
            eta_to_stopline_s=20.0,
            schedule_delay_s=120.0,
            headway_deviation_s=0.0,
            current_edge_id="CITY_EAST_I1",
            current_lane_id="CITY_EAST_I1_0",
            next_edge_id="I1_I2",
            priority_movement_id="I1_westbound_public_transport",
            target_signal_group_id="I1_priority_westbound",
            phase_duration_s=2.0,
            target_phase_index=0,
            current_phase_index=0,
        )

        result = safety.validate(decision, signal_state(phase=0, state="G"), 0.0)

        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "early_green_target_phase_already_active")

    def test_degraded_network_state_does_not_mark_blocked_decision_as_granted(self) -> None:
        cits, tsp = configs()
        controller = TSPControlController(cits, tsp)
        controller.safety.set_signal_program_verified(True)
        srem = request()
        controller.request_store.ingest_requests([srem], 0.0)
        response = processing_ssem(srem)
        decision_log = MemoryLogger()
        actuation_log = MemoryLogger()

        final = controller._process_acknowledged_requests(
            responses=[response],
            requests_by_id={},
            signal_states={"I1": signal_state(phase=0, state="G", next_switch=12.0, spent=10.0)},
            network_states={
                "I1": NetworkStateSnapshot(
                    tls_id="I1",
                    timestamp_s=0.0,
                    degraded=True,
                    detector_read_failures=1,
                )
            },
            actuator=TraciTSPActuator(adapter=object(), apply_actuation=False),
            sim_time_s=0.0,
            decision_logger=decision_log,
            actuation_logger=actuation_log,
            decisions=[],
            actuations=[],
        )

        self.assertEqual(len(final), 1)
        self.assertEqual(decision_log.items[0].status, DecisionStatus.BLOCKED_BY_SAFETY.value)
        self.assertEqual(controller.request_store.granted_count, 0)
        self.assertEqual(controller.safety.last_intervention_time_by_tls, {})

    def test_controller_rejection_does_not_consume_tls_step_slot(self) -> None:
        cits, tsp = configs()
        controller = TSPControlController(cits, tsp)
        controller.safety.set_signal_program_verified(True)
        first = request(vehicle_id="bus_1", request_id=1, sequence_number=1)
        second = request(
            vehicle_id="bus_2", request_id=2, sequence_number=1, eta_to_stopline_s=16.0
        )
        responses = [processing_ssem(first), processing_ssem(second)]
        requests_by_id = {first.request_id: first, second.request_id: second}
        actuator = RejectThenNoActuation()

        controller._process_acknowledged_requests(
            responses=responses,
            requests_by_id=requests_by_id,
            signal_states={"I1": signal_state(phase=0, state="G", next_switch=12.0, spent=10.0)},
            network_states={},
            actuator=actuator,
            sim_time_s=0.0,
            decision_logger=MemoryLogger(),
            actuation_logger=MemoryLogger(),
            decisions=[],
            actuations=[],
        )

        self.assertEqual(len(actuator.calls), 2)

    def test_delayed_response_sorting_uses_request_store_priority(self) -> None:
        cits, tsp = configs()
        controller = TSPControlController(cits, tsp)
        controller.safety.set_signal_program_verified(True)
        low = request(vehicle_id="bus_low", request_id=1, sequence_number=1, eta_to_stopline_s=18.0)
        high = request(
            vehicle_id="ev_high",
            request_id=2,
            sequence_number=1,
            operator_priority_class=OperatorPriorityClass.EMERGENCY.value,
            eta_to_stopline_s=18.0,
        )
        controller.request_store.ingest_requests([low, high], 0.0)
        actuator = NoActuationRecorder()

        controller._process_acknowledged_requests(
            responses=[processing_ssem(low), processing_ssem(high)],
            requests_by_id={},
            signal_states={"I1": signal_state(phase=0, state="G", next_switch=12.0, spent=10.0)},
            network_states={},
            actuator=actuator,
            sim_time_s=0.0,
            decision_logger=MemoryLogger(),
            actuation_logger=MemoryLogger(),
            decisions=[],
            actuations=[],
        )

        self.assertEqual(actuator.calls[0].vehicle_id, "ev_high")

    def test_dry_run_actuation_is_reported_as_not_granted(self) -> None:
        cits, tsp = configs()
        controller = TSPControlController(cits, tsp)
        controller.safety.set_signal_program_verified(True)
        srem = request()
        final = controller._process_acknowledged_requests(
            responses=[processing_ssem(srem)],
            requests_by_id={srem.request_id: srem},
            signal_states={"I1": signal_state(phase=0, state="G", next_switch=12.0, spent=10.0)},
            network_states={},
            actuator=TraciTSPActuator(adapter=object(), apply_actuation=False),
            sim_time_s=0.0,
            decision_logger=MemoryLogger(),
            actuation_logger=MemoryLogger(),
            decisions=[],
            actuations=[],
        )

        self.assertEqual(final[0].status, ResponseStatus.REJECTED.value)
        self.assertIn("tsp_would_grant_not_applied=true", final[0].audit.notes)
        self.assertEqual(controller.request_store.granted_count, 0)

    def test_cancellation_request_clears_store_without_actuation(self) -> None:
        cits, tsp = configs()
        controller = TSPControlController(cits, tsp)
        active = request(vehicle_id="bus_cancel", request_id=1)
        cancellation = request(
            vehicle_id="bus_cancel", request_id=2, request_type="priorityCancellation"
        )
        controller.request_store.ingest_requests([active], 0.0)
        decision_log = MemoryLogger()

        final = controller._process_acknowledged_requests(
            responses=[processing_ssem(cancellation)],
            requests_by_id={cancellation.request_id: cancellation},
            signal_states={"I1": signal_state(phase=0, state="G")},
            network_states={},
            actuator=NoActuationRecorder(),
            sim_time_s=1.0,
            decision_logger=decision_log,
            actuation_logger=MemoryLogger(),
            decisions=[],
            actuations=[],
        )

        summary = controller.request_store.to_summary()
        self.assertEqual(
            decision_log.items[0].reason, "priority_request_cancellation_no_tsp_actuation"
        )
        self.assertEqual(final[0].status, ResponseStatus.REJECTED.value)
        self.assertEqual(summary["by_status"]["cleared"], 1)

    def test_early_green_blocks_when_current_phase_is_not_configured_conflict(self) -> None:
        cits, tsp = configs()
        safety = TSPSafetyLayer(cits, tsp)
        safety.set_signal_program_verified(True)
        decision = TSPDecision(
            timestamp_s=0.0,
            request_id="req",
            vehicle_id="bus",
            intersection_id="I1",
            tls_id="I1",
            rsu_id="RSU_BOAVISTA_01",
            action=TSPAction.EARLY_GREEN.value,
            status=DecisionStatus.PROPOSED.value,
            reason="test",
            priority_score=1.0,
            eta_to_stopline_s=20.0,
            schedule_delay_s=120.0,
            headway_deviation_s=0.0,
            current_edge_id="CITY_EAST_I1",
            current_lane_id="CITY_EAST_I1_0",
            next_edge_id="I1_I2",
            priority_movement_id="I1_westbound_public_transport",
            target_signal_group_id="I1_priority_westbound",
            phase_duration_s=2.0,
            target_phase_index=0,
            current_phase_index=2,
        )

        result = safety.validate(decision, signal_state(phase=2, state="rrr", spent=10.0), 0.0)

        self.assertFalse(result.approved)
        self.assertEqual(result.reason, "early_green_current_phase_signal_group_unknown")

    def test_all_red_verification_is_per_service_transition(self) -> None:
        contract = ControllerContract(
            tls_id="I1",
            adapter_type="sumo_traci",
            fixed_time_required=True,
            allowed_actions=[TSPAction.GREEN_EXTENSION.value, TSPAction.EARLY_GREEN.value],
            phase_sequence=[0, 1, 2, 3, 4, 5],
            service_green_phase_indices=[0, 3],
            intergreen_phase_indices=[1, 2, 4, 5],
            min_yellow_s=None,
            min_all_red_s=1.0,
            expected_cycle_s=None,
            pedestrian_phase_required=False,
            pedestrian_phase_indices=[],
            signal_groups={
                "I1_priority_westbound": SignalGroupContract(
                    signal_group_id="I1_priority_westbound",
                    phase_index=0,
                    movement_ids=["I1_westbound_public_transport"],
                    allowed_actions=[TSPAction.GREEN_EXTENSION.value, TSPAction.EARLY_GREEN.value],
                    conflicts_with=["I1_secondary"],
                ),
                "I1_secondary": SignalGroupContract(
                    signal_group_id="I1_secondary",
                    phase_index=3,
                    allowed_actions=[],
                    conflicts_with=["I1_priority_westbound"],
                ),
            },
        )
        adapter = TraciSignalControlAdapter(
            FakeProgramAdapter(
                states=["Grr", "yyy", "rrr", "rGr", "yyy", "yyy"],
                durations=[30.0, 3.0, 1.0, 30.0, 3.0, 1.0],
            )
        )

        problems = adapter.verify_controller_contracts([contract])

        self.assertTrue(any("transição 3->0" in problem for problem in problems))
        self.assertFalse(any("transição 0->3" in problem for problem in problems))

    def test_pedestrian_phase_must_be_explicitly_configured(self) -> None:
        contract = ControllerContract(
            tls_id="I1",
            adapter_type="sumo_traci",
            fixed_time_required=False,
            allowed_actions=[TSPAction.GREEN_EXTENSION.value],
            phase_sequence=[0, 1, 2, 3, 4, 5],
            service_green_phase_indices=[0, 3],
            intergreen_phase_indices=[1, 2, 4, 5],
            min_yellow_s=None,
            min_all_red_s=None,
            expected_cycle_s=None,
            pedestrian_phase_required=True,
            pedestrian_phase_indices=[],
            signal_groups={
                "I1_priority_westbound": SignalGroupContract(
                    signal_group_id="I1_priority_westbound",
                    phase_index=0,
                    movement_ids=["I1_westbound_public_transport"],
                    allowed_actions=[TSPAction.GREEN_EXTENSION.value],
                    conflicts_with=["I1_secondary"],
                ),
                "I1_secondary": SignalGroupContract(
                    signal_group_id="I1_secondary",
                    phase_index=3,
                    allowed_actions=[],
                    conflicts_with=["I1_priority_westbound"],
                ),
            },
        )
        adapter = TraciSignalControlAdapter(
            FakeProgramAdapter(
                states=["Grr", "yyy", "rrr", "rGr", "yyy", "rrr", "ggg"],
                durations=[30.0, 3.0, 1.0, 30.0, 3.0, 1.0, 20.0],
            )
        )

        problems = adapter.verify_controller_contracts([contract])
        configured = contract.__class__(**{**contract.__dict__, "pedestrian_phase_indices": [6]})
        configured_problems = adapter.verify_controller_contracts([configured])

        self.assertTrue(
            any("fase pedonal exclusiva configurada" in problem for problem in problems)
        )
        self.assertFalse(
            any("fase pedonal exclusiva configurada" in problem for problem in configured_problems)
        )

    def test_priority_score_tolerates_zero_normalisation_config(self) -> None:
        cits, tsp = configs()
        raw = copy.deepcopy(tsp.raw)
        raw["decision_policy"]["delay_normalisation_s"] = 0
        raw["decision_policy"]["headway_normalisation_s"] = 0
        raw["decision_policy"]["distance_normalisation_m"] = 0
        engine = TSPDecisionEngine(cits, TSPConfig(root=tsp.root, raw=raw))

        score = engine.priority_score(request())

        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_decision_tolerates_invalid_numeric_policy_fields(self) -> None:
        cits, tsp = configs()
        raw = copy.deepcopy(tsp.raw)
        raw["decision_policy"].update(
            {
                "eta_arrival_buffer_s": "bad",
                "green_extension_min_s": -1,
                "green_extension_max_s": "bad",
                "green_extension_default_s": 0,
                "early_green_min_eta_s": "bad",
                "red_truncation_to_s": "bad",
                "weights": {"schedule_delay": "bad"},
            }
        )
        engine = TSPDecisionEngine(cits, TSPConfig(root=tsp.root, raw=raw))

        decision = engine.decide(request(), signal_state(phase=0, state="G", next_switch=12.0), 0.0)

        self.assertIn(decision.action, {TSPAction.GREEN_EXTENSION.value, TSPAction.NO_ACTION.value})

    def test_emergency_preemption_bypasses_cooldown_but_keeps_clearance_checks(self) -> None:
        cits, tsp = configs()
        safety = TSPSafetyLayer(cits, tsp)
        safety.set_signal_program_verified(True)
        safety.last_intervention_time_by_tls["I1"] = 9.0
        decision = TSPDecision(
            timestamp_s=10.0,
            request_id="req",
            vehicle_id="ev",
            intersection_id="I1",
            tls_id="I1",
            rsu_id="RSU_BOAVISTA_01",
            action=TSPAction.EARLY_GREEN.value,
            status=DecisionStatus.PROPOSED.value,
            reason="test",
            priority_score=1.0,
            eta_to_stopline_s=5.0,
            schedule_delay_s=0.0,
            headway_deviation_s=0.0,
            priority_level=OperatorPriorityClass.EMERGENCY.value,
            current_edge_id="N_I1_I1",
            current_lane_id="N_I1_I1_0",
            next_edge_id="I1_I2",
            priority_movement_id="I1_westbound_public_transport",
            target_signal_group_id="I1_priority_westbound",
            phase_duration_s=2.0,
            target_phase_index=0,
            current_phase_index=3,
        )

        result = safety.validate(
            decision, signal_state(phase=3, state="rG", lane="N_I1_I1_0", spent=10.0), 10.0
        )

        self.assertNotEqual(result.reason, "cooldown_after_priority_active")

    def test_emergency_preemption_bypasses_recovery_debt_cap(self) -> None:
        # Caps de RACIONAMENTO (recovery-debt) protegem capacidade da
        # transversal, não segurança física: a emergência ultrapassa-os. Com a
        # dívida acima do tecto, o autocarro é bloqueado mas a emergência passa.
        # Lock-in do invariante de safety.py (follow-up dos caps na PR #57).
        cits, tsp = configs()
        max_debt = cits.safety_constraints.get("max_recovery_debt_s")
        self.assertIsNotNone(max_debt)

        def validate(priority_level: str):
            safety = TSPSafetyLayer(cits, tsp)
            safety.set_signal_program_verified(True)
            safety.recovery_debt_by_tls["I1"] = float(max_debt) + 100.0
            safety.recovery_debt_update_time_by_tls["I1"] = 10.0
            return safety.validate(
                early_green_decision(priority_level=priority_level),
                signal_state(phase=3, state="rG", lane="N_I1_I1_0", spent=10.0, next_switch=20.0),
                10.0,
            )

        bus = validate(OperatorPriorityClass.HIGH_DELAY.value)
        self.assertFalse(bus.approved)
        self.assertEqual(bus.reason, "recovery_debt_limit_active")

        emergency = validate(OperatorPriorityClass.EMERGENCY.value)
        self.assertTrue(emergency.approved)
        self.assertNotEqual(emergency.reason, "recovery_debt_limit_active")

    def test_emergency_preemption_still_blocked_by_min_green_clearance(self) -> None:
        # Caps de CLEARANCE (min-green da fase conflituante) são segurança
        # física — peões/veículos a libertar o cruzamento — e NÃO são bypassed
        # nem para emergência. Alinha com a preempção real (NEMA TS2 / MUTCD):
        # encurta mas nunca elimina min-green + amarelo + all-red.
        cits, tsp = configs()
        min_green = cits.safety_constraints.get("min_green_s")
        self.assertIsNotNone(min_green)
        spent = float(min_green) - 1.0  # fase conflituante ainda não serviu o min-green

        safety = TSPSafetyLayer(cits, tsp)
        safety.set_signal_program_verified(True)
        result = safety.validate(
            early_green_decision(priority_level=OperatorPriorityClass.EMERGENCY.value),
            signal_state(phase=3, state="rG", lane="N_I1_I1_0", spent=spent, next_switch=20.0),
            10.0,
        )
        self.assertFalse(result.approved)
        self.assertTrue(result.reason.startswith("min_green_not_satisfied"))

    def test_runtime_policy_cannot_suppress_baseline_actuation_by_default(self) -> None:
        cits, tsp = configs()
        srem = request()
        state = signal_state(phase=3, state="rG", spent=10.0)
        baseline = TSPDecisionEngine(cits, tsp).decide(srem, state, 0.0)
        self.assertTrue(baseline.requires_actuation)
        bucket = "priority_movement_not_green|eta_mid|delay_high|switch_open|traffic_pressure_low|intervention_unknown"
        policy = RuntimePolicy(
            tsp_config=tsp,
            rules={
                bucket: RuntimePolicyRule(
                    state_bucket=bucket,
                    action=TSPAction.REJECT.value,
                    reward=10.0,
                )
            },
            is_reinforcement_learning=True,
        )

        decision = policy.decide(srem, state, 0.0, baseline)

        self.assertEqual(decision.action, baseline.action)
        self.assertTrue(
            any("did not suppress baseline actuation" in note for note in decision.notes)
        )


if __name__ == "__main__":
    unittest.main()
