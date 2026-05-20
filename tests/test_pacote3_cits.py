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
from pps57_cits.map_spat import build_mapem_messages, build_static_spatem_messages
from pps57_cits.messages import MessageType, RequestStatus, SREMLike
from pps57_cits.models import VehicleObservation
from pps57_cits.obu import OBUEmulator
from pps57_cits.rsu import RSUAgent


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
        spatem = build_static_spatem_messages(self.config, sim_time_s=0.0)
        self.assertEqual(len(mapem), 7)
        self.assertEqual(len(spatem), 7)
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

    def test_dry_run_generates_summary_and_logs(self) -> None:
        controller = CITSEmulationController(self.config)
        summary = controller.run_dry_run(steps=20)
        self.assertGreater(summary["total_messages"], 0)
        self.assertIn("MAPEM_like", summary["by_type"])
        self.assertIn("SREM_like", summary["by_type"])
        self.assertIn("SSEM_like", summary["by_type"])
        self.assertTrue((ROOT / "outputs/cits_messages.jsonl").exists())
        self.assertTrue((ROOT / "reports/cits_emulation_summary.json").exists())


if __name__ == "__main__":
    unittest.main()
