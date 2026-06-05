#!/usr/bin/env python3
"""Testa as estatísticas de réplicas multi-seed do runner de cenários:
intervalo de confiança 95% (t de Student) e teste de significância emparelhado
por seed sobre o KPI de timeLoss dos autocarros."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

_SPEC = importlib.util.spec_from_file_location("run_sumo_scenario", ROOT / "scripts" / "run_sumo_scenario.py")
rss = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(rss)


class MeanCi95TestCase(unittest.TestCase):
    def test_empty_returns_nones(self) -> None:
        out = rss._mean_ci95([])
        self.assertEqual(out["n"], 0)
        self.assertIsNone(out["mean"])
        self.assertIsNone(out["ci95_low"])

    def test_single_value_has_zero_width_ci(self) -> None:
        out = rss._mean_ci95([10.0])
        self.assertEqual(out["mean"], 10.0)
        self.assertEqual(out["ci95_half_width"], 0.0)
        self.assertEqual(out["ci95_low"], 10.0)
        self.assertEqual(out["ci95_high"], 10.0)

    def test_five_values_student_t_interval(self) -> None:
        out = rss._mean_ci95([10, 12, 11, 9, 13])
        self.assertEqual(out["n"], 5)
        self.assertEqual(out["mean"], 11.0)
        # stdev amostral = 1.5811; sem = 0.7071; t(4)=2.776 -> half ~ 1.963
        self.assertAlmostEqual(out["stdev_sample"], 1.581, places=2)
        self.assertAlmostEqual(out["ci95_half_width"], 1.963, places=2)
        self.assertAlmostEqual(out["ci95_low"], 9.037, places=2)
        self.assertAlmostEqual(out["ci95_high"], 12.963, places=2)

    def test_t_critical_falls_back_to_normal_for_large_df(self) -> None:
        self.assertEqual(rss._t_critical_95(200), 1.96)
        self.assertEqual(rss._t_critical_95(4), 2.776)


class PairedSignificanceTestCase(unittest.TestCase):
    def _run_with_seeds(self, tmp: Path, label: str, seed_to_value: dict[int, float]) -> dict:
        reps = []
        for seed, value in seed_to_value.items():
            path = tmp / f"{label}_{seed}.json"
            path.write_text(json.dumps({"buses": {"mean_time_loss_s": value}}), encoding="utf-8")
            reps.append({"seed": seed, "kpis": str(path)})
        return {"replication_summaries": reps}

    def test_significant_improvement_when_ci_excludes_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            base = self._run_with_seeds(tmp_path, "b", {57: 40, 58: 42, 59: 41, 60: 39, 61: 43})
            cand = self._run_with_seeds(tmp_path, "c", {57: 30, 58: 31, 59: 29, 60: 32, 61: 28})
            sig = rss._paired_significance(base, cand, "buses", "mean_time_loss_s", lower_is_better=True)
        self.assertIsNotNone(sig)
        self.assertEqual(sig["verdict"], "significant_improvement")
        self.assertEqual(sig["n"], 5)
        self.assertGreater(sig["ci95_low"], 0)

    def test_inconclusive_when_ci_includes_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            # melhorias mistas em torno de zero -> IC95 inclui zero
            base = self._run_with_seeds(tmp_path, "b", {57: 40, 58: 40, 59: 40, 60: 40, 61: 40})
            cand = self._run_with_seeds(tmp_path, "c", {57: 41, 58: 39, 59: 42, 60: 38, 61: 40})
            sig = rss._paired_significance(base, cand, "buses", "mean_time_loss_s", lower_is_better=True)
        self.assertIsNotNone(sig)
        self.assertEqual(sig["verdict"], "inconclusive_ci_includes_zero")

    def test_none_without_two_paired_seeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            base = self._run_with_seeds(tmp_path, "b", {57: 40, 58: 42})
            cand = self._run_with_seeds(tmp_path, "c", {99: 30})  # nenhum seed comum
            self.assertIsNone(
                rss._paired_significance(base, cand, "buses", "mean_time_loss_s", lower_is_better=True)
            )

    def test_single_seed_runs_have_no_significance_block(self) -> None:
        # Runs de seed único não têm replication_summaries -> sem bloco.
        runs = {
            "baseline": {"kpis": None},
            "tsp_actuation": {"kpis": None},
        }
        self.assertEqual(rss.compare_scenario_runs(runs), {})


if __name__ == "__main__":
    unittest.main()
