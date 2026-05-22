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
from pps57_platform.runner import ScenarioRunOptions, ScenarioRunner, RunnerUnsupportedError


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
                            "policy_candidates": "outputs/policy_candidates.jsonl",
                            "optimization_summary": "reports/policy_optimization_summary.json",
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
                [{"action": "green_extension", "applied": True, "no_actuation": False, "tls_id": "TLS_1"}],
            )
            write_jsonl(
                root / "outputs" / "policy_candidates.jsonl",
                [
                    {"action": "green_extension", "selected": True, "safety_status": "approved"},
                    {"action": "early_green", "selected": False, "safety_status": "blocked_by_safety"},
                ],
            )
            (root / "reports" / "policy_optimization_summary.json").write_text(
                json.dumps({"reward_delta": 7.5, "candidate_count": 2}),
                encoding="utf-8",
            )
            (root / "reports" / "tsp_baseline_vs_rl_comparison.json").write_text(
                json.dumps({"rows": [{"metric": "total_decisions", "baseline": 2, "rl": 2, "delta": 0}]}),
                encoding="utf-8",
            )
            (root / "reports" / "decision_outcome_evaluation.json").write_text(
                json.dumps(
                    {
                        "decision_count": 2,
                        "matched_decision_count": 2,
                        "network_impact_verdict": "inconclusive_without_kpis",
                        "verdict_counts": {"same": 2},
                        "rows": [],
                    }
                ),
                encoding="utf-8",
            )

            snapshot = collect_snapshot(root)
            overview = snapshot["aggregates"]["overview"]
            experiments = snapshot["aggregates"]["experiments"]

            self.assertEqual(overview["total_cits_messages"], 2)
            self.assertEqual(overview["total_tsp_decisions"], 2)
            self.assertEqual(overview["blocked_by_safety"], 1)
            self.assertEqual(overview["applied_actuation_events"], 1)
            self.assertEqual(overview["unsafe_candidates_filtered"], 1)
            self.assertEqual(overview["reward_delta"], 7.5)
            self.assertEqual(experiments["tsp_rows"][0]["metric"], "total_decisions")
            self.assertEqual(experiments["decision_outcomes"]["verdict_counts"]["same"], 2)
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
                            "policy_candidates": "outputs/policy_candidates.jsonl",
                            "cits_summary": "reports/cits_emulation_summary.json",
                            "tsp_summary": "reports/tsp_emulation_summary.json",
                            "optimization_summary": "reports/policy_optimization_summary.json",
                        }
                    }
                ),
                encoding="utf-8",
            )
            (root / "outputs" / "cits_messages.jsonl").write_text('{"message_type":"SREM_like"}\n{broken}\n', encoding="utf-8")
            write_jsonl(root / "outputs" / "tsp_decisions.jsonl", [{"status": "approved"}])
            write_jsonl(root / "outputs" / "tsp_actuation.jsonl", [{"applied": False, "no_actuation": "false"}])
            write_jsonl(
                root / "outputs" / "policy_candidates.jsonl",
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
            (root / "reports" / "policy_optimization_summary.json").write_text(
                json.dumps({"unsafe_candidates_filtered": 9}),
                encoding="utf-8",
            )

            overview = collect_snapshot(root)["aggregates"]["overview"]

            self.assertEqual(overview["total_cits_messages"], 1)
            self.assertEqual(overview["blocked_by_safety"], 0)
            self.assertEqual(overview["applied_actuation_events"], 0)
            self.assertEqual(overview["unsafe_candidates_filtered"], 0)

    def test_truncated_logs_use_summary_counts_for_platform_totals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs").mkdir()
            (root / "outputs").mkdir()
            (root / "reports").mkdir()
            (root / "configs" / "platform_config.json").write_text(
                json.dumps(
                    {
                        "artifacts": {
                            "tsp_decisions": "outputs/tsp_decisions.jsonl",
                            "tsp_actuation": "outputs/tsp_actuation.jsonl",
                            "tsp_summary": "reports/tsp_emulation_summary.json",
                        }
                    }
                ),
                encoding="utf-8",
            )
            write_jsonl(
                root / "outputs" / "tsp_decisions.jsonl",
                [{"status": "approved"} for _ in range(5)] + [{"status": "blocked_by_safety"} for _ in range(5)],
            )
            write_jsonl(
                root / "outputs" / "tsp_actuation.jsonl",
                [{"applied": True} for _ in range(3)] + [{"applied": False} for _ in range(7)],
            )
            (root / "reports" / "tsp_emulation_summary.json").write_text(
                json.dumps(
                    {
                        "total_decisions": 10,
                        "actuation_events": 10,
                        "blocked_by_safety": 5,
                        "applied_events": 3,
                    }
                ),
                encoding="utf-8",
            )

            snapshot = collect_snapshot(root, max_records=2)
            overview = snapshot["aggregates"]["overview"]
            tsp_status = {item["key"]: item for item in snapshot["artifacts"]}["tsp_decisions"]

            self.assertTrue(tsp_status["truncated"])
            self.assertEqual(tsp_status["record_count"], 10)
            self.assertEqual(overview["total_tsp_decisions"], 10)
            self.assertEqual(overview["total_actuation_events"], 10)
            self.assertEqual(overview["blocked_by_safety"], 5)
            self.assertEqual(overview["applied_actuation_events"], 3)

    def test_snapshot_warns_about_stale_global_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs").mkdir()
            (root / "outputs").mkdir()
            (root / "reports").mkdir()
            (root / "configs" / "platform_config.json").write_text(
                json.dumps(
                    {
                        "artifacts": {
                            "tsp_decisions": "outputs/tsp_decisions.jsonl",
                            "tsp_summary": "reports/tsp_emulation_summary.json",
                        }
                    }
                ),
                encoding="utf-8",
            )
            write_jsonl(root / "outputs" / "tsp_decisions.jsonl", [{"status": "approved"} for _ in range(3)])
            (root / "reports" / "tsp_emulation_summary.json").write_text(
                json.dumps({"total_decisions": 1}),
                encoding="utf-8",
            )

            warnings = collect_snapshot(root)["artifact_warnings"]

            self.assertTrue(any("stale_or_inconsistent:tsp_summary.total_decisions" in item for item in warnings))

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

    def test_scenario_runner_builds_safe_scenario_command(self) -> None:
        runner = ScenarioRunner(ROOT)
        options = ScenarioRunOptions(
            scenario_id="baseline_am_peak",
            run_type="comparison",
            steps=30,
            sumo_binary="sumo",
            gui=True,
            traci_port=8813,
        )
        command = runner._command_for(options)
        self.assertIn("scripts/run_sumo_scenario.py", command)
        self.assertIn("--scenario", command)
        self.assertIn("baseline_am_peak", command)
        self.assertIn("--run-type", command)
        self.assertIn("comparison", command)
        self.assertIn("--sumo-binary", command)
        self.assertIn("--steps", command)
        self.assertIn("30", command)
        self.assertIn("--gui", command)
        self.assertEqual(runner._environment_for(options)["TRACI_PORT"], "8813")

        all_command = runner._command_for(ScenarioRunOptions(all_scenarios=True, run_type="baseline"))
        self.assertIn("--all", all_command)
        self.assertNotIn("--scenario", all_command)

    def test_scenario_runner_rejects_unknown_scenario_and_bad_run_type(self) -> None:
        runner = ScenarioRunner(ROOT)
        with self.assertRaises(RunnerUnsupportedError):
            runner._validate_options(ScenarioRunOptions(scenario_id="unknown"))
        with self.assertRaises(RunnerUnsupportedError):
            runner._validate_options(ScenarioRunOptions(run_type="shell-arbitrary"))  # type: ignore[arg-type]
        self.assertIsNone(runner.process)

    def test_run_dashboard_loopback_detection(self) -> None:
        from scripts.run_dashboard import _is_loopback  # type: ignore[import-not-found]

        self.assertTrue(_is_loopback("127.0.0.1"))
        self.assertTrue(_is_loopback("localhost"))
        self.assertTrue(_is_loopback("::1"))
        self.assertFalse(_is_loopback("0.0.0.0"))
        self.assertFalse(_is_loopback("10.0.0.5"))
        self.assertFalse(_is_loopback("not-a-host"))

    def test_run_dashboard_blocks_non_loopback_without_explicit_opt_in(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "run_dashboard.py"),
                "--host",
                "0.0.0.0",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("BLOQUEADO", result.stderr)

    def test_fastapi_dashboard_exposes_expected_routes(self) -> None:
        from pps57_platform.api import ScenarioRunRequest, create_app

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "configs").mkdir()
            (root / "outputs").mkdir()
            (root / "reports").mkdir()
            (root / "configs" / "sumo_scenario_base.json").write_text(
                (ROOT / "configs" / "sumo_scenario_base.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (root / "configs" / "scenario_catalog.yaml").write_text(
                (ROOT / "configs" / "scenario_catalog.yaml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            app = create_app(root)
            paths = {route.path for route in app.routes}

            self.assertIn("/", paths)
            self.assertIn("/dashboard", paths)
            self.assertIn("/api/scenarios", paths)
            self.assertIn("/api/runs/start", paths)
            self.assertIn("/api/runs/current", paths)
            self.assertIn("/api/reports", paths)
            options = ScenarioRunRequest(scenario_id="baseline_am_peak", steps=3, run_type="comparison").to_options()
            self.assertEqual(options.steps, 3)
            self.assertEqual(options.scenario_id, "baseline_am_peak")
            self.assertFalse(options.gui)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
