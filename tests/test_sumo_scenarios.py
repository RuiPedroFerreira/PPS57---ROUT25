#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.generate_plain_corridor import build_route_xml  # noqa: E402
from pps57_sumo.detector_kpis import parse_detector_kpis  # noqa: E402
from pps57_sumo.parse_tripinfo import parse_tripinfo  # noqa: E402
from pps57_sumo.scenarios import (  # noqa: E402
    ScenarioConfigError,
    apply_scenario_profile,
    load_catalog,
    scenario_summary,
    validate_scenario_catalog,
)

SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from run_sumo_scenario import compare_kpis  # noqa: E402


class SumoScenarioProfilesTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = json.loads((ROOT / "configs/sumo_scenario_base.json").read_text(encoding="utf-8"))
        cls.catalog = load_catalog(ROOT / "configs/scenario_catalog.yaml")

    def test_catalog_scenarios_have_matching_valid_profiles(self) -> None:
        summaries = validate_scenario_catalog(self.base, self.catalog)
        scenario_ids = {item["scenario_id"] for item in summaries}
        self.assertEqual(scenario_ids, set(self.catalog["scenarios"]))
        for item in summaries:
            self.assertGreater(item["estimated_car_departures"], 0)
            self.assertGreater(item["estimated_bus_departures"], 0)
            self.assertTrue(item["kpi_focus"])

    def test_off_peak_reduces_car_and_bus_departures(self) -> None:
        peak = scenario_summary(apply_scenario_profile(self.base, "baseline_am_peak"))
        off_peak = scenario_summary(apply_scenario_profile(self.base, "baseline_off_peak"))
        self.assertLess(off_peak["estimated_car_departures"], peak["estimated_car_departures"])
        self.assertLess(off_peak["estimated_bus_departures"], peak["estimated_bus_departures"])

    def test_cross_pressure_makes_selected_minor_flows_more_frequent(self) -> None:
        config = apply_scenario_profile(self.base, "cross_traffic_pressure")
        flows = {
            flow["id"]: flow
            for flow in config["demand_profiles"][config["active_demand_profile"]]["flows"]
        }
        i2_ns = next(flow for flow in flows.values() if flow["route"] == "route_cross_NS_I2")
        i6_sn = next(flow for flow in flows.values() if flow["route"] == "route_cross_SN_I6")
        self.assertEqual(float(i2_ns["period"]), 12.0)
        self.assertEqual(float(i6_sn["period"]), 15.0)

    def test_emergency_profile_is_the_only_catalog_profile_with_emergency_event(self) -> None:
        for scenario_id in self.catalog["scenarios"]:
            config = apply_scenario_profile(self.base, scenario_id)
            event_count = len(config.get("events", []))
            if scenario_id == "emergency_vehicle_conflict":
                self.assertEqual(event_count, 1)
                self.assertEqual(config["events"][0]["type"], "emergency_vehicle")
            else:
                self.assertEqual(event_count, 0, msg=scenario_id)

    def test_profile_routes_are_sorted_by_departure_time_after_generation(self) -> None:
        config = apply_scenario_profile(self.base, "bunched_buses")
        route_defs = {
            "route_boavista_east_to_west": ["CITY_EAST_I1", "I1_I2"],
            "route_boavista_west_to_east": ["ATLANTIC_WEST_I7", "I7_I6"],
            "route_emergency_west_to_east": ["ATLANTIC_WEST_I7", "I7_I6"],
            "route_cross_NS_I1": ["N_I1_I1", "I1_S_I1"],
            "route_cross_SN_I1": ["S_I1_I1", "I1_N_I1"],
            "route_cross_NS_I2": ["N_I2_I2", "I2_S_I2"],
            "route_cross_SN_I2": ["S_I2_I2", "I2_N_I2"],
            "route_cross_NS_I3": ["N_I3_I3", "I3_S_I3"],
            "route_cross_SN_I3": ["S_I3_I3", "I3_N_I3"],
            "route_cross_NS_I4": ["N_I4_I4", "I4_S_I4"],
            "route_cross_SN_I4": ["S_I4_I4", "I4_N_I4"],
            "route_cross_NS_I5": ["N_I5_I5", "I5_S_I5"],
            "route_cross_SN_I5": ["S_I5_I5", "I5_N_I5"],
            "route_cross_NS_I6": ["N_I6_I6", "I6_S_I6"],
            "route_cross_SN_I6": ["S_I6_I6", "I6_N_I6"],
            "route_cross_NS_I7": ["N_I7_I7", "I7_S_I7"],
            "route_cross_SN_I7": ["S_I7_I7", "I7_N_I7"],
        }
        root = build_route_xml(config, route_defs)
        times = []
        for child in root:
            if child.tag == "flow":
                times.append(float(child.attrib["begin"]))
            elif child.tag == "vehicle":
                times.append(float(child.attrib["depart"]))
        self.assertEqual(times, sorted(times))

    def test_invalid_service_override_is_rejected(self) -> None:
        broken = json.loads(json.dumps(self.base))
        broken["scenario_profiles"]["broken"] = {
            "demand_profile": "am_peak",
            "service_overrides": [{"line_id": "UNKNOWN", "direction": "W", "headway_s": 600}],
        }
        with self.assertRaises(ScenarioConfigError):
            apply_scenario_profile(broken, "broken")


class SumoKpiParsingTestCase(unittest.TestCase):
    def test_tripinfo_parser_reports_speed_and_bus_headways(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tripinfo.xml"
            path.write_text(
                "<tripinfos>"
                '<tripinfo id="bus_STCP500_W_0000" vType="bus_12m" depart="0" arrival="100" duration="100" routeLength="1000" waitingTime="10" timeLoss="20" waitingCount="2" />'
                '<tripinfo id="bus_STCP500_W_0600" vType="bus_12m" depart="600" arrival="700" duration="100" routeLength="1000" waitingTime="12" timeLoss="22" waitingCount="3" />'
                '<tripinfo id="car_1" vType="car" depart="0" arrival="50" duration="50" routeLength="500" waitingTime="5" timeLoss="8" waitingCount="1" />'
                "</tripinfos>",
                encoding="utf-8",
            )
            kpis = parse_tripinfo(path)
            self.assertEqual(kpis["all_vehicles"]["vehicles"], 3)
            self.assertEqual(kpis["buses"]["mean_speed_mps"], 10.0)
            self.assertEqual(kpis["bus_headways"]["STCP500:W"]["mean_headway_s"], 600)
            self.assertEqual(kpis["general_traffic"]["vehicles"], 1)

    def test_tripinfo_parser_does_not_classify_flow_name_as_emergency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tripinfo.xml"
            path.write_text(
                "<tripinfos>"
                '<tripinfo id="flow_car_inbound_emergency_conflict.0" vType="car" depart="0" duration="10" routeLength="100" waitingTime="1" timeLoss="2" />'
                '<tripinfo id="ev_conflict_west_to_east_3600" vType="emergency_vehicle" depart="3600" duration="10" routeLength="100" waitingTime="1" timeLoss="2" />'
                "</tripinfos>",
                encoding="utf-8",
            )
            kpis = parse_tripinfo(path)
            self.assertEqual(kpis["emergency_vehicles"]["vehicles"], 1)
            self.assertEqual(kpis["general_traffic"]["vehicles"], 1)

    def test_detector_parser_reports_network_queue_kpis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            e2 = Path(tmp) / "e2.xml"
            e2.write_text(
                "<detector>"
                '<interval id="e2_I2_I3_0" begin="0" end="60" meanOccupancy="12.5" meanSpeed="3" meanJamLengthInVehicles="2" maxJamLengthInVehicles="9" />'
                '<interval id="e2_I2_I3_0" begin="60" end="120" meanOccupancy="20" meanSpeed="2" meanJamLengthInVehicles="4" maxJamLengthInVehicles="12" />'
                "</detector>",
                encoding="utf-8",
            )
            kpis = parse_detector_kpis(e2_path=e2)
            self.assertEqual(kpis["network_queue"]["edge_count"], 1)
            self.assertEqual(kpis["network_queue"]["max_queue_vehicles"], 12)
            self.assertEqual(kpis["network_queue"]["intervals_above_8_veh"], 2)

    def test_kpi_comparison_fails_large_general_traffic_penalty(self) -> None:
        baseline = {
            "buses": {"mean_time_loss_s": 100},
            "general_traffic": {"mean_time_loss_s": 50},
        }
        candidate = {
            "buses": {"mean_time_loss_s": 90},
            "general_traffic": {"mean_time_loss_s": 200},
            "detectors": {"network_queue": {"max_queue_vehicles": 5}},
        }
        comparison = compare_kpis(baseline, candidate)
        self.assertEqual(comparison["verdict"], "fail")
        self.assertIn("general_traffic_time_loss_penalty_gt_90s", comparison["fail_reasons"])


if __name__ == "__main__":
    unittest.main()
