#!/usr/bin/env python3
"""Generate the PPS57 SUMO scenario from one declarative corridor config.

The scenario remains a controlled SUMO model, not an automatic OSM import. The
config anchors geometry, public-transport services, demand and detector layout in
one place so the baseline can be calibrated without hand-editing XML artifacts.
"""
from __future__ import annotations

import argparse
import json
import math
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
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    routes_output.parent.mkdir(parents=True, exist_ok=True)
    bus_stops_output.parent.mkdir(parents=True, exist_ok=True)
    detectors_output.parent.mkdir(parents=True, exist_ok=True)

    nodes = ET.Element("nodes")
    edges = ET.Element("edges")
    node_xy: Dict[str, tuple[float, float]] = {}
    edge_defs: Dict[str, Dict[str, Any]] = {}

    def add_node(node_id: str, x: float, y: float, node_type: str = "priority") -> None:
        node_xy[node_id] = (float(x), float(y))
        ET.SubElement(nodes, "node", {"id": node_id, "x": f"{x:.2f}", "y": f"{y:.2f}", "type": node_type})

    def add_edge(edge_id: str, src: str, dst: str, lanes: int, speed: float, priority: int) -> None:
        attrs = {
            "id": edge_id,
            "from": src,
            "to": dst,
            "numLanes": str(int(lanes)),
            "speed": f"{float(speed):.2f}",
            "priority": str(int(priority)),
        }
        ET.SubElement(edges, "edge", attrs)
        edge_defs[edge_id] = attrs

    network = config["network"]
    intersections = network["intersections"]
    terminals = {item["id"]: item for item in network["terminals"]}
    major_speed = float(network.get("default_major_speed_mps", config.get("default_major_speed_mps", 13.89)))
    minor_speed = float(network.get("default_minor_speed_mps", config.get("default_minor_speed_mps", 8.33)))
    major_priority = int(network.get("major_priority", 4))
    minor_priority = int(network.get("minor_priority", 2))
    approach_length_m = float(network.get("minor_approach_length_m", 420.0))

    for terminal in network["terminals"]:
        add_node(terminal["id"], float(terminal["x"]), float(terminal["y"]), terminal.get("type", "priority"))

    for inter in intersections:
        add_node(inter["id"], float(inter["x"]), float(inter["y"]), inter.get("type", "traffic_light"))
        for approach in ("N", "S"):
            offset = approach_length_m if approach == "N" else -approach_length_m
            add_node(f"{approach}_{inter['id']}", float(inter["x"]), float(inter["y"]) + offset, "priority")

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
    routes = build_route_xml(config, route_defs)

    (output_dir / "corredor.nod.xml").write_text(_pretty_xml(nodes), encoding="utf-8")
    (output_dir / "corredor.edg.xml").write_text(_pretty_xml(edges), encoding="utf-8")
    routes_output.write_text(_pretty_xml(routes), encoding="utf-8")
    bus_stops_output.write_text(_pretty_xml(bus_stops), encoding="utf-8")
    detectors_output.write_text(_pretty_xml(detector_defs), encoding="utf-8")


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
    for item in config.get("routes", []):
        route_defs[str(item["id"])] = [str(edge) for edge in item["edges"]]
    return route_defs


def build_bus_stops(config: dict, edge_defs: Dict[str, Dict[str, Any]], node_xy: Dict[str, tuple[float, float]]) -> ET.Element:
    root = ET.Element("additional")
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
                "lane": f"{edge_id}_{lane_index}",
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

    for edge_id, attrs in sorted(edge_defs.items()):
        length = edge_length(attrs, node_xy)
        lane_count = int(attrs["numLanes"])
        e1_pos = max(5.0, length - stopline_setback_m)
        e2_pos = max(1.0, e1_pos - queue_detector_length_m / 2.0)
        e2_len = min(queue_detector_length_m, max(5.0, length - e2_pos - 1.0))
        for lane_index in range(lane_count):
            lane_id = f"{edge_id}_{lane_index}"
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


def build_route_xml(config: dict, route_defs: Dict[str, List[str]]) -> ET.Element:
    root = ET.Element(
        "routes",
        {
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xsi:noNamespaceSchemaLocation": "http://sumo.dlr.de/xsd/routes_file.xsd",
        },
    )
    for vehicle_type in config.get("vehicle_types", []):
        attrs = {key: str(value) for key, value in vehicle_type.items()}
        ET.SubElement(root, "vType", attrs)

    for route_id, edges in route_defs.items():
        ET.SubElement(root, "route", {"id": route_id, "edges": " ".join(edges)})

    timed_elements: list[tuple[float, int, ET.Element]] = []
    order = 0
    demand = config.get("demand_profiles", {}).get(config.get("active_demand_profile", "am_peak"), {})
    for flow in demand.get("flows", []):
        attrs = {key: str(value) for key, value in flow.items() if key != "description"}
        timed_elements.append((float(attrs.get("begin", 0.0)), order, ET.Element("flow", attrs)))
        order += 1

    public_transport = config.get("public_transport", {})
    line_by_id = {str(line["id"]): line for line in public_transport.get("lines", [])}
    stop_by_id = {str(stop["id"]): stop for stop in public_transport.get("stops", [])}
    for service in public_transport.get("services", []):
        line = line_by_id[str(service["line_id"])]
        route_id = str(service["route"])
        stop_ids = [str(stop_id) for stop_id in service["stops"]]
        for depart in _service_departures(service, config):
            vehicle = ET.Element(
                "vehicle",
                {
                    "id": f"bus_{service['line_id']}_{service['direction']}_{int(depart):04d}",
                    "type": str(line.get("vehicle_type", "bus_12m")),
                    "route": route_id,
                    "depart": _format_time(depart),
                    "line": str(service.get("line_code", f"{service['line_id']}_{service['direction']}")),
                    "departLane": "best",
                    "departSpeed": "max",
                },
            )
            for stop_id in stop_ids:
                stop = stop_by_id[stop_id]
                ET.SubElement(vehicle, "stop", {"busStop": stop_id, "duration": _format_time(stop.get("dwell_s", 20))})
            timed_elements.append((float(depart), order, vehicle))
            order += 1

    for event in config.get("events", []):
        attrs = {key: str(value) for key, value in event.items() if key not in {"description", "depart"}}
        attrs["depart"] = _format_time(event.get("depart", 0))
        vehicle = ET.Element("vehicle", attrs)
        timed_elements.append((float(event.get("depart", 0)), order, vehicle))
        order += 1

    for _time, _order, element in sorted(timed_elements, key=lambda item: (item[0], item[1])):
        root.append(element)
    return root


def _service_departures(service: dict, config: dict) -> Iterable[float]:
    begin = float(service.get("begin_s", config.get("simulation_begin_s", 0)))
    end = float(service.get("end_s", config.get("simulation_end_s", 7200)))
    headway = float(service["headway_s"])
    offset = float(service.get("offset_s", 0))
    current = begin + offset
    while current < end:
        yield current
        current += headway


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
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    config = apply_scenario_profile(config, args.scenario)
    generate(
        config,
        args.output,
        routes_output=args.routes_output,
        bus_stops_output=args.bus_stops_output,
        detectors_output=args.detectors_output,
    )
    suffix = f" scenario={args.scenario}" if args.scenario else ""
    print(f"Generated PPS57 corridor scenario from {args.config}{suffix}")


if __name__ == "__main__":
    main()
