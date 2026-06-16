#!/usr/bin/env python3
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace as dataclasses_replace
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.broker import InMemoryMessageBroker
from pps57_cits.audit import audit_protocol_lifecycle
from pps57_cits.config import load_cits_config
from pps57_cits.lifecycle import PriorityRequestState, transition_request_state
from pps57_cits.map_spat import build_mapem_messages
from pps57_cits.messages import (
    GrantedStrategy,
    MessageType,
    OperatorPriorityClass,
    RequestType,
    ResponseStatus,
    SREMLike,
    synth_srem,
    validate_cits_message,
)
from pps57_cits.models import VehicleObservation
from pps57_cits.obu import OBUEmulator
from pps57_cits.rsu import RSUAgent


class Package3CITSTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_cits_config(ROOT / "configs/cits_v2x_config.json", root=ROOT)

    # ------------------------------------------------------------------
    # Config / serialização.
    # ------------------------------------------------------------------

    def test_config_indexes_edges_to_intersections(self) -> None:
        self.assertIn("I1_I2", self.config.edge_to_intersection)
        self.assertEqual(self.config.edge_to_intersection["I1_I2"].rsu_id, "RSU_BOAVISTA_02")
        self.assertEqual(len(self.config.intersections), 7)
        self.assertEqual(len(self.config.signal_controlled_intersections), 6)
        self.assertFalse(self.config.intersection_by_alias["I6"].signal_controlled)

    def test_mapem_and_spatem_are_json_serialisable(self) -> None:
        mapem = build_mapem_messages(self.config, sim_time_s=0.0)
        self.assertEqual(len(mapem), 7)
        payload = mapem[0].to_dict()
        self.assertEqual(payload["message_type"], MessageType.MAPEM.value)
        # `intersection_alias` continua operacional ("I1"); `intersection_ref_id` é o uint16 CDD.
        self.assertEqual(payload["intersection_alias"], "I1")
        self.assertEqual(payload["intersection_ref_id"], 1)
        # Envelope de segurança é mandatório em todas as PDUs (TS 103 097).
        self.assertIn("security", payload)
        self.assertIn("signer_id", payload["security"])
        self.assertIn("certificate_id", payload["security"])
        self.assertIsNotNone(payload["ref_point"])
        self.assertIn("latitude_e7", payload["ref_point"])
        json.dumps(payload, ensure_ascii=False)

    def test_mapem_does_not_advertise_tsp_movements_for_roundabout(self) -> None:
        mapem = build_mapem_messages(self.config, sim_time_s=0.0)
        i6 = next(message for message in mapem if message.intersection_alias == "I6")
        self.assertTrue(i6.approaches)
        self.assertTrue(
            all(not approach.priority_movement_ids for approach in i6.approaches)
        )

    def test_cits_validator_accepts_eligible_srem(self) -> None:
        self.assertEqual(validate_cits_message(_eligible_srem()), [])

    def test_cits_validator_reports_invalid_request_id_range(self) -> None:
        request = _eligible_srem()
        request.requests[0].request_id = 999
        self.assertIn(
            "srem.requests[0].request_id_out_of_range",
            validate_cits_message(request),
        )

    # ------------------------------------------------------------------
    # OBU.
    # ------------------------------------------------------------------

    def test_obu_generates_srem_when_bus_is_in_eta_window_and_delayed(self) -> None:
        obu = OBUEmulator(self.config)
        observation = VehicleObservation(
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
        results = obu.generate_request(observation, sim_time_s=100.0)
        self.assertEqual(len(results), 1)
        request = results[0]
        self.assertIsInstance(request, SREMLike)
        self.assertEqual(request.destination_id, "RSU_BOAVISTA_02")
        self.assertEqual(request.intersection_id, "I2")
        self.assertEqual(request.message_type, MessageType.SREM.value)
        # OBU não decide manobra — só declara intenção (requestType).
        self.assertEqual(request.request_type, RequestType.PRIORITY_REQUEST.value)

    def test_obu_suppresses_requests_while_bus_at_stop(self) -> None:
        # v2.1: bus atrasado mas a servir uma paragem (getStopState bit 4) não
        # emite SREM — espelho do inibidor de porta-aberta dos sistemas reais.
        obu = OBUEmulator(self.config)
        observation = VehicleObservation(
            vehicle_id="bus_STCP500_W_UNIT",
            vehicle_class="bus",
            type_id="bus_12m",
            line_id="STCP500_PROXY_W",
            route_id="route_boavista_east_to_west",
            edge_id="I1_I2",
            lane_id="I1_I2_0",
            lane_position_m=500.0,
            lane_length_m=650.0,
            speed_mps=0.0,
            schedule_delay_s=90.0,
            stop_count=16,
        )
        self.assertEqual(obu.generate_request(observation, sim_time_s=100.0), [])

    def test_obu_cancels_active_request_when_bus_dwells_at_stop(self) -> None:
        # Pedido activo + bus encosta na paragem -> priorityCancellation.
        obu = OBUEmulator(self.config)
        moving = VehicleObservation(
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
        initial = obu.generate_requests([moving], sim_time_s=100.0)
        self.assertEqual(len(initial), 1)
        self.assertFalse(initial[0].is_cancellation)

        dwelling = dataclasses_replace(moving, speed_mps=0.0, stop_count=16)
        followup = obu.generate_requests([dwelling], sim_time_s=101.0)
        self.assertEqual(len(followup), 1)
        self.assertTrue(followup[0].is_cancellation)

    def test_obu_gates_on_time_bus_without_priority_need(self) -> None:
        # v2: prioridade condicional ativa na config (allow_nominal_priority_
        # requests=false) — um autocarro a horas e com headway nominal não
        # gera SREM.
        obu = OBUEmulator(self.config)
        observation = VehicleObservation(
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
            schedule_delay_s=0.0,
            headway_deviation_s=0.0,
        )
        self.assertEqual(obu.generate_request(observation, sim_time_s=100.0), [])

    def test_obu_generates_nominal_priority_request_when_policy_allows(self) -> None:
        # O caminho "nominal" continua suportado quando a config o permite
        # explicitamente (allow_nominal_priority_requests=true).
        raw = deepcopy(self.config.raw)
        raw["obu_policy"]["allow_nominal_priority_requests"] = True
        config = dataclasses_replace(self.config, raw=raw)
        obu = OBUEmulator(config)
        observation = VehicleObservation(
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
            schedule_delay_s=0.0,
            headway_deviation_s=0.0,
        )
        results = obu.generate_request(observation, sim_time_s=100.0)
        self.assertEqual(len(results), 1)
        request = results[0]
        # `priority_level` no novo modelo é a OperatorPriorityClass — não-standard,
        # carregada na operator_telemetry. Para um pedido nominal devolve "nominal".
        self.assertEqual(request.priority_level, OperatorPriorityClass.NOMINAL.value)
        self.assertEqual(request.priority_movement_id, "I2_westbound_public_transport")

    def test_obu_does_not_emit_srem_for_unsignalized_roundabout(self) -> None:
        obu = OBUEmulator(self.config)
        observation = VehicleObservation(
            vehicle_id="bus_STCP500_W_UNIT",
            vehicle_class="bus",
            type_id="bus_12m",
            line_id="STCP500_PROXY_W",
            route_id="route_boavista_east_to_west",
            edge_id="I5_I6",
            lane_id="I5_I6_0",
            lane_position_m=500.0,
            lane_length_m=650.0,
            speed_mps=10.0,
            schedule_delay_s=120.0,
        )
        self.assertEqual(obu.generate_request(observation, sim_time_s=100.0), [])

    def test_obu_suppresses_repeated_request_inside_refresh_window(self) -> None:
        obu = OBUEmulator(self.config)
        observation = VehicleObservation(
            vehicle_id="bus_STCP500_W_UNIT",
            vehicle_class="bus",
            type_id="bus_12m",
            line_id="STCP500_PROXY_W",
            route_id="route_boavista_east_to_west",
            edge_id="I2_I3",
            lane_id="I2_I3_0",
            lane_position_m=500.0,
            lane_length_m=650.0,
            speed_mps=10.0,
            schedule_delay_s=90.0,
        )
        first = obu.generate_request(observation, sim_time_s=10.0)
        second = obu.generate_request(observation, sim_time_s=12.0)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)

    def test_obu_emits_cancellation_when_vehicle_leaves_observation_window(self) -> None:
        """Novo no v0.4: a OBU emite priorityCancellation quando o veículo sai
        do contexto da interseção. Em v0.3 isto era implícito (sem mensagem)."""
        obu = OBUEmulator(self.config)
        observation = VehicleObservation(
            vehicle_id="bus_STCP500_W_UNIT",
            vehicle_class="bus",
            type_id="bus_12m",
            line_id="STCP500_PROXY_W",
            route_id="route_boavista_east_to_west",
            edge_id="I2_I3",
            lane_id="I2_I3_0",
            lane_position_m=500.0,
            lane_length_m=650.0,
            speed_mps=10.0,
            schedule_delay_s=90.0,
        )
        initial = obu.generate_requests([observation], sim_time_s=10.0)
        self.assertEqual(len(initial), 1)
        # Vehicle disappears next tick.
        followup = obu.generate_requests([], sim_time_s=11.0)
        self.assertEqual(len(followup), 1)
        self.assertTrue(followup[0].is_cancellation)
        self.assertEqual(followup[0].correlation_id, initial[0].message_id)
        self.assertNotEqual(followup[0].correlation_id, "vehicle_left_observation_window")
        self.assertEqual(
            followup[0].operator_telemetry.cancellation_reason,
            "vehicle_left_observation_window",
        )

    # ------------------------------------------------------------------
    # RSU.
    # ------------------------------------------------------------------

    def test_rsu_forwards_eligible_request_for_tsp_processing(self) -> None:
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        request = _eligible_srem()
        response = rsu.evaluate_request(request, sim_time_s=101.0)
        # ResponseStatus.PROCESSING significa "encaminhado ao motor de decisão TSP",
        # que era o significado original de RequestStatus.ACKNOWLEDGED no v0.3.
        self.assertEqual(response.status, ResponseStatus.PROCESSING.value)
        self.assertEqual(response.destination_id, "OBU_bus_1")

    def test_rsu_forwards_emergency_request_without_accumulated_delay(self) -> None:
        # Emergência tem prioridade incondicional: o RSU deve encaminhar o SREM
        # ao motor mesmo sem atraso de horário/headway acumulado. O OBU já faz
        # bypass da condição operacional ao emitir o pedido; o RSU tem de
        # espelhar esse bypass, senão a preempção só dispara após atraso
        # artificial (>= delay_threshold_s). Regressão do P2 da PR #57.
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        request = _eligible_srem(
            vehicle_id="ev_conflict_west_to_east_3600",
            eta_to_stopline_s=5.0,
            distance_to_stopline_m=60.0,
            schedule_delay_s=0.0,
            headway_deviation_s=0.0,
            operator_priority_class=OperatorPriorityClass.EMERGENCY.value,
            basic_vehicle_role="emergency",
        )
        self.assertEqual(request.priority_level, OperatorPriorityClass.EMERGENCY.value)
        response = rsu.evaluate_request(request, sim_time_s=101.0)
        self.assertEqual(response.status, ResponseStatus.PROCESSING.value)
        self.assertFalse(response.reason)

    def test_rsu_rejects_on_time_non_emergency_request_as_not_eligible(self) -> None:
        # Contraponto ao teste de emergência: o bypass não pode vazar para
        # transporte público pontual — a condição de delay/headway continua a
        # filtrar pedidos sem necessidade operacional.
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        request = _eligible_srem(
            schedule_delay_s=0.0,
            headway_deviation_s=0.0,
            operator_priority_class=OperatorPriorityClass.HIGH_DELAY.value,
        )
        response = rsu.evaluate_request(request, sim_time_s=101.0)
        self.assertEqual(response.status, ResponseStatus.REJECTED.value)
        self.assertEqual(response.reason, "not_eligible_for_priority")

    def test_rsu_does_not_start_vehicle_cooldown_on_forward_only_ack(self) -> None:
        # Forwarding to the TSP engine is not a granted priority intervention.
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        first = rsu.evaluate_request(_eligible_srem(sequence_number=1), sim_time_s=101.0)
        second = rsu.evaluate_request(_eligible_srem(sequence_number=2), sim_time_s=102.0)
        self.assertEqual(first.status, ResponseStatus.PROCESSING.value)
        self.assertEqual(second.status, ResponseStatus.PROCESSING.value)

        rsu.mark_priority_granted("bus_1", sim_time_s=103.0)
        rejected = rsu.evaluate_request(_eligible_srem(sequence_number=3), sim_time_s=104.0)
        self.assertEqual(rejected.status, ResponseStatus.REJECTED.value)
        self.assertEqual(rejected.reason, "cooldown_active_for_vehicle")

    def test_rsu_rejects_request_with_mismatched_rsu_id(self) -> None:
        # Telemetry rsu_id should match this RSU — defesa mínima contra
        # roteamento incorreto entre RSUs.
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        request = _eligible_srem(rsu_id="RSU_BOAVISTA_07")
        response = rsu.evaluate_request(request, sim_time_s=101.0)
        self.assertEqual(response.status, ResponseStatus.REJECTED.value)
        self.assertEqual(response.reason, "request_rsu_id_mismatch")

    def test_rsu_rejects_request_with_source_id_not_matching_vehicle(self) -> None:
        # source_id deve ser f"OBU_{vehicle_id}" — mismatch indica spoofing.
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        request = _eligible_srem()
        # Forge: alterar source_id sem alterar requestor.
        request.source_id = "OBU_someone_else"
        response = rsu.evaluate_request(request, sim_time_s=101.0)
        self.assertEqual(response.status, ResponseStatus.REJECTED.value)
        self.assertEqual(response.reason, "source_id_does_not_match_vehicle")

    def test_rsu_rejects_request_with_expired_security_envelope(self) -> None:
        """Novo no v0.4: o envelope de segurança (TS 103 097) tem `valid_until_ms`.
        A RSU deve rejeitar PDUs cujo certificado já não é válido."""
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        request = _eligible_srem()
        request.security.valid_until_ms = 1000  # expirou aos 1.0 s
        response = rsu.evaluate_request(request, sim_time_s=10.0)
        self.assertEqual(response.status, ResponseStatus.REJECTED.value)
        self.assertEqual(response.reason, "certificate_expired")

    def test_rsu_rejects_signer_outside_simulated_trust_store(self) -> None:
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        request = _eligible_srem(vehicle_id="car_unauthorized")
        response = rsu.evaluate_request(request, sim_time_s=101.0)
        self.assertEqual(response.status, ResponseStatus.REJECTED.value)
        self.assertEqual(response.reason, "security_signer_not_authorized")

    def test_rsu_dedupes_duplicate_request_in_same_batch(self) -> None:
        # Dois SREMs com a mesma (station, request, sequence) na mesma chamada
        # handle_messages: a segunda é rejeitada como duplicado.
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        req = _eligible_srem()
        responses = rsu.handle_messages([req, req], sim_time_s=101.0)
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0].status, ResponseStatus.PROCESSING.value)
        self.assertEqual(responses[1].status, ResponseStatus.REJECTED.value)
        self.assertEqual(responses[1].reason, "duplicate_request_in_batch")

    def test_rsu_dedupes_duplicate_request_replayed_in_later_tick(self) -> None:
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        req = _eligible_srem(sequence_number=7)
        first = rsu.handle_messages([req], sim_time_s=101.0)
        second = rsu.handle_messages([req], sim_time_s=102.0)
        self.assertEqual(first[0].status, ResponseStatus.PROCESSING.value)
        self.assertEqual(second[0].status, ResponseStatus.REJECTED.value)
        self.assertEqual(second[0].reason, "duplicate_request_replayed")

    def test_rsu_rejects_out_of_order_request_sequence(self) -> None:
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        newer = _eligible_srem(sequence_number=2)
        older = _eligible_srem(sequence_number=1)
        first = rsu.handle_messages([newer], sim_time_s=101.0)
        second = rsu.handle_messages([older], sim_time_s=102.0)
        self.assertEqual(first[0].status, ResponseStatus.PROCESSING.value)
        self.assertEqual(second[0].status, ResponseStatus.REJECTED.value)
        self.assertEqual(second[0].reason, "out_of_order_request_sequence")

    def test_rsu_rejects_protocol_invalid_srem(self) -> None:
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        request = _eligible_srem()
        request.requests[0].request_id = 999
        response = rsu.handle_messages([request], sim_time_s=101.0)[0]
        self.assertEqual(response.status, ResponseStatus.REJECTED.value)
        self.assertEqual(
            response.reason,
            "message_validation_failed:srem.requests[0].request_id_out_of_range",
        )

    def test_rsu_active_count_only_counts_eligible_requests(self) -> None:
        # SREMs inelegíveis (errada intersection, expirados, etc.) não devem
        # consumir o quota de pedidos ativos e bloquear pedidos legítimos.
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        max_active = int(self.config.rsu_policy.get("max_active_requests_per_rsu", 4))
        junk = [
            _eligible_srem(
                vehicle_id=f"bus_junk_{i}",
                intersection_alias="I1",  # endereçado a outra intersection
                request_id=i + 1,
                sequence_number=i + 1,
            )
            for i in range(max_active + 2)
        ]
        legit = _eligible_srem(vehicle_id="bus_legit", request_id=99, sequence_number=99)
        responses = rsu.handle_messages(junk + [legit], sim_time_s=101.0)
        legit_responses = [r for r in responses if r.correlation_token == legit.correlation_token]
        self.assertEqual(len(legit_responses), 1)
        self.assertEqual(legit_responses[0].status, ResponseStatus.PROCESSING.value)

    def test_rsu_rejects_request_expired_at_zero_timestamp(self) -> None:
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        request = _eligible_srem(sim_time_s=0.0, ttl_s=0.0)
        request.expires_at_s = 0.0
        # Security envelope expira em sim_time=0, então também rejeitaria por
        # certificate_expired primeiro. Avançamos o relógio mas mantemos
        # `expires_at_s=0.0`, e estendemos a security envelope para isolar a
        # condição de expirar APENAS o request.
        request.security.valid_until_ms = 10_000_000
        response = rsu.evaluate_request(request, sim_time_s=1.0)
        self.assertEqual(response.status, ResponseStatus.REJECTED.value)
        self.assertEqual(response.reason, "request_expired")

    def test_rsu_rejects_priority_request_at_unsignalized_roundabout(self) -> None:
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_06"]
        rsu = RSUAgent(self.config, intersection)
        request = _eligible_srem(
            intersection_alias="I6",
            tls_id="I6",
            rsu_id="RSU_BOAVISTA_06",
            lane_id="I5_I6_0",
        )
        response = rsu.evaluate_request(request, sim_time_s=101.0)
        self.assertEqual(response.status, ResponseStatus.REJECTED.value)
        self.assertEqual(response.reason, "intersection_not_signal_controlled")

    # ------------------------------------------------------------------
    # Broker / event-logger.
    # ------------------------------------------------------------------

    def test_broker_drain_keeps_counts_but_frees_queues(self) -> None:
        broker = InMemoryMessageBroker()
        mapem = build_mapem_messages(self.config, sim_time_s=0.0)
        for m in mapem:
            broker.publish(m)
        self.assertEqual(broker.count_by_type().get(MessageType.MAPEM.value), len(mapem))
        self.assertGreater(len(broker.peek("BROADCAST")), 0)
        broker.drain_all_except([])
        self.assertEqual(len(broker.peek("BROADCAST")), 0)
        # Contagens preservam-se (telemetria correta).
        self.assertEqual(broker.count_by_type().get(MessageType.MAPEM.value), len(mapem))

    def test_incremental_cits_summary_matches_batch(self) -> None:
        from pps57_cits.event_logger import IncrementalCITSSummary, summarise_messages

        msgs = build_mapem_messages(self.config, sim_time_s=0.0)
        incremental = IncrementalCITSSummary()
        for m in msgs:
            incremental.add(m)
        self.assertEqual(incremental.to_dict(), summarise_messages(msgs))

    def test_incremental_cits_summary_keeps_acknowledged_alias_for_processing(self) -> None:
        from pps57_cits.event_logger import IncrementalCITSSummary

        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        response = rsu.evaluate_request(_eligible_srem(), sim_time_s=101.0)
        summary = IncrementalCITSSummary()
        summary.add(response)

        payload = summary.to_dict()
        self.assertEqual(payload["processing_messages"], 1)
        self.assertEqual(payload["acknowledged_messages"], 1)

    def test_broker_routes_messages_to_destination(self) -> None:
        broker = InMemoryMessageBroker()
        mapem = build_mapem_messages(self.config, sim_time_s=0.0)[0]
        broker.publish(mapem)
        self.assertEqual(len(broker.peek("BROADCAST")), 1)
        self.assertEqual(len(broker.consume("BROADCAST")), 1)
        self.assertEqual(len(broker.consume("BROADCAST")), 0)

    def test_broker_simulates_transport_latency(self) -> None:
        broker = InMemoryMessageBroker(
            transport_config={
                "enabled": True,
                "latency_steps": 1,
                "jitter_steps": 0,
                "drop_rate": 0.0,
                "duplicate_rate": 0.0,
                "reorder_window_steps": 0,
                "random_seed": 57,
            }
        )
        message = build_mapem_messages(self.config, sim_time_s=0.0)[0]
        broker.publish(message)
        self.assertEqual(len(broker.peek("BROADCAST")), 0)
        self.assertEqual(broker.transport_stats()["pending"], 1)
        broker.advance_time(1)
        self.assertEqual(len(broker.peek("BROADCAST")), 1)
        self.assertEqual(broker.transport_stats()["delivered"], 1)

    def test_broker_simulates_duplicate_delivery(self) -> None:
        broker = InMemoryMessageBroker(
            transport_config={
                "enabled": True,
                "latency_steps": 0,
                "drop_rate": 0.0,
                "duplicate_rate": 1.0,
                "random_seed": 57,
            }
        )
        message = build_mapem_messages(self.config, sim_time_s=0.0)[0]
        broker.publish(message)
        self.assertEqual(len(broker.peek("BROADCAST")), 2)
        self.assertEqual(broker.transport_stats()["duplicates_scheduled"], 1)

    def test_lifecycle_state_machine_rejects_invalid_transition(self) -> None:
        state = transition_request_state(
            PriorityRequestState.CREATED.value,
            PriorityRequestState.PROCESSING.value,
        )
        self.assertEqual(state, PriorityRequestState.PROCESSING.value)
        with self.assertRaises(ValueError):
            transition_request_state(
                PriorityRequestState.GRANTED.value,
                PriorityRequestState.PROCESSING.value,
            )

    def test_protocol_audit_reconstructs_request_lifecycle(self) -> None:
        import tempfile

        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        request = _eligible_srem()
        processing = rsu.evaluate_request(request, sim_time_s=101.0)
        final = deepcopy(processing)
        final.response.response_status = ResponseStatus.GRANTED.value
        final.audit.granted_strategy = GrantedStrategy.GREEN_EXTENSION.value
        final.audit.rejection_reason = None
        final.security.generation_time_ms = 102_000
        final.security.valid_until_ms = 117_000

        with tempfile.TemporaryDirectory() as tmp:
            cits_path = Path(tmp) / "cits.jsonl"
            cits_path.write_text(
                "\n".join([request.to_json(), processing.to_json(), final.to_json()]) + "\n",
                encoding="utf-8",
            )
            audit = audit_protocol_lifecycle(cits_path)
        self.assertEqual(audit["protocol_kpis"]["lifecycle_chains"], 1)
        self.assertEqual(audit["protocol_kpis"]["with_processing_ssem"], 1)
        self.assertEqual(audit["protocol_kpis"]["with_final_ssem"], 1)
        self.assertEqual(audit["final_ssem_by_status"], {"granted": 1})

    # ------------------------------------------------------------------
    # Validação project + TraCI gates (não-protocolo, mantidos).
    # ------------------------------------------------------------------

    def test_route_file_sortedness_guard(self) -> None:
        from pps57_sumo.validate_project import validate_routes_sorted
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            unsorted_path = Path(tmp) / "unsorted.rou.xml"
            unsorted_path.write_text(
                '<routes>'
                '<vehicle id="v1" depart="10"/>'
                '<vehicle id="v2" depart="5"/>'
                '</routes>',
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit) as ctx:
                validate_routes_sorted(unsorted_path)
            self.assertIn("not sorted by departure", str(ctx.exception))
            self.assertIn("v2", str(ctx.exception))

            sorted_path = Path(tmp) / "sorted.rou.xml"
            sorted_path.write_text(
                '<routes>'
                '<flow id="f1" begin="0"/>'
                '<vehicle id="v1" depart="5"/>'
                '<flow id="f2" begin="10"/>'
                '<vehicle id="v2" depart="20"/>'
                '</routes>',
                encoding="utf-8",
            )
            validate_routes_sorted(sorted_path)  # must not raise

    def test_safety_config_validation_accepts_current_configs(self) -> None:
        from pps57_sumo.validate_project import validate_safety_configs

        # must-not-raise: validate_safety_configs devolve None e aborta com
        # SystemExit à primeira violação; passar sem excepção é o critério.
        validate_safety_configs(ROOT)
        # Garante que o validador correu sobre configs reais e não vazias.
        cits = json.loads((ROOT / "configs/cits_v2x_config.json").read_text(encoding="utf-8"))
        tsp = json.loads((ROOT / "configs/tsp_safety_config.json").read_text(encoding="utf-8"))
        self.assertTrue(cits.get("safety_constraints"))
        self.assertTrue(tsp.get("decision_policy"))

    def test_safety_config_validation_rejects_inverted_green_extension(self) -> None:
        from pps57_sumo.validate_project import validate_safety_configs
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            (tmp_root / "configs").mkdir()
            shutil.copy(ROOT / "configs/sumo_scenario_base.json", tmp_root / "configs/sumo_scenario_base.json")
            shutil.copy(ROOT / "configs/cits_v2x_config.json", tmp_root / "configs/cits_v2x_config.json")
            tsp = json.loads((ROOT / "configs/tsp_safety_config.json").read_text(encoding="utf-8"))
            tsp["decision_policy"]["green_extension_min_s"] = 20
            tsp["decision_policy"]["green_extension_max_s"] = 12
            (tmp_root / "configs/tsp_safety_config.json").write_text(
                json.dumps(tsp), encoding="utf-8"
            )
            with self.assertRaises(SystemExit) as ctx:
                validate_safety_configs(tmp_root)
            self.assertIn("green_extension", str(ctx.exception))

    def test_safety_config_validation_rejects_weights_not_summing_to_one(self) -> None:
        from pps57_sumo.validate_project import validate_safety_configs
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            (tmp_root / "configs").mkdir()
            shutil.copy(ROOT / "configs/sumo_scenario_base.json", tmp_root / "configs/sumo_scenario_base.json")
            shutil.copy(ROOT / "configs/cits_v2x_config.json", tmp_root / "configs/cits_v2x_config.json")
            tsp = json.loads((ROOT / "configs/tsp_safety_config.json").read_text(encoding="utf-8"))
            tsp["decision_policy"]["weights"]["schedule_delay"] = 0.9
            (tmp_root / "configs/tsp_safety_config.json").write_text(
                json.dumps(tsp), encoding="utf-8"
            )
            with self.assertRaises(SystemExit) as ctx:
                validate_safety_configs(tmp_root)
            self.assertIn("weights", str(ctx.exception))

    def test_safety_config_validation_rejects_sumo_cits_control_mismatch(self) -> None:
        from pps57_sumo.validate_project import validate_safety_configs
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            (tmp_root / "configs").mkdir()
            shutil.copy(ROOT / "configs/sumo_scenario_base.json", tmp_root / "configs/sumo_scenario_base.json")
            shutil.copy(ROOT / "configs/tsp_safety_config.json", tmp_root / "configs/tsp_safety_config.json")
            cits = json.loads((ROOT / "configs/cits_v2x_config.json").read_text(encoding="utf-8"))
            for intersection in cits["intersections"]:
                if intersection["intersection_id"] == "I6":
                    intersection["signal_controlled"] = True
            (tmp_root / "configs/cits_v2x_config.json").write_text(
                json.dumps(cits), encoding="utf-8"
            )
            with self.assertRaises(SystemExit) as ctx:
                validate_safety_configs(tmp_root)
            self.assertIn("signal_controlled=true", str(ctx.exception))

    def _write_tmp_configs_with_tsp(self, tmp_root: Path, tsp: dict) -> None:
        import shutil

        (tmp_root / "configs").mkdir()
        shutil.copy(ROOT / "configs/sumo_scenario_base.json", tmp_root / "configs/sumo_scenario_base.json")
        shutil.copy(ROOT / "configs/cits_v2x_config.json", tmp_root / "configs/cits_v2x_config.json")
        (tmp_root / "configs/tsp_safety_config.json").write_text(json.dumps(tsp), encoding="utf-8")

    def test_safety_config_validation_rejects_priority_level_weights_wrong_class(self) -> None:
        from pps57_sumo.validate_project import validate_safety_configs
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tsp = json.loads((ROOT / "configs/tsp_safety_config.json").read_text(encoding="utf-8"))
            # Renomeia uma classe válida para uma inexistente: cairia silenciosamente
            # no peso 0.0 sem a validação keys==enum.
            tsp["decision_policy"]["priority_level_weights"]["vip"] = tsp["decision_policy"][
                "priority_level_weights"
            ].pop("nominal")
            self._write_tmp_configs_with_tsp(tmp_root, tsp)
            with self.assertRaises(SystemExit) as ctx:
                validate_safety_configs(tmp_root)
            self.assertIn("priority_level_weights", str(ctx.exception))

    def test_safety_config_validation_rejects_priority_level_weights_out_of_range(self) -> None:
        from pps57_sumo.validate_project import validate_safety_configs
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tsp = json.loads((ROOT / "configs/tsp_safety_config.json").read_text(encoding="utf-8"))
            tsp["decision_policy"]["priority_level_weights"]["emergency"] = 1.5
            self._write_tmp_configs_with_tsp(tmp_root, tsp)
            with self.assertRaises(SystemExit) as ctx:
                validate_safety_configs(tmp_root)
            self.assertIn("priority_level_weights", str(ctx.exception))

    def test_safety_config_validation_rejects_invalid_actuating_action(self) -> None:
        from pps57_sumo.validate_project import validate_safety_configs
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tsp = json.loads((ROOT / "configs/tsp_safety_config.json").read_text(encoding="utf-8"))
            tsp["decision_policy"]["actuating_actions"] = ["green_extension", "teleport"]
            self._write_tmp_configs_with_tsp(tmp_root, tsp)
            with self.assertRaises(SystemExit) as ctx:
                validate_safety_configs(tmp_root)
            self.assertIn("actuating_actions", str(ctx.exception))

    def test_tsp_config_actuating_actions_reads_config_with_fallback(self) -> None:
        from pps57_tsp.config import TSPConfig, load_tsp_config
        from pps57_tsp.models import DEFAULT_ACTUATING_ACTIONS

        configured = load_tsp_config(ROOT / "configs/tsp_safety_config.json", root=ROOT)
        self.assertEqual(configured.actuating_actions(), frozenset({"green_extension", "early_green"}))

        # Ausência da chave recai no default em código (comportamento idêntico ao literal antigo).
        empty = TSPConfig(root=ROOT, raw={"decision_policy": {}})
        self.assertEqual(empty.actuating_actions(), DEFAULT_ACTUATING_ACTIONS)

    def test_traci_gui_command_includes_start_flag(self) -> None:
        from pps57_cits.traci_adapter import TraciSimulationAdapter

        gui_cmd = TraciSimulationAdapter(self.config, gui=True)._sumo_command("sumo-gui")
        headless_cmd = TraciSimulationAdapter(self.config, gui=False)._sumo_command("sumo")
        self.assertIn("--start", gui_cmd)
        self.assertIn("--quit-on-end", gui_cmd)
        self.assertEqual(gui_cmd[0], "sumo-gui")
        self.assertNotIn("--start", headless_cmd)
        self.assertEqual(headless_cmd[0], "sumo")


def _eligible_srem(**overrides) -> SREMLike:
    """Constrói um SREM elegível (alta-prioridade, ETA na janela, destinado a I2/RSU_BOAVISTA_02)."""
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


if __name__ == "__main__":
    unittest.main()
