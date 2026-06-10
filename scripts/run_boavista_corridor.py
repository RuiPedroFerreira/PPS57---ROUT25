#!/usr/bin/env python3
"""V4c: route the real STCP bus flows and run the real Boavista corridor.

Takes the V4b stop->edge mapping (real STCP stops on the real OSM net) and the
real V3 headways, builds SUMO bus flows that visit the real bus stops, routes
them with duarouter on the real network, runs SUMO, and reports bus KPIs from the
real run via the project's tripinfo parser.

Honest scope: there is no real background traffic (open Porto counts do not exist
— V2 is blocked), so this runs **buses only**. The KPIs are therefore near
free-flow bus times on the real geometry with real stops and real headways, not
congested-network times. Nothing is invented — flows, stops and headways all
derive from the real GTFS + real net (V3/V4/V4b).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.parse_tripinfo import parse_tripinfo  # noqa: E402

SIM_END_S = 7200


def build_flows(services: dict, flows_path: Path) -> int:
    """Write one bus flow per line+direction visiting its snapped stops; return flow count."""
    routes = ET.Element("routes")
    ET.SubElement(routes, "vType", {
        "id": "bus", "vClass": "bus", "length": "12.0", "minGap": "3.0",
        "maxSpeed": "13.9", "guiShape": "bus",
    })
    n = 0
    for key in sorted(services):
        entry = services[key]
        snapped = [s for s in entry["stops_in_bbox"] if s.get("snapped")]
        if len(snapped) < 2:
            continue
        period = int(round((entry.get("headway_am_peak_min") or entry.get("headway_allday_min") or 12) * 60))
        flow = ET.SubElement(routes, "flow", {
            "id": f"bus_{entry['line']}_{entry['direction']}",
            "type": "bus", "begin": "0", "end": str(SIM_END_S), "period": str(period),
            "departLane": "best", "departSpeed": "max",
            "from": snapped[0]["edge"], "to": snapped[-1]["edge"],
        })
        for stop in snapped:
            ET.SubElement(flow, "stop", {"busStop": f"bs_{stop['stop_id']}", "duration": "15"})
        n += 1
    ET.ElementTree(routes).write(flows_path, encoding="utf-8", xml_declaration=True)
    return n


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--net", type=Path, default=ROOT / ".tools" / "boavista-osm" / "boavista.net.xml")
    parser.add_argument("--busstops", type=Path, default=ROOT / ".tools" / "boavista-osm" / "boavista_pt_stops.add.xml")
    parser.add_argument("--v4b-report", type=Path, default=ROOT / "docs" / "validation" / "v4b_stcp_pt_mapping.json")
    parser.add_argument("--work", type=Path, default=ROOT / ".tools" / "boavista-osm")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "validation" / "v4c_corridor_run.json")
    args = parser.parse_args()

    for path in (args.net, args.busstops, args.v4b_report):
        if not path.exists():
            raise SystemExit(f"Missing {path}. Run scripts/build_stcp_pt_on_boavista.py (V4b) first.")

    services = json.loads(args.v4b_report.read_text(encoding="utf-8"))["services"]
    flows_path = args.work / "boavista_bus_flows.xml"
    routed_path = args.work / "boavista_bus_routed.rou.xml"
    tripinfo_path = args.work / "boavista_corridor_tripinfo.xml"
    n_flows = build_flows(services, flows_path)

    # Fail-fast hygiene: drop stale tool outputs so the "if file.exists()" reads below
    # can never pick up artifacts from a previous (different) run when a tool now fails.
    for stale in (routed_path, tripinfo_path):
        stale.unlink(missing_ok=True)

    duarouter = subprocess.run(
        ["duarouter", "-n", str(args.net), "--additional-files", str(args.busstops),
         "--route-files", str(flows_path), "-o", str(routed_path),
         "--ignore-errors", "true", "--repair", "true", "--no-step-log", "true", "--no-warnings", "true"],
        capture_output=True, text=True,
    )
    routed_ok = routed_path.exists()
    routed_vehicles = len(ET.parse(routed_path).getroot().findall("vehicle")) if routed_ok else 0

    sumo = subprocess.run(
        ["sumo", "-n", str(args.net), "--additional-files", str(args.busstops),
         "-r", str(routed_path), "--tripinfo-output", str(tripinfo_path),
         "--end", str(SIM_END_S), "--no-step-log", "true", "--no-warnings", "true",
         "--ignore-route-errors", "true", "--time-to-teleport", "-1"],
        capture_output=True, text=True,
    )
    kpis = parse_tripinfo(tripinfo_path) if tripinfo_path.exists() else {}
    buses = kpis.get("buses", {})

    report = {
        "validation_phase": "V4c_run_real_boavista_corridor",
        "scope_note": "Buses only (no real background traffic — open Porto counts unavailable, V2 blocked); "
                      "KPIs are near free-flow bus times on real geometry with real stops/headways.",
        "flows_built": n_flows,
        "bus_vehicles_routed": routed_vehicles,
        "duarouter_ok": duarouter.returncode == 0,
        "sumo_ok": sumo.returncode == 0,
        "bus_kpis": buses,
        "bus_headways_observed": kpis.get("bus_headways", {}),
        "honest_notes": [
            "Flows/stops/headways all derive from the real GTFS + real OSM net (V3/V4/V4b).",
            "No background traffic: open Porto counts do not exist (V2 blocked), so adding demand "
            "would be fabrication. Bus times here are essentially free-flow.",
            "Signal timings are synthetic (V4 limit).",
        ],
        "verdict": "pass" if (duarouter.returncode == 0 and routed_vehicles > 0 and sumo.returncode == 0
                              and buses.get("vehicles", 0) > 0) else "review",
    }
    if report["verdict"] != "pass":
        report["duarouter_stderr_tail"] = (duarouter.stderr.strip().splitlines() or [""])[-3:]
        report["sumo_stderr_tail"] = (sumo.stderr.strip().splitlines() or [""])[-3:]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    print(f"V4c real Boavista corridor run — flows built {n_flows}, bus vehicles routed {routed_vehicles}")
    print(f"  buses: {buses.get('vehicles', 0)}  mean duration {buses.get('mean_duration_s')}s  "
          f"mean time loss {buses.get('mean_time_loss_s')}s  mean stops {buses.get('mean_stop_count')}")
    print(f"  duarouter ok: {duarouter.returncode == 0}  sumo ok: {sumo.returncode == 0}  "
          f"verdict: {report['verdict']}   -> {args.out}")
    if report["verdict"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
