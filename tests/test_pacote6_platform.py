#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_platform.data_loader import collect_snapshot, latest_records, parse_tripinfo, read_jsonl


class PlatformDataLoaderTest(unittest.TestCase):
    def test_collect_snapshot_aggregates_cits_tsp_and_optimization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs").mkdir()
            (root / "outputs").mkdir()
            (root / "reports").mkdir()
            (root / "configs" / "platform_config.json").write_text(
                json.dumps(
                    {
                        "artifacts": {
                            "cits_messages": "outputs/cits_messages.jsonl",
                            "tsp_decisions": "outputs/tsp_decisions.jsonl",
                            "tsp_actuation": "outputs/tsp_actuation.jsonl",
                            "policy_candidates": "outputs/pacote5_policy_candidates.jsonl",
                            "optimization_summary": "reports/pacote5_optimization_summary.json",
                        },
                        "critical_artifacts": ["cits_messages", "tsp_decisions"],
                    }
                ),
                encoding="utf-8",
            )
            write_jsonl(
                root / "outputs" / "cits_messages.jsonl",
                [
                    {"message_type": "SREM_like", "vehicle_id": "bus_1", "rsu_id": "RSU_1"},
                    {"message_type": "SSEM_like", "status": "acknowledged", "action": "forward_to_decision_engine"},
                ],
            )
            write_jsonl(
                root / "outputs" / "tsp_decisions.jsonl",
                [
                    {"action": "green_extension", "status": "approved", "rsu_id": "RSU_1", "vehicle_id": "bus_1"},
                    {"action": "early_green", "status": "blocked_by_safety", "rsu_id": "RSU_2", "vehicle_id": "bus_2"},
                ],
            )
            write_jsonl(
                root / "outputs" / "tsp_actuation.jsonl",
                [{"action": "green_extension", "applied": True, "dry_run": True, "tls_id": "TLS_1"}],
            )
            write_jsonl(
                root / "outputs" / "pacote5_policy_candidates.jsonl",
                [
                    {"action": "green_extension", "selected": True, "safety_status": "approved"},
                    {"action": "early_green", "selected": False, "safety_status": "blocked_by_safety"},
                ],
            )
            (root / "reports" / "pacote5_optimization_summary.json").write_text(
                json.dumps({"reward_delta": 7.5, "candidate_count": 2}),
                encoding="utf-8",
            )

            snapshot = collect_snapshot(root)
            overview = snapshot["aggregates"]["overview"]

            self.assertEqual(overview["total_cits_messages"], 2)
            self.assertEqual(overview["total_tsp_decisions"], 2)
            self.assertEqual(overview["blocked_by_safety"], 1)
            self.assertEqual(overview["applied_actuation_events"], 1)
            self.assertEqual(overview["unsafe_candidates_filtered"], 1)
            self.assertEqual(overview["reward_delta"], 7.5)
            self.assertFalse(snapshot["missing_critical_artifacts"])

    def test_existing_logs_do_not_fallback_to_stale_nonzero_summaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs").mkdir()
            (root / "outputs").mkdir()
            (root / "reports").mkdir()
            (root / "configs" / "platform_config.json").write_text(
                json.dumps(
                    {
                        "artifacts": {
                            "cits_messages": "outputs/cits_messages.jsonl",
                            "tsp_decisions": "outputs/tsp_decisions.jsonl",
                            "tsp_actuation": "outputs/tsp_actuation.jsonl",
                            "policy_candidates": "outputs/pacote5_policy_candidates.jsonl",
                            "cits_summary": "reports/cits_emulation_summary.json",
                            "tsp_summary": "reports/tsp_emulation_summary.json",
                            "optimization_summary": "reports/pacote5_optimization_summary.json",
                        }
                    }
                ),
                encoding="utf-8",
            )
            (root / "outputs" / "cits_messages.jsonl").write_text('{"message_type":"SREM_like"}\n{broken}\n', encoding="utf-8")
            write_jsonl(root / "outputs" / "tsp_decisions.jsonl", [{"status": "approved"}])
            write_jsonl(root / "outputs" / "tsp_actuation.jsonl", [{"applied": False, "dry_run": "false"}])
            write_jsonl(
                root / "outputs" / "pacote5_policy_candidates.jsonl",
                [{"selected": "false", "is_safety_blocked": "false", "safety_status": "approved"}],
            )
            (root / "reports" / "cits_emulation_summary.json").write_text(
                json.dumps({"total_messages": 99}),
                encoding="utf-8",
            )
            (root / "reports" / "tsp_emulation_summary.json").write_text(
                json.dumps({"blocked_by_safety": 7, "applied_events": 8}),
                encoding="utf-8",
            )
            (root / "reports" / "pacote5_optimization_summary.json").write_text(
                json.dumps({"unsafe_candidates_filtered": 9}),
                encoding="utf-8",
            )

            overview = collect_snapshot(root)["aggregates"]["overview"]

            self.assertEqual(overview["total_cits_messages"], 1)
            self.assertEqual(overview["blocked_by_safety"], 0)
            self.assertEqual(overview["applied_actuation_events"], 0)
            self.assertEqual(overview["unsafe_candidates_filtered"], 0)

    def test_corrupt_platform_config_surfaces_error_without_crashing(self) -> None:
        # M5: config inválido -> defaults + config_error visível, sem rebentar.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs").mkdir()
            (root / "outputs").mkdir()
            (root / "reports").mkdir()
            (root / "configs" / "platform_config.json").write_text("{ not valid json", encoding="utf-8")

            snapshot = collect_snapshot(root)

            self.assertTrue(snapshot["config_error"])
            self.assertIn("platform config", snapshot["config_error"])
            # Defaults continuam a ser aplicados (artefactos esperados presentes).
            self.assertIn("cits_messages", snapshot["config"]["artifacts"])
            self.assertEqual(snapshot["aggregates"]["overview"]["total_cits_messages"], 0)

    def test_jsonl_reader_keeps_parse_errors_visible(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broken.jsonl"
            path.write_text('{"ok": true}\n{broken}\n', encoding="utf-8")
            records = read_jsonl(path)
            self.assertEqual(len(records), 2)
            self.assertIn("__parse_error__", records[1])

    def test_parse_tripinfo_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tripinfo.xml"
            path.write_text(
                '<tripinfos>'
                '<tripinfo id="veh1" duration="10" routeLength="100" waitingTime="2" />'
                '<tripinfo id="veh2" duration="20" routeLength="140" waitingTime="4" />'
                '</tripinfos>',
                encoding="utf-8",
            )
            summary = parse_tripinfo(path)
            self.assertEqual(summary["vehicle_count"], 2)
            self.assertEqual(summary["avg_duration_s"], 15.0)
            self.assertEqual(summary["avg_route_length_m"], 120.0)
            self.assertEqual(summary["avg_waiting_time_s"], 3.0)

    def test_latest_records(self) -> None:
        records = [{"i": i} for i in range(5)]
        self.assertEqual(latest_records(records, 2), [{"i": 3}, {"i": 4}])
        self.assertEqual(latest_records(records, 0), [])

    def test_check_platform_data_default_output_follows_requested_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "check_platform_data.py"),
                    "--root",
                    str(root),
                    "--max-records",
                    "10",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            expected = root / "reports" / "platform_snapshot.json"
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(expected.exists())
            self.assertIn(str(expected), result.stdout)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
