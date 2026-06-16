#!/usr/bin/env python3
"""P5: guard that TraCI/libsumo stay isolated behind the pps57_cits seam.

The SignalControlAdapter Protocol (pps57_tsp.signal_control) is the documented
field-readiness boundary: the decision/safety/actuation layers talk to it, and
the real TraCI/libsumo runtime is reached ONLY inside src/pps57_cits. This test
fails if any other library module imports or calls the traci/libsumo modules
directly — so a future NTCIP (or other) adapter stays a clean drop-in and the
SUMO dependency cannot quietly leak across the seam.

Scope: src/ (the library). Standalone scripts under scripts/ may use raw TraCI.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

_RUNTIME_MODULES = {"traci", "libsumo"}
_ALLOWED_PACKAGE = "pps57_cits"  # the only place the runtime may be touched


def _runtime_usages(path: Path) -> list:
    """Return (lineno, kind) for real traci/libsumo module usage in a file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _RUNTIME_MODULES:
                    hits.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] in _RUNTIME_MODULES:
                hits.append((node.lineno, f"from {node.module}"))
        elif isinstance(node, ast.Call):
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr == "import_module"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and node.args[0].value.split(".")[0] in _RUNTIME_MODULES
            ):
                hits.append((node.lineno, f"import_module({node.args[0].value!r})"))
        elif isinstance(node, ast.Attribute):
            # bare `traci.foo` / `libsumo.foo` module attribute access
            if isinstance(node.value, ast.Name) and node.value.id in _RUNTIME_MODULES:
                hits.append((node.lineno, f"{node.value.id}.{node.attr}"))
    return hits


class TraciIsolationGuardTestCase(unittest.TestCase):
    def test_no_traci_usage_outside_pps57_cits(self) -> None:
        violations = []
        for path in sorted(SRC.rglob("*.py")):
            rel = path.relative_to(SRC)
            if rel.parts[0] == _ALLOWED_PACKAGE:
                continue
            for lineno, kind in _runtime_usages(path):
                violations.append(f"{rel}:{lineno} -> {kind}")
        self.assertEqual(
            violations,
            [],
            "TraCI/libsumo must stay behind the pps57_cits seam; leaks found:\n"
            + "\n".join(violations),
        )

    def test_scanner_detects_the_real_seam_usage(self) -> None:
        # Sanity: the scanner is not a no-op — it DOES find the runtime import
        # inside the allowed seam (importlib.import_module("traci")).
        seam = SRC / "pps57_cits" / "traci_adapter.py"
        self.assertTrue(_runtime_usages(seam), "expected traci usage inside the pps57_cits seam")


if __name__ == "__main__":
    unittest.main()
