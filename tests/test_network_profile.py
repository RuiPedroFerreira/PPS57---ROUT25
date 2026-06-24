#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config
from pps57_cits.messages import OperatorPriorityClass, synth_srem
from pps57_cits.models import SignalState
from pps57_opt.state import state_bucket_for_context
from pps57_sumo.environment import ensure_sumo_environment, resolve_sumo_home
from pps57_sumo.network_profile import load_network_profile
from pps57_sumo.validate_project import validate_network_profile_config
from pps57_tsp.config import TSPConfig
from pps57_tsp.engine import TSPDecisionEngine
from pps57_tsp.models import TSPAction
from pps57_tsp.safety import TSPSafetyLayer
from pps57_tsp.signal_control import build_controller_contract


class NetworkProfileTests(unittest.TestCase):
    def test_sumo_environment_repairs_stale_sumo_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake_home = Path(tmp) / "sumo"
            (fake_home / "tools").mkdir(parents=True)
            (fake_home / "data" / "xsd").mkdir(parents=True)

            with patch.dict(os.environ, {"SUMO_HOME": "/path/that/does/not/exist"}), patch(
                "pps57_sumo.environment._standard_sumo_home_candidates",
                return_value=[fake_home],
            ):
                resolved = resolve_sumo_home()
                env = ensure_sumo_environment()

            self.assertIsNotNone(resolved)
            assert resolved is not None
            self.assertTrue((resolved / "tools").is_dir())
            self.assertEqual(env["SUMO_HOME"], str(resolved))

    def test_profile_extracts_tls_phases_movements_and_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            net = _write_simple_net(Path(tmp))

            profile = load_network_profile(net)
            tls = profile.tls_profile("J1")

            self.assertIsNotNone(tls)
            assert tls is not None
            self.assertEqual(tls.phase_sequence, [0, 1, 2, 3, 4, 5])
            self.assertEqual(tls.service_green_phase_indices, [0, 3])
            self.assertEqual(tls.intergreen_phase_indices, [1, 2, 4, 5])
            movement_a = tls.movement_for_edges("A_J1", "J1_C")
            movement_b = tls.movement_for_edges("B_J1", "J1_D")
            self.assertIsNotNone(movement_a)
            self.assertIsNotNone(movement_b)
            assert movement_a is not None and movement_b is not None
            self.assertEqual(movement_a.target_phase_index, 0)
            self.assertEqual(movement_b.target_phase_index, 3)
            self.assertIn(movement_b.signal_group_id, movement_a.conflicts_with)

    def test_validate_defers_unbuilt_generated_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cits = {
                "sumo": {"network": "sumo/network/corredor.net.xml"},
                "network_discovery": {"enabled": True},
                "intersections": [],
            }
            # B33 — a missing GENERATED net.xml DEFERS by default (does not raise), so
            # config validation stays usable in a clean checkout / no-SUMO `make test`.
            # The fail-closed guarantee is `make build` re-validating with the net present.
            validate_network_profile_config(root, cits, {})

            # A missing IMPORTED (non-generated) path is a hard config error — that
            # path is not something `make build` will produce.
            cits["sumo"]["network"] = "imported/missing.net.xml"
            with self.assertRaises(SystemExit):
                validate_network_profile_config(root, cits, {})

    def test_generated_contract_supports_imported_map_without_phase_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            net = _write_simple_net(root)
            cits_path = root / "cits.json"
            cits_path.write_text(json.dumps(_auto_cits_config(net.name)), encoding="utf-8")
            cits = load_cits_config(cits_path, root=root)
            tsp = TSPConfig(root=root, raw=_auto_tsp_config())

            intersection = cits.tls_to_intersection["J1"]
            movement = next(
                item
                for item in intersection.priority_movements
                if item.approach_edges == ["A_J1"] and item.egress_edges == ["J1_C"]
            )
            contract = build_controller_contract(cits, tsp, "J1")
            group = contract.signal_group_for_movement(movement.movement_id)

            self.assertIsNotNone(group)
            assert group is not None
            self.assertEqual(group.phase_index, 0)
            self.assertTrue(group.conflicts_with)

            request = synth_srem(
                sim_time_s=0.0,
                vehicle_id="bus_auto_1",
                intersection_alias="J1",
                tls_id="J1",
                rsu_id=intersection.rsu_id,
                lane_id="A_J1_0",
                next_edge_id="J1_C",
                eta_to_stopline_s=15.0,
                distance_to_stopline_m=80.0,
                schedule_delay_s=120.0,
                operator_priority_class=OperatorPriorityClass.HIGH_DELAY.value,
                priority_movement_id=movement.movement_id,
                target_signal_group_id_hint=movement.target_signal_group_id,
            )
            signal_state = SignalState(
                intersection_id="J1",
                tls_id="J1",
                rsu_id=intersection.rsu_id,
                timestamp_s=0.0,
                current_phase_index=3,
                current_program_id="0",
                red_yellow_green_state="rG",
                next_switch_s=30.0,
                spent_duration_s=10.0,
                controlled_lanes=["A_J1_0", "B_J1_0"],
                controlled_links=[],
            )

            decision = TSPDecisionEngine(cits, tsp).decide(request, signal_state, 0.0)
            self.assertEqual(decision.action, TSPAction.EARLY_GREEN.value)
            self.assertEqual(decision.target_phase_index, 0)

            safety = TSPSafetyLayer(cits, tsp)
            safety.set_signal_program_verified(True)
            validation = safety.validate(decision, signal_state, 0.0)
            self.assertTrue(validation.approved, validation.reason)

            green_state = SignalState(
                intersection_id="J1",
                tls_id="J1",
                rsu_id=intersection.rsu_id,
                timestamp_s=0.0,
                current_phase_index=0,
                current_program_id="0",
                red_yellow_green_state="Gr",
                next_switch_s=20.0,
                spent_duration_s=5.0,
                controlled_lanes=["A_J1_0", "B_J1_0"],
                controlled_links=[],
            )
            bucket = state_bucket_for_context(tsp, {}, request, green_state, 0.0)
            self.assertTrue(bucket.startswith("priority_movement_green|"), bucket)


def _write_simple_net(root: Path) -> Path:
    net = root / "network.net.xml"
    net.write_text(
        """<net>
    <tlLogic id="J1" type="static" programID="0" offset="0">
        <phase duration="20" state="Gr"/>
        <phase duration="3" state="yr"/>
        <phase duration="1" state="rr"/>
        <phase duration="20" state="rG"/>
        <phase duration="3" state="ry"/>
        <phase duration="1" state="rr"/>
    </tlLogic>
    <junction id="J1" type="traffic_light" incLanes="A_J1_0 B_J1_0" intLanes=""/>
    <connection from="A_J1" to="J1_C" fromLane="0" toLane="0" tl="J1" linkIndex="0" dir="s" state="o"/>
    <connection from="B_J1" to="J1_D" fromLane="0" toLane="0" tl="J1" linkIndex="1" dir="s" state="o"/>
</net>
""",
        encoding="utf-8",
    )
    return net


def _auto_cits_config(network_name: str) -> dict:
    return {
        "sumo": {"network": network_name},
        "network_discovery": {
            "enabled": True,
            "augment_missing_intersections": True,
            "priority_vehicle_classes": ["public_transport"],
            "rsu_id_prefix": "RSU_AUTO_",
        },
        "obu_policy": {},
        "rsu_policy": {},
        "safety_constraints": {
            "min_green_s": 8,
            "max_green_extension_s": 12,
            "max_total_green_s": 55,
            "yellow_s": 3,
            "all_red_s": 1,
            "pedestrian_clearance_must_not_be_shortened": True,
            "never_skip_yellow_or_all_red": True,
            "max_consecutive_priority_interventions_per_tls": 2,
            "cooldown_after_priority_s": 90,
        },
        "intersections": [],
    }


def _auto_tsp_config() -> dict:
    return {
        "decision_policy": {
            "min_priority_score": 0.0,
            "eta_arrival_buffer_s": 4,
            "green_extension_min_s": 3,
            "green_extension_default_s": 8,
            "green_extension_max_s": 12,
            "early_green_min_eta_s": 10,
            "red_truncation_to_s": 2,
            "delay_normalisation_s": 180,
            "headway_normalisation_s": 240,
            "distance_normalisation_m": 250,
            "weights": {
                "schedule_delay": 0.45,
                "headway_deviation": 0.2,
                "proximity": 0.2,
                "priority_level": 0.15,
            },
        },
        "actuation": {
            "allow_green_extension": True,
            "allow_red_truncation": True,
            "allow_direct_phase_jump": False,
        },
        "network_profile": {
            "enabled": True,
            "prefer_generated_contracts_for_unknown_tls": True,
        },
        "controller_contracts": {
            "default": {
                "adapter_type": "sumo_traci",
                "fixed_time_required": True,
                "allowed_actions": ["green_extension", "early_green"],
            }
        },
        "phase_mapping": {},
    }


if __name__ == "__main__":
    unittest.main()
