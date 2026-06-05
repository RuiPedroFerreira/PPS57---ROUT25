#!/usr/bin/env python3
"""P3 explainability: ReasonCode registry, score_components, counterfactual join."""
from __future__ import annotations

import ast
from pathlib import Path
import re
from types import SimpleNamespace
import sys
import unittest

# Forma de um código de motivo: snake_case, opcional sufixo ":suffix".
_REASON_SHAPE = re.compile(r"^[a-z][a-z0-9_]*(?::[a-z0-9_]+)?$")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config
from pps57_opt.demonstrator import DemonstratorRun, build_demonstrator_report, render_demonstrator_markdown
from pps57_tsp.config import load_tsp_config
from pps57_tsp.engine import TSPDecisionEngine, _component
from pps57_tsp.models import ReasonCode


def _const_or_prefix(node: ast.AST) -> set:
    """Reason string from a Constant, or the static prefix of an f-string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.JoinedStr) and node.values:
        first = node.values[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return {first.value.split(":")[0]}
    return set()


def _reason_literals(path: Path, scan_returns: bool = False) -> set:
    """Extract reason string literals emitted in a module via AST.

    Captures: `reason=` keyword args and the 2nd positional arg of `_blocked(...)`.
    With `scan_returns`, also captures any `return "<reason-shaped>"` literal
    (some safety reasons are returned by helpers and passed to `_blocked` as a
    variable). The shape filter is reason-aware rather than helper-name-aware, so
    a future return-style reason in a newly-named helper still cannot escape the
    drift guard.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "reason":
                    found |= _const_or_prefix(kw.value)
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "_blocked" and len(node.args) >= 2:
                found |= _const_or_prefix(node.args[1])
        if scan_returns and isinstance(node, ast.Return) and node.value is not None:
            for literal in _const_or_prefix(node.value):
                if "_" in literal and _REASON_SHAPE.match(literal):
                    found.add(literal)
    return found


class ReasonCodeRegistryTestCase(unittest.TestCase):
    def test_values_are_unique(self) -> None:
        values = [c.value for c in ReasonCode]
        self.assertEqual(len(values), len(set(values)))

    def test_every_safety_reason_literal_is_registered(self) -> None:
        codes = {c.value for c in ReasonCode}
        literals = _reason_literals(SRC / "pps57_tsp/safety.py", scan_returns=True)
        # sanity: the scanner actually found the safety reasons
        self.assertGreater(len(literals), 20)
        unregistered = {lit for lit in literals if lit not in codes and lit.split(":")[0] not in codes}
        self.assertEqual(unregistered, set(), f"unregistered safety reason literals: {unregistered}")

    def test_engine_and_controller_emit_via_reasoncode_not_literals(self) -> None:
        # After P3 conversion these files should emit reasons via ReasonCode,
        # so the AST scan finds no bare reason string literals.
        self.assertEqual(_reason_literals(SRC / "pps57_tsp/engine.py"), set())
        self.assertEqual(_reason_literals(SRC / "pps57_tsp/controller.py"), set())


class ScoreComponentsTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.cits = load_cits_config(ROOT / "configs/cits_v2x_config.json", root=ROOT)
        self.tsp = load_tsp_config(ROOT / "configs/tsp_safety_config.json", root=ROOT)
        self.engine = TSPDecisionEngine(self.cits, self.tsp)

    def test_component_helper_shape(self) -> None:
        comp = _component(30.0, 0.5, 0.45)
        self.assertEqual(comp["normalised"], 0.5)
        self.assertEqual(comp["weight"], 0.45)
        self.assertEqual(comp["contribution"], round(0.45 * 0.5, 4))

    def test_breakdown_contributions_sum_to_score(self) -> None:
        # Values chosen so the weighted sum stays < 1 (no clip).
        request = SimpleNamespace(
            schedule_delay_s=30.0,
            headway_deviation_s=60.0,
            distance_to_stopline_m=100.0,
            priority_level="high_delay",
        )
        score, components = self.engine._score_breakdown(request)
        self.assertEqual(
            set(components), {"schedule_delay", "headway_deviation", "proximity", "priority_level"}
        )
        total = sum(c["contribution"] for c in components.values())
        self.assertAlmostEqual(total, score, places=3)
        for comp in components.values():
            self.assertIn("raw", comp)
            self.assertIn("normalised", comp)
            self.assertIn("weight", comp)


class CounterfactualJoinTestCase(unittest.TestCase):
    def _run(self, label: str, summary: dict) -> DemonstratorRun:
        return DemonstratorRun(
            label=label, root=ROOT, summary=summary, cits_summary={},
            decisions=[], actuations=[], kpis=None,
        )

    def test_join_present_when_summary_provided(self) -> None:
        tsp = self._run("tsp", {"by_action": {"green_extension": 4, "no_action": 2}})
        policy_summary = {
            "methodology": "deterministic_argmax_over_event_derived_sumo_traci_scenarios",
            "scenario_count": 12,
            "baseline_by_action": {"no_action": 8, "green_extension": 4},
            "selected_by_action": {"green_extension": 9, "no_action": 3},
            "optimized_action_changes_vs_baseline": 5,
            "optimized_action_unchanged_vs_baseline": 7,
        }
        report = build_demonstrator_report(
            baseline=self._run("sumo_baseline", {}),
            tsp=tsp,
            tsp_controller=self._run("tsp_controller", {}),
            policy_optimization_summary=policy_summary,
        )
        cf = report["offline_policy_counterfactuals"]
        self.assertTrue(cf["available"])
        self.assertEqual(cf["offline_action_changes_vs_baseline"], 5)
        self.assertEqual(cf["runtime_by_action"], {"green_extension": 4, "no_action": 2})
        self.assertEqual(cf["offline_selected_by_action"], {"green_extension": 9, "no_action": 3})
        self.assertIn("Offline Policy Counterfactuals", render_demonstrator_markdown(report))

    def test_unavailable_without_summary(self) -> None:
        report = build_demonstrator_report(
            baseline=self._run("sumo_baseline", {}),
            tsp=self._run("tsp", {}),
            tsp_controller=self._run("tsp_controller", {}),
        )
        self.assertFalse(report["offline_policy_counterfactuals"]["available"])


if __name__ == "__main__":
    unittest.main()
