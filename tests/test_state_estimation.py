#!/usr/bin/env python3
"""Tests for P2 state enrichment: configurable ETA, real back-of-queue, spillback."""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config
from pps57_cits.models import EtaParams, VehicleObservation
from pps57_cits.traci_adapter import TraciSimulationAdapter


def make_obs(**overrides) -> VehicleObservation:
    base = dict(
        vehicle_id="bus_1",
        vehicle_class="bus",
        type_id="bus_12m",
        line_id="STCP500_PROXY_W",
        route_id="r",
        edge_id="I1_I2",
        lane_id="I1_I2_0",
        lane_position_m=550.0,
        lane_length_m=650.0,
        speed_mps=10.0,
    )
    base.update(overrides)
    return VehicleObservation(**base)


class EtaParamsTestCase(unittest.TestCase):
    def test_default_eta_reproduces_prior_literals(self) -> None:
        # Moving vehicle: min(distance/speed, distance/free_flow + queue_penalty).
        obs = make_obs(speed_mps=10.0, queue_ahead_vehicle_count=0)
        distance = obs.distance_to_stopline_m  # 100.0
        expected = min(distance / 10.0, distance / 8.0)
        self.assertAlmostEqual(obs.eta_to_stopline_s, expected)

    def test_default_eta_halted_branch_uses_waiting_cap(self) -> None:
        obs = make_obs(speed_mps=0.0, waiting_time_s=999.0, queue_ahead_vehicle_count=2)
        distance = obs.distance_to_stopline_m  # 100.0
        expected = distance / 8.0 + 2 * 2.0 + min(999.0, 15.0)
        self.assertAlmostEqual(obs.eta_to_stopline_s, expected)

    def test_custom_eta_params_change_result(self) -> None:
        slow = make_obs(
            speed_mps=0.0, waiting_time_s=999.0, eta_params=EtaParams(waiting_cap_s=30.0)
        )
        distance = slow.distance_to_stopline_m
        expected = distance / 8.0 + 0.0 + min(999.0, 30.0)
        self.assertAlmostEqual(slow.eta_to_stopline_s, expected)


class ContiguousQueueTestCase(unittest.TestCase):
    fn = staticmethod(TraciSimulationAdapter._contiguous_halted_from_stopline)

    def test_counts_contiguous_halted_run_from_stopline(self) -> None:
        # positions ascending toward the stop line (lane end = highest position).
        # Two halted nearest the stop line, then a moving vehicle breaks the run.
        samples = [(640.0, 0.0), (630.0, 0.0), (620.0, 5.0), (610.0, 0.0)]
        self.assertEqual(self.fn(samples, 0.1), 2)

    def test_front_vehicle_moving_yields_zero(self) -> None:
        samples = [(640.0, 3.0), (630.0, 0.0), (620.0, 0.0)]
        self.assertEqual(self.fn(samples, 0.1), 0)

    def test_all_halted_counts_all(self) -> None:
        samples = [(640.0, 0.0), (630.0, 0.0), (620.0, 0.05)]
        self.assertEqual(self.fn(samples, 0.1), 3)

    def test_distinct_from_total_halted_when_gap(self) -> None:
        # 3 halted total, but only 1 contiguous from the stop line (gap by mover).
        samples = [(640.0, 0.0), (630.0, 4.0), (620.0, 0.0), (610.0, 0.0)]
        total_halted = sum(1 for _p, s in samples if s < 0.1)
        self.assertEqual(total_halted, 3)
        self.assertEqual(self.fn(samples, 0.1), 1)

    def test_empty(self) -> None:
        self.assertEqual(self.fn([], 0.1), 0)


class AdapterConfigWiringTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_cits_config(ROOT / "configs/cits_v2x_config.json", root=ROOT)

    def test_defaults_match_prior_literals(self) -> None:
        adapter = TraciSimulationAdapter(self.config)
        self.assertEqual(adapter._eta_params, EtaParams())  # 8.0/2.0/15.0/0.5
        self.assertEqual(adapter._spillback_occupancy, 0.75)
        self.assertEqual(adapter._spillback_halted_per_lane, 4.0)
        self.assertFalse(adapter._real_back_of_queue)  # opt-in

    def test_eta_params_absent_block_is_byte_identical(self) -> None:
        raw = copy.deepcopy(self.config.raw)
        raw.pop("state_estimation", None)
        adapter = TraciSimulationAdapter(replace(self.config, raw=raw))
        self.assertEqual(adapter._eta_params, EtaParams())
        self.assertEqual(adapter._spillback_occupancy, 0.75)
        self.assertFalse(adapter._real_back_of_queue)

    def test_config_overrides_are_read(self) -> None:
        raw = copy.deepcopy(self.config.raw)
        raw["state_estimation"] = {
            "eta_free_flow_speed_mps": 12.0,
            "spillback_occupancy_threshold": 0.5,
            "real_back_of_queue": True,
            "queue_halt_speed_mps": 0.2,
        }
        adapter = TraciSimulationAdapter(replace(self.config, raw=raw))
        self.assertEqual(adapter._eta_params.free_flow_speed_mps, 12.0)
        # Unspecified ETA keys still fall back to their literal defaults.
        self.assertEqual(adapter._eta_params.queue_penalty_s, 2.0)
        self.assertEqual(adapter._spillback_occupancy, 0.5)
        self.assertTrue(adapter._real_back_of_queue)
        self.assertEqual(adapter._queue_halt_speed_mps, 0.2)

    def test_malformed_scalar_falls_back_to_default(self) -> None:
        raw = copy.deepcopy(self.config.raw)
        raw["state_estimation"] = {
            "eta_free_flow_speed_mps": "oops",
            "spillback_occupancy_threshold": "bad",
        }
        adapter = TraciSimulationAdapter(replace(self.config, raw=raw))
        self.assertEqual(adapter._eta_params.free_flow_speed_mps, 8.0)
        self.assertEqual(adapter._spillback_occupancy, 0.75)


if __name__ == "__main__":
    unittest.main()
