#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import run_ingolstadt_demo as rid  # noqa: E402


class IngolstadtDemoRunnerTestCase(unittest.TestCase):
    def test_citywide_tsp_config_disables_corridor_debt_and_specific_mappings(self) -> None:
        raw = json.loads((ROOT / "configs/tsp_safety_config.json").read_text(encoding="utf-8"))
        resolved = rid._citywide_tsp_raw(raw)
        self.assertIsNone(resolved["corridor"]["max_corridor_recovery_debt_s"])
        self.assertTrue(resolved["corridor"]["respect_downstream_spillback"])
        self.assertEqual(resolved["controller_contracts"]["controllers"], {})
        self.assertEqual(resolved["phase_mapping"]["priority_movements"], {})
        self.assertTrue(resolved["network_profile"]["enabled"])


if __name__ == "__main__":
    unittest.main()
