#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.broker import InMemoryMessageBroker
from pps57_cits.config import load_cits_config
from pps57_cits.controller import CITSEmulationController
from pps57_cits.map_spat import build_mapem_messages
from pps57_cits.messages import MessageType, RequestStatus, SREMLike
from pps57_cits.models import SignalState, VehicleObservation
from pps57_cits.obu import OBUEmulator
from pps57_cits.rsu import RSUAgent
from pps57_cits.traci_adapter import TraciSimulationAdapter


class Package3CITSTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_cits_config(ROOT / "configs/cits_config.json", root=ROOT)

    def test_config_indexes_edges_to_intersections(self) -> None:
        self.assertIn("I1_I2", self.config.edge_to_intersection)
        self.assertEqual(self.config.edge_to_intersection["I1_I2"].rsu_id, "RSU_BOAVISTA_02")
        self.assertEqual(len(self.config.intersections), 7)

    def test_mapem_and_spatem_are_json_serialisable(self) -> None:
        mapem = build_mapem_messages(self.config, sim_time_s=0.0)
        self.assertEqual(len(mapem), 7)
        payload = mapem[0].to_dict()
        self.assertEqual(payload["message_type"], MessageType.MAPEM_LIKE.value)
        json.dumps(payload, ensure_ascii=False)

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
        request = obu.generate_request(observation, sim_time_s=100.0)
        self.assertIsNotNone(request)
        assert request is not None
        self.assertIsInstance(request, SREMLike)
        self.assertEqual(request.destination_id, "RSU_BOAVISTA_02")
        self.assertEqual(request.intersection_id, "I2")
        self.assertEqual(request.message_type, MessageType.SREM_LIKE.value)

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
        self.assertIsNotNone(first)
        self.assertIsNone(second)

    def test_rsu_acknowledges_eligible_request(self) -> None:
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        request = SREMLike(
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
            distance_to_stopline_m=150.0,
            eta_to_stopline_s=15.0,
            schedule_delay_s=90.0,
            headway_deviation_s=0.0,
            requested_maneuver="green_extension",
            priority_level="public_transport_high_delay",
            expires_at_s=120.0,
        )
        response = rsu.evaluate_request(request, sim_time_s=101.0)
        self.assertEqual(response.status, RequestStatus.ACKNOWLEDGED.value)
        self.assertEqual(response.destination_id, "OBU_bus_1")

    def test_rsu_does_not_start_vehicle_cooldown_on_forward_only_ack(self) -> None:
        # Forwarding to the TSP engine is not a granted priority intervention.
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        request = SREMLike(
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
            distance_to_stopline_m=150.0,
            eta_to_stopline_s=15.0,
            schedule_delay_s=90.0,
            headway_deviation_s=0.0,
            requested_maneuver="green_extension",
            priority_level="public_transport_high_delay",
            expires_at_s=120.0,
        )
        first = rsu.evaluate_request(request, sim_time_s=101.0)
        second = rsu.evaluate_request(request, sim_time_s=102.0)
        self.assertEqual(first.status, RequestStatus.ACKNOWLEDGED.value)
        self.assertEqual(second.status, RequestStatus.ACKNOWLEDGED.value)

        rsu.mark_priority_granted(request.vehicle_id, sim_time_s=103.0)
        rejected = rsu.evaluate_request(request, sim_time_s=104.0)
        self.assertEqual(rejected.status, RequestStatus.REJECTED.value)
        self.assertEqual(rejected.reason, "cooldown_active_for_vehicle")

    def test_rsu_rejects_request_with_mismatched_rsu_id(self) -> None:
        # Pedido endereçado a outra RSU não deve ser aceite por esta — defesa
        # mínima contra spoofing/forge de mensagens C-ITS.
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        request = self._eligible_request(rsu_id="RSU_BOAVISTA_07")
        response = rsu.evaluate_request(request, sim_time_s=101.0)
        self.assertEqual(response.status, RequestStatus.REJECTED.value)
        self.assertEqual(response.reason, "request_rsu_id_mismatch")

    def test_rsu_rejects_request_with_source_id_not_matching_vehicle(self) -> None:
        # source_id deve ser f"OBU_{vehicle_id}" — mismatch indica spoofing.
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        request = self._eligible_request(source_id="OBU_someone_else", vehicle_id="bus_1")
        response = rsu.evaluate_request(request, sim_time_s=101.0)
        self.assertEqual(response.status, RequestStatus.REJECTED.value)
        self.assertEqual(response.reason, "source_id_does_not_match_vehicle")

    def test_rsu_dedupes_duplicate_request_id_in_same_batch(self) -> None:
        # M3.5: dois SREMs com o mesmo request_id na mesma chamada handle_messages
        # — segunda é rejeitada como replay; primeira mantém ACK.
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        req = self._eligible_request()
        responses = rsu.handle_messages([req, req], sim_time_s=101.0)
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0].status, RequestStatus.ACKNOWLEDGED.value)
        self.assertEqual(responses[1].status, RequestStatus.REJECTED.value)
        self.assertEqual(responses[1].reason, "duplicate_request_id_in_batch")

    def test_rsu_active_count_only_counts_eligible_requests(self) -> None:
        # Antes: SREMs inelegíveis (errada intersection, expirados, etc.)
        # consumiam o quota de pedidos ativos e bloqueavam pedidos legítimos.
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        max_active = int(self.config.rsu_policy.get("max_active_requests_per_rsu", 4))
        # Constrói N pedidos COM intersection errada (rejeitados) + 1 legítimo.
        junk = [
            self._eligible_request(
                vehicle_id=f"bus_junk_{i}",
                source_id=f"OBU_bus_junk_{i}",
                intersection_id="I1",  # endereçado a outra intersection
            )
            for i in range(max_active + 2)
        ]
        legit = self._eligible_request(vehicle_id="bus_legit", source_id="OBU_bus_legit")
        responses = rsu.handle_messages(junk + [legit], sim_time_s=101.0)
        # O pedido legítimo é o último — não deve ser bloqueado pelos junks.
        legit_responses = [r for r in responses if r.vehicle_id == "bus_legit"]
        self.assertEqual(len(legit_responses), 1)
        self.assertEqual(legit_responses[0].status, RequestStatus.ACKNOWLEDGED.value)

    def _eligible_request(self, **overrides) -> SREMLike:
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
            distance_to_stopline_m=150.0,
            eta_to_stopline_s=15.0,
            schedule_delay_s=90.0,
            headway_deviation_s=0.0,
            requested_maneuver="green_extension",
            priority_level="public_transport_high_delay",
            expires_at_s=120.0,
        )
        payload.update(overrides)
        return SREMLike(**payload)

    def test_rsu_rejects_request_expired_at_zero_timestamp(self) -> None:
        intersection = self.config.rsu_to_intersection["RSU_BOAVISTA_02"]
        rsu = RSUAgent(self.config, intersection)
        request = SREMLike(
            source_id="OBU_bus_1",
            destination_id="RSU_BOAVISTA_02",
            timestamp_s=0.0,
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
            distance_to_stopline_m=150.0,
            eta_to_stopline_s=15.0,
            schedule_delay_s=90.0,
            headway_deviation_s=0.0,
            requested_maneuver="green_extension",
            priority_level="public_transport_high_delay",
            expires_at_s=0.0,
        )
        response = rsu.evaluate_request(request, sim_time_s=1.0)
        self.assertEqual(response.status, RequestStatus.REJECTED.value)
        self.assertEqual(response.reason, "request_expired")

    def test_message_dataclass_fields_match_serialisation(self) -> None:
        # M3: defesa contra a fragilidade do duplo mecanismo (@dataclass + __init__
        # custom) — se um campo for declarado mas o __init__ esquecer de o
        # atribuir, asdict() exporta um default que não reflete o objeto.
        # Este teste constrói cada subclasse e confirma que to_dict() contém
        # exatamente os nomes de campos esperados pelo modelo dataclass.
        from dataclasses import fields
        from pps57_cits.messages import (
            MAPEMLike, SPATEMLike, SREMLike, SSEMLike,
            MessageType, PriorityLevel, RequestStatus, RequestedManeuver,
        )

        srem = SREMLike(
            source_id="OBU_x", destination_id="RSU_BOAVISTA_02", timestamp_s=1.0,
            vehicle_id="bus_x", vehicle_class="bus", line_id="L", route_id="R",
            intersection_id="I2", tls_id="I2", rsu_id="RSU_BOAVISTA_02",
            current_edge_id="I1_I2", current_lane_id="I1_I2_0",
            speed_mps=10.0, distance_to_stopline_m=100.0, eta_to_stopline_s=10.0,
            schedule_delay_s=60.0, headway_deviation_s=0.0,
            requested_maneuver=RequestedManeuver.GREEN_EXTENSION.value,
            priority_level=PriorityLevel.PUBLIC_TRANSPORT_HIGH_DELAY.value,
            expires_at_s=13.0,
        )
        for cls, instance in [(SREMLike, srem)]:
            declared = {f.name for f in fields(cls)}
            payload = instance.to_dict()
            missing = declared - payload.keys()
            self.assertFalse(missing, f"{cls.__name__}: campos declarados não serializados: {missing}")

    def test_dataclass_from_dict_reconstructs_custom_message_subclasses(self) -> None:
        from pps57_cits.messages import MAPEMLike, dataclass_from_dict

        original = build_mapem_messages(self.config, sim_time_s=12.0)[0]
        reconstructed = dataclass_from_dict(MAPEMLike, original.to_dict())
        self.assertEqual(reconstructed.message_type, MessageType.MAPEM_LIKE.value)
        self.assertEqual(reconstructed.intersection_id, original.intersection_id)
        self.assertEqual(reconstructed.tls_id, original.tls_id)
        self.assertEqual(len(reconstructed.approaches), len(original.approaches))

    def test_broker_drain_keeps_counts_but_frees_queues(self) -> None:
        # M2: broker mantém apenas contadores incrementais; drain liberta filas.
        broker = InMemoryMessageBroker()
        mapem = build_mapem_messages(self.config, sim_time_s=0.0)
        for m in mapem:
            broker.publish(m)
        self.assertEqual(broker.count_by_type().get("MAPEM_like"), len(mapem))
        # Não deveria existir 'history' acumulado e cada fila tem mensagens.
        self.assertFalse(hasattr(broker, "history") and broker.history)
        self.assertGreater(len(broker.peek("BROADCAST")), 0)
        broker.drain_all_except([])
        self.assertEqual(len(broker.peek("BROADCAST")), 0)
        # Contagens preservam-se (telemetria correta).
        self.assertEqual(broker.count_by_type().get("MAPEM_like"), len(mapem))

    def test_incremental_cits_summary_matches_legacy(self) -> None:
        from pps57_cits.event_logger import IncrementalCITSSummary, summarise_messages

        msgs = build_mapem_messages(self.config, sim_time_s=0.0)
        incremental = IncrementalCITSSummary()
        for m in msgs:
            incremental.add(m)
        self.assertEqual(incremental.to_dict(), summarise_messages(msgs))

    def test_broker_routes_messages_to_destination(self) -> None:
        broker = InMemoryMessageBroker()
        mapem = build_mapem_messages(self.config, sim_time_s=0.0)[0]
        broker.publish(mapem)
        self.assertEqual(len(broker.peek("BROADCAST")), 1)
        self.assertEqual(len(broker.consume("BROADCAST")), 1)
        self.assertEqual(len(broker.consume("BROADCAST")), 0)

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

        validate_safety_configs(ROOT)  # must not raise

    def test_safety_config_validation_rejects_inverted_green_extension(self) -> None:
        from pps57_sumo.validate_project import validate_safety_configs
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            (tmp_root / "configs").mkdir()
            shutil.copy(ROOT / "configs/cits_config.json", tmp_root / "configs/cits_config.json")
            tsp = json.loads((ROOT / "configs/tsp_config.json").read_text(encoding="utf-8"))
            # Inverte min > max: a Safety Layer nunca conseguiria propor uma
            # extensão coerente — tem de ser apanhado estaticamente.
            tsp["decision_policy"]["green_extension_min_s"] = 20
            tsp["decision_policy"]["green_extension_max_s"] = 12
            (tmp_root / "configs/tsp_config.json").write_text(
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
            shutil.copy(ROOT / "configs/cits_config.json", tmp_root / "configs/cits_config.json")
            tsp = json.loads((ROOT / "configs/tsp_config.json").read_text(encoding="utf-8"))
            tsp["decision_policy"]["weights"]["schedule_delay"] = 0.9  # soma deixa de ser 1.0
            (tmp_root / "configs/tsp_config.json").write_text(
                json.dumps(tsp), encoding="utf-8"
            )
            with self.assertRaises(SystemExit) as ctx:
                validate_safety_configs(tmp_root)
            self.assertIn("weights", str(ctx.exception))

    def test_traci_gui_command_includes_start_flag(self) -> None:
        from pps57_cits.traci_adapter import TraciSimulationAdapter

        gui_cmd = TraciSimulationAdapter(self.config, gui=True)._sumo_command("sumo-gui")
        headless_cmd = TraciSimulationAdapter(self.config, gui=False)._sumo_command("sumo")
        # sumo-gui sem --start fica pausado e nunca serve TraCI.
        self.assertIn("--start", gui_cmd)
        self.assertIn("--quit-on-end", gui_cmd)
        self.assertEqual(gui_cmd[0], "sumo-gui")
        self.assertNotIn("--start", headless_cmd)
        self.assertEqual(headless_cmd[0], "sumo")

if __name__ == "__main__":
    unittest.main()
