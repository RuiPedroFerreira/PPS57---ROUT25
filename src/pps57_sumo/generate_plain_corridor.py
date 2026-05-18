#!/usr/bin/env python3
"""Generate plain SUMO node/edge files for the PPS57 realistic Porto/Boavista proxy corridor.

This generator reads configs/corridor_config.json. It creates a topological proxy of a real urban
arterial, not a fully calibrated OSM-derived network. The next hardening step is to replace these
plain XML files with a netconvert import from OSM/JOSM and GTFS-based public transport mapping.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.dom import minidom


def _pretty_xml(element: ET.Element) -> str:
    rough = ET.tostring(element, encoding="utf-8")
    parsed = minidom.parseString(rough)
    return parsed.toprettyxml(indent="  ")


def generate(config: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    nodes = ET.Element("nodes")
    edges = ET.Element("edges")

    def add_node(node_id: str, x: float, y: float, node_type: str = "priority") -> None:
        ET.SubElement(nodes, "node", {"id": node_id, "x": f"{x:.2f}", "y": f"{y:.2f}", "type": node_type})

    def add_edge(edge_id: str, src: str, dst: str, lanes: int, speed: float, priority: int) -> None:
        ET.SubElement(edges, "edge", {
            "id": edge_id,
            "from": src,
            "to": dst,
            "numLanes": str(lanes),
            "speed": f"{speed:.2f}",
            "priority": str(priority),
        })

    terminals = {t["id"]: t for t in config["terminals"]}
    intersections = config["intersections"]
    major_speed = float(config["default_major_speed_mps"])
    minor_speed = float(config["default_minor_speed_mps"])

    add_node("CITY_EAST", terminals["CITY_EAST"]["x"], terminals["CITY_EAST"]["y"])
    add_node("ATLANTIC_WEST", terminals["ATLANTIC_WEST"]["x"], terminals["ATLANTIC_WEST"]["y"])

    for inter in intersections:
        add_node(inter["id"], inter["x"], inter["y"], inter.get("type", "traffic_light"))
        add_node(f"N_{inter['id']}", inter["x"], inter["y"] + 420, "priority")
        add_node(f"S_{inter['id']}", inter["x"], inter["y"] - 420, "priority")

    add_edge("CITY_EAST_I1", "CITY_EAST", "I1", 2, major_speed, 4)
    add_edge("I1_CITY_EAST", "I1", "CITY_EAST", 2, major_speed, 4)
    for a, b in zip(intersections, intersections[1:]):
        lanes = min(int(a.get("major_lanes", 2)), int(b.get("major_lanes", 2)))
        add_edge(f"{a['id']}_{b['id']}", a["id"], b["id"], lanes, major_speed, 4)
        add_edge(f"{b['id']}_{a['id']}", b["id"], a["id"], lanes, major_speed, 4)
    add_edge("I7_ATLANTIC_WEST", "I7", "ATLANTIC_WEST", 2, major_speed, 4)
    add_edge("ATLANTIC_WEST_I7", "ATLANTIC_WEST", "I7", 2, major_speed, 4)

    for inter in intersections:
        lanes = int(inter.get("minor_lanes", 1))
        add_edge(f"N_{inter['id']}_{inter['id']}", f"N_{inter['id']}", inter["id"], lanes, minor_speed, 2)
        add_edge(f"{inter['id']}_N_{inter['id']}", inter["id"], f"N_{inter['id']}", lanes, minor_speed, 2)
        add_edge(f"S_{inter['id']}_{inter['id']}", f"S_{inter['id']}", inter["id"], lanes, minor_speed, 2)
        add_edge(f"{inter['id']}_S_{inter['id']}", inter["id"], f"S_{inter['id']}", lanes, minor_speed, 2)

    (output_dir / "corredor.nod.xml").write_text(_pretty_xml(nodes), encoding="utf-8")
    (output_dir / "corredor.edg.xml").write_text(_pretty_xml(edges), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    generate(config, args.output)
    print(f"Generated realistic Porto/Boavista plain network files in {args.output}")


if __name__ == "__main__":
    main()
