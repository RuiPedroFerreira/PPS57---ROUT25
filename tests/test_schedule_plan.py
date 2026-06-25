#!/usr/bin/env python3
"""Tests for the deterministic schedule-adherence stand-in (P1)."""

from __future__ import annotations

import copy
import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config
from pps57_cits.models import VehicleObservation
from pps57_cits.obu import OBUEmulator
from pps57_cits.schedule_plan import SchedulePlanProvider
from pps57_cits.traci_adapter import TraciSimulationAdapter

SERVICE_W = {
    "line_id": "STCP500",
    "line_code": "STCP500_PROXY_W",
    "direction": "W",
    "begin_s": 0,
    "end_s": 7200,
    "headway_s": 600,
    "offset_s": 0,
    "terminus_jitter_s": 30,
    "headway_schedule": [
        {"begin_s": 0, "end_s": 1800, "headway_s": 720},
        {"begin_s": 1800, "end_s": 4800, "headway_s": 480},
        {"begin_s": 4800, "end_s": 7200, "headway_s": 660},
    ],
}


def make_obs(**overrides) -> VehicleObservation:
    base = {
        "vehicle_id": "bus_1",
        "vehicle_class": "bus",
        "type_id": "bus_12m",
        "line_id": "STCP500_PROXY_W",
        "route_id": "route_boavista_east_to_west",
        "edge_id": "CITY_EAST_I1",
        "lane_id": "CITY_EAST_I1_0",
        "lane_position_m": 10.0,
        "lane_length_m": 100.0,
        "speed_mps": 5.0,
    }
    base.update(overrides)
    return VehicleObservation(**base)


def provider() -> SchedulePlanProvider:
    return SchedulePlanProvider(
        services_by_line_code={"STCP500_PROXY_W": SERVICE_W},
        seed=57,
        schedule_delay_scale_s=90.0,
        headway_deviation_fraction=0.25,
    )


class SchedulePlanProviderTestCase(unittest.TestCase):
    def test_is_deterministic_for_same_inputs(self) -> None:
        prov = provider()
        obs = make_obs()
        first = prov.schedule_adherence_for(obs, 100.0)
        second = prov.schedule_adherence_for(obs, 100.0)
        self.assertIsNotNone(first)
        self.assertEqual(first, second)

    def test_delay_non_negative_and_headway_bounded_by_scheduled_headway(self) -> None:
        prov = provider()
        delay, headway = prov.schedule_adherence_for(make_obs(), 100.0)
        self.assertGreaterEqual(delay, 0.0)
        self.assertLessEqual(delay, 90.0)
        # At t=100 the active scheduled headway is 720s; deviation is bounded by
        # fraction * scheduled_headway.
        self.assertLessEqual(abs(headway), 0.25 * 720.0)

    def test_unknown_line_returns_none(self) -> None:
        self.assertIsNone(provider().schedule_adherence_for(make_obs(line_id="NOT_A_LINE"), 100.0))

    def test_scheduled_headway_window_selection(self) -> None:
        prov = provider()
        self.assertEqual(prov._scheduled_headway_s(SERVICE_W, 100.0), 720.0)
        self.assertEqual(prov._scheduled_headway_s(SERVICE_W, 2000.0), 480.0)
        self.assertEqual(prov._scheduled_headway_s(SERVICE_W, 5000.0), 660.0)
        # Outside all windows -> fall back to the base service headway.
        self.assertEqual(prov._scheduled_headway_s(SERVICE_W, 99999.0), 600.0)

    def test_varies_by_approach_edge(self) -> None:
        prov = provider()
        a = prov.schedule_adherence_for(make_obs(edge_id="CITY_EAST_I1"), 100.0)
        b = prov.schedule_adherence_for(make_obs(edge_id="CITY_EAST_I2"), 100.0)
        self.assertNotEqual(a, b)

    def test_from_config_disabled_by_default(self) -> None:
        config = load_cits_config(ROOT / "configs/cits_v2x_config.json", root=ROOT)
        self.assertIsNone(SchedulePlanProvider.from_config(config))

    def test_from_config_enabled_loads_services(self) -> None:
        config = load_cits_config(ROOT / "configs/cits_v2x_config.json", root=ROOT)
        raw = copy.deepcopy(config.raw)
        raw["schedule_plan"]["enabled"] = True
        prov = SchedulePlanProvider.from_config(replace(config, raw=raw))
        self.assertIsNotNone(prov)
        self.assertIn("STCP500_PROXY_W", prov.services_by_line_code)
        self.assertEqual(prov.seed, 57)

    def _enabled_config_with(self, **schedule_plan_overrides):
        config = load_cits_config(ROOT / "configs/cits_v2x_config.json", root=ROOT)
        raw = copy.deepcopy(config.raw)
        raw["schedule_plan"]["enabled"] = True
        raw["schedule_plan"].update(schedule_plan_overrides)
        return replace(config, raw=raw)

    def test_from_config_tolerates_malformed_scalars(self) -> None:
        # A config typo on the opt-in path must not crash adapter construction:
        # malformed scalars fall back to their defaults instead of raising.
        prov = SchedulePlanProvider.from_config(
            self._enabled_config_with(
                random_seed="abc", schedule_delay_scale_s="oops", headway_deviation_fraction="bad"
            )
        )
        self.assertIsNotNone(prov)
        self.assertEqual(prov.seed, 57)
        self.assertEqual(prov.schedule_delay_scale_s, 30.0)  # delay_threshold_s(20)*1.5
        self.assertEqual(prov.headway_deviation_fraction, 0.25)

    def test_from_config_clamps_negative_scale_and_fraction(self) -> None:
        prov = SchedulePlanProvider.from_config(
            self._enabled_config_with(schedule_delay_scale_s=-50.0, headway_deviation_fraction=-0.5)
        )
        self.assertIsNotNone(prov)
        self.assertEqual(prov.schedule_delay_scale_s, 0.0)
        self.assertEqual(prov.headway_deviation_fraction, 0.0)

    def test_from_config_missing_timetable_source_returns_none(self) -> None:
        prov = SchedulePlanProvider.from_config(
            self._enabled_config_with(timetable_source="configs/does_not_exist.json")
        )
        self.assertIsNone(prov)


class ScheduleAdherenceIntegrationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_cits_config(ROOT / "configs/cits_v2x_config.json", root=ROOT)
        raw = copy.deepcopy(self.config.raw)
        raw["schedule_plan"]["enabled"] = True
        self.enabled_config = replace(self.config, raw=raw)

    def test_obu_prefers_sourced_delay_over_waiting_proxy(self) -> None:
        obu = OBUEmulator(self.config)
        sourced = make_obs(
            schedule_delay_s=80.0, waiting_time_s=200.0, schedule_adherence_sourced=True
        )
        self.assertEqual(obu._effective_schedule_delay(sourced), 80.0)

    def test_obu_falls_back_to_proxy_when_not_sourced(self) -> None:
        obu = OBUEmulator(self.config)
        proxy = make_obs(
            schedule_delay_s=0.0, waiting_time_s=200.0, schedule_adherence_sourced=False
        )
        self.assertEqual(obu._effective_schedule_delay(proxy), 200.0)

    def test_adapter_leaves_observation_unchanged_when_disabled(self) -> None:
        adapter = TraciSimulationAdapter(self.config)  # schedule_plan disabled
        obs = make_obs()
        self.assertIs(adapter._with_schedule_adherence(obs, 100.0), obs)

    def test_adapter_injects_sourced_fields_when_enabled(self) -> None:
        adapter = TraciSimulationAdapter(self.enabled_config)
        obs = make_obs()
        out = adapter._with_schedule_adherence(obs, 100.0)
        self.assertIsNot(out, obs)
        self.assertTrue(out.schedule_adherence_sourced)
        self.assertGreaterEqual(out.schedule_delay_s, 0.0)

    def test_adapter_leaves_unknown_line_unchanged_when_enabled(self) -> None:
        adapter = TraciSimulationAdapter(self.enabled_config)
        obs = make_obs(line_id="NOT_A_LINE")
        self.assertIs(adapter._with_schedule_adherence(obs, 100.0), obs)


class GtfsScheduleAdherenceTestCase(unittest.TestCase):
    """Aderência a horário a partir do GTFS REAL (tempos `until` por paragem)."""

    def _provider(self):
        from pps57_cits.schedule_plan import GtfsScheduleAdherenceProvider

        return GtfsScheduleAdherenceProvider(
            stops_by_vehicle={"Bus_:1": [("E1", 100.0), ("E3", 200.0), ("E5", 300.0)]}
        )

    def test_delay_vs_next_scheduled_stop_ahead(self) -> None:
        obs = make_obs(
            vehicle_id="Bus_:1",
            edge_id="E2",
            route_edges=["E1", "E2", "E3", "E4", "E5"],
            route_index=1,
        )
        # próxima paragem à frente (pos>=1) = E3 (agendada 200s); às 250s -> 50s.
        self.assertEqual(self._provider().schedule_adherence_for(obs, 250.0), (50.0, 0.0))

    def test_on_time_is_zero_delay(self) -> None:
        obs = make_obs(
            vehicle_id="Bus_:1",
            edge_id="E2",
            route_edges=["E1", "E2", "E3", "E4", "E5"],
            route_index=1,
        )
        self.assertEqual(self._provider().schedule_adherence_for(obs, 150.0), (0.0, 0.0))

    def test_returns_none_without_route_index(self) -> None:
        # Sem route_index autoritativo, NÃO fabrica atraso (recai no proxy).
        obs = make_obs(
            vehicle_id="Bus_:1", edge_id="E2", route_edges=["E1", "E2", "E3"], route_index=None
        )
        self.assertIsNone(self._provider().schedule_adherence_for(obs, 250.0))

    def test_unknown_vehicle_returns_none(self) -> None:
        obs = make_obs(
            vehicle_id="desconhecido", edge_id="E2", route_edges=["E1", "E2"], route_index=1
        )
        self.assertIsNone(self._provider().schedule_adherence_for(obs, 250.0))

    def test_from_config_builds_gtfs_provider_from_files(self) -> None:
        import json
        import tempfile

        from pps57_cits.schedule_plan import (
            GtfsScheduleAdherenceProvider,
            SchedulePlanProvider,
        )

        work = Path(tempfile.mkdtemp())
        (work / "stops.add.xml").write_text(
            '<additional><busStop id="S1" lane="E1_0"/><busStop id="S2" lane="E3_0"/></additional>',
            encoding="utf-8",
        )
        (work / "trips.rou.xml").write_text(
            '<routes><trip id="Bus_:1" line="10" from="E1" to="E5">'
            '<stop busStop="S1" until="00:01:40"/><stop busStop="S2" until="00:03:20"/>'
            "</trip></routes>",
            encoding="utf-8",
        )
        cfg = {
            "schedule_plan": {
                "enabled": True,
                "mode": "gtfs",
                "gtfs_trips": str(work / "trips.rou.xml"),
                "pt_stops": str(work / "stops.add.xml"),
            },
            "intersections": [],
        }
        cfg_path = work / "cits.json"
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        provider = SchedulePlanProvider.from_config(load_cits_config(cfg_path, root=work))
        self.assertIsInstance(provider, GtfsScheduleAdherenceProvider)
        self.assertEqual(provider.stops_by_vehicle["Bus_:1"], [("E1", 100.0), ("E3", 200.0)])


if __name__ == "__main__":
    unittest.main()
