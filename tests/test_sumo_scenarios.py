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

from pps57_sumo.apply_tls_offsets import apply_tls_offsets  # noqa: E402
from pps57_sumo.build_network import build_sumo_artifacts  # noqa: E402
from pps57_sumo.detector_kpis import parse_detector_kpis  # noqa: E402
from pps57_sumo.generate_plain_corridor import (  # noqa: E402
    _service_departures,
    build_route_xml,
    build_tls_offsets,
    generate,
)
from pps57_sumo.parse_emissions import parse_emissions  # noqa: E402
from pps57_sumo.parse_insertion import parse_insertion_kpis  # noqa: E402
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

from run_sumo_scenario import _effective_end_s, compare_kpis, run_verdict  # noqa: E402


class SumoScenarioProfilesTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = json.loads(
            (ROOT / "configs/sumo_scenario_base.json").read_text(encoding="utf-8")
        )
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

    def test_baseline_am_peak_uses_operational_demand_profile(self) -> None:
        raw_peak = scenario_summary(self.base)
        operational_peak = scenario_summary(apply_scenario_profile(self.base, "baseline_am_peak"))
        self.assertEqual(
            apply_scenario_profile(self.base, "baseline_am_peak")["active_demand_profile"],
            "am_peak_operational",
        )
        self.assertLess(
            operational_peak["estimated_car_departures"], raw_peak["estimated_car_departures"]
        )

    def test_scenario_summary_counts_scheduled_bus_departures(self) -> None:
        for scenario_id in ("baseline_am_peak", "baseline_off_peak", "congested_delayed_bus"):
            config = apply_scenario_profile(self.base, scenario_id)
            actual = sum(
                len(list(_service_departures(service, config)))
                for service in config["public_transport"]["services"]
            )
            self.assertEqual(scenario_summary(config)["estimated_bus_departures"], actual)

    def test_cross_pressure_makes_selected_minor_flows_more_frequent(self) -> None:
        baseline = apply_scenario_profile(self.base, "baseline_am_peak")
        config = apply_scenario_profile(self.base, "cross_traffic_pressure")
        baseline_flows = {
            flow["route"]: flow
            for flow in baseline["demand_profiles"][baseline["active_demand_profile"]]["flows"]
        }
        pressure_flows = {
            flow["route"]: flow
            for flow in config["demand_profiles"][config["active_demand_profile"]]["flows"]
        }
        self.assertLess(
            float(pressure_flows["route_cross_NS_I2"]["period"]),
            float(baseline_flows["route_cross_NS_I2"]["period"]),
        )
        self.assertLess(
            float(pressure_flows["route_cross_SN_I6"]["period"]),
            float(baseline_flows["route_cross_SN_I6"]["period"]),
        )
        self.assertAlmostEqual(float(pressure_flows["route_cross_NS_I2"]["period"]), 39.6)
        self.assertAlmostEqual(float(pressure_flows["route_cross_SN_I6"]["period"]), 29.7)

    def test_emergency_profile_is_the_only_catalog_profile_with_emergency_event(self) -> None:
        for scenario_id in self.catalog["scenarios"]:
            config = apply_scenario_profile(self.base, scenario_id)
            event_types = {event.get("type") for event in config.get("events", [])}
            if scenario_id == "emergency_vehicle_conflict":
                self.assertIn("emergency_vehicle", event_types)
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

    def test_weather_vehicle_overrides_apply_to_vehicle_types(self) -> None:
        # O catalogo v05 ja nao inclui cenarios meteorologicos; a mecanica de
        # vehicle_overrides continua suportada e valida-se com perfil inline.
        cfg = json.loads(json.dumps(self.base))
        cfg["scenario_profiles"]["rainy_inline"] = {
            "demand_profile": "am_peak",
            "vehicle_overrides": {
                "all": {
                    "tau_delta": 0.2,
                    "speed_factor_multiplier": 0.88,
                    "decel_multiplier": 0.85,
                    "min_gap_multiplier": 1.15,
                }
            },
        }
        baseline = apply_scenario_profile(self.base, "baseline_am_peak")
        rainy = apply_scenario_profile(cfg, "rainy_inline")
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
        inbound = next(
            flow for flow in flows.values() if flow["route"] == "route_boavista_west_to_east"
        )
        self.assertLess(float(inbound["period"]), 7.5)

    def test_actuated_tls_type_propagates_to_generated_nodes(self) -> None:
        # O base config é atualmente todo em tempo fixo (sem tls_type=actuated)
        # para o atuador TSP via TraCI não competir com a lógica atuada interna
        # do SUMO. Para validar a *plumbing* do flag actuated, injetamos um
        # tls_type=actuated numa cópia do config e confirmamos a propagação.
        cfg = json.loads(json.dumps(self.base))
        traffic_lights = [
            inter
            for inter in cfg["network"]["intersections"]
            if inter.get("type") == "traffic_light"
        ]
        self.assertGreaterEqual(len(traffic_lights), 2)
        actuated_inter = traffic_lights[0]
        actuated_inter["tls_type"] = "actuated"
        static_inter = next(inter for inter in traffic_lights if "tls_type" not in inter)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            generate(
                cfg,
                tmp_path,
                routes_output=tmp_path / "routes.rou.xml",
                bus_stops_output=tmp_path / "bus_stops.add.xml",
                detectors_output=tmp_path / "detectors.add.xml",
            )
            nod_text = (tmp_path / "corredor.nod.xml").read_text(encoding="utf-8")
        actuated_line = next(
            line for line in nod_text.splitlines() if f'id="{actuated_inter["id"]}"' in line
        )
        self.assertIn('tlType="actuated"', actuated_line)
        # Static intersections must NOT carry a tlType attribute on the node.
        static_line = next(
            line for line in nod_text.splitlines() if f'id="{static_inter["id"]}"' in line
        )
        self.assertNotIn("tlType=", static_line)

    def test_sumocfg_emits_actuated_flags_only_when_needed(self) -> None:
        # tls.actuated.jam-threshold is a sumo-runtime option (lives in the
        # sumocfg <processing> block, not in netconvert). detector-gap is NOT
        # a CLI option in SUMO 1.26 and must never be emitted here — it would
        # cause sumo to abort with "No option with the name ... exists".
        from pps57_sumo.build_network import artifact_paths, netconvert_command, write_sumocfg

        artifacts = artifact_paths(ROOT / "sumo")
        cmd = netconvert_command(self.base, artifacts)
        self.assertNotIn("--tls.actuated.detector-gap", cmd)
        self.assertNotIn("--tls.actuated.jam-threshold", cmd)

        # O base config é todo estático; injeta um tls_type=actuated numa cópia
        # para exercitar a emissão do flag runtime jam-threshold.
        with_actuated = json.loads(json.dumps(self.base))
        first_tl = next(
            inter
            for inter in with_actuated["network"]["intersections"]
            if inter.get("type") == "traffic_light"
        )
        first_tl["tls_type"] = "actuated"

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            tmp_artifacts = artifact_paths(tmp_path)
            tmp_artifacts.sumocfg_file.parent.mkdir(parents=True, exist_ok=True)
            write_sumocfg(with_actuated, tmp_artifacts, output_dir=tmp_path / "outputs")
            sumocfg = tmp_artifacts.sumocfg_file.read_text(encoding="utf-8")
            self.assertNotIn("tls.actuated.detector-gap", sumocfg)
            self.assertIn('<tls.actuated.jam-threshold value="30"/>', sumocfg)

            # Strip all tls_type=actuated and confirm the jam-threshold line disappears.
            static_only = json.loads(json.dumps(self.base))
            for inter in static_only["network"]["intersections"]:
                inter.pop("tls_type", None)
            write_sumocfg(static_only, tmp_artifacts, output_dir=tmp_path / "outputs")
            sumocfg_static = tmp_artifacts.sumocfg_file.read_text(encoding="utf-8")
            self.assertNotIn("tls.actuated.detector-gap", sumocfg_static)
            self.assertNotIn("tls.actuated.jam-threshold", sumocfg_static)

    def test_roundabout_element_lists_ring_edges_and_nodes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            generate(
                self.base,
                tmp_path,
                routes_output=tmp_path / "routes.rou.xml",
                bus_stops_output=tmp_path / "bus_stops.add.xml",
                detectors_output=tmp_path / "detectors.add.xml",
            )
            edg_text = (tmp_path / "corredor.edg.xml").read_text(encoding="utf-8")
        self.assertIn("<roundabout", edg_text)
        # 4 ring edges + 4 ring nodes for I6.
        for arm in ("CITY_TO_NORTH", "NORTH_TO_ATLANTIC", "ATLANTIC_TO_SOUTH", "SOUTH_TO_CITY"):
            self.assertIn(f"RB_I6_{arm}", edg_text)
        for node in ("RB_I6_CITY", "RB_I6_NORTH", "RB_I6_ATLANTIC", "RB_I6_SOUTH"):
            self.assertIn(node, edg_text)
        # Ring lanes upgraded from 1 to 2 — match on the ring edge specifically.
        ring_line = next(
            line for line in edg_text.splitlines() if 'id="RB_I6_CITY_TO_NORTH"' in line
        )
        self.assertIn('numLanes="2"', ring_line)

    def test_av_penetration_high_replaces_urban_mix(self) -> None:
        # Mecanica de vehicle_distribution_overrides validada com perfil inline
        # (mix ~60% AV do antigo cenario av_penetration_high do catalogo v04).
        cfg = json.loads(json.dumps(self.base))
        cfg["scenario_profiles"]["av_high_inline"] = {
            "demand_profile": "am_peak",
            "vehicle_distribution_overrides": {
                "urban_mix": [
                    {"type": "car", "probability": 0.1},
                    {"type": "car_cautious", "probability": 0.05},
                    {"type": "car_aggressive", "probability": 0.05},
                    {"type": "car_acc", "probability": 0.27},
                    {"type": "car_acc_ev", "probability": 0.08},
                    {"type": "car_cacc", "probability": 0.18},
                    {"type": "car_cacc_ev", "probability": 0.07},
                    {"type": "motorcycle", "probability": 0.05},
                    {"type": "taxi", "probability": 0.04},
                    {"type": "lcv", "probability": 0.07},
                    {"type": "hgv", "probability": 0.04},
                ]
            },
        }
        av = apply_scenario_profile(cfg, "av_high_inline")
        urban = next(d for d in av["vehicle_type_distributions"] if d["id"] == "urban_mix")
        types = {c["type"]: float(c["probability"]) for c in urban["components"]}
        self.assertIn("car_acc", types)
        self.assertIn("car_cacc", types)
        self.assertIn("car_acc_ev", types)
        self.assertIn("car_cacc_ev", types)
        automated_share = sum(
            prob for typ, prob in types.items() if typ.startswith(("car_acc", "car_cacc"))
        )
        electric_automated_share = types["car_acc_ev"] + types["car_cacc_ev"]
        self.assertGreater(automated_share, 0.5)
        self.assertGreater(electric_automated_share, 0.0)

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

    def test_vehicle_distribution_probabilities_must_sum_to_one(self) -> None:
        broken = json.loads(json.dumps(self.base))
        broken["scenario_profiles"]["broken_distribution_sum"] = {
            "demand_profile": "am_peak",
            "vehicle_distribution_overrides": {
                "urban_mix": [
                    {"type": "car", "probability": 0.8},
                    {"type": "lcv", "probability": 0.1},
                ]
            },
        }
        with self.assertRaises(ScenarioConfigError):
            apply_scenario_profile(broken, "broken_distribution_sum")

    def test_stochastic_incidents_materialise_deterministic_events(self) -> None:
        from pps57_sumo.generate_plain_corridor import build_routes

        # Mecanica de stochastic_incidents validada com perfil inline (template
        # do antigo cenario stochastic_incidents_am_peak do catalogo v04).
        cfg = json.loads(json.dumps(self.base))
        cfg["scenario_profiles"]["stochastic_inline"] = {
            "demand_profile": "am_peak",
            "stochastic_incidents": [
                {
                    "id_prefix": "stop_lcv_corridor",
                    "type": "stopped_vehicle",
                    "vehicle_type": "lcv",
                    "route_candidates": [
                        "route_boavista_west_to_east",
                        "route_boavista_east_to_west",
                    ],
                    "edge_candidates": ["I2_I3", "I3_I4", "I4_I5", "I5_I6"],
                    "depart_window_s": [1500, 5400],
                    "duration_s_mean": 180,
                    "duration_s_std": 45,
                    "probability": 0.6,
                }
            ],
        }
        cfg_a = apply_scenario_profile(cfg, "stochastic_inline")
        # Same seed -> same events (determinism).
        cfg_b = apply_scenario_profile(cfg, "stochastic_inline")
        self.assertEqual(cfg_a["events"], cfg_b["events"])
        intersections = cfg_a["network"]["intersections"]
        terminals = {t["id"]: t for t in cfg_a["network"]["terminals"]}
        routes = build_routes(cfg_a, intersections, terminals)
        for event in cfg_a["events"]:
            self.assertEqual(event["type"], "stopped_vehicle")
            self.assertIn(event["stop_edge"], {"I2_I3", "I3_I4", "I4_I5", "I5_I6"})
            self.assertIn(event["stop_edge"], routes[event["route"]])

    def test_stopped_vehicle_event_must_stop_on_its_route(self) -> None:
        broken = json.loads(json.dumps(self.base))
        broken["scenario_profiles"]["broken_stopped_vehicle"] = {
            "demand_profile": "am_peak",
            "events": [
                {
                    "id": "broken_stop",
                    "type": "stopped_vehicle",
                    "vehicle_type": "lcv",
                    "route": "route_boavista_west_to_east",
                    "depart": 1200,
                    "stop_edge": "I2_I3",
                    "stop_duration_s": 180,
                }
            ],
        }
        with self.assertRaises(ScenarioConfigError):
            apply_scenario_profile(broken, "broken_stopped_vehicle")

    def test_signal_offsets_present_per_intersection(self) -> None:
        cfg = apply_scenario_profile(self.base, "baseline_am_peak")
        intersections = cfg["network"]["intersections"]
        self.assertEqual(len(intersections), 7)
        # I6 (Praca do Imperio) is modelled as a priority ring roundabout, so
        # it does not carry a TLS offset. The remaining six intersections form the
        # signalised corridor and must carry coordination offsets.
        tls_intersections = [i for i in intersections if i.get("type") == "traffic_light"]
        self.assertEqual(len(tls_intersections), 6)
        priority_intersections = [i for i in intersections if i.get("type") == "priority"]
        self.assertEqual([i["id"] for i in priority_intersections], ["I6"])
        self.assertEqual(priority_intersections[0].get("roundabout_model"), "ring")
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
        turn_flow_ids = {
            flow["id"]
            for flow in flows
            if "turn" in flow["id"] or "_to_city" in flow["id"] or "_to_atlantic" in flow["id"]
        }
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
        inbound = next(
            f for f in am_peak["flows"] if f["id"] == "flow_car_inbound_west_to_east_am_peak"
        )
        outbound = next(
            f for f in am_peak["flows"] if f["id"] == "flow_car_outbound_east_to_west_am_peak"
        )
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
            inter
            for inter in cfg["network"]["intersections"]
            if inter.get("type") == "traffic_light"
        ]
        self.assertEqual(len(tls_intersections), 6)
        for inter in tls_intersections:
            program = inter.get("tls_program")
            self.assertIsInstance(program, dict, msg=inter["id"])
            required_keys = (
                "green_main_s",
                "yellow_main_s",
                "all_red_main_to_cross_s",
                "green_minor_s",
                "yellow_minor_s",
                "all_red_cross_to_main_s",
            )
            for required in required_keys:
                self.assertIn(required, program, msg=inter["id"])
            cycle = float(inter["tls_cycle_s"])
            optional_keys = ("green_ped_s",)
            sum_keys = required_keys + tuple(k for k in optional_keys if k in program)
            program_sum = sum(float(program[k]) for k in sum_keys)
            self.assertAlmostEqual(program_sum, cycle, delta=0.5, msg=inter["id"])
            # g/C for the main approach should fall in the [0.45, 0.70] window — HCM-credible
            # for an urban arterial with light-to-moderate cross-traffic. When an
            # exclusive pedestrian phase is configured the floor drops to 0.30 because
            # the ped phase deliberately trades main green for pedestrian protection.
            g_over_c_main = float(program["green_main_s"]) / cycle
            g_min = 0.30 if float(program.get("green_ped_s", 0)) > 0 else 0.45
            self.assertGreaterEqual(g_over_c_main, g_min, msg=inter["id"])
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
        base_roles = [
            "main_green",
            "main_yellow",
            "all_red_main_to_cross",
            "cross_green",
            "cross_yellow",
            "all_red_cross_to_main",
        ]
        intersections_by_id = {inter["id"]: inter for inter in cfg["network"]["intersections"]}
        for tls_id, elem in tls_elements.items():
            phase_roles = [p.attrib["role"] for p in elem.findall("phase")]
            program = intersections_by_id[tls_id].get("tls_program", {})
            expected = list(base_roles)
            if float(program.get("green_ped_s", 0)) > 0:
                expected.append("pedestrian")
            self.assertEqual(phase_roles, expected, msg=tls_id)

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
            "bs_casa_musica_w",
            "bs_casa_musica_e",
            "bs_serralves_w",
            "bs_serralves_e",
            "bs_praca_imperio_w",
            "bs_praca_imperio_e",
            "bs_castelo_queijo_w",
            "bs_castelo_queijo_e",
        ):
            self.assertIn(expected_bay, bay_stops)
        for expected_curbside in (
            "bs_bessa_w",
            "bs_bessa_e",
            "bs_antunes_guimaraes_w",
            "bs_antunes_guimaraes_e",
            "bs_marechal_w",
            "bs_marechal_e",
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
        for match in re.finditer(r"<stop\s+([^/]+)/>", routes_xml):
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
            "bs_casa_musica_w",
            "bs_casa_musica_e",
            "bs_serralves_w",
            "bs_serralves_e",
            "bs_praca_imperio_w",
            "bs_praca_imperio_e",
            "bs_castelo_queijo_w",
            "bs_castelo_queijo_e",
        ):
            self.assertIn(expected_bay, bay_stop_ids)
            self.assertNotIn(expected_bay, curbside_stop_ids)
        for expected_curbside in (
            "bs_bessa_w",
            "bs_bessa_e",
            "bs_antunes_guimaraes_w",
            "bs_antunes_guimaraes_e",
            "bs_marechal_w",
            "bs_marechal_e",
        ):
            self.assertIn(expected_curbside, curbside_stop_ids)
            self.assertNotIn(expected_curbside, bay_stop_ids)

    def test_main_flows_use_smooth_multi_step_ramp(self) -> None:
        am_peak = self.base["demand_profiles"]["am_peak"]
        inbound = next(
            f for f in am_peak["flows"] if f["id"] == "flow_car_inbound_west_to_east_am_peak"
        )
        outbound = next(
            f for f in am_peak["flows"] if f["id"] == "flow_car_outbound_east_to_west_am_peak"
        )
        for flow in (inbound, outbound):
            tp = flow["time_profile"]
            # At least 7 segments — replaces the previous 3-step coarse profile so demand ramp is
            # closer to a smooth pickup/decay shape rather than discrete jumps.
            self.assertGreaterEqual(len(tp), 7)
            # Time profile must cover the full 0–7200 window contiguously.
            self.assertEqual(float(tp[0]["begin"]), 0.0)
            self.assertEqual(float(tp[-1]["end"]), 7200.0)
            for prev, curr in zip(tp, tp[1:], strict=False):
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
            self.assertEqual(kpis["bus_lines"]["STCP500"]["vehicles"], 2)
            self.assertEqual(kpis["bus_headways"]["STCP500:W"]["mean_headway_s"], 600)
            self.assertEqual(kpis["general_traffic"]["vehicles"], 1)

    def test_tripinfo_parser_reports_ingolstadt_bus_line_kpis(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tripinfo.xml"
            path.write_text(
                "<tripinfos>"
                '<tripinfo id="Bus_11_0001" vType="bus" line="11" depart="0" arrival="100" duration="100" routeLength="1000" waitingTime="10" timeLoss="20" waitingCount="2" />'
                '<tripinfo id="Bus_11_0002" vType="bus" line="11" depart="600" arrival="720" duration="120" routeLength="1000" waitingTime="15" timeLoss="30" waitingCount="3" />'
                '<tripinfo id="car_1" vType="car" depart="0" arrival="80" duration="80" routeLength="900" waitingTime="5" timeLoss="8" waitingCount="1" />'
                "</tripinfos>",
                encoding="utf-8",
            )
            kpis = parse_tripinfo(path)
            self.assertEqual(kpis["buses"]["vehicles"], 2)
            self.assertEqual(kpis["general_traffic"]["vehicles"], 1)
            self.assertEqual(kpis["bus_lines"]["11"]["vehicles"], 2)
            self.assertEqual(kpis["bus_lines"]["11"]["mean_time_loss_s"], 25.0)
            self.assertEqual(kpis["bus_headways"]["11"]["mean_headway_s"], 600)

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

    def test_tripinfo_parser_does_not_classify_scenario_name_bus_as_bus(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tripinfo.xml"
            path.write_text(
                "<tripinfos>"
                '<tripinfo id="flow_car_inbound_west_to_east_am_operational_delayed_bus_w.0" vType="car" depart="0" duration="10" routeLength="100" waitingTime="1" timeLoss="2" />'
                '<tripinfo id="flow_car_outbound_east_to_west_am_operational_bunched_buses.0" vType="car" depart="0" duration="10" routeLength="100" waitingTime="1" timeLoss="2" />'
                '<tripinfo id="bus_STCP500_W_0000" vType="bus_12m" depart="0" duration="20" routeLength="200" waitingTime="2" timeLoss="3" />'
                "</tripinfos>",
                encoding="utf-8",
            )
            kpis = parse_tripinfo(path)
            self.assertEqual(kpis["buses"]["vehicles"], 1)
            self.assertEqual(kpis["general_traffic"]["vehicles"], 2)

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
                '<vehicle id="Bus_11_0001" type="bus" CO2="250" fuel="90" NOx="10" />'
                "</timestep>"
                '<timestep time="60">'
                '<vehicle id="car_1" type="car" CO2="700" fuel="280" NOx="35" />'
                '<vehicle id="bus_STCP500_W_0000" type="bus_12m" CO2="3500" fuel="1260" NOx="140" />'
                '<vehicle id="Bus_11_0001" type="bus" CO2="750" fuel="270" NOx="30" />'
                "</timestep>"
                "</emission-export>",
                encoding="utf-8",
            )
            kpis = parse_emissions(path)
            self.assertTrue(kpis["available"])
            self.assertEqual(kpis["vehicle_count"], 3)
            # Valores SUMO de emission-output são POR-STEP (não cumulativos):
            # o total de cada veículo é a soma dos seus steps.
            self.assertEqual(kpis["totals_mg"]["CO2"], 5800.0)
            self.assertEqual(kpis["totals_mg"]["fuel"], 2120.0)
            self.assertEqual(kpis["bus_count"], 2)
            self.assertEqual(kpis["bus_totals_mg"]["CO2"], 5000.0)

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
            self.assertIn("car_acc_ev", routes)
            self.assertIn("car_cacc_ev", routes)
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
            self.assertIn('allow="bus emergency taxi"', edges)
            # I6 roundabout emits a circulating ring and routes through it.
            self.assertIn("RB_I6_CITY_TO_NORTH", edges)
            self.assertIn("RB_I6_NORTH_TO_ATLANTIC", routes)
            # Elevation z attribute present on nodes
            self.assertIn('z="95.00"', nodes)
            # Edge width attribute present
            self.assertIn("width=", edges)

    def test_apply_tls_offsets_modifies_net_xml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            net = tmp_path / "corredor.net.xml"
            net.write_text(
                '<?xml version="1.0"?>'
                "<net>"
                '<tlLogic id="I2" programID="0" offset="0" type="static"><phase duration="30" state="G"/></tlLogic>'
                '<tlLogic id="I3" programID="0" offset="0" type="static"><phase duration="30" state="G"/></tlLogic>'
                "</net>",
                encoding="utf-8",
            )
            overrides = tmp_path / "tls.add.xml"
            overrides.write_text(
                "<tlsOffsetOverrides>"
                '<tls id="I2" offset_s="42"/>'
                '<tls id="I3" offset_s="13"/>'
                "</tlsOffsetOverrides>",
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
            "<net>"
            '<tlLogic id="I2" programID="0" offset="0" type="static">'
            '<phase duration="42" state="rrrGGGgrrrGGGg"/>'
            '<phase duration="3"  state="rrryyyyrrryyyy"/>'
            '<phase duration="42" state="GGgrrrrGGgrrrr"/>'
            '<phase duration="3"  state="yyyrrrryyyrrrr"/>'
            "</tlLogic>"
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
            "</net>",
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
                "<tlsOffsetOverrides>"
                '<tls id="I2" offset_s="38">'
                '<phase role="main_green" duration_s="52"/>'
                '<phase role="main_yellow" duration_s="3"/>'
                '<phase role="cross_green" duration_s="32"/>'
                '<phase role="cross_yellow" duration_s="3"/>'
                "</tls>"
                "</tlsOffsetOverrides>",
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
                "<tlsOffsetOverrides>"
                '<tls id="I2" offset_s="38">'
                '<phase role="main_green" duration_s="51"/>'
                '<phase role="main_yellow" duration_s="3"/>'
                '<phase role="all_red_main_to_cross" duration_s="1"/>'
                '<phase role="cross_green" duration_s="31"/>'
                '<phase role="cross_yellow" duration_s="3"/>'
                '<phase role="all_red_cross_to_main" duration_s="1"/>'
                "</tls>"
                "</tlsOffsetOverrides>",
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
                "<tlsOffsetOverrides>"
                '<tls id="I2" offset_s="38">'
                '<phase role="main_green" duration_s="51"/>'
                '<phase role="main_yellow" duration_s="3"/>'
                '<phase role="all_red_main_to_cross" duration_s="1"/>'
                '<phase role="cross_green" duration_s="31"/>'
                '<phase role="cross_yellow" duration_s="3"/>'
                '<phase role="all_red_cross_to_main" duration_s="1"/>'
                "</tls>"
                "</tlsOffsetOverrides>",
                encoding="utf-8",
            )
            apply_tls_offsets(net, overrides)
            apply_tls_offsets(net, overrides)
            from xml.etree import ElementTree as ET

            tl = ET.parse(net).getroot().find("tlLogic")
            assert tl is not None
            phases = tl.findall("phase")
            self.assertEqual(len(phases), 6, msg="all-red phases were duplicated on second run")
            self.assertEqual(
                [float(p.attrib["duration"]) for p in phases], [51.0, 3.0, 1.0, 31.0, 3.0, 1.0]
            )

    def _write_synthetic_i2_net_with_crossings(self, net_path: Path) -> None:
        """Synthetic I2 with 14 vehicle linkIndices + 4 pedestrian crossing slots.

        Mirrors the netconvert layout produced under ``--crossings.guess``:
        a pair of vehicle-green phases (with concurrent crossings G's at the
        trailing indices), each followed by a flashing-don't-walk sub-phase
        that clears the crossings, then yellow + all-red. Pedestrian indices
        14-17 ride internal walking-area/crossing edges (``:I2_wN`` /
        ``:I2_cN``).
        """
        net_path.write_text(
            '<?xml version="1.0"?>'
            "<net>"
            '<tlLogic id="I2" programID="0" offset="0" type="static">'
            '<phase duration="51" state="rrrGGGgrrrGGGgGrGr"/>'
            '<phase duration="5"  state="rrrGGGgrrrGGGgrrrr"/>'
            '<phase duration="3"  state="rrryyyyrrryyyyrrrr"/>'
            '<phase duration="1"  state="rrrrrrrrrrrrrrrrrr"/>'
            '<phase duration="31" state="GGgrrrrGGgrrrrrGrG"/>'
            '<phase duration="5"  state="GGgrrrrGGgrrrrrrrr"/>'
            '<phase duration="3"  state="yyyrrrryyyrrrrrrrr"/>'
            '<phase duration="1"  state="rrrrrrrrrrrrrrrrrr"/>'
            "</tlLogic>"
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
            '<connection from=":I2_w0" to=":I2_c0" tl="I2" linkIndex="14"/>'
            '<connection from=":I2_w1" to=":I2_c1" tl="I2" linkIndex="15"/>'
            '<connection from=":I2_w2" to=":I2_c2" tl="I2" linkIndex="16"/>'
            '<connection from=":I2_w3" to=":I2_c3" tl="I2" linkIndex="17"/>'
            "</net>",
            encoding="utf-8",
        )

    def test_apply_tls_offsets_inserts_exclusive_pedestrian_phase(self) -> None:
        """Seven-role override inserts an exclusive ped phase, strips the FDW,
        disables concurrent crossings inside the surviving vehicle greens, and
        keeps every vehicle turn ('g' permissive) untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            net = tmp_path / "corredor.net.xml"
            self._write_synthetic_i2_net_with_crossings(net)
            overrides = tmp_path / "tls.add.xml"
            overrides.write_text(
                "<tlsOffsetOverrides>"
                '<tls id="I2" offset_s="0">'
                '<phase role="main_green" duration_s="31"/>'
                '<phase role="main_yellow" duration_s="3"/>'
                '<phase role="all_red_main_to_cross" duration_s="1"/>'
                '<phase role="cross_green" duration_s="39"/>'
                '<phase role="cross_yellow" duration_s="3"/>'
                '<phase role="all_red_cross_to_main" duration_s="1"/>'
                '<phase role="pedestrian" duration_s="12"/>'
                "</tls>"
                "</tlsOffsetOverrides>",
                encoding="utf-8",
            )
            apply_tls_offsets(net, overrides)
            from xml.etree import ElementTree as ET

            tl = ET.parse(net).getroot().find("tlLogic")
            assert tl is not None
            phases = tl.findall("phase")
            # FDW phases stripped → 8 original phases collapse to 6 vehicle phases + 1 ped phase.
            self.assertEqual(
                len(phases),
                7,
                msg="expected main_g, main_y, all_red, cross_g, cross_y, all_red, ped",
            )
            # Cycle sum exact.
            self.assertAlmostEqual(sum(float(p.attrib["duration"]) for p in phases), 90.0, places=3)
            # Last phase is the exclusive ped phase: G on indices 14-17, r on 0-13.
            ped_phase = phases[-1]
            self.assertEqual(float(ped_phase.attrib["duration"]), 12.0)
            self.assertEqual(ped_phase.attrib["state"], "r" * 14 + "G" * 4)
            # Surviving main_green keeps protected G's on through main links AND the
            # permissive lowercase g's on turns, but the crossing indices flipped to r.
            self.assertEqual(phases[0].attrib["state"], "rrrGGGgrrrGGGgrrrr")
            self.assertEqual(phases[3].attrib["state"], "GGgrrrrGGgrrrrrrrr")

    def test_apply_tls_offsets_pedestrian_insertion_is_idempotent(self) -> None:
        """Re-running the seven-role override does not duplicate ped or all-red phases."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            net = tmp_path / "corredor.net.xml"
            self._write_synthetic_i2_net_with_crossings(net)
            overrides = tmp_path / "tls.add.xml"
            overrides.write_text(
                "<tlsOffsetOverrides>"
                '<tls id="I2" offset_s="0">'
                '<phase role="main_green" duration_s="31"/>'
                '<phase role="main_yellow" duration_s="3"/>'
                '<phase role="all_red_main_to_cross" duration_s="1"/>'
                '<phase role="cross_green" duration_s="39"/>'
                '<phase role="cross_yellow" duration_s="3"/>'
                '<phase role="all_red_cross_to_main" duration_s="1"/>'
                '<phase role="pedestrian" duration_s="12"/>'
                "</tls>"
                "</tlsOffsetOverrides>",
                encoding="utf-8",
            )
            apply_tls_offsets(net, overrides)
            apply_tls_offsets(net, overrides)
            apply_tls_offsets(net, overrides)
            from xml.etree import ElementTree as ET

            tl = ET.parse(net).getroot().find("tlLogic")
            assert tl is not None
            phases = tl.findall("phase")
            self.assertEqual(len(phases), 7, msg="ped/all-red phases were duplicated across runs")
            self.assertEqual(
                [float(p.attrib["duration"]) for p in phases],
                [31.0, 3.0, 1.0, 39.0, 3.0, 1.0, 12.0],
            )

    def _write_tiny_synthetic_net(self, net_path: Path, *, phases_xml: str) -> None:
        """Six-link synthetic net (2 main + 2 cross + 2 ped) used by the FDW-strip
        robustness tests. Callers supply the ``<phase>`` block so each test can
        provoke a specific netconvert-output shape."""
        net_path.write_text(
            '<?xml version="1.0"?>'
            "<net>"
            '<tlLogic id="I9" programID="0" offset="0" type="static">'
            f"{phases_xml}"
            "</tlLogic>"
            '<connection from="I1_I9" to="I9_I2" tl="I9" linkIndex="0"/>'
            '<connection from="I1_I9" to="I9_I2" tl="I9" linkIndex="1"/>'
            '<connection from="N_I9_I9" to="I9_S_I9" tl="I9" linkIndex="2"/>'
            '<connection from="N_I9_I9" to="I9_S_I9" tl="I9" linkIndex="3"/>'
            '<connection from=":I9_w0" to=":I9_c0" tl="I9" linkIndex="4"/>'
            '<connection from=":I9_w1" to=":I9_c1" tl="I9" linkIndex="5"/>'
            "</net>",
            encoding="utf-8",
        )

    _PED_PHASE_OVERRIDE_I9 = (
        "<tlsOffsetOverrides>"
        '<tls id="I9" offset_s="0">'
        '<phase role="main_green" duration_s="31"/>'
        '<phase role="main_yellow" duration_s="3"/>'
        '<phase role="all_red_main_to_cross" duration_s="1"/>'
        '<phase role="cross_green" duration_s="39"/>'
        '<phase role="cross_yellow" duration_s="3"/>'
        '<phase role="all_red_cross_to_main" duration_s="1"/>'
        '<phase role="pedestrian" duration_s="12"/>'
        "</tls>"
        "</tlsOffsetOverrides>"
    )

    def test_fdw_strip_accepts_genuine_clearance_pattern(self) -> None:
        """The classic netconvert shape (second vehicle-green identical except for
        peds flipping G→r) is recognised as a FDW sub-phase and stripped."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            net = tmp_path / "corredor.net.xml"
            self._write_tiny_synthetic_net(
                net,
                phases_xml=(
                    '<phase duration="20" state="GGrrGG"/>'  # main_green WITH peds
                    '<phase duration="5"  state="GGrrrr"/>'  # FDW clearance (peds G→r)
                    '<phase duration="3"  state="yyrrrr"/>'  # main_yellow
                    '<phase duration="20" state="rrGGGG"/>'  # cross_green WITH peds
                    '<phase duration="5"  state="rrGGrr"/>'  # FDW clearance
                    '<phase duration="3"  state="rryyrr"/>'  # cross_yellow
                ),
            )
            overrides = tmp_path / "tls.add.xml"
            overrides.write_text(self._PED_PHASE_OVERRIDE_I9, encoding="utf-8")
            apply_tls_offsets(net, overrides)
            from xml.etree import ElementTree as ET

            tl = ET.parse(net).getroot().find("tlLogic")
            assert tl is not None
            phases = tl.findall("phase")
            self.assertEqual(
                len(phases), 7, msg="expected FDW phases stripped + ped phase appended"
            )
            # Last phase is exclusive ped: r on vehicle indices 0-3, G on ped 4-5.
            self.assertEqual(phases[-1].attrib["state"], "rrrrGG")

    def test_fdw_strip_rejects_vehicle_state_mismatch(self) -> None:
        """If two main_green phases disagree on a vehicle index, the second is NOT
        a FDW sub-phase. The strip function must leave it intact AND raise on
        the post-assertion (two main_greens remain) so we never silently drop
        a vehicle program the user actually intended."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            net = tmp_path / "corredor.net.xml"
            self._write_tiny_synthetic_net(
                net,
                phases_xml=(
                    '<phase duration="20" state="GGrrGG"/>'  # main_green
                    # Same classification (G's on main, no cross G's) but vehicle
                    # state DIFFERS (only one main link green this time).
                    '<phase duration="5"  state="GrrrGG"/>'
                    '<phase duration="3"  state="yyrrrr"/>'
                    '<phase duration="20" state="rrGGGG"/>'
                    '<phase duration="5"  state="rrGGrr"/>'
                    '<phase duration="3"  state="rryyrr"/>'
                ),
            )
            overrides = tmp_path / "tls.add.xml"
            overrides.write_text(self._PED_PHASE_OVERRIDE_I9, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "expected exactly one 'main_green'"):
                apply_tls_offsets(net, overrides)

    def test_fdw_strip_rejects_ped_promotion_pattern(self) -> None:
        """A second main_green that *adds* G on a ped index (r→G) is a promotion,
        not a clearance. The strip function must refuse to drop it and the
        post-assertion fails because two main_greens survive."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            net = tmp_path / "corredor.net.xml"
            self._write_tiny_synthetic_net(
                net,
                phases_xml=(
                    '<phase duration="20" state="GGrrrr"/>'  # main_green, peds dark
                    # Same vehicle state but peds go r→G — a promotion, not FDW.
                    '<phase duration="5"  state="GGrrGG"/>'
                    '<phase duration="3"  state="yyrrrr"/>'
                    '<phase duration="20" state="rrGGGG"/>'
                    '<phase duration="5"  state="rrGGrr"/>'
                    '<phase duration="3"  state="rryyrr"/>'
                ),
            )
            overrides = tmp_path / "tls.add.xml"
            overrides.write_text(self._PED_PHASE_OVERRIDE_I9, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "expected exactly one 'main_green'"):
                apply_tls_offsets(net, overrides)

    def test_build_tls_offsets_emits_pedestrian_phase_and_preserves_cycle_sum(self) -> None:
        """Every signalized intersection on the corridor is configured with an
        exclusive pedestrian (Barnes Dance) phase so netconvert does not
        auto-insert a 5s clearance phase that would silently inflate the cycle
        from 90s to 100s and break corridor coordination."""
        cfg = json.loads((ROOT / "configs" / "sumo_scenario_base.json").read_text(encoding="utf-8"))
        # I1-I5 and I7 all carry a green_ped_s; I6 is the Praca do Imperio roundabout.
        ped_ids = {"I1", "I2", "I3", "I4", "I5", "I7"}
        overrides_root = build_tls_offsets(cfg)
        self.assertIsNotNone(overrides_root)
        assert overrides_root is not None
        tls_elements = {elem.attrib["id"]: elem for elem in overrides_root.findall("tls")}
        for tls_id, elem in tls_elements.items():
            phases = elem.findall("phase")
            roles = [p.attrib["role"] for p in phases]
            durations = [float(p.attrib["duration_s"]) for p in phases]
            inter = next(i for i in cfg["network"]["intersections"] if i["id"] == tls_id)
            cycle = float(inter["tls_cycle_s"])
            self.assertAlmostEqual(sum(durations), cycle, delta=0.5, msg=tls_id)
            if tls_id in ped_ids:
                self.assertIn("pedestrian", roles, msg=tls_id)
                ped_idx = roles.index("pedestrian")
                # Pedestrian must be the LAST role (end of cycle, after all_red_cross_to_main).
                self.assertEqual(ped_idx, len(roles) - 1, msg=tls_id)
                self.assertEqual(durations[ped_idx], 12.0, msg=tls_id)
            else:
                self.assertNotIn("pedestrian", roles, msg=tls_id)

    def test_classify_phase_role_recognises_pedestrian_and_all_red(self) -> None:
        from pps57_sumo.apply_tls_offsets import _classify_phase_role

        main_links = {3, 4, 5, 6, 10, 11, 12, 13}
        cross_links = {0, 1, 2, 7, 8, 9}
        self.assertEqual(
            _classify_phase_role("rrrGGGgrrrGGGg", main_links, cross_links), "main_green"
        )
        self.assertEqual(
            _classify_phase_role("GGgrrrrGGgrrrr", main_links, cross_links), "cross_green"
        )
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

    def test_run_verdict_fails_sumo_health_gates(self) -> None:
        kpis = {
            "all_vehicles": {"vehicles": 100},
            "buses": {"vehicles": 5},
            "scenario": {
                "sumo_quality_thresholds": {
                    "max_teleports_total": 0,
                    "max_emergency_braking": 2,
                    "max_vehicles_waiting_at_end": 0,
                    "max_insertion_gap_at_end": 0,
                    "max_backlog_step_ratio": 0.10,
                }
            },
            "insertion": {
                "steps": 100,
                "backlog_step_count": 20,
                "max_waiting_to_insert": 151,
                "vehicles_waiting": 1,
                "insertion_gap_at_end": 1,
                "teleports_total": 1,
                "emergency_braking": 3,
                "collisions": 0,
            },
        }
        verdict = run_verdict(kpis)
        self.assertEqual(verdict["status"], "fail")
        self.assertIn("sumo_teleports_gt_threshold", verdict["reasons"])
        self.assertIn("sumo_emergency_braking_gt_threshold", verdict["reasons"])
        self.assertIn("sumo_waiting_to_insert_at_end_gt_threshold", verdict["reasons"])
        self.assertIn("sumo_insertion_gap_at_end_gt_threshold", verdict["reasons"])
        self.assertIn("sumo_max_waiting_to_insert_gt_threshold", verdict["reasons"])
        self.assertIn("sumo_backlog_step_ratio_gt_threshold", verdict["reasons"])

    def test_run_verdict_keeps_short_smoke_inconclusive_when_buses_do_not_finish(self) -> None:
        kpis = {
            "all_vehicles": {"vehicles": 50},
            "buses": {"vehicles": 0},
            "scenario": {"max_steps": 600},
            "insertion": {
                "steps": 600,
                "backlog_step_count": 0,
                "vehicles_waiting": 0,
                "insertion_gap_at_end": 0,
                "teleports_total": 0,
                "emergency_braking": 0,
                "collisions": 0,
            },
        }
        verdict = run_verdict(kpis)
        self.assertEqual(verdict["status"], "inconclusive")
        self.assertEqual(verdict["reasons"], ["no_completed_buses_in_short_smoke_run"])

    def test_run_verdict_skips_rate_gates_until_minimum_completed_vehicle_sample(self) -> None:
        kpis = {
            "all_vehicles": {"vehicles": 52},
            "buses": {"vehicles": 1},
            "scenario": {
                "max_steps": 600,
                "sumo_quality_thresholds": {
                    "max_emergency_braking": 150,
                    "max_emergency_braking_per_1000_vehicles": 30,
                    "min_completed_vehicles_for_rate_gates": 500,
                },
            },
            "insertion": {
                "steps": 600,
                "backlog_step_count": 0,
                "vehicles_waiting": 0,
                "insertion_gap_at_end": 0,
                "teleports_total": 0,
                "emergency_braking": 2,
                "collisions": 0,
            },
        }
        self.assertEqual(run_verdict(kpis), {"status": "pass", "reasons": []})

    def test_steps_convert_to_effective_end_seconds(self) -> None:
        base = json.loads((ROOT / "configs/sumo_scenario_base.json").read_text(encoding="utf-8"))
        cfg = apply_scenario_profile(base, "baseline_am_peak")
        self.assertEqual(_effective_end_s(cfg, 600), 300.0)
        self.assertEqual(_effective_end_s(cfg, None), 7200.0)

    def test_parse_insertion_reads_sumo_safety_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            summary = tmp_path / "summary.xml"
            statistics = tmp_path / "statistics.xml"
            summary.write_text(
                '<summary><step time="0.0" loaded="2" inserted="1" running="1" waiting="1"/></summary>',
                encoding="utf-8",
            )
            statistics.write_text(
                (
                    "<statistics>"
                    '<vehicles loaded="2" inserted="1" running="1" waiting="1"/>'
                    '<teleports total="1" jam="1" yield="0" wrongLane="0"/>'
                    '<safety collisions="0" emergencyStops="0" emergencyBraking="3"/>'
                    "</statistics>"
                ),
                encoding="utf-8",
            )
            parsed = parse_insertion_kpis(summary, statistics)
        self.assertEqual(parsed["vehicles_waiting"], 1)
        self.assertEqual(parsed["teleports_total"], 1)
        self.assertEqual(parsed["collisions"], 0)
        self.assertEqual(parsed["emergency_braking"], 3)

    def test_shared_build_command_uses_realism_flags(self) -> None:
        base = json.loads((ROOT / "configs/sumo_scenario_base.json").read_text(encoding="utf-8"))
        cfg = apply_scenario_profile(base, "baseline_am_peak")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            calls: list[list[str]] = []

            def fake_runner(cmd, cwd):
                calls.append(list(cmd))
                net = tmp_path / "sumo/network/corredor.net.xml"
                net.parent.mkdir(parents=True, exist_ok=True)
                net.write_text("<net/>", encoding="utf-8")

            artifacts = build_sumo_artifacts(
                cfg,
                root=tmp_path,
                base_dir=Path("sumo"),
                build_net=True,
                runner=fake_runner,
            )
            self.assertEqual(artifacts.sumocfg_file, tmp_path / "sumo/corredor.sumocfg")
            self.assertEqual(len(calls), 1)
            cmd = calls[0]
            self.assertIn("--sidewalks.guess", cmd)
            self.assertIn("--crossings.guess", cmd)
            self.assertIn("--walkingareas", cmd)
            self.assertIn(str(tmp_path / "sumo/network/corredor.net.xml"), cmd)

    def test_per_run_sumocfg_points_to_isolated_artifacts(self) -> None:
        base = json.loads((ROOT / "configs/sumo_scenario_base.json").read_text(encoding="utf-8"))
        cfg = apply_scenario_profile(base, "baseline_am_peak")
        cfg.setdefault("detectors", {})
        cfg["detectors"]["e1_output"] = "../../e1_detectors.xml"
        cfg["detectors"]["e2_output"] = "../../e2_queues.xml"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            run_dir = tmp_path / "outputs/scenarios/baseline/seed_57"
            artifacts = build_sumo_artifacts(
                cfg,
                root=tmp_path,
                base_dir=run_dir / "sumo",
                output_dir=run_dir,
                build_net=False,
            )
            sumocfg = artifacts.sumocfg_file.read_text(encoding="utf-8")
            detectors = artifacts.detectors_file.read_text(encoding="utf-8")
            self.assertIn('net-file value="network/corredor.net.xml"', sumocfg)
            self.assertIn('route-files value="routes/routes.rou.xml"', sumocfg)
            self.assertIn('tripinfo-output value="../tripinfo.xml"', sumocfg)
            self.assertIn('file="../../e1_detectors.xml"', detectors)


class RoundaboutInternalPathTestCase(unittest.TestCase):
    """The ring internal path must be derived from the corridor's actual topology."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.base = json.loads(
            (ROOT / "configs/sumo_scenario_base.json").read_text(encoding="utf-8")
        )

    def test_base_config_ring_expansion_unchanged(self) -> None:
        # Regression: the committed config (ring at I6, neighbours I5/I7) must keep
        # producing exactly the internal paths the previous generator emitted.
        from pps57_sumo.generate_plain_corridor import build_routes

        intersections = self.base["network"]["intersections"]
        terminals = {t["id"]: t for t in self.base["network"]["terminals"]}
        routes = build_routes(self.base, intersections, terminals)
        self.assertEqual(
            routes["route_boavista_east_to_west"][5:9],
            ["I5_I6", "RB_I6_CITY_TO_NORTH", "RB_I6_NORTH_TO_ATLANTIC", "I6_I7"],
        )
        self.assertEqual(
            routes["route_boavista_west_to_east"][1:5],
            ["I7_I6", "RB_I6_ATLANTIC_TO_SOUTH", "RB_I6_SOUTH_TO_CITY", "I6_I5"],
        )
        self.assertEqual(
            routes["route_cross_NS_I6"],
            ["N_I6_I6", "RB_I6_NORTH_TO_ATLANTIC", "RB_I6_ATLANTIC_TO_SOUTH", "I6_S_I6"],
        )

    def test_neighbours_derived_from_corridor_order_not_hardcoded_ids(self) -> None:
        from pps57_sumo.generate_plain_corridor import (
            _expand_roundabout_routes,
            _roundabout_corridor_neighbours,
        )

        intersections = [{"id": "A"}, {"id": "B", "roundabout_model": "ring"}, {"id": "C"}]
        terminals = {
            "CITY_EAST": {"id": "CITY_EAST"},
            "ATLANTIC_WEST": {"id": "ATLANTIC_WEST"},
        }
        self.assertEqual(
            _roundabout_corridor_neighbours(intersections, terminals),
            {"B": {"CITY": "A", "ATLANTIC": "C"}},
        )
        expanded = _expand_roundabout_routes({"r": ["A_B", "B_C"]}, intersections, terminals)
        self.assertEqual(
            expanded["r"], ["A_B", "RB_B_CITY_TO_NORTH", "RB_B_NORTH_TO_ATLANTIC", "B_C"]
        )

    def test_ring_at_corridor_ends_uses_terminals_as_neighbours(self) -> None:
        from pps57_sumo.generate_plain_corridor import _roundabout_corridor_neighbours

        terminals = {
            "CITY_EAST": {"id": "CITY_EAST"},
            "ATLANTIC_WEST": {"id": "ATLANTIC_WEST"},
        }
        first_ring = [{"id": "A", "roundabout_model": "ring"}, {"id": "B"}]
        last_ring = [{"id": "A"}, {"id": "B", "roundabout_model": "ring"}]
        self.assertEqual(
            _roundabout_corridor_neighbours(first_ring, terminals),
            {"A": {"CITY": "CITY_EAST", "ATLANTIC": "B"}},
        )
        self.assertEqual(
            _roundabout_corridor_neighbours(last_ring, terminals),
            {"B": {"CITY": "A", "ATLANTIC": "ATLANTIC_WEST"}},
        )

    def test_underivable_internal_path_fails_loudly(self) -> None:
        from pps57_sumo.generate_plain_corridor import (
            _expand_roundabout_routes,
            _roundabout_internal_path,
        )

        with self.assertRaises(ValueError):
            _roundabout_internal_path(
                "B", "A_B", "X_Y", city_neighbour="A", atlantic_neighbour="C", route_id="broken"
            )
        intersections = [{"id": "A"}, {"id": "B", "roundabout_model": "ring"}, {"id": "C"}]
        terminals = {
            "CITY_EAST": {"id": "CITY_EAST"},
            "ATLANTIC_WEST": {"id": "ATLANTIC_WEST"},
        }
        with self.assertRaises(ValueError):
            _expand_roundabout_routes({"broken": ["A_B", "X_Y"]}, intersections, terminals)


if __name__ == "__main__":
    unittest.main()
