#!/usr/bin/env python3
"""V4d: standards/reference-aligned background demand + Webster signals on the real net.

Upgrades the two synthetic layers of the real Boavista corridor to reference grade:

  * Background demand: random origin-destination car traffic (SUMO randomTrips) at
    a rate calibrated so the arterial intensity lands in the band measured on a real
    European urban network (Madrid open data: median 397, P75 819, P90 1329 veh/h
    per detector), consistent with the project's HCM-derived volumes
    (~1224 veh/h inbound = ~612 veh/h/lane) and bounded by HCM practical capacity.
  * Signal timings: Webster's optimal cycle/green split (Webster 1958) computed by
    SUMO tlsCycleAdaptation.py from the actual routed demand — not netconvert defaults.

It then runs the corridor (cars + the real STCP buses) under the Webster signals and
reports KPIs plus the Madrid demand-band validation. Every input is sourced; only the
calibration *rate* is chosen to hit the referenced intensity band (and reported).
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.environment import ensure_sumo_environment, resolve_sumo_home  # noqa: E402
from pps57_sumo.parse_tripinfo import parse_tripinfo  # noqa: E402

SIM_END_S = 7200
# Madrid open-data intensity band (veh/h per detector) for face validation.
MADRID_BAND = {"median": 397, "p75": 819, "p90": 1329, "source": "datos.madrid.es / PMC11416623"}


def _sumo_home() -> Path:
    # Use the shared resolver: it validates tools/ + data/xsd and handles the
    # /usr/bin + /usr/share split (the binary's grandparent is not SUMO_HOME there).
    home = resolve_sumo_home()
    if home is None:
        raise SystemExit("Could not resolve SUMO_HOME (is SUMO installed / .venv active?).")
    return home


def _tool(sumo_home: Path, name: str) -> Path:
    hits = list((sumo_home / "tools").rglob(name))
    if not hits:
        raise SystemExit(f"{name} not found under {sumo_home}/tools")
    return hits[0]


def _run(cmd, sumo_home: Path) -> subprocess.CompletedProcess:
    # Run from ROOT so SUMO tools echo RELATIVE paths into their XML comments. The
    # repo dir name 'PPS57---ROUT25' contains '---', which is illegal inside an XML
    # comment and breaks the tools' own read-back of absolute paths.
    env = ensure_sumo_environment()
    env["SUMO_HOME"] = str(sumo_home)
    return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(ROOT))


def _rel(path: Path) -> str:
    return os.path.relpath(str(path), str(ROOT))


def _parse_sumo_xml(path: Path):
    """Parse a SUMO-generated XML, stripping its header comment first.

    SUMO tools echo absolute paths into an XML comment; this repo's path contains
    '---' (PPS57---ROUT25), which is illegal inside an XML comment and breaks strict
    parsers. Removing comments makes the file well-formed.
    """
    text = re.sub(r"<!--.*?-->", "", path.read_text(encoding="utf-8"), flags=re.S)
    return ET.fromstring(text)


def build_bus_flows(v4b_report: Path, path: Path) -> int:
    """Bus flows (one per line+direction) from the V4b mapping; same shape as V4c."""
    services = json.loads(v4b_report.read_text(encoding="utf-8"))["services"]
    routes = ET.Element("routes")
    ET.SubElement(routes, "vType", {"id": "bus", "vClass": "bus", "length": "12.0",
                                    "minGap": "3.0", "maxSpeed": "13.9", "guiShape": "bus"})
    n = 0
    for key in sorted(services):
        entry = services[key]
        snapped = [s for s in entry["stops_in_bbox"] if s.get("snapped")]
        if len(snapped) < 2:
            continue
        period = int(round((entry.get("headway_am_peak_min") or entry.get("headway_allday_min") or 12) * 60))
        flow = ET.SubElement(routes, "flow", {
            "id": f"bus_{entry['line']}_{entry['direction']}", "type": "bus",
            "begin": "0", "end": str(SIM_END_S), "period": str(period),
            "departLane": "best", "departSpeed": "max",
            "from": snapped[0]["edge"], "to": snapped[-1]["edge"]})
        for stop in snapped:
            ET.SubElement(flow, "stop", {"busStop": f"bs_{stop['stop_id']}", "duration": "15"})
        n += 1
    ET.ElementTree(routes).write(path, encoding="utf-8", xml_declaration=True)
    return n


def build_arterial_car_flows(v4b_report: Path, path: Path, veh_h_per_dir: float) -> tuple[int, float]:
    """Explicit car flows along the dominant corridor line (per direction) at a target
    intensity, so the arterial carries realistic, controllable through-traffic
    (HCM/Madrid level). randomTrips adds diffuse cross-traffic on top.
    """
    services = json.loads(v4b_report.read_text(encoding="utf-8"))["services"]
    by_dir: dict[str, tuple[int, str, str]] = {}
    for entry in services.values():
        snapped = [s for s in entry["stops_in_bbox"] if s.get("snapped")]
        if len(snapped) < 2:
            continue
        d = str(entry["direction"])
        if d not in by_dir or len(snapped) > by_dir[d][0]:
            by_dir[d] = (len(snapped), snapped[0]["edge"], snapped[-1]["edge"])
    routes = ET.Element("routes")
    ET.SubElement(routes, "vType", {"id": "car", "vClass": "passenger", "length": "4.5", "minGap": "2.5"})
    period = round(3600.0 / veh_h_per_dir, 3)
    n = 0
    for direction, (_, fr, to) in sorted(by_dir.items()):
        ET.SubElement(routes, "flow", {"id": f"car_arterial_{direction}", "type": "car",
                                       "begin": "0", "end": str(SIM_END_S), "period": str(period),
                                       "from": fr, "to": to, "departLane": "best", "departSpeed": "max"})
        n += 1
    ET.ElementTree(routes).write(path, encoding="utf-8", xml_declaration=True)
    return n, period


def bus_route_edges(routed_path: Path) -> set[str]:
    edges: set[str] = set()
    for veh in _parse_sumo_xml(routed_path).findall("vehicle"):
        if veh.get("id", "").startswith("bus_"):
            route = veh.find("route")
            if route is not None:
                edges.update(route.get("edges", "").split())
    return edges


def measure_arterial_intensity(edgedata_path: Path, arterial_edges: set[str]) -> dict:
    """Mean per-edge intensity (veh/h) on the arterial edges, for Madrid-band validation."""
    intensities = []
    for interval in _parse_sumo_xml(edgedata_path).findall("interval"):
        begin = float(interval.get("begin", 0)); end = float(interval.get("end", SIM_END_S))
        hours = max((end - begin) / 3600.0, 1e-9)
        for edge in interval.findall("edge"):
            if edge.get("id") in arterial_edges:
                entered = float(edge.get("entered", 0) or 0)
                if entered > 0:
                    intensities.append(entered / hours)
    intensities.sort()
    if not intensities:
        return {"arterial_edges_measured": 0}
    return {
        "arterial_edges_measured": len(intensities),
        "mean_veh_h": round(sum(intensities) / len(intensities), 1),
        "median_veh_h": round(intensities[len(intensities) // 2], 1),
        "p90_veh_h": round(intensities[int(len(intensities) * 0.9)], 1),
        "max_veh_h": round(intensities[-1], 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--net", type=Path, default=ROOT / ".tools" / "boavista-osm" / "boavista.net.xml")
    parser.add_argument("--busstops", type=Path, default=ROOT / ".tools" / "boavista-osm" / "boavista_pt_stops.add.xml")
    parser.add_argument("--v4b-report", type=Path, default=ROOT / "docs" / "validation" / "v4b_stcp_pt_mapping.json")
    parser.add_argument("--car-period", type=float, default=4.0, help="randomTrips period (s) for diffuse cross-traffic")
    parser.add_argument("--arterial-veh-h", type=float, default=700.0, help="explicit arterial through-flow per direction (veh/h)")
    parser.add_argument("--work", type=Path, default=ROOT / ".tools" / "boavista-osm")
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "validation" / "v4d_reference_corridor.json")
    args = parser.parse_args()
    for p in (args.net, args.busstops, args.v4b_report):
        if not p.exists():
            raise SystemExit(f"Missing {p}. Run V4 build + V4b first.")

    sumo_home = _sumo_home()
    car_trips = args.work / "boavista_car_trips.xml"
    car_routes = args.work / "boavista_car_routes.rou.xml"  # randomTrips default route output (keep out of ROOT)
    arterial_flows = args.work / "boavista_arterial_flows.xml"
    bus_flows = args.work / "boavista_bus_flows.xml"
    routed = args.work / "boavista_all_routed.rou.xml"
    webster = args.work / "boavista_webster_tls.add.xml"
    edgedata_add = args.work / "boavista_edgedata.add.xml"
    edgedata_out = args.work / "boavista_edgedata.xml"
    tripinfo = args.work / "boavista_reference_tripinfo.xml"

    # Fail-fast hygiene: drop stale tool outputs so a later "if file.exists()" can never
    # read artifacts from a previous (different) run when a tool now fails.
    for stale in (car_trips, car_routes, arterial_flows, bus_flows, routed, webster, edgedata_out, tripinfo):
        stale.unlink(missing_ok=True)

    # 1) reference background demand (randomTrips, fringe-biased through traffic)
    rt = _run([sys.executable, str(_tool(sumo_home, "randomTrips.py")), "-n", _rel(args.net),
               "-o", _rel(car_trips), "-r", _rel(car_routes), "-b", "0", "-e", str(SIM_END_S),
               "-p", str(args.car_period), "--fringe-factor", "5", "--vehicle-class", "passenger"], sumo_home)
    n_cars = len(_parse_sumo_xml(car_trips).findall("trip")) if car_trips.exists() else 0

    # 2) explicit arterial through-flows (HCM/Madrid target) + real STCP bus flows
    n_art, art_period = build_arterial_car_flows(args.v4b_report, arterial_flows, args.arterial_veh_h)
    n_flows = build_bus_flows(args.v4b_report, bus_flows)

    # 3) route arterial cars + background cars + buses together
    dua = _run(["duarouter", "-n", _rel(args.net), "--additional-files", _rel(args.busstops),
                "--route-files", f"{_rel(car_trips)},{_rel(arterial_flows)},{_rel(bus_flows)}", "-o", _rel(routed),
                "--ignore-errors", "true", "--repair", "true", "--no-step-log", "true", "--no-warnings", "true"], sumo_home)
    n_routed = len(_parse_sumo_xml(routed).findall("vehicle")) if routed.exists() else 0

    # 4) Webster signals from the routed demand (tlsCycleAdaptation = Webster 1958)
    wj = _run([sys.executable, str(_tool(sumo_home, "tlsCycleAdaptation.py")),
               "-n", _rel(args.net), "-r", _rel(routed), "-o", _rel(webster)], sumo_home)
    if webster.exists():
        # rewrite without the SUMO header comment (its '---' path is invalid XML) so
        # both ET and SUMO can load it
        webster.write_text(ET.tostring(_parse_sumo_xml(webster), encoding="unicode"), encoding="utf-8")
    n_tls = len(_parse_sumo_xml(webster).findall("tlLogic")) if webster.exists() else 0

    # 5) run the corridor under Webster signals, with edge intensity output
    edgedata_add.write_text(
        '<additional><edgeData id="ed" file="%s" begin="0" end="%d"/></additional>\n'
        % (edgedata_out.name, SIM_END_S), encoding="utf-8")  # file= is resolved next to the add file
    adds = ",".join(_rel(p) for p in ([webster, args.busstops, edgedata_add] if n_tls else [args.busstops, edgedata_add]))
    # No --end: demand departs through SIM_END_S, so capping SUMO at the same time would
    # drop late departures still finishing their trips from the KPIs. Let SUMO run until all
    # vehicles arrive. Explicit high time-to-teleport so the KPI run does not fall back to
    # SUMO's 300 s default (which teleports normal queues and skews KPIs); 900 s still lets a
    # genuine deadlock resolve rather than gridlocking the whole run.
    sumo = _run(["sumo", "-n", _rel(args.net), "--additional-files", adds, "-r", _rel(routed),
                 "--tripinfo-output", _rel(tripinfo), "--no-step-log", "true",
                 "--no-warnings", "true", "--ignore-route-errors", "true",
                 "--time-to-teleport", "900"], sumo_home)

    kpis = parse_tripinfo(tripinfo) if tripinfo.exists() else {}
    arterial = measure_arterial_intensity(edgedata_out, bus_route_edges(routed)) if edgedata_out.exists() else {}
    p90 = arterial.get("p90_veh_h")
    # In-band = arterial p90 at least the documented Madrid median and at most its P90;
    # the carriageway should carry no less than the citywide median intensity.
    in_band = bool(p90 is not None and MADRID_BAND["median"] <= p90 <= MADRID_BAND["p90"])

    report = {
        "validation_phase": "V4d_reference_demand_and_webster_signals",
        "demand": {
            "model": "explicit arterial through-flows (HCM/Madrid target) + randomTrips diffuse cross-traffic",
            "arterial_flow_per_dir_veh_h": args.arterial_veh_h, "arterial_flow_period_s": art_period,
            "background_car_trips": n_cars, "background_period_s": args.car_period,
            "arterial_intensity_measured": arterial, "madrid_reference_band_veh_h": MADRID_BAND,
            "arterial_p90_in_madrid_band": in_band,
            "hcm_anchor": "HCM-derived ~1224 veh/h inbound (~612 veh/h/lane); see configs calibration_methodology",
        },
        "signals": {"tool": "SUMO tlsCycleAdaptation (Webster 1958)", "tls_programs_optimised": n_tls,
                    "note": "per-intersection Webster; coordination (green wave) would need tlsCoordinator.py"},
        "run": {"vehicles_routed": n_routed, "bus_flows": n_flows, "arterial_flows": n_art,
                "duarouter_ok": dua.returncode == 0, "webster_ok": wj.returncode == 0,
                "sumo_ok": sumo.returncode == 0},
        "kpis_all_vehicles": kpis.get("all_vehicles", {}),
        "kpis_buses": kpis.get("buses", {}),
        "honest_notes": [
            "Demand is reference-ADAPTED (HCM magnitudes, validated vs measured Madrid intensities), not Porto-measured (V2/CMP).",
            "Signals are Webster-optimal for this demand, not the actual CMP plans.",
            "randomTrips OD is synthetic but rate-calibrated to the referenced arterial band.",
        ],
        "verdict": "pass" if (n_tls > 0 and dua.returncode == 0 and sumo.returncode == 0
                              and kpis.get("buses", {}).get("vehicles", 0) > 0 and in_band) else "review",
    }
    if report["verdict"] != "pass":
        report["debug"] = {"randomtrips_tail": (rt.stderr.strip().splitlines() or [""])[-2:],
                           "duarouter_tail": (dua.stderr.strip().splitlines() or [""])[-2:],
                           "webster_tail": (wj.stderr.strip().splitlines() or [""])[-2:],
                           "sumo_tail": (sumo.stderr.strip().splitlines() or [""])[-2:]}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    print(f"V4d reference corridor — cars {n_cars} (p={args.car_period}s), Webster TLS {n_tls}, routed {n_routed}")
    print(f"  arterial p90 {arterial.get('p90_veh_h')} veh/h (median {arterial.get('median_veh_h')})  Madrid band {MADRID_BAND['median']}-{MADRID_BAND['p90']}  in_band={in_band}")
    b = kpis.get("buses", {})
    print(f"  buses: {b.get('vehicles', 0)}  mean duration {b.get('mean_duration_s')}s  time loss {b.get('mean_time_loss_s')}s")
    print(f"  verdict: {report['verdict']}  -> {args.out}")
    if report["verdict"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
