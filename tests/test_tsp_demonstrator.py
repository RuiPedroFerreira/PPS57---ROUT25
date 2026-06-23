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

from pps57_opt.demonstrator import (  # noqa: E402
    _verdict,
    build_demonstrator_report,
    load_demonstrator_run,
    render_demonstrator_markdown,
)


class TSPDemonstratorTestCase(unittest.TestCase):
    def test_report_counts_safety_and_controller_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline_root = root / "baseline"
            tsp_root = root / "tsp"
            controller_root = root / "tsp_controller"

            _write_kpis(baseline_root, "sumo_baseline", bus_loss=100.0, traffic_loss=50.0)
            _write_kpis(tsp_root, "tsp", bus_loss=85.0, traffic_loss=50.0)
            _write_kpis(controller_root, "tsp_controller", bus_loss=80.0, traffic_loss=50.0)
            _write_tsp_artifacts(tsp_root, blocked=False, rejected=False)
            _write_tsp_artifacts(controller_root, blocked=True, rejected=True)

            report = build_demonstrator_report(
                baseline=load_demonstrator_run(baseline_root, "sumo_baseline"),
                tsp=load_demonstrator_run(tsp_root, "tsp"),
                tsp_controller=load_demonstrator_run(controller_root, "tsp_controller"),
            )

            controller_runtime = report["runs"]["tsp_controller"]["runtime"]
            self.assertEqual(report["verdict"]["status"], "passes_primary_demonstrator_goal")
            self.assertEqual(controller_runtime["blocked_by_safety"], 1)
            self.assertEqual(controller_runtime["controller_rejections"], 1)
            self.assertEqual(
                controller_runtime["safety_block_by_reason"]["missing_conflict_matrix"], 1
            )
            self.assertEqual(
                controller_runtime["controller_rejection_by_reason"][
                    "controller_locked_manual_mode"
                ],
                1,
            )
            self.assertEqual(controller_runtime["per_tls"]["I1"]["safety_blocks"], 1)

            markdown = render_demonstrator_markdown(report)
            self.assertIn("Controller rejects", markdown)
            self.assertIn("passes_primary_demonstrator_goal", markdown)

    def test_missing_kpis_makes_verdict_inconclusive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for label in ["sumo_baseline", "tsp", "tsp_controller"]:
                _write_tsp_artifacts(root / label, blocked=False, rejected=False)

            report = build_demonstrator_report(
                baseline=load_demonstrator_run(root / "sumo_baseline", "sumo_baseline"),
                tsp=load_demonstrator_run(root / "tsp", "tsp"),
                tsp_controller=load_demonstrator_run(root / "tsp_controller", "tsp_controller"),
            )

            self.assertEqual(report["verdict"]["status"], "inconclusive_without_sumo_kpis")
            self.assertTrue(report["limitations"])


def _write_kpis(root: Path, label: str, *, bus_loss: float, traffic_loss: float) -> None:
    path = root / f"reports/{label}_kpis.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "source": str(root / "outputs/tripinfo.xml"),
                "all_vehicles": {
                    "vehicles": 3,
                    "mean_duration_s": 120.0,
                    "mean_waiting_time_s": 10.0,
                    "mean_time_loss_s": traffic_loss,
                    "mean_depart_delay_s": 0.0,
                },
                "buses": {
                    "vehicles": 1,
                    "mean_duration_s": 100.0,
                    "mean_waiting_time_s": 8.0,
                    "mean_time_loss_s": bus_loss,
                    "mean_depart_delay_s": 0.0,
                },
                "general_traffic": {
                    "vehicles": 2,
                    "mean_duration_s": 130.0,
                    "mean_waiting_time_s": 11.0,
                    "mean_time_loss_s": traffic_loss,
                    "mean_depart_delay_s": 0.0,
                },
            }
        ),
        encoding="utf-8",
    )


def _write_tsp_artifacts(root: Path, *, blocked: bool, rejected: bool) -> None:
    (root / "reports").mkdir(parents=True, exist_ok=True)
    (root / "outputs").mkdir(parents=True, exist_ok=True)
    decisions = [
        {
            "decision_id": "d1",
            "tls_id": "I1",
            "action": "green_extension",
            "status": "approved",
            "reason": "priority_request",
        }
    ]
    if blocked:
        decisions.append(
            {
                "decision_id": "d2",
                "tls_id": "I1",
                "action": "green_extension",
                "status": "blocked_by_safety",
                "reason": "missing_conflict_matrix",
            }
        )
    actuations = [
        {
            "decision_id": "d1",
            "tls_id": "I1",
            "action": "green_extension",
            "applied": True,
            "no_actuation": False,
            "command": "set_phase_duration",
            "reason": "applied",
            "controller_response": {"accepted": True, "reason": "accepted"},
        }
    ]
    if rejected:
        actuations.append(
            {
                "decision_id": "d2",
                "tls_id": "I1",
                "action": "green_extension",
                "applied": False,
                "no_actuation": False,
                "command": "none",
                "reason": "controller_locked_manual_mode",
                "controller_response": {
                    "accepted": False,
                    "reason": "controller_locked_manual_mode",
                },
            }
        )
    (root / "outputs/tsp_decisions.jsonl").write_text(
        "\n".join(json.dumps(item) for item in decisions) + "\n",
        encoding="utf-8",
    )
    (root / "outputs/tsp_actuation.jsonl").write_text(
        "\n".join(json.dumps(item) for item in actuations) + "\n",
        encoding="utf-8",
    )
    (root / "reports/tsp_emulation_summary.json").write_text(
        json.dumps(
            {
                "total_decisions": len(decisions),
                "applied_events": 1,
                "blocked_by_safety": 1 if blocked else 0,
                "actuation_enabled": True,
                "by_action": {"green_extension": len(decisions)},
                "by_status": {
                    "approved": 1,
                    "blocked_by_safety": 1 if blocked else 0,
                },
            }
        ),
        encoding="utf-8",
    )


class DemonstratorVerdictTestCase(unittest.TestCase):
    """B20: the primary-goal verdict must not pass on a missing general-traffic delta."""

    @staticmethod
    def _comparisons(rows: list[dict]) -> dict:
        return {
            "tsp_controller_vs_tsp_runtime": {
                "rows": [{"metric": "applied_events", "tsp_controller": 5}]
            },
            "tsp_controller_vs_sumo_baseline_kpis": {"available": True, "rows": rows},
        }

    def test_missing_general_traffic_delta_is_inconclusive(self) -> None:
        # bus improved (-10) but no general_traffic row → cannot claim the no-cost goal.
        verdict = _verdict(
            self._comparisons([{"group": "buses", "metric": "mean_time_loss_s", "delta": -10.0}])
        )
        self.assertEqual(verdict["status"], "inconclusive_missing_general_traffic_kpi")

    def test_bus_improved_and_general_traffic_not_worse_passes(self) -> None:
        verdict = _verdict(
            self._comparisons(
                [
                    {"group": "buses", "metric": "mean_time_loss_s", "delta": -10.0},
                    {"group": "general_traffic", "metric": "mean_time_loss_s", "delta": -2.0},
                ]
            )
        )
        self.assertEqual(verdict["status"], "passes_primary_demonstrator_goal")


if __name__ == "__main__":
    unittest.main()
