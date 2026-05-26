#!/usr/bin/env python3
"""Generate the PPS57 SUMO scenario from one declarative corridor config.

The scenario remains a controlled SUMO model, not an automatic OSM import. The
config anchors geometry, public-transport services, demand and detector layout in
one place so the baseline can be calibrated without hand-editing XML artifacts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, List
from xml.dom import minidom
from xml.etree import ElementTree as ET

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.scenarios import apply_scenario_profile


def _pretty_xml(element: ET.Element) -> str:
    rough = ET.tostring(element, encoding="utf-8")
    parsed = minidom.parseString(rough)
    return parsed.toprettyxml(indent="  ")


def generate(
    config: dict,
    output_dir: Path,
    *,
    routes_output: Path,
    bus_stops_output: Path,
    detectors_output: Path,
    parking_output: Path | None = None,
    pedestrians_output: Path | None = None,
    calibrators_output: Path | None = None,
    tls_offsets_output: Path | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    routes_output.parent.mkdir(parents=True, exist_ok=True)
    bus_stops_output.parent.mkdir(parents=True, exist_ok=True)
    detectors_output.parent.mkdir(parents=True, exist_ok=True)
    for opt_path in (parking_output, pedestrians_output, calibrators_output, tls_offsets_output):
        if opt_path is not None:
            opt_path.parent.mkdir(parents=True, exist_ok=True)

    nodes = ET.Element("nodes")
    edges = ET.Element("edges")
    node_xy: Dict[str, tuple[float, float]] = {}
    node_z: Dict[str, float] = {}
    edge_defs: Dict[str, Dict[str, Any]] = {}
    edge_lane_overrides: Dict[str, list[dict[str, Any]]] = {}

    network = config["network"]
    edge_widths = network.get("edge_widths", []) or []

    def _resolve_edge_width(edge_id: str) -> float | None:
        for rule in edge_widths:
            prefix = str(rule.get("edge_id_prefix", ""))
            if prefix and edge_id.startswith(prefix):
                return float(rule.get("width_m", 0)) or None
        return None

    for rule in network.get("lane_allow_rules", []) or []:
        edge_lane_overrides.setdefault(str(rule["edge_id"]), []).append(rule)

    def add_node(node_id: str, x: float, y: float, node_type: str = "priority", z: float | None = None) -> None:
        node_xy[node_id] = (float(x), float(y))
        attrs = {"id": node_id, "x": f"{x:.2f}", "y": f"{y:.2f}", "type": node_type}
        if z is not None:
            attrs["z"] = f"{float(z):.2f}"
            node_z[node_id] = float(z)
        ET.SubElement(nodes, "node", attrs)

    def add_edge(edge_id: str, src: str, dst: str, lanes: int, speed: float, priority: int) -> None:
        attrs = {
            "id": edge_id,
            "from": src,
            "to": dst,
            "numLanes": str(int(lanes)),
            "speed": f"{float(speed):.2f}",
            "priority": str(int(priority)),
        }
        width = _resolve_edge_width(edge_id)
        if width is not None:
            attrs["width"] = f"{width:.2f}"
        edge_elem = ET.SubElement(edges, "edge", attrs)
        for rule in edge_lane_overrides.get(edge_id, []):
            lane_attrs = {"index": str(int(rule.get("lane_index", 0)))}
            if "allow" in rule:
                lane_attrs["allow"] = str(rule["allow"])
            if "disallow" in rule:
                lane_attrs["disallow"] = str(rule["disallow"])
            if "speed" in rule:
                lane_attrs["speed"] = f"{float(rule['speed']):.2f}"
            ET.SubElement(edge_elem, "lane", lane_attrs)
        edge_defs[edge_id] = attrs

    intersections = network["intersections"]
    terminals = {item["id"]: item for item in network["terminals"]}
    major_speed = float(network.get("default_major_speed_mps", config.get("default_major_speed_mps", 13.89)))
    minor_speed = float(network.get("default_minor_speed_mps", config.get("default_minor_speed_mps", 8.33)))
    major_priority = int(network.get("major_priority", 4))
    minor_priority = int(network.get("minor_priority", 2))
    approach_length_m = float(network.get("minor_approach_length_m", 420.0))
    terminal_z = network.get("terminal_z_m", {}) or {}
    approach_z_drop = float(network.get("minor_approach_z_drop_m", 0.0))

    for terminal in network["terminals"]:
        z_val = terminal_z.get(terminal["id"], terminal.get("z"))
        add_node(
            terminal["id"],
            float(terminal["x"]),
            float(terminal["y"]),
            terminal.get("type", "priority"),
            z=float(z_val) if z_val is not None else None,
        )

    for inter in intersections:
        z_val = inter.get("z")
        add_node(
            inter["id"],
            float(inter["x"]),
            float(inter["y"]),
            inter.get("type", "traffic_light"),
            z=float(z_val) if z_val is not None else None,
        )
        for approach in ("N", "S"):
            offset = approach_length_m if approach == "N" else -approach_length_m
            approach_z = None
            if z_val is not None:
                approach_z = float(z_val) - approach_z_drop
            add_node(
                f"{approach}_{inter['id']}",
                float(inter["x"]),
                float(inter["y"]) + offset,
                "priority",
                z=approach_z,
            )

    first = intersections[0]
    last = intersections[-1]
    add_edge(
        f"{terminals['CITY_EAST']['id']}_{first['id']}",
        terminals["CITY_EAST"]["id"],
        first["id"],
        int(first.get("major_lanes", network.get("default_major_lanes", 2))),
        major_speed,
        major_priority,
    )
    add_edge(
        f"{first['id']}_{terminals['CITY_EAST']['id']}",
        first["id"],
        terminals["CITY_EAST"]["id"],
        int(first.get("major_lanes", network.get("default_major_lanes", 2))),
        major_speed,
        major_priority,
    )
    for a, b in zip(intersections, intersections[1:]):
        lanes = min(
            int(a.get("major_lanes", network.get("default_major_lanes", 2))),
            int(b.get("major_lanes", network.get("default_major_lanes", 2))),
        )
        add_edge(f"{a['id']}_{b['id']}", a["id"], b["id"], lanes, major_speed, major_priority)
        add_edge(f"{b['id']}_{a['id']}", b["id"], a["id"], lanes, major_speed, major_priority)
    add_edge(
        f"{last['id']}_{terminals['ATLANTIC_WEST']['id']}",
        last["id"],
        terminals["ATLANTIC_WEST"]["id"],
        int(last.get("major_lanes", network.get("default_major_lanes", 2))),
        major_speed,
        major_priority,
    )
    add_edge(
        f"{terminals['ATLANTIC_WEST']['id']}_{last['id']}",
        terminals["ATLANTIC_WEST"]["id"],
        last["id"],
        int(last.get("major_lanes", network.get("default_major_lanes", 2))),
        major_speed,
        major_priority,
    )

    for inter in intersections:
        lanes = int(inter.get("minor_lanes", network.get("default_minor_lanes", 1)))
        add_edge(f"N_{inter['id']}_{inter['id']}", f"N_{inter['id']}", inter["id"], lanes, minor_speed, minor_priority)
        add_edge(f"{inter['id']}_N_{inter['id']}", inter["id"], f"N_{inter['id']}", lanes, minor_speed, minor_priority)
        add_edge(f"S_{inter['id']}_{inter['id']}", f"S_{inter['id']}", inter["id"], lanes, minor_speed, minor_priority)
        add_edge(f"{inter['id']}_S_{inter['id']}", inter["id"], f"S_{inter['id']}", lanes, minor_speed, minor_priority)

    route_defs = build_routes(config, intersections, terminals)
    bus_stops = build_bus_stops(config, edge_defs, node_xy)
    detector_defs = build_detectors(config, edge_defs, node_xy)
    routes = build_route_xml(config, route_defs, edge_defs=edge_defs, node_xy=node_xy)

    (output_dir / "corredor.nod.xml").write_text(_pretty_xml(nodes), encoding="utf-8")
    (output_dir / "corredor.edg.xml").write_text(_pretty_xml(edges), encoding="utf-8")
    routes_output.write_text(_pretty_xml(routes), encoding="utf-8")
    bus_stops_output.write_text(_pretty_xml(bus_stops), encoding="utf-8")
    detectors_output.write_text(_pretty_xml(detector_defs), encoding="utf-8")

    if parking_output is not None:
        parking_output.write_text(_pretty_xml(build_parking_areas(config, edge_defs, node_xy)), encoding="utf-8")
    if calibrators_output is not None:
        calibrators_output.write_text(_pretty_xml(build_calibrators(config, edge_defs, node_xy)), encoding="utf-8")
    if tls_offsets_output is not None:
        tls_xml = build_tls_offsets(config)
        if tls_xml is not None:
            tls_offsets_output.write_text(_pretty_xml(tls_xml), encoding="utf-8")


def build_routes(config: dict, intersections: list[dict], terminals: dict[str, dict]) -> Dict[str, List[str]]:
    east_to_west = [f"{terminals['CITY_EAST']['id']}_{intersections[0]['id']}"]
    east_to_west.extend(f"{a['id']}_{b['id']}" for a, b in zip(intersections, intersections[1:]))
    east_to_west.append(f"{intersections[-1]['id']}_{terminals['ATLANTIC_WEST']['id']}")

    west_to_east = [f"{terminals['ATLANTIC_WEST']['id']}_{intersections[-1]['id']}"]
    west_to_east.extend(f"{b['id']}_{a['id']}" for a, b in reversed(list(zip(intersections, intersections[1:]))))
    west_to_east.append(f"{intersections[0]['id']}_{terminals['CITY_EAST']['id']}")

    route_defs = {
        "route_boavista_east_to_west": east_to_west,
        "route_boavista_west_to_east": west_to_east,
        "route_emergency_west_to_east": west_to_east,
    }
    for inter in intersections:
        route_defs[f"route_cross_NS_{inter['id']}"] = [f"N_{inter['id']}_{inter['id']}", f"{inter['id']}_S_{inter['id']}"]
        route_defs[f"route_cross_SN_{inter['id']}"] = [f"S_{inter['id']}_{inter['id']}", f"{inter['id']}_N_{inter['id']}"]

    _add_turning_movement_routes(route_defs, intersections, terminals)

    for item in config.get("routes", []):
        route_defs[str(item["id"])] = [str(edge) for edge in item["edges"]]
    return route_defs


def _add_turning_movement_routes(
    route_defs: Dict[str, List[str]],
    intersections: list[dict],
    terminals: dict[str, dict],
) -> None:
    """Emit eight turning-movement routes per intersection.

    For each intersection ``Ii``:

      * ``route_main_inbound_turn_to_N_Ii`` / ``..._S_Ii``
        — west-to-east (inbound toward city) flow turning onto the N or S
        approach at Ii.
      * ``route_main_outbound_turn_to_N_Ii`` / ``..._S_Ii``
        — east-to-west (outbound toward sea) flow turning onto the N or S
        approach at Ii.
      * ``route_minor_N_Ii_to_city`` / ``..._to_atlantic``
        — north approach merging into the corridor and exiting at CITY_EAST or
        ATLANTIC_WEST respectively (i.e., the two turn options for a vehicle
        arriving from the north).
      * ``route_minor_S_Ii_to_city`` / ``..._to_atlantic`` — same, from south.

    These routes plus ``route_cross_NS_Ii``/``route_cross_SN_Ii`` cover all 12
    discrete movements at every junction (per inflow direction: through-left-
    right, minus U-turns).
    """
    last_idx = len(intersections) - 1
    city_id = terminals["CITY_EAST"]["id"]
    atlantic_id = terminals["ATLANTIC_WEST"]["id"]

    for j, inter in enumerate(intersections):
        inter_id = inter["id"]

        # Edges reaching this intersection along the inbound (W→E) corridor.
        edges_inbound_to: List[str] = [f"{atlantic_id}_{intersections[-1]['id']}"]
        for k in range(last_idx, j, -1):
            edges_inbound_to.append(f"{intersections[k]['id']}_{intersections[k - 1]['id']}")

        # Edges reaching this intersection along the outbound (E→W) corridor.
        edges_outbound_to: List[str] = [f"{city_id}_{intersections[0]['id']}"]
        for k in range(j):
            edges_outbound_to.append(f"{intersections[k]['id']}_{intersections[k + 1]['id']}")

        route_defs[f"route_main_inbound_turn_to_N_{inter_id}"] = edges_inbound_to + [f"{inter_id}_N_{inter_id}"]
        route_defs[f"route_main_inbound_turn_to_S_{inter_id}"] = edges_inbound_to + [f"{inter_id}_S_{inter_id}"]
        route_defs[f"route_main_outbound_turn_to_N_{inter_id}"] = edges_outbound_to + [f"{inter_id}_N_{inter_id}"]
        route_defs[f"route_main_outbound_turn_to_S_{inter_id}"] = edges_outbound_to + [f"{inter_id}_S_{inter_id}"]

        # Edges leaving this intersection eastward toward CITY_EAST (inbound exit).
        edges_to_city: List[str] = []
        for k in range(j, 0, -1):
            edges_to_city.append(f"{intersections[k]['id']}_{intersections[k - 1]['id']}")
        edges_to_city.append(f"{intersections[0]['id']}_{city_id}")

        # Edges leaving this intersection westward toward ATLANTIC_WEST (outbound exit).
        edges_to_atlantic: List[str] = []
        for k in range(j, last_idx):
            edges_to_atlantic.append(f"{intersections[k]['id']}_{intersections[k + 1]['id']}")
        edges_to_atlantic.append(f"{intersections[-1]['id']}_{atlantic_id}")

        route_defs[f"route_minor_N_{inter_id}_to_city"] = [f"N_{inter_id}_{inter_id}"] + edges_to_city
        route_defs[f"route_minor_N_{inter_id}_to_atlantic"] = [f"N_{inter_id}_{inter_id}"] + edges_to_atlantic
        route_defs[f"route_minor_S_{inter_id}_to_city"] = [f"S_{inter_id}_{inter_id}"] + edges_to_city
        route_defs[f"route_minor_S_{inter_id}_to_atlantic"] = [f"S_{inter_id}_{inter_id}"] + edges_to_atlantic


def _sidewalk_lane_offset(config: dict) -> int:
    """Lane-index offset to apply when ``--sidewalks.guess`` is enabled.

    netconvert inserts the pedestrian sidewalk as lane index 0, pushing all
    vehicle lanes up by one. ``lane_index`` values in the JSON are written
    from the engineer's perspective ("0 = first vehicle lane from the curb"),
    so the generator shifts them by this offset when emitting artifacts that
    reference the *output* network (busStop / parkingArea / detector lane
    ids). ``lane_allow_rules`` stays unshifted because it feeds the *input*
    edge XML before netconvert adds the sidewalk.
    """
    return 1 if bool(config.get("network", {}).get("enable_sidewalks", False)) else 0


def build_bus_stops(config: dict, edge_defs: Dict[str, Dict[str, Any]], node_xy: Dict[str, tuple[float, float]]) -> ET.Element:
    root = ET.Element("additional")
    lane_offset = _sidewalk_lane_offset(config)
    for stop in config.get("public_transport", {}).get("stops", []):
        edge_id = str(stop["edge_id"])
        if edge_id not in edge_defs:
            raise ValueError(f"Bus stop {stop['id']} references unknown edge {edge_id}")
        lane_index = int(stop.get("lane_index", 0))
        lanes = int(edge_defs[edge_id]["numLanes"])
        if not 0 <= lane_index < lanes:
            raise ValueError(f"Bus stop {stop['id']} lane_index={lane_index} outside edge {edge_id} lanes={lanes}")
        length = edge_length(edge_defs[edge_id], node_xy)
        stop_len = float(stop.get("length_m", 30.0))
        center = min(max(float(stop.get("center_m", 120.0)), stop_len / 2.0 + 1.0), length - stop_len / 2.0 - 1.0)
        start = max(0.1, center - stop_len / 2.0)
        end = min(length - 0.1, center + stop_len / 2.0)
        ET.SubElement(
            root,
            "busStop",
            {
                "id": str(stop["id"]),
                "lane": f"{edge_id}_{lane_index + lane_offset}",
                "startPos": f"{start:.1f}",
                "endPos": f"{end:.1f}",
                "friendlyPos": "true",
            },
        )
    return root


def build_detectors(config: dict, edge_defs: Dict[str, Dict[str, Any]], node_xy: Dict[str, tuple[float, float]]) -> ET.Element:
    root = ET.Element("additional")
    detector_cfg = config.get("detectors", {})
    frequency_s = str(int(detector_cfg.get("frequency_s", 60)))
    stopline_setback_m = float(detector_cfg.get("stopline_setback_m", 85.0))
    queue_detector_length_m = float(detector_cfg.get("queue_detector_length_m", 80.0))
    e1_file = str(detector_cfg.get("e1_output", "../../outputs/e1_detectors.xml"))
    e2_file = str(detector_cfg.get("e2_output", "../../outputs/e2_queues.xml"))
    lane_offset = _sidewalk_lane_offset(config)

    for edge_id, attrs in sorted(edge_defs.items()):
        length = edge_length(attrs, node_xy)
        lane_count = int(attrs["numLanes"])
        e1_pos = max(5.0, length - stopline_setback_m)
        e2_pos = max(1.0, e1_pos - queue_detector_length_m / 2.0)
        e2_len = min(queue_detector_length_m, max(5.0, length - e2_pos - 1.0))
        # Iterate over vehicle-lane indices in the JSON's frame and shift to
        # the post-netconvert output index when sidewalks are enabled.
        for lane_index in range(lane_count):
            lane_id = f"{edge_id}_{lane_index + lane_offset}"
            ET.SubElement(
                root,
                "inductionLoop",
                {
                    "id": f"e1_{lane_id}",
                    "lane": lane_id,
                    "pos": f"{e1_pos:.1f}",
                    "freq": frequency_s,
                    "file": e1_file,
                },
            )
            ET.SubElement(
                root,
                "laneAreaDetector",
                {
                    "id": f"e2_{lane_id}",
                    "lane": lane_id,
                    "pos": f"{e2_pos:.1f}",
                    "length": f"{e2_len:.1f}",
                    "freq": frequency_s,
                    "file": e2_file,
                },
            )
    return root


_VTYPE_PARAM_KEYS = {"params"}
_VTYPE_SKIP_KEYS = _VTYPE_PARAM_KEYS  # attributes that need child elements, not serialised as attrs


def build_route_xml(
    config: dict,
    route_defs: Dict[str, List[str]],
    *,
    edge_defs: Dict[str, Dict[str, Any]] | None = None,
    node_xy: Dict[str, tuple[float, float]] | None = None,
) -> ET.Element:
    root = ET.Element(
        "routes",
        {
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:noNamespaceSchemaLocation": "http://sumo.dlr.de/xsd/routes_file.xsd",
        },
    )
    for vehicle_type in config.get("vehicle_types", []):
        attrs = {
            key: str(value)
            for key, value in vehicle_type.items()
            if key not in _VTYPE_SKIP_KEYS
        }
        vtype_elem = ET.SubElement(root, "vType", attrs)
        for param in vehicle_type.get("params", []) or []:
            ET.SubElement(
                vtype_elem,
                "param",
                {"key": str(param["key"]), "value": str(param["value"])},
            )

    for distribution in config.get("vehicle_type_distributions", []):
        components = distribution.get("components", [])
        if not components:
            continue
        ET.SubElement(
            root,
            "vTypeDistribution",
            {
                "id": str(distribution["id"]),
                "vTypes": " ".join(str(item["type"]) for item in components),
                "probabilities": " ".join(f"{float(item['probability']):.4f}" for item in components),
            },
        )

    for route_id, edges in route_defs.items():
        ET.SubElement(root, "route", {"id": route_id, "edges": " ".join(edges)})

    timed_elements: list[tuple[float, int, ET.Element]] = []
    order = 0
    demand = config.get("demand_profiles", {}).get(config.get("active_demand_profile", "am_peak"), {})
    stochastic_arrivals = bool(config.get("stochastic_arrivals", True))
    skip_keys = {"description", "time_profile"}
    for flow in demand.get("flows", []):
        sub_flows = _expand_time_profile(flow)
        for sub_flow in sub_flows:
            attrs = {key: str(value) for key, value in sub_flow.items() if key not in skip_keys}
            if stochastic_arrivals and "period" in sub_flow:
                period_value = float(sub_flow["period"])
                if period_value > 0:
                    attrs["period"] = f"exp({1.0 / period_value:.6f})"
            timed_elements.append((float(attrs.get("begin", 0.0)), order, ET.Element("flow", attrs)))
            order += 1

    public_transport = config.get("public_transport", {})
    line_by_id = {str(line["id"]): line for line in public_transport.get("lines", [])}
    stop_by_id = {str(stop["id"]): stop for stop in public_transport.get("stops", [])}
    base_seed = int(config.get("random_seed", 57))
    for service in public_transport.get("services", []):
        line = line_by_id[str(service["line_id"])]
        route_id = str(service["route"])
        stop_ids = [str(stop_id) for stop_id in service["stops"]]
        service_rng = random.Random(_seed_for(base_seed, "service", service.get("line_code", service.get("line_id", "")), service.get("direction", "")))
        for depart in _service_departures(service, config, rng=service_rng):
            dwell_rng = random.Random(_seed_for(base_seed, "dwell", service.get("line_code", ""), service.get("direction", ""), int(depart)))
            vehicle = ET.Element(
                "vehicle",
                {
                    "id": f"bus_{service['line_id']}_{service['direction']}_{int(depart):04d}",
                    "type": str(line.get("vehicle_type", "bus_12m")),
                    "route": route_id,
                    "depart": _format_time(depart),
                    "line": str(service.get("line_code", f"{service['line_id']}_{service['direction']}")),
                    "departLane": "best",
                    "departSpeed": "0",
                },
            )
            for stop_id in stop_ids:
                stop = stop_by_id[stop_id]
                dwell = _sample_dwell(stop.get("dwell_s", 20), dwell_rng)
                stop_attrs = {"busStop": stop_id, "duration": _format_time(dwell)}
                # bay=true approximates a physical bus bay: parking="true" removes the bus from the
                # traffic lane during dwell, so following buses on the dedicated lane are not blocked.
                # Curbside stops (bay=false) keep the bus on the lane — realistic for Bessa,
                # Antunes Guimaraes and Marechal where there is no dedicated bay.
                if bool(stop.get("bay", False)):
                    stop_attrs["parking"] = "true"
                ET.SubElement(vehicle, "stop", stop_attrs)
            timed_elements.append((float(depart), order, vehicle))
            order += 1

    for event in config.get("events", []):
        vehicle, depart_t = _event_to_vehicle(event)
        timed_elements.append((depart_t, order, vehicle))
        order += 1

    for parking_event in config.get("parking_events", []) or []:
        vehicle, depart_t = _parking_event_to_vehicle(parking_event)
        timed_elements.append((depart_t, order, vehicle))
        order += 1

    for ped_flow in config.get("pedestrian_flows", []) or []:
        person_flow, begin_t = _pedestrian_flow_to_xml(ped_flow)
        timed_elements.append((begin_t, order, person_flow))
        order += 1

    for _time, _order, element in sorted(timed_elements, key=lambda item: (item[0], item[1])):
        root.append(element)
    return root


def _event_to_vehicle(event: dict) -> tuple[ET.Element, float]:
    """Serialise a scenario event into a SUMO vehicle element.

    Supports two flavours:
      * standard ``emergency_vehicle`` / generic events — dump-all-attrs (legacy behaviour);
      * ``stopped_vehicle`` events — set ``type``/``route`` and emit a ``<stop>``
        child on ``stop_edge`` with the requested duration.
    """
    depart_t = float(event.get("depart", 0))
    event_type = str(event.get("type", ""))
    if event_type == "stopped_vehicle":
        vehicle_type = str(event.get("vehicle_type", "lcv"))
        attrs = {
            "id": str(event.get("id", f"stop_{int(depart_t)}")),
            "type": vehicle_type,
            "route": str(event.get("route", "")),
            "depart": _format_time(depart_t),
            "departLane": str(event.get("departLane", "best")),
            "departSpeed": str(event.get("departSpeed", "max")),
        }
        vehicle = ET.Element("vehicle", attrs)
        stop_edge = str(event.get("stop_edge", ""))
        if stop_edge:
            ET.SubElement(
                vehicle,
                "stop",
                {
                    "edge": stop_edge,
                    "endPos": f"{float(event.get('stop_pos_m', 80.0)):.1f}",
                    "duration": _format_time(event.get("stop_duration_s", 240.0)),
                    "parking": "true",
                    "triggered": "false",
                },
            )
        return vehicle, depart_t
    skip = {"description", "depart", "stop_edge", "stop_duration_s", "stop_pos_m", "vehicle_type"}
    attrs = {key: str(value) for key, value in event.items() if key not in skip}
    attrs["depart"] = _format_time(depart_t)
    return ET.Element("vehicle", attrs), depart_t


def _parking_event_to_vehicle(parking_event: dict) -> tuple[ET.Element, float]:
    depart_t = float(parking_event.get("depart", 0))
    attrs = {
        "id": str(parking_event.get("id", f"park_{int(depart_t)}")),
        "type": str(parking_event.get("vehicle_type", "lcv")),
        "route": str(parking_event.get("route", "")),
        "depart": _format_time(depart_t),
        "departLane": str(parking_event.get("departLane", "best")),
        "departSpeed": str(parking_event.get("departSpeed", "max")),
    }
    vehicle = ET.Element("vehicle", attrs)
    ET.SubElement(
        vehicle,
        "stop",
        {
            "parkingArea": str(parking_event["parking_area_id"]),
            "duration": _format_time(parking_event.get("duration_s", 180.0)),
        },
    )
    return vehicle, depart_t


def _pedestrian_flow_to_xml(ped_flow: dict) -> tuple[ET.Element, float]:
    begin_t = float(ped_flow.get("begin", 0))
    end_t = float(ped_flow.get("end", 7200))
    period = float(ped_flow.get("period", 60))
    attrs = {
        "id": str(ped_flow.get("id", "ped_flow")),
        "begin": _format_time(begin_t),
        "end": _format_time(end_t),
        "period": f"exp({1.0 / period:.6f})" if period > 0 else "60",
    }
    if "departPos" in ped_flow:
        attrs["departPos"] = str(ped_flow["departPos"])
    flow_elem = ET.Element("personFlow", attrs)
    walk_attrs = {
        "from": str(ped_flow.get("from_edge", "")),
        "to": str(ped_flow.get("to_edge", "")),
    }
    if "arrivalPos" in ped_flow:
        walk_attrs["arrivalPos"] = str(ped_flow["arrivalPos"])
    ET.SubElement(flow_elem, "walk", walk_attrs)
    return flow_elem, begin_t


def build_parking_areas(
    config: dict,
    edge_defs: Dict[str, Dict[str, Any]],
    node_xy: Dict[str, tuple[float, float]],
) -> ET.Element:
    root = ET.Element("additional")
    lane_offset = _sidewalk_lane_offset(config)
    for area in config.get("parking_areas", []) or []:
        edge_id = str(area["edge_id"])
        if edge_id not in edge_defs:
            raise ValueError(f"Parking area {area['id']} references unknown edge {edge_id}")
        lane_index = int(area.get("lane_index", 0))
        lanes = int(edge_defs[edge_id]["numLanes"])
        if not 0 <= lane_index < lanes:
            raise ValueError(
                f"Parking area {area['id']} lane_index={lane_index} outside edge {edge_id} lanes={lanes}"
            )
        attrs = {
            "id": str(area["id"]),
            "lane": f"{edge_id}_{lane_index + lane_offset}",
            "startPos": f"{float(area.get('start_m', 0)):.1f}",
            "endPos": f"{float(area.get('end_m', 30)):.1f}",
            "roadsideCapacity": str(int(area.get("capacity", 4))),
            "friendlyPos": "true",
        }
        if "angle" in area:
            attrs["angle"] = str(area["angle"])
        ET.SubElement(root, "parkingArea", attrs)
    return root


def build_calibrators(
    config: dict,
    edge_defs: Dict[str, Dict[str, Any]],
    node_xy: Dict[str, tuple[float, float]],
) -> ET.Element:
    root = ET.Element("additional")
    for cal in config.get("calibrators", []) or []:
        edge_id = str(cal["edge_id"])
        if edge_id not in edge_defs:
            raise ValueError(f"Calibrator {cal['id']} references unknown edge {edge_id}")
        attrs = {
            "id": str(cal["id"]),
            "edge": edge_id,
            "pos": f"{float(cal.get('pos_m', 100)):.1f}",
            "freq": str(int(cal.get("freq_s", 300))),
        }
        if "output" in cal:
            attrs["output"] = str(cal["output"])
        cal_elem = ET.SubElement(root, "calibrator", attrs)
        for entry in cal.get("schedule", []) or []:
            flow_attrs = {
                "begin": _format_time(entry.get("begin", 0)),
                "end": _format_time(entry.get("end", 3600)),
                "vehsPerHour": str(int(entry.get("vehs_per_hour", 0))),
            }
            if "speed_mps" in entry:
                flow_attrs["speed"] = f"{float(entry['speed_mps']):.2f}"
            if "type" in entry:
                flow_attrs["type"] = str(entry["type"])
            ET.SubElement(cal_elem, "flow", flow_attrs)
    return root


def build_tls_offsets(config: dict) -> ET.Element | None:
    """Return a minimal `<additional>` doc that lists TLS overrides (offset + phases).

    The doc is consumed by ``apply_tls_offsets.py`` (post-build step) — not by
    SUMO directly. Each `<tls>` entry pairs an intersection id with the desired
    offset in seconds and, optionally, `<phase role="..." duration_s="...">`
    children that specify the desired green/yellow timing per role. The
    post-build script identifies which generated phase corresponds to each role
    by inspecting the link topology and rewrites the durations in-place,
    leaving the state strings intact.
    """
    intersections = config.get("network", {}).get("intersections", [])
    signalised = [
        inter
        for inter in intersections
        if str(inter.get("type", "")) == "traffic_light"
        and ("tls_offset_s" in inter or "tls_program" in inter)
    ]
    if not signalised:
        return None
    root = ET.Element("tlsOffsetOverrides")
    for inter in signalised:
        tls_id = str(inter["id"])
        attrs = {"id": tls_id}
        if "tls_offset_s" in inter:
            attrs["offset_s"] = f"{float(inter['tls_offset_s']):.1f}"
        tls_elem = ET.SubElement(root, "tls", attrs)
        program = inter.get("tls_program")
        if not isinstance(program, dict):
            continue
        cycle_s = float(inter.get("tls_cycle_s", 0))
        # Authored phases follow the temporal order of the cycle:
        # main_green → main_yellow → all_red_main_to_cross → cross_green →
        # cross_yellow → all_red_cross_to_main. The two all-red roles are
        # optional; when absent the post-build step keeps the netconvert
        # 4-phase layout unchanged. The cycle sum must equal tls_cycle_s.
        role_pairs = [
            ("main_green", "green_main_s"),
            ("main_yellow", "yellow_main_s"),
            ("all_red_main_to_cross", "all_red_main_to_cross_s"),
            ("cross_green", "green_minor_s"),
            ("cross_yellow", "yellow_minor_s"),
            ("all_red_cross_to_main", "all_red_cross_to_main_s"),
        ]
        emitted_sum = 0.0
        for role, key in role_pairs:
            if key not in program:
                continue
            duration = float(program[key])
            emitted_sum += duration
            ET.SubElement(
                tls_elem,
                "phase",
                {"role": role, "duration_s": f"{duration:.1f}"},
            )
        if cycle_s > 0 and abs(emitted_sum - cycle_s) > 0.51:
            raise ValueError(
                f"TLS {tls_id}: tls_program durations sum to {emitted_sum:.1f}s but tls_cycle_s is {cycle_s:.1f}s."
            )
    return root


def _service_departures(service: dict, config: dict, *, rng: random.Random | None = None) -> Iterable[float]:
    begin = float(service.get("begin_s", config.get("simulation_begin_s", 0)))
    end = float(service.get("end_s", config.get("simulation_end_s", 7200)))
    offset = float(service.get("offset_s", 0))
    jitter = float(service.get("terminus_jitter_s", 0))
    schedule = service.get("headway_schedule")
    current = begin + offset
    if isinstance(schedule, list) and schedule:
        intervals = [(float(item["begin_s"]), float(item["end_s"]), float(item["headway_s"])) for item in schedule]
        while current < end:
            applied = _headway_at(intervals, current, float(service.get("headway_s", intervals[0][2])))
            yield _apply_jitter(current, jitter, rng)
            current += applied
    else:
        headway = float(service["headway_s"])
        while current < end:
            yield _apply_jitter(current, jitter, rng)
            current += headway


def _headway_at(intervals: List[tuple[float, float, float]], t: float, default: float) -> float:
    for begin, end, headway in intervals:
        if begin <= t < end:
            return headway
    return default


def _apply_jitter(value: float, jitter: float, rng: random.Random | None) -> float:
    if jitter <= 0 or rng is None:
        return value
    return max(0.0, value + rng.uniform(-jitter, jitter))


def _sample_dwell(spec: Any, rng: random.Random) -> float:
    if isinstance(spec, (int, float)):
        return float(spec)
    if isinstance(spec, dict):
        mean = float(spec.get("mean", spec.get("dwell_s", 20)))
        std = float(spec.get("std", 0.0))
        lo = float(spec.get("min", max(0.0, mean - 3 * std) if std else mean))
        hi = float(spec.get("max", mean + 3 * std if std else mean))
        if std <= 0:
            return mean
        for _ in range(8):
            value = rng.gauss(mean, std)
            if lo <= value <= hi:
                return value
        return min(hi, max(lo, mean))
    return float(spec)


def _seed_for(base: int, *parts: Any) -> int:
    key = f"{base}:" + ":".join(str(part) for part in parts)
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def _expand_time_profile(flow: dict) -> List[dict]:
    """Expand a flow with a time_profile into a list of sub-flows.

    Each entry yields a sub-flow with id "{base}_t{i}", inherited attributes,
    and a period equal to base_period * (1 / scale) when 'scale' is given, or
    the explicit 'period' if provided in the entry. Higher scale => more demand
    => shorter period.
    """
    entries = flow.get("time_profile")
    if not entries:
        return [flow]
    base_id = str(flow.get("id", "flow"))
    base_period = float(flow.get("period", 0.0))
    expanded: List[dict] = []
    for index, entry in enumerate(entries):
        sub = {key: value for key, value in flow.items() if key != "time_profile"}
        sub["id"] = f"{base_id}_t{index}"
        sub["begin"] = entry["begin"]
        sub["end"] = entry["end"]
        if "period" in entry:
            sub["period"] = float(entry["period"])
        else:
            scale = float(entry.get("scale", 1.0))
            if scale <= 0 or base_period <= 0:
                continue
            sub["period"] = round(base_period / scale, 3)
        expanded.append(sub)
    return expanded


def edge_length(attrs: Dict[str, Any], node_xy: Dict[str, tuple[float, float]]) -> float:
    x1, y1 = node_xy[str(attrs["from"])]
    x2, y2 = node_xy[str(attrs["to"])]
    return math.hypot(x2 - x1, y2 - y1)


def _format_time(value: Any) -> str:
    numeric = float(value)
    if numeric.is_integer():
        return str(int(numeric))
    return f"{numeric:.3f}".rstrip("0").rstrip(".")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--scenario", help="Scenario profile id from the config's scenario_profiles mapping.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--routes-output", default=Path("sumo/routes/routes.rou.xml"), type=Path)
    parser.add_argument("--bus-stops-output", default=Path("sumo/additional/bus_stops.add.xml"), type=Path)
    parser.add_argument("--detectors-output", default=Path("sumo/additional/detectors.add.xml"), type=Path)
    parser.add_argument("--parking-output", default=Path("sumo/additional/parking.add.xml"), type=Path)
    parser.add_argument("--calibrators-output", default=Path("sumo/additional/calibrators.add.xml"), type=Path)
    parser.add_argument("--tls-offsets-output", default=Path("sumo/additional/tls_offsets.add.xml"), type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    config = apply_scenario_profile(config, args.scenario)
    generate(
        config,
        args.output,
        routes_output=args.routes_output,
        bus_stops_output=args.bus_stops_output,
        detectors_output=args.detectors_output,
        parking_output=args.parking_output,
        calibrators_output=args.calibrators_output,
        tls_offsets_output=args.tls_offsets_output,
    )
    suffix = f" scenario={args.scenario}" if args.scenario else ""
    print(f"Generated PPS57 corridor scenario from {args.config}{suffix}")


if __name__ == "__main__":
    main()
