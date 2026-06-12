#!/usr/bin/env python3
"""NetworkBinding: authoritative conflict-matrix tests.

Fixtures are tiny, hand-built SUMO nets whose conflict answer is computable by
inspection (the SUMO foes-bitmask rule and the (from,to)->signal-group mapping are
the source of truth). They assert the binding reads the network's own conflict
data correctly — in particular the case the phase-disjointness heuristic gets
wrong: two *permissive* movements that share a green phase but physically cross.
"""
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

from pps57_cits.config import load_cits_config  # noqa: E402
from pps57_sumo.network_binding import (  # noqa: E402
    build_network_binding,
    foe_local_indices,
)
from pps57_sumo.network_profile import load_network_profile  # noqa: E402
from pps57_tsp.config import TSPConfig  # noqa: E402
from pps57_tsp.controller import TSPControlController  # noqa: E402
from pps57_tsp.signal_control import (  # noqa: E402
    ControllerContract,
    SignalGroupContract,
    TraciSignalControlAdapter,
    apply_network_binding,
)

# Two crossing movements, BOTH green in phase 0 (permissive) — so the
# phase-disjointness heuristic finds no conflict — but the junction foes say they
# conflict. foes for a 2-link junction: rightmost char = link 0.
#   index 0 foes="10" -> link 0 conflicts with link 1
#   index 1 foes="01" -> link 1 conflicts with link 0
PERMISSIVE_NET = """<?xml version="1.0" encoding="UTF-8"?>
<net>
  <junction id="J" type="traffic_light" incLanes="E1_0 E3_0" intLanes=":J_0_0 :J_1_0">
    <request index="0" response="00" foes="10" cont="0"/>
    <request index="1" response="00" foes="01" cont="0"/>
  </junction>
  <tlLogic id="J" type="static" programID="0" offset="0">
    <phase duration="30" state="GG"/>
    <phase duration="5" state="yy"/>
  </tlLogic>
  <connection from="E1" to="E2" fromLane="0" toLane="0" tl="J" linkIndex="0" dir="s" state="O" via=":J_0_0"/>
  <connection from="E3" to="E4" fromLane="0" toLane="0" tl="J" linkIndex="1" dir="s" state="O" via=":J_1_0"/>
</net>
"""

G1 = "J_movement_E1_to_E2"
G2 = "J_movement_E3_to_E4"

# Multi-lane internal edge: :J_0 has TWO lanes (:J_0_0, :J_0_1), so the request
# index is the position in intLanes (0,1,2), NOT the number embedded in the lane
# id (parsing the id collapses :J_0_0 and :J_0_1 onto the same slot and misindexes
# :J_2_0). The left turn (index 1) and the cross through (index 2) are foes.
MULTILANE_NET = """<?xml version="1.0" encoding="UTF-8"?>
<net>
  <junction id="J" type="traffic_light" incLanes="E1_0 E1_1 E3_0" intLanes=":J_0_0 :J_0_1 :J_2_0">
    <request index="0" response="000" foes="000" cont="0"/>
    <request index="1" response="000" foes="100" cont="0"/>
    <request index="2" response="000" foes="010" cont="0"/>
  </junction>
  <tlLogic id="J" type="static" programID="0" offset="0">
    <phase duration="30" state="GGG"/>
    <phase duration="5" state="yyy"/>
  </tlLogic>
  <connection from="E1" to="E2" fromLane="0" toLane="0" tl="J" linkIndex="0" dir="s" state="O" via=":J_0_0"/>
  <connection from="E1" to="E4" fromLane="1" toLane="0" tl="J" linkIndex="1" dir="l" state="o" via=":J_0_1"/>
  <connection from="E3" to="E4" fromLane="0" toLane="0" tl="J" linkIndex="2" dir="s" state="O" via=":J_2_0"/>
</net>
"""

ML_THROUGH = "J_movement_E1_to_E2"
ML_LEFT = "J_movement_E1_to_E4"
ML_CROSS = "J_movement_E3_to_E4"

# One connection carries no via (e.g. --no-internal-links): its group must still
# be *recorded* with conflict_source="none" instead of silently dropped.
NOVIA_NET = """<?xml version="1.0" encoding="UTF-8"?>
<net>
  <junction id="J" type="traffic_light" incLanes="E1_0 E3_0 E5_0" intLanes=":J_0_0 :J_1_0">
    <request index="0" response="00" foes="10" cont="0"/>
    <request index="1" response="00" foes="01" cont="0"/>
  </junction>
  <tlLogic id="J" type="static" programID="0" offset="0">
    <phase duration="30" state="GGG"/>
    <phase duration="5" state="yyy"/>
  </tlLogic>
  <connection from="E1" to="E2" fromLane="0" toLane="0" tl="J" linkIndex="0" dir="s" state="O" via=":J_0_0"/>
  <connection from="E3" to="E4" fromLane="0" toLane="0" tl="J" linkIndex="1" dir="s" state="O" via=":J_1_0"/>
  <connection from="E5" to="E6" fromLane="0" toLane="0" tl="J" linkIndex="2" dir="s" state="O"/>
</net>
"""


def _write_net(text: str) -> Path:
    handle = tempfile.NamedTemporaryFile("w", suffix=".net.xml", delete=False)
    handle.write(text)
    handle.close()
    return Path(handle.name)


class HelperTests(unittest.TestCase):
    def test_foe_local_indices_bit_order(self) -> None:
        # rightmost char is link 0.
        self.assertEqual(foe_local_indices("10"), [1])
        self.assertEqual(foe_local_indices("01"), [0])
        self.assertEqual(foe_local_indices("1001"), [0, 3])
        self.assertEqual(foe_local_indices("0000"), [])


class BindingTests(unittest.TestCase):
    def test_heuristic_misses_permissive_conflict(self) -> None:
        net = _write_net(PERMISSIVE_NET)
        profile = load_network_profile(net)
        # Both movements are green in phase 0 -> heuristic finds NO conflict.
        for movement in profile.tls_profile("J").movements:
            self.assertEqual(movement.conflicts_with, [], movement.signal_group_id)

    def test_binding_reads_authoritative_conflict(self) -> None:
        net = _write_net(PERMISSIVE_NET)
        binding = build_network_binding(net)
        self.assertEqual(binding.conflicts_for("J", G1), [G2])
        self.assertEqual(binding.conflicts_for("J", G2), [G1])
        bound = binding.binding_for_tls("J")
        self.assertTrue(bound.signal_groups[G1].conflict_matrix_known)
        self.assertEqual(bound.signal_groups[G1].conflict_source, "sumo_request_foes")

    def test_coverage_report(self) -> None:
        net = _write_net(PERMISSIVE_NET)
        report = build_network_binding(net).coverage_report()
        self.assertEqual(report["n_signal_groups"], 2)
        self.assertEqual(report["groups_with_authoritative_conflicts"], 2)
        self.assertEqual(report["coverage_fraction"], 1.0)

    def test_multilane_internal_edge_uses_intlanes_positions(self) -> None:
        # Regression: the request index is the position in intLanes; deriving it
        # from the lane-id numbers collapses sibling lanes of a multi-lane
        # internal edge and loses the LEFT<->CROSS conflict below.
        net = _write_net(MULTILANE_NET)
        binding = build_network_binding(net)
        self.assertEqual(binding.conflicts_for("J", ML_LEFT), [ML_CROSS])
        self.assertEqual(binding.conflicts_for("J", ML_CROSS), [ML_LEFT])
        through = binding.binding_for_tls("J").signal_groups[ML_THROUGH]
        self.assertTrue(through.conflict_matrix_known)
        self.assertEqual(through.conflicts_with, [])

    def test_group_without_via_is_recorded_not_dropped(self) -> None:
        net = _write_net(NOVIA_NET)
        binding = build_network_binding(net)
        bound = binding.binding_for_tls("J")
        group = bound.signal_groups.get("J_movement_E5_to_E6")
        self.assertIsNotNone(group, "group sem via deve ser registado, não omitido")
        self.assertEqual(group.conflict_source, "none")
        self.assertFalse(group.conflict_matrix_known)
        report = binding.coverage_report()
        self.assertEqual(report["n_signal_groups"], 3)
        self.assertEqual(report["groups_with_authoritative_conflicts"], 2)


def _contract_with_group(group: SignalGroupContract) -> ControllerContract:
    return ControllerContract(
        tls_id="J", adapter_type="sumo_traci", fixed_time_required=False,
        allowed_actions=["green_extension"], phase_sequence=[0, 1],
        service_green_phase_indices=[0], intergreen_phase_indices=[1],
        min_yellow_s=None, min_all_red_s=None, expected_cycle_s=None,
        pedestrian_phase_required=False, pedestrian_phase_indices=[],
        signal_groups={group.signal_group_id: group},
    )


class ApplyBindingTests(unittest.TestCase):
    def test_apply_fills_conflicts_and_flag(self) -> None:
        net = _write_net(PERMISSIVE_NET)
        binding = build_network_binding(net)
        group = SignalGroupContract(signal_group_id=G1, phase_index=0, movement_ids=["m"], conflicts_with=[])
        self.assertFalse(group.conflict_matrix_known)
        bound = apply_network_binding([_contract_with_group(group)], binding)[0]
        out = bound.signal_groups[G1]
        self.assertEqual(out.conflicts_with, [G2])
        self.assertTrue(out.conflict_matrix_known)

    def test_apply_translates_config_aliases(self) -> None:
        # Hand-written configs rename profile groups (e.g. SG_NORTH); without the
        # alias map the group is never bound and conflict lists reference profile
        # names absent from the contract.
        net = _write_net(PERMISSIVE_NET)
        binding = build_network_binding(net)
        group = SignalGroupContract(
            signal_group_id="SG_NORTH", phase_index=0, movement_ids=["m"], conflicts_with=[]
        )
        contract = _contract_with_group(group)

        unaliased = apply_network_binding([contract], binding)[0]
        self.assertFalse(unaliased.signal_groups["SG_NORTH"].conflict_matrix_known)

        aliases = {"J": {G1: "SG_NORTH", G2: "SG_SOUTH"}}
        bound = apply_network_binding([contract], binding, aliases_by_tls=aliases)[0]
        out = bound.signal_groups["SG_NORTH"]
        self.assertTrue(out.conflict_matrix_known)
        self.assertEqual(out.conflicts_with, ["SG_SOUTH"])


class _FakeAdapter:
    """Minimal signal-program reader returning a valid fixed-time program."""

    def read_program_phase_count(self, tls_id: str):  # noqa: D401
        return 2

    def read_program_phase_states(self, tls_id: str):
        return ["GG", "ry"]

    def read_program_phase_durations(self, tls_id: str):
        return [30.0, 5.0]

    def read_program_is_fixed_time(self, tls_id: str):
        return True

    def read_program_type(self, tls_id: str):
        return "static"

    def set_phase_duration(self, tls_id: str, duration_s: float) -> None:
        pass


def _auto_cits_config(net: Path):
    """Same auto-discovery CITS config the demo/validation scripts use."""
    payload = {
        "sumo": {"network": str(net)},
        "network_discovery": {
            "enabled": True, "augment_missing_intersections": True,
            "auto_generate_priority_movements": True,
            "priority_vehicle_classes": ["public_transport"], "rsu_id_prefix": "RSU_AUTO_",
        },
        "obu_policy": {}, "rsu_policy": {},
        "safety_constraints": {
            "min_green_s": 8, "max_green_extension_s": 12, "max_total_green_s": 55,
            "yellow_s": 3, "all_red_s": 0,
            "pedestrian_clearance_must_not_be_shortened": True, "never_skip_yellow_or_all_red": True,
            "max_consecutive_priority_interventions_per_tls": 2, "cooldown_after_priority_s": 90,
        },
        "intersections": [],
    }
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(payload, handle)
    handle.close()
    config_path = Path(handle.name)
    try:
        return load_cits_config(config_path, root=config_path.parent)
    finally:
        config_path.unlink(missing_ok=True)


def _auto_tsp_config() -> TSPConfig:
    return TSPConfig(root=ROOT, raw={
        "decision_policy": {"min_priority_score": 0.0},
        "actuation": {"allow_green_extension": True, "allow_red_truncation": True},
        "network_profile": {"enabled": True, "prefer_generated_contracts_for_unknown_tls": True},
        "controller_contracts": {"default": {"adapter_type": "sumo_traci", "fixed_time_required": True,
                                             "allowed_actions": ["green_extension", "early_green"]}},
        "phase_mapping": {}})


class ControllerWiringTests(unittest.TestCase):
    """TSPControlController builds/applies the binding itself: one source of truth."""

    def test_controller_binds_network_and_sets_safety_binding(self) -> None:
        net = _write_net(PERMISSIVE_NET)
        controller = TSPControlController(_auto_cits_config(net), _auto_tsp_config())
        self.assertIsNone(controller.network_binding_error)
        self.assertIsNotNone(controller.network_binding)
        # A Safety Layer valida contra o MESMO binding (não duas fontes de verdade).
        self.assertIs(controller.safety.network_binding, controller.network_binding)
        self.assertEqual(
            controller.safety.network_binding_aliases, controller.network_binding_aliases
        )

    def test_controller_contracts_are_bound_for_verification(self) -> None:
        net = _write_net(PERMISSIVE_NET)
        controller = TSPControlController(_auto_cits_config(net), _auto_tsp_config())
        contracts = controller._signal_controlled_contracts()
        groups = {
            group.signal_group_id: group
            for contract in contracts
            for group in contract.signal_groups.values()
        }
        self.assertIn(G1, groups)
        self.assertTrue(groups[G1].conflict_matrix_known)
        self.assertEqual(groups[G1].conflicts_with, [G2])
        self.assertTrue(groups[G2].conflict_matrix_known)
        self.assertEqual(groups[G2].conflicts_with, [G1])
        # O gate "sem matriz de conflitos" deixa de disparar no pipeline real.
        problems = controller._verify_signal_programs(_FakeAdapter())
        self.assertFalse(any("sem matriz de conflitos" in p for p in problems), problems)

    def test_controller_without_binding_still_fail_closes(self) -> None:
        # Mesma rede, binding desligado: a verificação tem de continuar a
        # disparar o gate — prova que é o binding (e só ele) que o resolve.
        net = _write_net(PERMISSIVE_NET)
        controller = TSPControlController(_auto_cits_config(net), _auto_tsp_config())
        controller.network_binding = None
        controller.network_binding_aliases = None
        problems = controller._verify_signal_programs(_FakeAdapter())
        self.assertTrue(any("sem matriz de conflitos" in p for p in problems), problems)

    def test_controller_handles_missing_network_without_crashing(self) -> None:
        missing = Path(tempfile.gettempdir()) / "pps57_missing_binding_fixture.net.xml"
        missing.unlink(missing_ok=True)
        controller = TSPControlController(_auto_cits_config(missing), _auto_tsp_config())
        self.assertIsNone(controller.network_binding)
        self.assertIsNone(controller.safety.network_binding)
        self.assertIsNotNone(controller.network_binding_error)
        self.assertIn(missing.name, controller.network_binding_error)


class VerifierFailCloseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = TraciSignalControlAdapter(adapter=_FakeAdapter())

    def test_unknown_empty_matrix_fail_closes(self) -> None:
        group = SignalGroupContract(signal_group_id="g", phase_index=0, movement_ids=["m"],
                                    conflicts_with=[], conflict_matrix_known=False)
        problems = self.adapter.verify_controller_contracts([_contract_with_group(group)])
        self.assertTrue(any("sem matriz de conflitos" in p for p in problems), problems)

    def test_known_empty_matrix_does_not_fail_close(self) -> None:
        # Genuinely conflict-free group: empty conflicts but authoritatively known.
        group = SignalGroupContract(signal_group_id="g", phase_index=0, movement_ids=["m"],
                                    conflicts_with=[], conflict_matrix_known=True)
        problems = self.adapter.verify_controller_contracts([_contract_with_group(group)])
        self.assertFalse(any("sem matriz de conflitos" in p for p in problems), problems)


if __name__ == "__main__":
    unittest.main()
