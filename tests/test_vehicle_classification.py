#!/usr/bin/env python3
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.vehicle_classification import is_bus_like  # noqa: E402


class VehicleClassificationTestCase(unittest.TestCase):
    def test_bus_like_supports_lowercase_and_capitalised_ids(self) -> None:
        self.assertTrue(is_bus_like("bus_STCP500_W_0000", "car"))
        self.assertTrue(is_bus_like("Bus_11_0001", "car"))
        self.assertTrue(is_bus_like("vehicle_1", "transit_bus"))
        self.assertTrue(is_bus_like("vehicle_2", "bus"))
        self.assertFalse(is_bus_like("flow_car_delayed_bus_0", "car"))


if __name__ == "__main__":
    unittest.main()
