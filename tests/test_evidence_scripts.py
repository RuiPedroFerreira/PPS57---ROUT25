#!/usr/bin/env python3
"""Regressões dos instrumentos de evidência VIVOS.

Cobre os instrumentos city-agnostic que continuam no caminho principal — o
network-binding check e o empirical network-profile check (reorientados para a
rede de Ingolstadt) — mais a sua config partilhada e o predicado fail-close.
Cada fixture é um vetor de verificação sintético, escolhido para o resultado ser
calculável à mão: prova que os instrumentos se comportam como documentado, nada
mais. NENHUM número é uma medição de tráfego.

A cobertura dos instrumentos LEGADOS de Porto/Boavista (V2-envelope, contagens de
referência, corredor de referência) vive em ``tests/test_legacy_porto_evidence.py``,
apontado a ``scripts/legacy_porto/`` (ver ``scripts/legacy_porto/README.md``).
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for entry in (str(SRC), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import _evidence_common  # noqa: E402
import empirical_network_profile_check  # noqa: E402
import run_network_binding_check  # noqa: E402

from pps57_sumo.validation.acceptance import (  # noqa: E402
    evaluate_tsp_face_validity,
    load_validation_config,
)
from pps57_tsp.signal_control import (  # noqa: E402
    ControllerContract,
    SignalGroupContract,
    signal_group_lacks_conflict_matrix,
)


class SharedEvidenceConfigTests(unittest.TestCase):
    def test_live_scripts_share_the_same_builders(self) -> None:
        for module in (run_network_binding_check, empirical_network_profile_check):
            self.assertIs(
                module.auto_discovery_cits_config, _evidence_common.auto_discovery_cits_config
            )
            self.assertIs(module.auto_tsp_config, _evidence_common.auto_tsp_config)

    def test_auto_tsp_config_keeps_the_demo_decision_policy(self) -> None:
        tsp = _evidence_common.auto_tsp_config(ROOT)
        policy = tsp.raw["decision_policy"]
        self.assertEqual(
            policy["weights"],
            {
                "schedule_delay": 0.45,
                "headway_deviation": 0.2,
                "proximity": 0.2,
                "priority_level": 0.15,
            },
        )
        self.assertFalse(tsp.raw["actuation"]["allow_direct_phase_jump"])
        self.assertEqual(
            tsp.raw["controller_contracts"]["default"]["allowed_actions"],
            ["green_extension", "early_green"],
        )

    def test_running_time_envelope_comes_from_validation_config(self) -> None:
        config = load_validation_config()
        band = config["tsp_face_validity"]["bus_running_time_improvement_pct"]
        envelope, source = _evidence_common.running_time_envelope(config)
        self.assertEqual(envelope, (float(band["min"]), float(band["max"])))
        self.assertEqual(source, band["source"])


def _contract(groups: dict) -> ControllerContract:
    return ControllerContract(
        tls_id="tls1",
        adapter_type="sumo_traci",
        fixed_time_required=True,
        allowed_actions=["green_extension"],
        phase_sequence=[0, 1],
        service_green_phase_indices=[0],
        intergreen_phase_indices=[1],
        min_yellow_s=3.0,
        min_all_red_s=None,
        expected_cycle_s=None,
        pedestrian_phase_required=False,
        pedestrian_phase_indices=[],
        signal_groups=groups,
    )


class FailClosePredicateTests(unittest.TestCase):
    def test_predicate_matches_documented_condition(self) -> None:
        lacking = SignalGroupContract(signal_group_id="g1", phase_index=0, movement_ids=["m1"])
        self.assertTrue(signal_group_lacks_conflict_matrix(lacking))
        with_conflicts = SignalGroupContract(
            signal_group_id="g2", phase_index=0, movement_ids=["m1"], conflicts_with=["g1"]
        )
        self.assertFalse(signal_group_lacks_conflict_matrix(with_conflicts))
        known_empty = SignalGroupContract(
            signal_group_id="g3", phase_index=0, movement_ids=["m1"], conflict_matrix_known=True
        )
        self.assertFalse(signal_group_lacks_conflict_matrix(known_empty))
        no_movements = SignalGroupContract(signal_group_id="g4", phase_index=0)
        self.assertFalse(signal_group_lacks_conflict_matrix(no_movements))

    def test_binding_check_counts_via_the_exported_predicate(self) -> None:
        groups = {
            "g1": SignalGroupContract(signal_group_id="g1", phase_index=0, movement_ids=["m1"]),
            "g2": SignalGroupContract(
                signal_group_id="g2", phase_index=1, movement_ids=["m2"], conflict_matrix_known=True
            ),
        }
        tripped = run_network_binding_check._fail_close_groups([_contract(groups)])
        self.assertEqual(tripped, [{"tls_id": "tls1", "signal_group_id": "g1"}])


class FaceValidityAnchorsTests(unittest.TestCase):
    def test_configured_anchors_echoed_as_context_only(self) -> None:
        config = load_validation_config()
        report = evaluate_tsp_face_validity(
            [{"metric": "bus_running_time_improvement_pct", "value_pct": 7.5}], config
        )
        anchors = report["published_corridor_anchors_pct"]
        expected = {
            key: value
            for key, value in config["tsp_face_validity"][
                "corridor_travel_time_anchors_pct"
            ].items()
            if key != "source"
        }
        self.assertEqual(anchors["anchors"], expected)
        self.assertIn("context only", anchors["role"])
        self.assertEqual(report["verdict"], "plausible")

    def test_absent_anchors_omit_the_context_block(self) -> None:
        config = json.loads(json.dumps(load_validation_config()))
        del config["tsp_face_validity"]["corridor_travel_time_anchors_pct"]
        report = evaluate_tsp_face_validity(
            [{"metric": "bus_running_time_improvement_pct", "value_pct": 7.5}], config
        )
        self.assertNotIn("published_corridor_anchors_pct", report)


if __name__ == "__main__":
    unittest.main()
