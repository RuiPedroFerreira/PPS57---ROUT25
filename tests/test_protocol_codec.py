#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.broker import InMemoryMessageBroker
from pps57_cits.event_logger import CITSJsonlLogger
from pps57_cits.map_spat import build_spatem_message_from_state
from pps57_cits.messages import OperatorPriorityClass, synth_srem
from pps57_cits.models import SignalState
from pps57_cits.protocol_codec import JsonSimulationCodec, ProtocolCodecError
from pps57_opt.event_dataset import build_event_training_rows, load_event_training_scenarios


def valid_srem():
    return synth_srem(
        sim_time_s=10.0,
        vehicle_id="bus_codec",
        intersection_alias="I1",
        tls_id="I1",
        rsu_id="RSU_BOAVISTA_01",
        lane_id="CITY_EAST_I1_0",
        next_edge_id="I1_I2",
        operator_priority_class=OperatorPriorityClass.HIGH_DELAY.value,
        priority_movement_id="I1_westbound_public_transport",
        target_signal_group_id_hint="I1_priority_westbound",
    )


class ProtocolCodecTests(unittest.TestCase):
    def test_json_simulation_codec_round_trips_srem(self) -> None:
        codec = JsonSimulationCodec()
        message = valid_srem()

        decoded = codec.decode(codec.encode(message))

        self.assertEqual(decoded.message_type, message.message_type)
        self.assertEqual(decoded.message_id, message.message_id)
        self.assertEqual(decoded.request_id, message.request_id)
        self.assertEqual(decoded.vehicle_id, "bus_codec")

    def test_json_simulation_codec_rejects_invalid_message_on_encode(self) -> None:
        codec = JsonSimulationCodec()
        message = valid_srem()
        message.requests[0].request_id = 0

        with self.assertRaisesRegex(ProtocolCodecError, "request_id_out_of_range"):
            codec.encode(message)

    def test_json_simulation_codec_rejects_invalid_payload_on_decode(self) -> None:
        codec = JsonSimulationCodec()

        with self.assertRaisesRegex(ProtocolCodecError, "message_type"):
            codec.decode({"message_type": "BOGUS"})

    def test_json_simulation_codec_normalizes_construction_errors(self) -> None:
        codec = JsonSimulationCodec()
        payload = valid_srem().to_dict()
        payload["station_id"] = "not-an-int"

        with self.assertRaisesRegex(
            ProtocolCodecError, "Invalid json-simulation-etsi-like payload"
        ):
            codec.decode(payload)

    def test_cits_jsonl_logger_uses_codec_boundary(self) -> None:
        codec = JsonSimulationCodec()
        message = valid_srem()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "messages.jsonl"
            with CITSJsonlLogger(path, codec=codec) as logger:
                logger.write(message)

            decoded = codec.decode(path.read_text(encoding="utf-8").strip())

        self.assertEqual(decoded.request_id, message.request_id)

    def test_broker_can_transport_encoded_payloads_internally(self) -> None:
        broker = InMemoryMessageBroker(transport_config={"encode_payloads": True})
        message = valid_srem()

        broker.publish(message)

        self.assertIsInstance(broker.queues[message.destination_id][0], str)
        decoded = broker.consume(message.destination_id)
        self.assertEqual(len(decoded), 1)
        self.assertEqual(decoded[0].request_id, message.request_id)

    def test_broker_decodes_delayed_encoded_payloads(self) -> None:
        broker = InMemoryMessageBroker(
            transport_config={
                "enabled": True,
                "encode_payloads": True,
                "latency_steps": 1,
                "random_seed": 57,
            }
        )
        message = valid_srem()

        broker.publish(message)
        self.assertEqual(broker.consume(message.destination_id), [])

        broker.advance_time(1)
        decoded = broker.consume(message.destination_id)

        self.assertEqual(len(decoded), 1)
        self.assertEqual(decoded[0].message_id, message.message_id)

    def test_event_dataset_loads_messages_through_codec_boundary(self) -> None:
        request = valid_srem()
        spatem = build_spatem_message_from_state(
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
        )
        row = {
            "decision_id": "decision_codec",
            "request_id": request.request_id,
            "timestamp_s": 70.0,
            "tls_id": "I1",
            "action": "green_extension",
            "current_phase_index": 2,
            "current_program_id": "program_1",
            "request": request.to_dict(),
            "signal_state": spatem.to_dict(),
            "network_state": {
                "active_request_count": 1,
                "queue_vehicle_count": 3,
                "halted_vehicle_count": 1,
                "mean_speed_mps": 5.0,
                "waiting_time_s": 12.0,
                "occupancy": 0.2,
                "spillback_risk": False,
            },
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "event_rows.jsonl"
            path.write_text(json.dumps(row, sort_keys=True) + "\n", encoding="utf-8")
            scenarios = load_event_training_scenarios(path)

        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0].request.request_id, request.request_id)
        self.assertEqual(scenarios[0].signal_state.timestamp_s, 70.0)
        self.assertEqual(scenarios[0].signal_state.current_phase_index, 2)
        self.assertEqual(scenarios[0].signal_state.red_yellow_green_state, "GGrr")

    def test_event_dataset_selects_spatem_by_cdd_timestamp(self) -> None:
        request = valid_srem()
        spatem = build_spatem_message_from_state(
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
        )
        decision = {
            "decision_id": "decision_cdd_time",
            "request_id": request.request_id,
            "timestamp_s": 70.0,
            "tls_id": "I1",
            "current_phase_index": 2,
            "notes": [
                "network_state=active_requests:1,queue:3,halted:1,mean_speed_mps:5.0,"
                "waiting_time_s:12.0,occupancy:0.2,spillback_risk:False"
            ],
        }
        actuation = {"decision_id": "decision_cdd_time", "applied": False}

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cits_log = root / "cits.jsonl"
            decision_log = root / "decision.jsonl"
            actuation_log = root / "actuation.jsonl"
            cits_log.write_text(
                json.dumps(request.to_dict(), sort_keys=True)
                + "\n"
                + json.dumps(spatem.to_dict(), sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            decision_log.write_text(json.dumps(decision, sort_keys=True) + "\n", encoding="utf-8")
            actuation_log.write_text(json.dumps(actuation, sort_keys=True) + "\n", encoding="utf-8")

            rows = build_event_training_rows(
                cits_log=cits_log,
                decision_log=decision_log,
                actuation_log=actuation_log,
            )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["signal_state"]["message_id"], spatem.message_id)

    def test_spatem_with_more_than_255_links_is_capped_and_valid(self) -> None:
        # A TLS with 260 links would produce signal_group_id=256 for the 256th link,
        # which the ASN.1 codec rejects (valid range 1–255). The fix silently drops
        # links beyond index 254 so the SPATEM stays encodable.
        ryg_260 = "G" * 260
        spatem = build_spatem_message_from_state(
            SignalState(
                intersection_id="I_big",
                tls_id="I_big",
                rsu_id="RSU_BIG",
                timestamp_s=10.0,
                current_phase_index=0,
                current_program_id="default",
                red_yellow_green_state=ryg_260,
                next_switch_s=20.0,
                spent_duration_s=1.0,
            )
        )

        self.assertEqual(len(spatem.movement_events), 255)
        self.assertEqual(spatem.movement_events[0].signal_group_id, 1)
        self.assertEqual(spatem.movement_events[-1].signal_group_id, 255)

        codec = JsonSimulationCodec()
        encoded = codec.encode(spatem)
        self.assertIsNotNone(encoded)


if __name__ == "__main__":
    unittest.main()
