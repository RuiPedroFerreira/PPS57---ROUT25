#!/usr/bin/env python3
"""Regressões dos instrumentos de evidência LEGADOS (fase Porto/Boavista).

Estes scripts foram arquivados em ``scripts/legacy_porto/`` quando o projeto
passou para o pipeline de cenários actual (ver
``scripts/legacy_porto/README.md``). Mantemos a cobertura para o código
arquivado não apodrecer silenciosamente — prova de proveniência/hash, despacho
do gate V2 configurado, contabilidade de fluxo-zero e a fonte da banda de
Madrid. Cada fixture é um vetor de verificação sintético, computável à mão;
NENHUM número é uma medição de tráfego.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
LEGACY = ROOT / "scripts" / "legacy_porto"
for entry in (str(SRC), str(LEGACY)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import build_reference_corridor  # noqa: E402
import fetch_reference_counts  # noqa: E402
import run_v2_demand_validation as run_v2  # noqa: E402

from pps57_sumo.validation.acceptance import load_validation_config  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
