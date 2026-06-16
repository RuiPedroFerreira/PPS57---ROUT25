#!/usr/bin/env python3
"""Regression tests for C-ITS emulation robustness and protocol correctness."""

from __future__ import annotations

import sys
import tempfile
import unittest
import zlib
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.audit import audit_protocol_lifecycle
from pps57_cits.config import (
    CITSConfig,
    PriorityMovementConfig,
    load_cits_config,
)
from pps57_cits.controller import CITSEmulationController
from pps57_cits.event_logger import CITSJsonlLogger, IncrementalCITSSummary
from pps57_cits.map_spat import build_spatem_message_from_state
from pps57_cits.messages import (
    EventState,
    GrantedStrategy,
    MessageType,
    OperatorPriorityClass,
    RequestType,
    ResponseStatus,
    SPATEMLike,
    StationType,
    build_security_envelope,
    derive_station_id,
    parse_intersection_ref_id,
    sumo_link_char_to_event_state,
    synth_srem,
    validate_cits_message,
)
from pps57_cits.models import SignalState, VehicleObservation
from pps57_cits.obu import OBUEmulator
from pps57_cits.protocol_codec import JsonSimulationCodec
from pps57_cits.rsu import RSUAgent
from pps57_cits.traci_adapter import TraciSimulationAdapter

try:
    from traci.exceptions import TraCIException as _VehicleReadError
except ImportError:

    class _VehicleReadError(Exception):
        pass


def _load_config() -> CITSConfig:
    return load_cits_config(ROOT / "configs/cits_v2x_config.json", root=ROOT)


def _eligible_srem(**overrides):
    params = dict(
        sim_time_s=100.0,
        vehicle_id="bus_1",
        intersection_alias="I2",
        tls_id="I2",
        rsu_id="RSU_BOAVISTA_02",
        lane_id="I1_I2_0",
        line_id="STCP500_PROXY_W",
        route_id="route_boavista_east_to_west",
        eta_to_stopline_s=15.0,
        distance_to_stopline_m=150.0,
        speed_mps=10.0,
        schedule_delay_s=90.0,
        headway_deviation_s=0.0,
        operator_priority_class=OperatorPriorityClass.HIGH_DELAY.value,
        ttl_s=30.0,
    )
    params.update(overrides)
    return synth_srem(**params)


def _signal_state(ryg, **overrides) -> SignalState:
    params = dict(
        intersection_id="I2",
        tls_id="I2",
        rsu_id="RSU_BOAVISTA_02",
        timestamp_s=10.0,
        current_phase_index=0,
        current_program_id="0",
        red_yellow_green_state=ryg,
        next_switch_s=25.0,
        spent_duration_s=2.0,
    )
    params.update(overrides)
    return SignalState(**params)


def _bus_observation(**overrides) -> VehicleObservation:
    params = dict(
        vehicle_id="bus_STCP500_W_UNIT",
        vehicle_class="bus",
        type_id="bus_12m",
        line_id="STCP500_PROXY_W",
        route_id="route_boavista_east_to_west",
        edge_id="I1_I2",
        lane_id="I1_I2_0",
        lane_position_m=500.0,
        lane_length_m=650.0,
        speed_mps=10.0,
        schedule_delay_s=90.0,
    )
    params.update(overrides)
    return VehicleObservation(**params)


class DegradedTlsReadTestCase(unittest.TestCase):
    """A transient TLS read failure must not abort the whole emulation run."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = _load_config()

    def test_spatem_from_degraded_tls_read_is_valid_and_flagged(self) -> None:
        spatem = build_spatem_message_from_state(
            _signal_state(None, next_switch_s=None, spent_duration_s=None)
        )
        self.assertEqual(
            spatem.intersection_status,
            {"noValidSPATisAvailableAtThisTime": True},
        )
        self.assertEqual(len(spatem.movement_events), 1)
        self.assertEqual(spatem.movement_events[0].event_state, EventState.UNAVAILABLE.value)
        self.assertEqual(validate_cits_message(spatem), [])
        JsonSimulationCodec().encode(spatem)  # must not raise

    def test_spatem_from_healthy_tls_read_is_unchanged(self) -> None:
        spatem = build_spatem_message_from_state(_signal_state("rG"))
        self.assertEqual(spatem.intersection_status, {})
        self.assertEqual(len(spatem.movement_events), 2)
        self.assertEqual(validate_cits_message(spatem), [])

    def test_controller_counts_codec_failure_instead_of_raising(self) -> None:
        controller = CITSEmulationController(self.config)
        invalid_spatem = SPATEMLike(
            message_type=MessageType.SPATEM.value,
            station_id=derive_station_id("RSU_BOAVISTA_02"),
            station_type=StationType.ROAD_SIDE_UNIT.value,
            source_id="RSU_BOAVISTA_02",
            destination_id="BROADCAST",
            generation_delta_time_ms=0,
            moy=0,
            timestamp_ms=0,
            security=build_security_envelope("RSU_BOAVISTA_02", 0.0),
            intersection_ref_id=2,
            intersection_alias="I2",
            tls_id="I2",
            revision=1,
            movement_events=[],  # codec rejects: spatem.movement_events_missing
        )
        summary = IncrementalCITSSummary()
        with tempfile.TemporaryDirectory() as tmp:
            with CITSJsonlLogger(Path(tmp) / "cits.jsonl") as logger:
                controller._publish_log_collect([invalid_spatem], logger, summary)
        self.assertEqual(controller.publish_codec_failures, {"SPATEM": 1})
        self.assertEqual(summary.total, 0)


class AuditSupersededSequenceTestCase(unittest.TestCase):
    """Healthy request updates must not inflate missing_final_ssem."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = _load_config()

    def test_request_update_then_final_ssem_has_no_missing_final(self) -> None:
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        initial = _eligible_srem(sequence_number=1)
        update = _eligible_srem(
            sequence_number=2,
            request_type=RequestType.PRIORITY_REQUEST_UPDATE.value,
            sim_time_s=105.0,
        )
        processing = rsu.evaluate_request(update, sim_time_s=105.0)
        final = deepcopy(processing)
        final.response.response_status = ResponseStatus.GRANTED.value
        final.audit.granted_strategy = GrantedStrategy.GREEN_EXTENSION.value
        final.audit.rejection_reason = None

        with tempfile.TemporaryDirectory() as tmp:
            cits_path = Path(tmp) / "cits.jsonl"
            cits_path.write_text(
                "\n".join(
                    [initial.to_json(), update.to_json(), processing.to_json(), final.to_json()]
                )
                + "\n",
                encoding="utf-8",
            )
            audit = audit_protocol_lifecycle(cits_path)

        self.assertEqual(audit["protocol_kpis"]["missing_final_ssem"], 0)
        self.assertEqual(audit["missing_final_request_keys"], [])
        self.assertEqual(audit["protocol_kpis"]["superseded_request_chains"], 1)
        self.assertEqual(audit["final_ssem_by_status"], {"granted": 1})

    def test_request_without_any_final_still_counts_as_missing(self) -> None:
        initial = _eligible_srem(sequence_number=1)
        with tempfile.TemporaryDirectory() as tmp:
            cits_path = Path(tmp) / "cits.jsonl"
            cits_path.write_text(initial.to_json() + "\n", encoding="utf-8")
            audit = audit_protocol_lifecycle(cits_path)
        self.assertEqual(audit["protocol_kpis"]["missing_final_ssem"], 1)


class _FakeLaneDomain:
    def __init__(self, occupancy_pct: float) -> None:
        self._occupancy_pct = occupancy_pct

    def getLastStepVehicleNumber(self, lane_id: str) -> int:
        return 1

    def getLastStepHaltingNumber(self, lane_id: str) -> int:
        return 0

    def getLastStepMeanSpeed(self, lane_id: str) -> float:
        return 5.0

    def getWaitingTime(self, lane_id: str) -> float:
        return 0.0

    def getLastStepOccupancy(self, lane_id: str) -> float:
        return self._occupancy_pct


class _FakeNetworkTraci:
    def __init__(self, occupancy_pct: float) -> None:
        self.lane = _FakeLaneDomain(occupancy_pct)


class OccupancyUnitTestCase(unittest.TestCase):
    """traci==1.26.0 reports lane occupancy in percent; never guess the unit."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = _load_config()

    def _snapshot(self, occupancy_pct: float):
        adapter = TraciSimulationAdapter(self.config)
        adapter.traci = _FakeNetworkTraci(occupancy_pct)
        intersection = self.config.intersection_by_alias["I2"]
        signal_state = _signal_state("rG", controlled_lanes=["I1_I2_0"])
        return adapter.read_network_state(intersection, signal_state, 0.0)

    def test_low_percentage_is_not_read_as_high_fraction(self) -> None:
        # 0.8% occupancy = nearly empty lane; the magnitude heuristic used to
        # read it as a 0.8 fraction and trip spillback_risk.
        snapshot = self._snapshot(0.8)
        self.assertAlmostEqual(snapshot.occupancy, 0.008)
        self.assertFalse(snapshot.spillback_risk)
        self.assertFalse(snapshot.degraded)

    def test_high_percentage_still_triggers_spillback(self) -> None:
        snapshot = self._snapshot(80.0)
        self.assertAlmostEqual(snapshot.occupancy, 0.8)
        self.assertTrue(snapshot.spillback_risk)


class _FakeVehicleDomain:
    def __init__(self, road_by_vehicle: dict, failing: set) -> None:
        self._road_by_vehicle = dict(road_by_vehicle)
        self._failing = set(failing)

    def getIDList(self):
        return list(self._road_by_vehicle)

    def getRoadID(self, vehicle_id: str) -> str:
        return self._road_by_vehicle[vehicle_id]

    def getLaneID(self, vehicle_id: str) -> str:
        if vehicle_id in self._failing:
            raise _VehicleReadError(f"vehicle {vehicle_id} is gone")
        return f"{self._road_by_vehicle[vehicle_id]}_0"


class _FakeSimulationDomain:
    def getTime(self) -> float:
        return 100.0


class _FakeVehicleTraci:
    def __init__(self, road_by_vehicle: dict, failing: set) -> None:
        self.vehicle = _FakeVehicleDomain(road_by_vehicle, failing)
        self.simulation = _FakeSimulationDomain()


class VehicleReadFailureTestCase(unittest.TestCase):
    """Skipped per-vehicle reads are counted, not silently swallowed."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = _load_config()

    def test_failed_vehicle_read_is_counted_and_skipped(self) -> None:
        adapter = TraciSimulationAdapter(self.config)
        adapter.traci = _FakeVehicleTraci({"bus_gone": "I1_I2"}, failing={"bus_gone"})
        observations = adapter.read_vehicle_observations()
        self.assertEqual(observations, [])
        self.assertEqual(adapter.vehicle_read_failures, 1)

    def test_off_corridor_vehicle_is_not_a_read_failure(self) -> None:
        adapter = TraciSimulationAdapter(self.config)
        adapter.traci = _FakeVehicleTraci({"car_far": "OFF_CORRIDOR_EDGE"}, failing=set())
        observations = adapter.read_vehicle_observations()
        self.assertEqual(observations, [])
        self.assertEqual(adapter.vehicle_read_failures, 0)


class OBUStatePruneTestCase(unittest.TestCase):
    """OBU per-vehicle state must not grow forever after vehicles depart."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = _load_config()

    def test_state_is_pruned_after_retention_window(self) -> None:
        obu = OBUEmulator(self.config)
        observation = _bus_observation()
        initial = obu.generate_requests([observation], sim_time_s=10.0)
        self.assertEqual(len(initial), 1)
        self.assertIn(observation.vehicle_id, obu.state_by_vehicle)

        # Vehicle departs: cancellation goes out, state is retained so a quick
        # return cannot reuse sequence numbers still in the RSU replay cache.
        followup = obu.generate_requests([], sim_time_s=11.0)
        self.assertEqual(len(followup), 1)
        self.assertTrue(followup[0].is_cancellation)
        self.assertIn(observation.vehicle_id, obu.state_by_vehicle)

        retention_s = float(self.config.obu_policy.get("state_retention_s", 60.0))
        late = obu.generate_requests([], sim_time_s=11.0 + retention_s)
        self.assertEqual(late, [])
        self.assertEqual(obu.state_by_vehicle, {})

    def test_observed_vehicle_state_is_kept(self) -> None:
        obu = OBUEmulator(self.config)
        observation = _bus_observation()
        obu.generate_requests([observation], sim_time_s=10.0)
        obu.generate_requests([observation], sim_time_s=500.0)
        self.assertIn(observation.vehicle_id, obu.state_by_vehicle)


class VehicleClassFilterTestCase(unittest.TestCase):
    """vehicle_classes filters must not fall back to a non-matching movement."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = _load_config()

    def test_no_movement_for_vehicle_class_outside_catalogue(self) -> None:
        # All I2 movements are restricted to public_transport.
        resolved = self.config.priority_movement_for_request(
            edge_id="I1_I2", vehicle_class="passenger"
        )
        self.assertIsNone(resolved)

    def test_matching_vehicle_class_still_resolves(self) -> None:
        resolved = self.config.priority_movement_for_request(edge_id="I1_I2", vehicle_class="bus")
        self.assertIsNotNone(resolved)
        self.assertIn("public_transport", resolved.vehicle_classes)

    def test_unrestricted_movements_keep_fallback(self) -> None:
        movement = PriorityMovementConfig(
            movement_id="m_open",
            direction="",
            approach_edges=["E1"],
            egress_edges=[],
            vehicle_classes=[],
            target_signal_group_id="sg1",
            allowed_actions=[],
            objectives=[],
        )
        config = CITSConfig(
            root=ROOT,
            raw={},
            intersections=[],
            edge_to_intersection={},
            edge_to_priority_movements={"E1": [movement]},
            movement_by_id={"m_open": movement},
            rsu_to_intersection={},
            tls_to_intersection={},
            intersection_by_alias={},
        )
        resolved = config.priority_movement_for_request(edge_id="E1", vehicle_class="passenger")
        self.assertIs(resolved, movement)


class IntersectionRefIdTestCase(unittest.TestCase):
    """Digit concatenation made distinct aliases collide on the same uint16."""

    def test_canonical_aliases_keep_simple_numeric_mapping(self) -> None:
        self.assertEqual(parse_intersection_ref_id("I1"), 1)
        self.assertEqual(parse_intersection_ref_id("I12"), 12)

    def test_exotic_aliases_no_longer_collide_with_canonical_ids(self) -> None:
        ref_ids = {
            alias: parse_intersection_ref_id(alias) for alias in ("I12", "TLS_1_2", "cluster_1_2")
        }
        self.assertEqual(len(set(ref_ids.values())), 3)

    def test_exotic_alias_hash_is_deterministic_and_uint16(self) -> None:
        for alias in ("TLS_1_2", "cluster_1_2", "joinedS_42_99"):
            ref_id = parse_intersection_ref_id(alias)
            self.assertEqual(ref_id, zlib.crc32(alias.encode("utf-8")) & 0xFFFF)
            self.assertGreaterEqual(ref_id, 0)
            self.assertLessEqual(ref_id, 65535)

    def test_producer_and_consumer_derive_the_same_ref_id(self) -> None:
        request = _eligible_srem(intersection_alias="cluster_1_2", tls_id="cluster_1_2")
        self.assertEqual(
            request.requests[0].intersection_ref_id,
            parse_intersection_ref_id("cluster_1_2"),
        )


class StopThenProceedMappingTestCase(unittest.TestCase):
    """SUMO 's' (right-turn-on-red) is not a protected green movement."""

    def test_s_char_maps_to_stop_then_proceed(self) -> None:
        self.assertEqual(sumo_link_char_to_event_state("s"), EventState.STOP_THEN_PROCEED.value)
        self.assertNotEqual(
            sumo_link_char_to_event_state("s"),
            EventState.PROTECTED_MOVEMENT_ALLOWED.value,
        )

    def test_spatem_with_s_state_survives_codec_roundtrip(self) -> None:
        spatem = build_spatem_message_from_state(_signal_state("Gsr"))
        states = [event.event_state for event in spatem.movement_events]
        self.assertEqual(
            states,
            [
                EventState.PROTECTED_MOVEMENT_ALLOWED.value,
                EventState.STOP_THEN_PROCEED.value,
                EventState.STOP_AND_REMAIN.value,
            ],
        )
        codec = JsonSimulationCodec()
        decoded = codec.decode(codec.encode(spatem))
        self.assertEqual(
            decoded.movement_events[1].event_state,
            EventState.STOP_THEN_PROCEED.value,
        )


class RSUActiveRequestCapTestCase(unittest.TestCase):
    """max_active_requests_per_rsu must apply across ticks, not per batch."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = _load_config()
        cls.max_active = int(cls.config.rsu_policy.get("max_active_requests_per_rsu", 4))

    def _rsu(self) -> RSUAgent:
        return RSUAgent(self.config, self.config.rsu_to_intersection["RSU_BOAVISTA_02"])

    def _fill_cap(self, rsu: RSUAgent, sim_time_s: float) -> None:
        batch = [
            _eligible_srem(
                vehicle_id=f"bus_active_{index}",
                request_id=index + 1,
                sequence_number=1,
                sim_time_s=sim_time_s,
            )
            for index in range(self.max_active)
        ]
        responses = rsu.handle_messages(batch, sim_time_s)
        for response in responses:
            self.assertEqual(response.status, ResponseStatus.PROCESSING.value)

    def test_cap_counts_processing_requests_from_previous_ticks(self) -> None:
        rsu = self._rsu()
        self._fill_cap(rsu, sim_time_s=101.0)
        overflow = _eligible_srem(
            vehicle_id="bus_overflow", request_id=50, sequence_number=1, sim_time_s=102.0
        )
        response = rsu.handle_messages([overflow], sim_time_s=102.0)[0]
        self.assertEqual(response.status, ResponseStatus.REJECTED.value)
        self.assertEqual(response.reason, "rsu_active_request_limit_exceeded")

    def test_update_of_active_request_is_not_blocked_by_cap(self) -> None:
        rsu = self._rsu()
        self._fill_cap(rsu, sim_time_s=101.0)
        update = _eligible_srem(
            vehicle_id="bus_active_0",
            request_id=1,
            sequence_number=2,
            request_type=RequestType.PRIORITY_REQUEST_UPDATE.value,
            sim_time_s=102.0,
        )
        response = rsu.handle_messages([update], sim_time_s=102.0)[0]
        self.assertEqual(response.status, ResponseStatus.PROCESSING.value)

    def test_cancellation_frees_a_slot(self) -> None:
        rsu = self._rsu()
        self._fill_cap(rsu, sim_time_s=101.0)
        cancel = _eligible_srem(
            vehicle_id="bus_active_0",
            request_id=1,
            sequence_number=2,
            request_type=RequestType.PRIORITY_CANCELLATION.value,
            sim_time_s=102.0,
        )
        ack = rsu.handle_messages([cancel], sim_time_s=102.0)[0]
        self.assertEqual(ack.status, ResponseStatus.UNKNOWN.value)
        newcomer = _eligible_srem(
            vehicle_id="bus_newcomer", request_id=60, sequence_number=1, sim_time_s=103.0
        )
        response = rsu.handle_messages([newcomer], sim_time_s=103.0)[0]
        self.assertEqual(response.status, ResponseStatus.PROCESSING.value)

    def test_expired_requests_stop_counting_toward_cap(self) -> None:
        rsu = self._rsu()
        self._fill_cap(rsu, sim_time_s=101.0)  # TTL 30 s -> expire at 131.0
        late = _eligible_srem(
            vehicle_id="bus_late", request_id=70, sequence_number=1, sim_time_s=200.0
        )
        response = rsu.handle_messages([late], sim_time_s=200.0)[0]
        self.assertEqual(response.status, ResponseStatus.PROCESSING.value)

    def test_rejected_update_does_not_free_active_slot(self) -> None:
        """Rejected *update* of a PROCESSING request must not pop the quota slot (issue #47)."""
        rsu = self._rsu()
        self._fill_cap(rsu, sim_time_s=101.0)
        # Update for request_id=1 with ETA below the window → REJECTED
        rejected_update = _eligible_srem(
            vehicle_id="bus_active_0",
            request_id=1,
            sequence_number=2,
            request_type=RequestType.PRIORITY_REQUEST_UPDATE.value,
            eta_to_stopline_s=2.0,
            sim_time_s=102.0,
        )
        update_response = rsu.handle_messages([rejected_update], sim_time_s=102.0)[0]
        self.assertEqual(update_response.status, ResponseStatus.REJECTED.value)
        # Slot must still be held — newcomer must be turned away
        newcomer = _eligible_srem(
            vehicle_id="bus_newcomer", request_id=60, sequence_number=1, sim_time_s=103.0
        )
        newcomer_response = rsu.handle_messages([newcomer], sim_time_s=103.0)[0]
        self.assertEqual(newcomer_response.status, ResponseStatus.REJECTED.value)
        self.assertEqual(newcomer_response.reason, "rsu_active_request_limit_exceeded")

    def test_downstream_grant_frees_a_slot(self) -> None:
        rsu = self._rsu()
        self._fill_cap(rsu, sim_time_s=101.0)
        rsu.mark_priority_granted("bus_active_0", sim_time_s=102.0)
        newcomer = _eligible_srem(
            vehicle_id="bus_after_grant", request_id=80, sequence_number=1, sim_time_s=103.0
        )
        response = rsu.handle_messages([newcomer], sim_time_s=103.0)[0]
        self.assertEqual(response.status, ResponseStatus.PROCESSING.value)


class ReplayedCancellationTestCase(unittest.TestCase):
    """Cancellations are idempotent: a replay is acked, not rejected."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = _load_config()

    def _rsu(self) -> RSUAgent:
        return RSUAgent(self.config, self.config.rsu_to_intersection["RSU_BOAVISTA_02"])

    def test_replayed_cancellation_is_acked_again(self) -> None:
        rsu = self._rsu()
        request = _eligible_srem(vehicle_id="bus_c", request_id=5, sequence_number=1)
        first = rsu.handle_messages([request], sim_time_s=101.0)[0]
        self.assertEqual(first.status, ResponseStatus.PROCESSING.value)

        cancel = _eligible_srem(
            vehicle_id="bus_c",
            request_id=5,
            sequence_number=2,
            request_type=RequestType.PRIORITY_CANCELLATION.value,
            sim_time_s=102.0,
        )
        ack = rsu.handle_messages([cancel], sim_time_s=102.0)[0]
        self.assertEqual(ack.status, ResponseStatus.UNKNOWN.value)
        self.assertEqual(ack.reason, "priority_request_cancelled")

        replayed = rsu.handle_messages([cancel], sim_time_s=103.0)[0]
        self.assertEqual(replayed.status, ResponseStatus.UNKNOWN.value)
        self.assertEqual(replayed.reason, "priority_request_cancelled")

    def test_duplicate_cancellation_in_same_batch_is_acked_twice(self) -> None:
        rsu = self._rsu()
        cancel = _eligible_srem(
            vehicle_id="bus_c",
            request_id=5,
            sequence_number=2,
            request_type=RequestType.PRIORITY_CANCELLATION.value,
            sim_time_s=102.0,
        )
        responses = rsu.handle_messages([cancel, cancel], sim_time_s=102.0)
        self.assertEqual(len(responses), 2)
        for response in responses:
            self.assertEqual(response.status, ResponseStatus.UNKNOWN.value)
            self.assertEqual(response.reason, "priority_request_cancelled")

    def test_replayed_priority_request_is_still_rejected(self) -> None:
        rsu = self._rsu()
        request = _eligible_srem(vehicle_id="bus_c", request_id=5, sequence_number=1)
        first = rsu.handle_messages([request], sim_time_s=101.0)[0]
        replayed = rsu.handle_messages([request], sim_time_s=102.0)[0]
        self.assertEqual(first.status, ResponseStatus.PROCESSING.value)
        self.assertEqual(replayed.status, ResponseStatus.REJECTED.value)
        self.assertEqual(replayed.reason, "duplicate_request_replayed")


if __name__ == "__main__":
    unittest.main()
