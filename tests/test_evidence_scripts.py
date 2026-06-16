#!/usr/bin/env python3
"""Evidence-script regression tests: provenance integrity, gate dispatch, dedupe.

Every fixture is a synthetic verification vector chosen so the expected result is
computable by hand. NONE of these numbers is a traffic measurement: this suite
proves the evidence instruments (provenance merge/hash gates, the configured V2
gate rule, the zero-flow accounting, the shared auto-config and the fail-close
predicate) behave as documented, nothing more.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for entry in (str(SRC), str(SCRIPTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import _evidence_common  # noqa: E402
import build_reference_corridor  # noqa: E402
import empirical_network_profile_check  # noqa: E402
import fetch_reference_counts  # noqa: E402
import run_network_binding_check  # noqa: E402
import run_tsp_demo  # noqa: E402
import run_v2_demand_validation as run_v2  # noqa: E402

from pps57_sumo.validation.acceptance import evaluate_tsp_face_validity, load_validation_config  # noqa: E402
from pps57_tsp.signal_control import (  # noqa: E402
    ControllerContract,
    SignalGroupContract,
    signal_group_lacks_conflict_matrix,
)


class ProvenanceMergeTests(unittest.TestCase):
    def test_merge_keeps_sections_for_skipped_sources(self) -> None:
        existing = {"fetched_at_utc": "old", "sources": {"madrid": {"intensity_sha256": "aa"}}}
        merged = fetch_reference_counts.merge_provenance(
            existing, {"dft": {"sha256": "bb"}}, fetched_at_utc="new"
        )
        self.assertEqual(merged["fetched_at_utc"], "new")
        self.assertEqual(merged["sources"]["madrid"], {"intensity_sha256": "aa"})
        self.assertEqual(merged["sources"]["dft"], {"sha256": "bb"})

    def test_merge_overwrites_refetched_source(self) -> None:
        existing = {"sources": {"dft": {"sha256": "old"}}}
        merged = fetch_reference_counts.merge_provenance(
            existing, {"dft": {"sha256": "new"}}, fetched_at_utc="now"
        )
        self.assertEqual(merged["sources"]["dft"], {"sha256": "new"})

    def test_merge_without_existing_provenance(self) -> None:
        merged = fetch_reference_counts.merge_provenance(
            None, {"madrid": {"intensity_sha256": "aa"}}, fetched_at_utc="now"
        )
        self.assertEqual(sorted(merged["sources"]), ["madrid"])

    def test_corrupt_existing_provenance_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "provenance.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(SystemExit):
                fetch_reference_counts.load_existing_provenance(path)

    def test_absent_provenance_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(
                fetch_reference_counts.load_existing_provenance(Path(tmp) / "provenance.json")
            )


def _raw_dir_with_provenance(tmp: str) -> tuple[Path, dict]:
    import hashlib

    raw_dir = Path(tmp)
    payloads = {
        "madrid_pm.xml": b"<pms><pm><intensidad>1</intensidad></pm></pms>",
        "madrid_catalogue.csv": b'"tipo_elem";"id"\n"URB";1\n',
        "dft_aadf.json": b"[]",
    }
    for name, blob in payloads.items():
        (raw_dir / name).write_bytes(blob)
    sha = {name: hashlib.sha256(blob).hexdigest() for name, blob in payloads.items()}
    provenance = {
        "sources": {
            "madrid": {
                "intensity_sha256": sha["madrid_pm.xml"],
                "catalogue_sha256": sha["madrid_catalogue.csv"],
            },
            "dft": {"sha256": sha["dft_aadf.json"], "local_authorities": {"Leeds": 3}},
        }
    }
    return raw_dir, provenance


class HashVerificationTests(unittest.TestCase):
    def test_matching_hashes_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir, provenance = _raw_dir_with_provenance(tmp)
            checks = run_v2.verify_raw_hashes(raw_dir, provenance)
            self.assertEqual(len(checks), 3)
            self.assertTrue(all(item["matches_provenance"] for item in checks))

    def test_tampered_raw_file_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir, provenance = _raw_dir_with_provenance(tmp)
            (raw_dir / "dft_aadf.json").write_bytes(b"[1]")
            with self.assertRaises(SystemExit) as ctx:
                run_v2.verify_raw_hashes(raw_dir, provenance)
            self.assertIn("SHA-256 mismatch", str(ctx.exception))

    def test_missing_source_section_refused_with_clear_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir, provenance = _raw_dir_with_provenance(tmp)
            del provenance["sources"]["dft"]
            with self.assertRaises(SystemExit) as ctx:
                run_v2.verify_raw_hashes(raw_dir, provenance)
            self.assertIn("'dft'", str(ctx.exception))
            self.assertIn("--skip-dft", str(ctx.exception))

    def test_missing_recorded_hash_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir, provenance = _raw_dir_with_provenance(tmp)
            del provenance["sources"]["madrid"]["catalogue_sha256"]
            with self.assertRaises(SystemExit):
                run_v2.verify_raw_hashes(raw_dir, provenance)

    def test_provenance_source_accessor_fails_loudly(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            run_v2.provenance_source({"sources": {}}, "madrid")
        self.assertIn("'madrid'", str(ctx.exception))


class GateRuleDispatchTests(unittest.TestCase):
    PLAUSIBLE = {"verdict": "plausible"}
    FLAGGED = {"verdict": "flagged"}
    NO_REF = {"verdict": "no_reference"}

    def test_corridor_appropriate_gates_on_plausibility(self) -> None:
        verdict = run_v2.gate_verdict("corridor_appropriate", self.PLAUSIBLE, self.FLAGGED, True)
        self.assertEqual(verdict, "pass")
        verdict = run_v2.gate_verdict("corridor_appropriate", self.FLAGGED, self.PLAUSIBLE, True)
        self.assertEqual(verdict, "review")

    def test_raw_percentile_spread_gates_on_envelope(self) -> None:
        verdict = run_v2.gate_verdict("raw_percentile_spread", self.FLAGGED, self.PLAUSIBLE, True)
        self.assertEqual(verdict, "pass")
        verdict = run_v2.gate_verdict("raw_percentile_spread", self.PLAUSIBLE, self.FLAGGED, True)
        self.assertEqual(verdict, "review")

    def test_insufficient_reference_paths(self) -> None:
        self.assertEqual(
            run_v2.gate_verdict("corridor_appropriate", self.PLAUSIBLE, self.PLAUSIBLE, False),
            "insufficient_reference",
        )
        self.assertEqual(
            run_v2.gate_verdict("corridor_appropriate", self.NO_REF, self.PLAUSIBLE, True),
            "insufficient_reference",
        )

    def test_unknown_rule_fails_loudly(self) -> None:
        with self.assertRaises(SystemExit):
            run_v2.gate_verdict("made_up_rule", self.PLAUSIBLE, self.PLAUSIBLE, True)

    def test_committed_config_rule_is_dispatchable(self) -> None:
        config = load_validation_config()
        rule = config["demand_reference_envelope"]["gate"]["rule"]
        self.assertIn(rule, {"corridor_appropriate", "raw_percentile_spread"})


class SharedEvidenceConfigTests(unittest.TestCase):
    def test_all_three_scripts_share_the_same_builders(self) -> None:
        for module in (run_tsp_demo, run_network_binding_check, empirical_network_profile_check):
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


class ZeroFlowEdgeAccountingTests(unittest.TestCase):
    def _edgedata(self, tmp: str, edges_xml: str) -> Path:
        path = Path(tmp) / "edgedata.xml"
        path.write_text(
            f'<meandata><interval begin="0" end="3600">{edges_xml}</interval></meandata>',
            encoding="utf-8",
        )
        return path

    def test_zero_flow_edges_counted_not_dropped_silently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._edgedata(
                tmp,
                '<edge id="e1" entered="10"/><edge id="e2" entered="0"/>'
                '<edge id="e3" entered="0"/><edge id="other" entered="99"/>',
            )
            stats = build_reference_corridor.measure_arterial_intensity(path, {"e1", "e2", "e3"})
            self.assertEqual(stats["arterial_edges_measured"], 1)
            self.assertEqual(stats["zero_flow_edges_excluded"], 2)
            self.assertEqual(stats["median_veh_h"], 10.0)

    def test_all_zero_flow_still_reports_excluded_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._edgedata(tmp, '<edge id="e1" entered="0"/><edge id="e2" entered="0"/>')
            stats = build_reference_corridor.measure_arterial_intensity(path, {"e1", "e2"})
            self.assertEqual(stats, {"arterial_edges_measured": 0, "zero_flow_edges_excluded": 2})


MADRID_SNAPSHOT_XML = (
    "<pms>\n"
    "  <fecha_hora>10/06/2026 16:20:06</fecha_hora>\n"
    "  <pm><idelem>1</idelem><intensidad>100</intensidad><error>N</error></pm>\n"
    "  <pm><idelem>2</idelem><intensidad>200</intensidad><error>N</error></pm>\n"
    "  <pm><idelem>3</idelem><intensidad>400</intensidad><error>N</error></pm>\n"
    "</pms>\n"
)
MADRID_SNAPSHOT_CSV = '"tipo_elem";"distrito";"id"\n"URB";4;1\n"URB";4;2\n"URB";4;3\n'


class MadridBandSourceTests(unittest.TestCase):
    def test_falls_back_to_pinned_static_band_when_no_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            band = build_reference_corridor.load_madrid_band(Path(tmp))
            self.assertEqual(band["band_origin"], "pinned_static_snapshot")
            self.assertEqual(band["median"], build_reference_corridor.MADRID_BAND["median"])

    def test_uses_fetched_snapshot_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            (raw_dir / "madrid_pm.xml").write_text(MADRID_SNAPSHOT_XML, encoding="utf-8")
            (raw_dir / "madrid_catalogue.csv").write_text(MADRID_SNAPSHOT_CSV, encoding="utf-8")
            (raw_dir / "provenance.json").write_text(
                json.dumps(
                    {
                        "sources": {
                            "madrid": {
                                "feed_timestamp": "10/06/2026 16:20:06",
                                "intensity_sha256": "ab" * 32,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            band = build_reference_corridor.load_madrid_band(raw_dir)
            self.assertEqual(band["band_origin"], "fetched_snapshot")
            self.assertEqual(band["n_detectors"], 3)
            self.assertEqual(band["median"], 200.0)
            self.assertEqual(band["p90"], 400.0)
            self.assertEqual(band["feed_timestamp"], "10/06/2026 16:20:06")
            self.assertEqual(band["intensity_sha256"], "ab" * 32)

    def test_unusable_snapshot_fails_instead_of_silent_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            (raw_dir / "madrid_pm.xml").write_text("<pms></pms>", encoding="utf-8")
            (raw_dir / "madrid_catalogue.csv").write_text(MADRID_SNAPSHOT_CSV, encoding="utf-8")
            (raw_dir / "provenance.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(SystemExit):
                build_reference_corridor.load_madrid_band(raw_dir)


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
