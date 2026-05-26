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

from pps57_sumo.generate_plain_corridor import (  # noqa: E402
    build_calibrators,
    build_parking_areas,
    build_route_xml,
    build_tls_offsets,
    generate,
)
from pps57_sumo.detector_kpis import parse_detector_kpis  # noqa: E402
from pps57_sumo.parse_tripinfo import parse_tripinfo  # noqa: E402
from pps57_sumo.parse_emissions import parse_emissions  # noqa: E402
from pps57_sumo.apply_tls_offsets import apply_tls_offsets  # noqa: E402
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
            event_types = {event.get("type") for event in config.get("events", [])}
            if scenario_id == "emergency_vehicle_conflict":
                self.assertIn("emergency_vehicle", event_types)
            elif scenario_id == "stochastic_incidents_am_peak":
                self.assertTrue(event_types.issubset({"stopped_vehicle"}), msg=scenario_id)
            else:
                self.assertEqual(event_types, set(), msg=scenario_id)

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

    def test_rainy_scenario_applies_weather_vehicle_overrides(self) -> None:
        baseline = apply_scenario_profile(self.base, "baseline_am_peak")
        rainy = apply_scenario_profile(self.base, "baseline_rainy_am_peak")
        base_car = {vt["id"]: vt for vt in baseline["vehicle_types"]}["car"]
        rain_car = {vt["id"]: vt for vt in rainy["vehicle_types"]}["car"]
        self.assertGreater(float(rain_car["tau"]), float(base_car["tau"]))
        self.assertLess(float(rain_car["decel"]), float(base_car["decel"]))
        self.assertGreater(float(rain_car["minGap"]), float(base_car["minGap"]))
        self.assertNotEqual(base_car["speedFactor"], rain_car["speedFactor"])

    def test_congested_peak_increases_corridor_demand(self) -> None:
        congested = apply_scenario_profile(self.base, "congested_am_peak")
        flows = {
            flow["id"]: flow
            for flow in congested["demand_profiles"][congested["active_demand_profile"]]["flows"]
        }
        inbound = next(flow for flow in flows.values() if flow["route"] == "route_boavista_west_to_east")
        self.assertLess(float(inbound["period"]), 7.5)

    def test_pm_peak_inverts_corridor_dominance(self) -> None:
        pm = apply_scenario_profile(self.base, "baseline_pm_peak")
        flows = {
            flow["route"]: flow
            for flow in pm["demand_profiles"][pm["active_demand_profile"]]["flows"]
            if flow["route"].startswith("route_boavista_")
        }
        self.assertLess(
            float(flows["route_boavista_east_to_west"]["period"]),
            float(flows["route_boavista_west_to_east"]["period"]),
        )

    def test_av_penetration_high_replaces_urban_mix(self) -> None:
        av = apply_scenario_profile(self.base, "av_penetration_high")
        urban = next(d for d in av["vehicle_type_distributions"] if d["id"] == "urban_mix")
        types = {c["type"]: float(c["probability"]) for c in urban["components"]}
        self.assertIn("car_acc", types)
        self.assertIn("car_cacc", types)
        self.assertGreater(types["car_acc"] + types["car_cacc"], 0.5)

    def test_av_penetration_rejects_unknown_vtype(self) -> None:
        broken = json.loads(json.dumps(self.base))
        broken["scenario_profiles"]["broken_av"] = {
            "demand_profile": "am_peak",
            "vehicle_distribution_overrides": {
                "urban_mix": [
                    {"type": "ghost_car", "probability": 0.5},
                    {"type": "car", "probability": 0.5},
                ]
            },
        }
        with self.assertRaises(ScenarioConfigError):
            apply_scenario_profile(broken, "broken_av")

    def test_stochastic_incidents_materialise_deterministic_events(self) -> None:
        cfg_a = apply_scenario_profile(self.base, "stochastic_incidents_am_peak")
        # Same seed -> same events (determinism).
        cfg_b = apply_scenario_profile(self.base, "stochastic_incidents_am_peak")
        self.assertEqual(cfg_a["events"], cfg_b["events"])
        for event in cfg_a["events"]:
            self.assertEqual(event["type"], "stopped_vehicle")
            self.assertIn(event["stop_edge"], {"I2_I3", "I3_I4", "I4_I5", "I5_I6"})

    def test_signal_offsets_present_per_intersection(self) -> None:
        cfg = apply_scenario_profile(self.base, "baseline_am_peak")
        intersections = cfg["network"]["intersections"]
        self.assertEqual(len(intersections), 7)
        # I6 (Praca do Imperio) is modelled as a priority intersection (rotunda approximation),
        # so it does not carry a TLS offset. The remaining six intersections form the
        # signalised corridor and must carry coordination offsets.
        tls_intersections = [i for i in intersections if i.get("type") == "traffic_light"]
        self.assertEqual(len(tls_intersections), 6)
        priority_intersections = [i for i in intersections if i.get("type") == "priority"]
        self.assertEqual([i["id"] for i in priority_intersections], ["I6"])
        positive_offsets = sum(
            1 for i in tls_intersections if i.get("tls_offset_s") and float(i["tls_offset_s"]) > 0
        )
        self.assertGreaterEqual(positive_offsets, 5)

    def test_lane_allow_rules_target_bus_lanes_on_major_edges(self) -> None:
        rules = self.base["network"]["lane_allow_rules"]
        self.assertGreaterEqual(len(rules), 12)
        for rule in rules:
            self.assertIn("bus", rule["allow"])
            self.assertEqual(int(rule["lane_index"]), 0)

    def test_random_seeds_resolved_from_scenario_profile(self) -> None:
        cfg = apply_scenario_profile(self.base, "baseline_am_peak")
        seeds = cfg["scenario_profile"].get("random_seeds")
        self.assertIsInstance(seeds, list)
        self.assertGreaterEqual(len(seeds), 3)

    def test_turning_movements_flows_validate_against_known_routes(self) -> None:
        cfg = apply_scenario_profile(self.base, "baseline_am_peak")
        flows = cfg["demand_profiles"][cfg["active_demand_profile"]]["flows"]
        turn_flow_ids = {flow["id"] for flow in flows if "turn" in flow["id"] or "_to_city" in flow["id"] or "_to_atlantic" in flow["id"]}
        # 8 turn flows per intersection * 7 intersections = 56.
        self.assertEqual(len(turn_flow_ids), 56)

    def test_turning_movement_routes_emitted_for_every_intersection(self) -> None:
        base = json.loads((ROOT / "configs/sumo_scenario_base.json").read_text(encoding="utf-8"))
        cfg = apply_scenario_profile(base, "baseline_am_peak")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plain = tmp_path / "plain"
            generate(
                cfg,
                plain,
                routes_output=tmp_path / "routes.rou.xml",
                bus_stops_output=tmp_path / "bus_stops.add.xml",
                detectors_output=tmp_path / "detectors.add.xml",
                parking_output=tmp_path / "parking.add.xml",
                calibrators_output=tmp_path / "calibrators.add.xml",
                tls_offsets_output=tmp_path / "tls_offsets.add.xml",
            )
            routes_xml = (tmp_path / "routes.rou.xml").read_text(encoding="utf-8")
        for inter_id in ["I1", "I2", "I3", "I4", "I5", "I6", "I7"]:
            for suffix in (
                f"route_main_inbound_turn_to_N_{inter_id}",
                f"route_main_inbound_turn_to_S_{inter_id}",
                f"route_main_outbound_turn_to_N_{inter_id}",
                f"route_main_outbound_turn_to_S_{inter_id}",
                f"route_minor_N_{inter_id}_to_city",
                f"route_minor_N_{inter_id}_to_atlantic",
                f"route_minor_S_{inter_id}_to_city",
                f"route_minor_S_{inter_id}_to_atlantic",
            ):
                self.assertIn(suffix, routes_xml)

    def test_mainline_periods_scaled_to_hcm_credible_window(self) -> None:
        """Path C lite: mainline base periods scaled up so peak demand falls in HCM range."""
        am_peak = self.base["demand_profiles"]["am_peak"]
        inbound = next(f for f in am_peak["flows"] if f["id"] == "flow_car_inbound_west_to_east_am_peak")
        outbound = next(f for f in am_peak["flows"] if f["id"] == "flow_car_outbound_east_to_west_am_peak")
        # Inbound period must produce ~720 veh/h base (5s) and peak ~935 (5/1.30 = 3.85s -> 935 veh/h).
        self.assertLessEqual(float(inbound["period"]), 5.5)
        self.assertGreaterEqual(float(inbound["period"]), 4.5)
        # Outbound period must produce ~553 veh/h base (6.5s) and peak ~664.
        self.assertLessEqual(float(outbound["period"]), 7.0)
        self.assertGreaterEqual(float(outbound["period"]), 6.0)
        # Inbound must remain heavier than outbound in AM peak.
        self.assertLess(float(inbound["period"]), float(outbound["period"]))

    def test_calibrators_marked_scaffolding_and_not_enforced(self) -> None:
        """Path B: calibrators present as documentation but flagged inactive."""
        self.assertEqual(self.base.get("calibration_status"), "scaffolding_pending_data")
        for cal in self.base.get("calibrators", []):
            self.assertFalse(cal.get("active_in_simulation", True))

    def test_calibration_methodology_documents_hcm_derivation(self) -> None:
        methodology = self.base.get("calibration_methodology", {})
        # Reference + formula + factors must be present so a reviewer can reproduce.
        self.assertIn("HCM", methodology.get("reference", ""))
        self.assertIn("s0", methodology.get("saturation_flow_formula", ""))
        factors = methodology.get("adjustment_factors_used", {})
        self.assertEqual(factors.get("s0_base_saturation_flow_veh_per_h_per_lane"), 1900)
        self.assertEqual(factors.get("N_lanes_mainline"), 2)
        # Derived targets must match the calibrator schedule values (consistency).
        derived = methodology.get("derived_targets_veh_per_h", {})
        self.assertAlmostEqual(derived.get("outbound_peak", 0), 884, delta=50)

    def test_calibrators_excluded_from_sumocfg_additional_files(self) -> None:
        cfg_text = (ROOT / "sumo/corredor.sumocfg").read_text(encoding="utf-8")
        # additional-files line must not load calibrators.add.xml.
        import re
        m = re.search(r'<additional-files\s+value="([^"]+)"', cfg_text)
        self.assertIsNotNone(m)
        loaded = (m.group(1) if m else "").split(",")
        self.assertNotIn("additional/calibrators.add.xml", loaded)

    def test_each_signalised_intersection_has_tls_program(self) -> None:
        cfg = apply_scenario_profile(self.base, "baseline_am_peak")
        tls_intersections = [
            inter for inter in cfg["network"]["intersections"]
            if inter.get("type") == "traffic_light"
        ]
        self.assertEqual(len(tls_intersections), 6)
        for inter in tls_intersections:
            program = inter.get("tls_program")
            self.assertIsInstance(program, dict, msg=inter["id"])
            required_keys = (
                "green_main_s", "yellow_main_s", "all_red_main_to_cross_s",
                "green_minor_s", "yellow_minor_s", "all_red_cross_to_main_s",
            )
            for required in required_keys:
                self.assertIn(required, program, msg=inter["id"])
            cycle = float(inter["tls_cycle_s"])
            program_sum = sum(float(program[k]) for k in required_keys)
            self.assertAlmostEqual(program_sum, cycle, delta=0.5, msg=inter["id"])
            # g/C for the main approach should fall in the [0.45, 0.70] window — HCM-credible
            # for an urban arterial with light-to-moderate cross-traffic.
            g_over_c_main = float(program["green_main_s"]) / cycle
            self.assertGreaterEqual(g_over_c_main, 0.45, msg=inter["id"])
            self.assertLessEqual(g_over_c_main, 0.70, msg=inter["id"])
            # All-red clearance must be strictly positive — safety-critical.
            self.assertGreater(float(program["all_red_main_to_cross_s"]), 0, msg=inter["id"])
            self.assertGreater(float(program["all_red_cross_to_main_s"]), 0, msg=inter["id"])

    def test_build_tls_offsets_emits_phase_children_with_program(self) -> None:
        cfg = apply_scenario_profile(self.base, "baseline_am_peak")
        overrides_root = build_tls_offsets(cfg)
        self.assertIsNotNone(overrides_root)
        assert overrides_root is not None
        tls_elements = {elem.attrib["id"]: elem for elem in overrides_root.findall("tls")}
        # I1..I5, I7 are signalised; I6 became priority.
        self.assertEqual(set(tls_elements), {"I1", "I2", "I3", "I4", "I5", "I7"})
        expected_roles = [
            "main_green", "main_yellow", "all_red_main_to_cross",
            "cross_green", "cross_yellow", "all_red_cross_to_main",
        ]
        for tls_id, elem in tls_elements.items():
            phase_roles = [p.attrib["role"] for p in elem.findall("phase")]
            self.assertEqual(phase_roles, expected_roles, msg=tls_id)

    def test_build_tls_offsets_rejects_program_sum_mismatch(self) -> None:
        broken = json.loads(json.dumps(self.base))
        intersection = next(i for i in broken["network"]["intersections"] if i["id"] == "I4")
        # Make the green_main_s blow past the 90s cycle so the consistency check fires.
        intersection["tls_program"]["green_main_s"] = 200
        with self.assertRaises(ValueError):
            build_tls_offsets(broken)

    def test_bay_attribute_present_on_each_bus_stop(self) -> None:
        stops = self.base["public_transport"]["stops"]
        for stop in stops:
            self.assertIn("bay", stop, msg=stop.get("id"))
            self.assertIsInstance(stop["bay"], bool, msg=stop.get("id"))
        bay_stops = {stop["id"] for stop in stops if stop["bay"]}
        curbside_stops = {stop["id"] for stop in stops if not stop["bay"]}
        # Major terminals/landmarks have physical bays; intermediate stops are curbside.
        for expected_bay in (
            "bs_casa_musica_w", "bs_casa_musica_e",
            "bs_serralves_w", "bs_serralves_e",
            "bs_praca_imperio_w", "bs_praca_imperio_e",
            "bs_castelo_queijo_w", "bs_castelo_queijo_e",
        ):
            self.assertIn(expected_bay, bay_stops)
        for expected_curbside in (
            "bs_bessa_w", "bs_bessa_e",
            "bs_antunes_guimaraes_w", "bs_antunes_guimaraes_e",
            "bs_marechal_w", "bs_marechal_e",
        ):
            self.assertIn(expected_curbside, curbside_stops)

    def test_bus_stop_with_bay_emits_parking_attribute(self) -> None:
        cfg = apply_scenario_profile(self.base, "baseline_am_peak")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plain = tmp_path / "plain"
            generate(
                cfg,
                plain,
                routes_output=tmp_path / "routes.rou.xml",
                bus_stops_output=tmp_path / "bus_stops.add.xml",
                detectors_output=tmp_path / "detectors.add.xml",
                parking_output=tmp_path / "parking.add.xml",
                calibrators_output=tmp_path / "calibrators.add.xml",
                tls_offsets_output=tmp_path / "tls_offsets.add.xml",
            )
            routes_xml = (tmp_path / "routes.rou.xml").read_text(encoding="utf-8")
        # Each <stop> element captures the bay attribute via parking="true". Inspect every
        # generated <stop> and group them by stop id to verify bay-equipped stops carry parking
        # while curbside stops do not.
        import re
        bay_stop_ids: set[str] = set()
        curbside_stop_ids: set[str] = set()
        for match in re.finditer(r'<stop\s+([^/]+)/>', routes_xml):
            attrs = match.group(1)
            stop_match = re.search(r'busStop="(bs_[a-z_]+)"', attrs)
            if not stop_match:
                continue
            stop_id = stop_match.group(1)
            if 'parking="true"' in attrs:
                bay_stop_ids.add(stop_id)
            else:
                curbside_stop_ids.add(stop_id)
        for expected_bay in (
            "bs_casa_musica_w", "bs_casa_musica_e",
            "bs_serralves_w", "bs_serralves_e",
            "bs_praca_imperio_w", "bs_praca_imperio_e",
            "bs_castelo_queijo_w", "bs_castelo_queijo_e",
        ):
            self.assertIn(expected_bay, bay_stop_ids)
            self.assertNotIn(expected_bay, curbside_stop_ids)
        for expected_curbside in (
            "bs_bessa_w", "bs_bessa_e",
            "bs_antunes_guimaraes_w", "bs_antunes_guimaraes_e",
            "bs_marechal_w", "bs_marechal_e",
        ):
            self.assertIn(expected_curbside, curbside_stop_ids)
            self.assertNotIn(expected_curbside, bay_stop_ids)

    def test_main_flows_use_smooth_multi_step_ramp(self) -> None:
        am_peak = self.base["demand_profiles"]["am_peak"]
        inbound = next(f for f in am_peak["flows"] if f["id"] == "flow_car_inbound_west_to_east_am_peak")
        outbound = next(f for f in am_peak["flows"] if f["id"] == "flow_car_outbound_east_to_west_am_peak")
        for flow in (inbound, outbound):
            tp = flow["time_profile"]
            # At least 7 segments — replaces the previous 3-step coarse profile so demand ramp is
            # closer to a smooth pickup/decay shape rather than discrete jumps.
            self.assertGreaterEqual(len(tp), 7)
            # Time profile must cover the full 0–7200 window contiguously.
            self.assertEqual(float(tp[0]["begin"]), 0.0)
            self.assertEqual(float(tp[-1]["end"]), 7200.0)
            for prev, curr in zip(tp, tp[1:]):
                self.assertEqual(float(prev["end"]), float(curr["begin"]))
            # The peak scale must be the largest and occur in the middle 1/3 of the window.
            peak_entry = max(tp, key=lambda entry: float(entry["scale"]))
            self.assertGreater(float(peak_entry["scale"]), 1.20)
            self.assertGreaterEqual(float(peak_entry["begin"]), 1800.0)
            self.assertLessEqual(float(peak_entry["end"]), 4500.0)

    def test_turning_movement_routes_terminate_correctly(self) -> None:
        from pps57_sumo.generate_plain_corridor import build_routes
        cfg = apply_scenario_profile(self.base, "baseline_am_peak")
        intersections = cfg["network"]["intersections"]
        terminals = {t["id"]: t for t in cfg["network"]["terminals"]}
        routes = build_routes(cfg, intersections, terminals)
        # Mainline turn N at I3 should end at N_I3 approach
        self.assertEqual(routes["route_main_inbound_turn_to_N_I3"][-1], "I3_N_I3")
        self.assertEqual(routes["route_main_outbound_turn_to_S_I3"][-1], "I3_S_I3")
        # Minor approach N→city should end at CITY_EAST
        self.assertEqual(routes["route_minor_N_I3_to_city"][-1], "I1_CITY_EAST")
        # Minor approach S→atlantic should end at ATLANTIC_WEST
        self.assertEqual(routes["route_minor_S_I3_to_atlantic"][-1], "I7_ATLANTIC_WEST")
        # First edge of N→atlantic should be the N approach edge
        self.assertEqual(routes["route_minor_N_I5_to_atlantic"][0], "N_I5_I5")


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

    def test_emissions_parser_aggregates_per_vehicle_totals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "emissions.xml"
            path.write_text(
                "<emission-export>"
                '<timestep time="0">'
                '<vehicle id="car_1" type="car" CO2="100" fuel="40" NOx="5" />'
                '<vehicle id="bus_STCP500_W_0000" type="bus_12m" CO2="500" fuel="180" NOx="20" />'
                '</timestep>'
                '<timestep time="60">'
                '<vehicle id="car_1" type="car" CO2="700" fuel="280" NOx="35" />'
                '<vehicle id="bus_STCP500_W_0000" type="bus_12m" CO2="3500" fuel="1260" NOx="140" />'
                '</timestep>'
                "</emission-export>",
                encoding="utf-8",
            )
            kpis = parse_emissions(path)
            self.assertTrue(kpis["available"])
            self.assertEqual(kpis["vehicle_count"], 2)
            self.assertEqual(kpis["totals_mg"]["CO2"], 4200.0)
            self.assertEqual(kpis["totals_mg"]["fuel"], 1540.0)
            self.assertEqual(kpis["bus_count"], 1)
            self.assertEqual(kpis["bus_totals_mg"]["CO2"], 3500.0)

    def test_emissions_parser_handles_missing_file(self) -> None:
        kpis = parse_emissions(Path("/nonexistent/emissions.xml"))
        self.assertFalse(kpis["available"])

    def test_full_generation_emits_all_new_artifacts(self) -> None:
        base = json.loads((ROOT / "configs/sumo_scenario_base.json").read_text(encoding="utf-8"))
        cfg = apply_scenario_profile(base, "baseline_am_peak")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            plain = tmp_path / "plain"
            generate(
                cfg,
                plain,
                routes_output=tmp_path / "routes.rou.xml",
                bus_stops_output=tmp_path / "bus_stops.add.xml",
                detectors_output=tmp_path / "detectors.add.xml",
                parking_output=tmp_path / "parking.add.xml",
                calibrators_output=tmp_path / "calibrators.add.xml",
                tls_offsets_output=tmp_path / "tls_offsets.add.xml",
            )
            routes = (tmp_path / "routes.rou.xml").read_text(encoding="utf-8")
            parking = (tmp_path / "parking.add.xml").read_text(encoding="utf-8")
            calibrators = (tmp_path / "calibrators.add.xml").read_text(encoding="utf-8")
            tls_offsets = (tmp_path / "tls_offsets.add.xml").read_text(encoding="utf-8")
            edges = (plain / "corredor.edg.xml").read_text(encoding="utf-8")
            nodes = (plain / "corredor.nod.xml").read_text(encoding="utf-8")
            # vTypes for AVs are present
            self.assertIn("car_acc", routes)
            self.assertIn("car_cacc", routes)
            # Driver state params emitted as <param> children
            self.assertIn("has.driverstate.device", routes)
            # actionStepLength forwarded as attribute
            self.assertIn("actionStepLength=", routes)
            # Pedestrian flows emitted
            self.assertIn("personFlow", routes)
            self.assertIn("walk", routes)
            # Parking events as <stop parkingArea=...>
            self.assertIn("parkingArea=", routes)
            # Parking areas additional doc
            self.assertIn("parkingArea", parking)
            self.assertIn("roadsideCapacity", parking)
            # Calibrators emitted
            self.assertIn("calibrator", calibrators)
            self.assertIn("vehsPerHour", calibrators)
            # TLS offsets emitted
            self.assertIn("tlsOffsetOverrides", tls_offsets)
            self.assertIn('id="I2"', tls_offsets)
            # Bus-only lane child elements present on edges
            self.assertIn("allow=\"bus emergency taxi\"", edges)
            # Elevation z attribute present on nodes
            self.assertIn("z=\"95.00\"", nodes)
            # Edge width attribute present
            self.assertIn("width=", edges)

    def test_apply_tls_offsets_modifies_net_xml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            net = tmp_path / "corredor.net.xml"
            net.write_text(
                '<?xml version="1.0"?>'
                '<net>'
                '<tlLogic id="I2" programID="0" offset="0" type="static"><phase duration="30" state="G"/></tlLogic>'
                '<tlLogic id="I3" programID="0" offset="0" type="static"><phase duration="30" state="G"/></tlLogic>'
                '</net>',
                encoding="utf-8",
            )
            overrides = tmp_path / "tls.add.xml"
            overrides.write_text(
                '<tlsOffsetOverrides>'
                '<tls id="I2" offset_s="42"/>'
                '<tls id="I3" offset_s="13"/>'
                '</tlsOffsetOverrides>',
                encoding="utf-8",
            )
            modified = apply_tls_offsets(net, overrides)
            self.assertEqual(modified, 2)
            text = net.read_text(encoding="utf-8")
            self.assertIn('id="I2"', text)
            self.assertIn('offset="42.0"', text)
            self.assertIn('offset="13.0"', text)

    def _write_synthetic_i2_net(self, net_path: Path) -> None:
        """Write a synthetic 14-link I2 mirroring the real netconvert layout.

        Links 0-2 come from N_I2 (cross), 3-6 from I3_I2 (main inbound),
        7-9 from S_I2 (cross), 10-13 from I1_I2 (main outbound).
        """
        net_path.write_text(
            '<?xml version="1.0"?>'
            '<net>'
            '<tlLogic id="I2" programID="0" offset="0" type="static">'
            '<phase duration="42" state="rrrGGGgrrrGGGg"/>'
            '<phase duration="3"  state="rrryyyyrrryyyy"/>'
            '<phase duration="42" state="GGgrrrrGGgrrrr"/>'
            '<phase duration="3"  state="yyyrrrryyyrrrr"/>'
            '</tlLogic>'
            '<connection from="N_I2_I2" to="I2_I1" tl="I2" linkIndex="0"/>'
            '<connection from="N_I2_I2" to="I2_S_I2" tl="I2" linkIndex="1"/>'
            '<connection from="N_I2_I2" to="I2_I3" tl="I2" linkIndex="2"/>'
            '<connection from="I3_I2" to="I2_N_I2" tl="I2" linkIndex="3"/>'
            '<connection from="I3_I2" to="I2_I1" tl="I2" linkIndex="4"/>'
            '<connection from="I3_I2" to="I2_I1" tl="I2" linkIndex="5"/>'
            '<connection from="I3_I2" to="I2_S_I2" tl="I2" linkIndex="6"/>'
            '<connection from="S_I2_I2" to="I2_I3" tl="I2" linkIndex="7"/>'
            '<connection from="S_I2_I2" to="I2_N_I2" tl="I2" linkIndex="8"/>'
            '<connection from="S_I2_I2" to="I2_I1" tl="I2" linkIndex="9"/>'
            '<connection from="I1_I2" to="I2_S_I2" tl="I2" linkIndex="10"/>'
            '<connection from="I1_I2" to="I2_I3" tl="I2" linkIndex="11"/>'
            '<connection from="I1_I2" to="I2_I3" tl="I2" linkIndex="12"/>'
            '<connection from="I1_I2" to="I2_N_I2" tl="I2" linkIndex="13"/>'
            '</net>',
            encoding="utf-8",
        )

    def test_apply_tls_offsets_rewrites_phase_durations_legacy_four_roles(self) -> None:
        """Four-role override stays backward compatible: durations rewritten, no all-red inserted."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            net = tmp_path / "corredor.net.xml"
            self._write_synthetic_i2_net(net)
            overrides = tmp_path / "tls.add.xml"
            overrides.write_text(
                '<tlsOffsetOverrides>'
                '<tls id="I2" offset_s="38">'
                '<phase role="main_green" duration_s="52"/>'
                '<phase role="main_yellow" duration_s="3"/>'
                '<phase role="cross_green" duration_s="32"/>'
                '<phase role="cross_yellow" duration_s="3"/>'
                '</tls>'
                '</tlsOffsetOverrides>',
                encoding="utf-8",
            )
            modified = apply_tls_offsets(net, overrides)
            self.assertEqual(modified, 1)
            from xml.etree import ElementTree as ET
            tl = ET.parse(net).getroot().find("tlLogic")
            assert tl is not None
            self.assertEqual(tl.attrib["offset"], "38.0")
            phases = tl.findall("phase")
            self.assertEqual([float(p.attrib["duration"]) for p in phases], [52.0, 3.0, 32.0, 3.0])
            self.assertEqual(phases[0].attrib["state"], "rrrGGGgrrrGGGg")
            self.assertEqual(phases[2].attrib["state"], "GGgrrrrGGgrrrr")

    def test_apply_tls_offsets_inserts_all_red_clearance(self) -> None:
        """Six-role override rewrites the four canonical phases AND inserts two all-red phases."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            net = tmp_path / "corredor.net.xml"
            self._write_synthetic_i2_net(net)
            overrides = tmp_path / "tls.add.xml"
            overrides.write_text(
                '<tlsOffsetOverrides>'
                '<tls id="I2" offset_s="38">'
                '<phase role="main_green" duration_s="51"/>'
                '<phase role="main_yellow" duration_s="3"/>'
                '<phase role="all_red_main_to_cross" duration_s="1"/>'
                '<phase role="cross_green" duration_s="31"/>'
                '<phase role="cross_yellow" duration_s="3"/>'
                '<phase role="all_red_cross_to_main" duration_s="1"/>'
                '</tls>'
                '</tlsOffsetOverrides>',
                encoding="utf-8",
            )
            modified = apply_tls_offsets(net, overrides)
            self.assertEqual(modified, 1)
            from xml.etree import ElementTree as ET
            tl = ET.parse(net).getroot().find("tlLogic")
            assert tl is not None
            phases = tl.findall("phase")
            # Expect 6 phases now: main_g, main_y, all_red, cross_g, cross_y, all_red.
            self.assertEqual(len(phases), 6)
            durations = [float(p.attrib["duration"]) for p in phases]
            self.assertEqual(durations, [51.0, 3.0, 1.0, 31.0, 3.0, 1.0])
            # Cycle preserved.
            self.assertAlmostEqual(sum(durations), 90.0, places=3)
            # All-red phases have state = all 'r' with the correct length (14 links at I2).
            self.assertEqual(phases[2].attrib["state"], "r" * 14)
            self.assertEqual(phases[5].attrib["state"], "r" * 14)
            # Existing state strings untouched.
            self.assertEqual(phases[0].attrib["state"], "rrrGGGgrrrGGGg")
            self.assertEqual(phases[3].attrib["state"], "GGgrrrrGGgrrrr")

    def test_apply_tls_offsets_is_idempotent_for_all_red(self) -> None:
        """Re-running the override on the same net does not duplicate all-red phases."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            net = tmp_path / "corredor.net.xml"
            self._write_synthetic_i2_net(net)
            overrides = tmp_path / "tls.add.xml"
            overrides.write_text(
                '<tlsOffsetOverrides>'
                '<tls id="I2" offset_s="38">'
                '<phase role="main_green" duration_s="51"/>'
                '<phase role="main_yellow" duration_s="3"/>'
                '<phase role="all_red_main_to_cross" duration_s="1"/>'
                '<phase role="cross_green" duration_s="31"/>'
                '<phase role="cross_yellow" duration_s="3"/>'
                '<phase role="all_red_cross_to_main" duration_s="1"/>'
                '</tls>'
                '</tlsOffsetOverrides>',
                encoding="utf-8",
            )
            apply_tls_offsets(net, overrides)
            apply_tls_offsets(net, overrides)
            from xml.etree import ElementTree as ET
            tl = ET.parse(net).getroot().find("tlLogic")
            assert tl is not None
            phases = tl.findall("phase")
            self.assertEqual(len(phases), 6, msg="all-red phases were duplicated on second run")
            self.assertEqual([float(p.attrib["duration"]) for p in phases], [51.0, 3.0, 1.0, 31.0, 3.0, 1.0])

    def test_classify_phase_role_recognises_pedestrian_and_all_red(self) -> None:
        from pps57_sumo.apply_tls_offsets import _classify_phase_role
        main_links = {3, 4, 5, 6, 10, 11, 12, 13}
        cross_links = {0, 1, 2, 7, 8, 9}
        self.assertEqual(_classify_phase_role("rrrGGGgrrrGGGg", main_links, cross_links), "main_green")
        self.assertEqual(_classify_phase_role("GGgrrrrGGgrrrr", main_links, cross_links), "cross_green")
        self.assertEqual(_classify_phase_role("rrrrrrrrrrrrrr", main_links, cross_links), "all_red")
        # Pedestrian-only phase: G at index 14 (outside main and cross link sets).
        ped_state = "rrrrrrrrrrrrrrG"
        self.assertEqual(_classify_phase_role(ped_state, main_links, cross_links), "pedestrian")
        # Mixed (main and cross both green) — not a canonical role.
        self.assertIsNone(_classify_phase_role("GGGGGGGGGGGGGG", main_links, cross_links))


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
