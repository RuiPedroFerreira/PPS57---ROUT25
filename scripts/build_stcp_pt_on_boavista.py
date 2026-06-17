#!/usr/bin/env python3
"""V4b: instantiate the real STCP corridor PT (lines 500/502/204) on the real net.

Maps the real GTFS stops of the corridor lines onto the real OSM-derived Boavista
network edges and emits SUMO bus stops on real lanes, a regenerated PT services
structure carrying the real (V3) headways including line 204, and a validation
report. The stop -> edge projection uses the net's own ``<location>`` metadata
(affine over the small corridor area) plus sumolib nearest-edge snapping, so the
locked .venv is untouched — no pyproj / rtree / pandas.

Nothing is invented: stop ids, coordinates and headways all come from the real
STCP GTFS feed; edges from the real OSM net. ``gtfs2pt`` is deliberately not used
(it needs heavy native deps that would pollute the locked environment).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from statistics import fmean, median

import sumolib  # ships with SUMO; already in .venv

ROOT = Path(__file__).resolve().parents[1]
CORRIDOR_LINES = ["500", "502", "204"]
WEEKDAY_SERVICE = "DIAS UTEIS"
SNAP_RADIUS_M = 60.0


def _gtfs_rows(zip_path: Path, name: str):
    with zipfile.ZipFile(zip_path) as zf, zf.open(name) as handle:
        return list(csv.DictReader(io.TextIOWrapper(handle, "utf-8-sig")))


def _secs(value: str) -> int | None:
    """GTFS HH:MM:SS -> seconds. Returns None for an absent/empty time:
    departure_time is only conditionally required in stop_times.txt."""
    if not value or not value.strip():
        return None
    h, m, s = (int(p) for p in value.strip().split(":"))
    return h * 3600 + m * 60 + s


def _coords(stop: dict) -> tuple[float | None, float | None]:
    """(lat, lon) from a GTFS stop row, or (None, None) when absent/invalid.
    stop_lat/stop_lon are not present for every location_type (e.g. stations,
    entrances, generic nodes)."""
    try:
        return float(stop["stop_lat"]), float(stop["stop_lon"])
    except (KeyError, ValueError, TypeError):
        return None, None


def affine_projector(orig_boundary, conv_boundary):
    """Return lon/lat -> net (x,y) using the net's location boundary corners.

    Over the small corridor area UTM is locally near-affine, so mapping the
    geo boundary box onto the net boundary box is accurate to a few metres
    (validated: median snap distance ~8 m). Approximate, but from real metadata.
    """
    min_lon, min_lat, max_lon, max_lat = orig_boundary
    min_x, min_y, max_x, max_y = conv_boundary
    ax = (max_x - min_x) / (max_lon - min_lon)
    ay = (max_y - min_y) / (max_lat - min_lat)

    def project(lon: float, lat: float):
        return (min_x + ax * (lon - min_lon), min_y + ay * (lat - min_lat))

    return project


def _headways_min(departures_s, window):
    start, end = window
    inside = sorted(d for d in departures_s if start <= d < end)
    if len(inside) < 2:
        return None
    gaps = [(inside[i] - inside[i - 1]) / 60.0 for i in range(1, len(inside))]
    return round(median(gaps), 2)


def corridor_lines(zip_path: Path, bbox):
    """Per line+direction: ordered in-bbox real stops and real weekday headways."""
    south, west, north, east = bbox
    routes = {
        r["route_id"]: r
        for r in _gtfs_rows(zip_path, "routes.txt")
        if r.get("route_short_name", "") in CORRIDOR_LINES
    }
    rid_to_short = {rid: r.get("route_short_name", "") for rid, r in routes.items()}
    trips = [
        t
        for t in _gtfs_rows(zip_path, "trips.txt")
        if t["route_id"] in rid_to_short and t["service_id"] == WEEKDAY_SERVICE
    ]
    trips_by_key = {}
    for t in trips:
        trips_by_key.setdefault(
            (rid_to_short[t["route_id"]], t.get("direction_id", "")), []
        ).append(t["trip_id"])
    stops = {s["stop_id"]: s for s in _gtfs_rows(zip_path, "stops.txt")}

    # one pass over stop_times: per trip ordered stops, and first-stop departures per key.
    # First departure = departure at the trip's MINIMUM stop_sequence (GTFS only requires
    # the sequence to increase, not to start at "1").
    target_trips = {tid for ids in trips_by_key.values() for tid in ids}
    trip_seq = {}
    trip_first = {}  # tid -> (min_seq, departure_s)
    trip_to_key = {tid: key for key, ids in trips_by_key.items() for tid in ids}
    for row in _gtfs_rows(zip_path, "stop_times.txt"):
        tid = row["trip_id"]
        if tid not in target_trips:
            continue
        seq = int(row["stop_sequence"])
        trip_seq.setdefault(tid, []).append((seq, row["stop_id"]))
        dep = _secs(row.get("departure_time", ""))
        if dep is not None and (tid not in trip_first or seq < trip_first[tid][0]):
            trip_first[tid] = (seq, dep)
    first_dep = {}
    for tid, (_seq, dep_s) in trip_first.items():
        first_dep.setdefault(trip_to_key[tid], []).append(dep_s)

    out = {}
    for (short, direction), trip_ids in sorted(trips_by_key.items()):
        # representative trip = the one whose stop sequence has the most in-bbox stops
        def in_bbox_count(tid):
            n = 0
            for _, sid in trip_seq.get(tid, []):
                s = stops.get(sid)
                if s is None:
                    continue
                lat, lon = _coords(s)
                if lat is None or lon is None:
                    continue
                if south <= lat <= north and west <= lon <= east:
                    n += 1
            return n

        rep = max(trip_ids, key=in_bbox_count)
        ordered = []
        for _seq, sid in sorted(trip_seq.get(rep, [])):
            s = stops.get(sid)
            if s is None:
                continue
            lat, lon = _coords(s)
            if lat is None or lon is None:
                continue
            if south <= lat <= north and west <= lon <= east:
                ordered.append(
                    {"stop_id": sid, "stop_name": s.get("stop_name", ""), "lat": lat, "lon": lon}
                )
        deps = first_dep.get((short, direction), [])
        out[f"{short}:{direction}"] = {
            "line": short,
            "direction": direction,
            "stops_in_bbox": ordered,
            "weekday_trips": len(deps),
            "headway_am_peak_min": _headways_min(deps, (7 * 3600, 9 * 3600)),
            "headway_allday_min": _headways_min(deps, (5 * 3600, 24 * 3600)),
        }
    return out


def snap_stops(net, project, services):
    """Snap each unique stop to its nearest bus-capable edge/lane; mutate services in place."""
    bus_stops = {}
    for entry in services.values():
        for stop in entry["stops_in_bbox"]:
            sid = stop["stop_id"]
            x, y = project(stop["lon"], stop["lat"])
            cands = [
                (e, d) for e, d in net.getNeighboringEdges(x, y, SNAP_RADIUS_M) if e.allows("bus")
            ]
            cands.sort(key=lambda ed: ed[1])
            if not cands:
                stop["snapped"] = False
                continue
            edge, dist = cands[0]
            lane = next((ln for ln in edge.getLanes() if ln.allows("bus")), edge.getLanes()[0])
            pos, _ = lane.getClosestLanePosAndDist((x, y))
            length = lane.getLength()
            stop.update(
                {
                    "snapped": True,
                    "edge": edge.getID(),
                    "lane": lane.getID(),
                    "lane_pos_m": round(pos, 2),
                    "snap_dist_m": round(dist, 2),
                }
            )
            bus_stops[sid] = {
                "id": f"bs_{sid}",
                "lane": lane.getID(),
                "startPos": round(max(0.0, pos - 15.0), 2),
                "endPos": round(min(length, pos + 15.0), 2),
                "name": stop["stop_name"],
            }
    return bus_stops


def write_busstops_add(bus_stops, path: Path) -> None:
    root = ET.Element("additional")
    for bs in bus_stops.values():
        ET.SubElement(
            root,
            "busStop",
            {
                "id": bs["id"],
                "lane": bs["lane"],
                "startPos": str(bs["startPos"]),
                "endPos": str(bs["endPos"]),
                "name": bs["name"],
            },
        )
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def validate_sumo_load(net_path: Path, add_path: Path) -> dict:
    proc = subprocess.run(
        [
            "sumo",
            "-n",
            str(net_path),
            "-a",
            str(add_path),
            "--no-step-log",
            "true",
            "--end",
            "1",
            "--no-warnings",
            "true",
        ],
        capture_output=True,
        text=True,
    )
    return {
        "sumo_loads_busstops": proc.returncode == 0,
        "stderr_tail": (proc.stderr.strip().splitlines() or [""])[-1][:200],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--gtfs", type=Path, default=ROOT / ".tools" / "stcp-gtfs" / "gtfs_feed.zip"
    )
    parser.add_argument(
        "--net", type=Path, default=ROOT / ".tools" / "boavista-osm" / "boavista.net.xml"
    )
    parser.add_argument(
        "--add-out",
        type=Path,
        default=ROOT / ".tools" / "boavista-osm" / "boavista_pt_stops.add.xml",
    )
    parser.add_argument(
        "--out", type=Path, default=ROOT / "docs" / "validation" / "v4b_stcp_pt_mapping.json"
    )
    args = parser.parse_args()

    for path in (args.gtfs, args.net):
        if not path.exists():
            raise SystemExit(
                f"Missing {path}. Run scripts/fetch_stcp_gtfs.py and scripts/build_boavista_network.py first."
            )

    location = ET.parse(args.net).getroot().find("location")
    orig = [float(v) for v in location.get("origBoundary").split(",")]
    conv = [float(v) for v in location.get("convBoundary").split(",")]
    project = affine_projector(orig, conv)
    net = sumolib.net.readNet(str(args.net))
    bbox = (orig[1], orig[0], orig[3], orig[2])  # the net's own geo extent (S,W,N,E)

    services = corridor_lines(args.gtfs, bbox)
    bus_stops = snap_stops(net, project, services)
    args.add_out.parent.mkdir(parents=True, exist_ok=True)
    write_busstops_add(bus_stops, args.add_out)
    load = validate_sumo_load(args.net, args.add_out)

    all_stops = [s for e in services.values() for s in e["stops_in_bbox"]]
    snapped = [s for s in all_stops if s.get("snapped")]
    dists = [s["snap_dist_m"] for s in snapped]
    lines_present = sorted({e["line"] for e in services.values()})

    # Classify misses: stops near the net's clipped boundary are expected losses
    # (the road continues outside the extracted corridor); a miss in the interior
    # would be a real snapping gap.
    south, west, north, east = bbox
    boundary_zone_m = 200.0

    def _edge_dist_m(stop):
        dlat = min(stop["lat"] - south, north - stop["lat"]) * 111320.0
        dlon = min(stop["lon"] - west, east - stop["lon"]) * 111320.0 * 0.75
        return min(dlat, dlon)

    unsnapped = [s for s in all_stops if not s.get("snapped")]
    boundary_losses = [s for s in unsnapped if _edge_dist_m(s) <= boundary_zone_m]
    interior_gaps = [s for s in unsnapped if _edge_dist_m(s) > boundary_zone_m]

    report = {
        "validation_phase": "V4b_stcp_pt_on_real_boavista_net",
        "source_of_truth": {
            "gtfs": json.loads((args.gtfs.parent / "PROVENANCE.json").read_text())
            if (args.gtfs.parent / "PROVENANCE.json").exists()
            else {},
            "net": json.loads((args.net.parent / "NET_PROVENANCE.json").read_text())
            if (args.net.parent / "NET_PROVENANCE.json").exists()
            else {},
            "projection": "affine from net <location> origBoundary->convBoundary (UTM z29, local approx)",
        },
        "lines_mapped": lines_present,
        "line_204_added": "204" in lines_present,
        "all_corridor_lines_present": set(CORRIDOR_LINES).issubset(lines_present),
        "stop_mapping": {
            "total_in_bbox": len(all_stops),
            "snapped_to_bus_edge": len(snapped),
            "boundary_clipping_losses": len(boundary_losses),
            "interior_snapping_gaps": len(interior_gaps),
            "snap_dist_m_median": round(median(dists), 2) if dists else None,
            "snap_dist_m_mean": round(fmean(dists), 2) if dists else None,
            "snap_dist_m_max": round(max(dists), 2) if dists else None,
        },
        "services": services,
        "busstops_emitted": len(bus_stops),
        "sumo_load_validation": load,
        "honest_notes": [
            "Projection is affine from the net's own location metadata (no pyproj/PROJ); "
            "validated at ~8 m median snap distance.",
            "Only the corridor segment inside the net bbox is instantiated; stops beyond it are dropped.",
            "Headways are the real weekday GTFS values (V3); signal timings remain synthetic (V4).",
            "gtfs2pt not used: it needs rtree+pandas (heavy native deps) that would pollute the locked .venv.",
            "This emits bus stops + a regenerated services structure on the real net; wiring full bus "
            "flows/routes into a runnable scenario (duarouter) is the remaining step.",
            f"Stops that did not snap ({len(unsnapped)}) are all within {boundary_zone_m:.0f} m of the "
            "net boundary (roads clipped by the corridor bbox), not interior gaps."
            if not interior_gaps
            else f"{len(interior_gaps)} stop(s) failed to snap in the interior (real gaps).",
        ],
        # Pass = ALL corridor lines (500/502/204) instantiated, SUMO loads the stops, and
        # every non-snapped stop is a boundary-clipping loss (zero interior snapping gaps).
        "verdict": "pass"
        if (
            set(CORRIDOR_LINES).issubset(lines_present)
            and len(snapped) > 0
            and load["sumo_loads_busstops"]
            and not interior_gaps
        )
        else "review",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(
        f"V4b STCP PT on real Boavista net — lines {lines_present} (204 added: {'204' in lines_present})"
    )
    print(
        f"  stops in bbox: {len(all_stops)}  snapped: {len(snapped)}  "
        f"median {report['stop_mapping']['snap_dist_m_median']}m  "
        f"(boundary-clipping misses: {len(boundary_losses)}, interior gaps: {len(interior_gaps)})"
    )
    for _key, e in sorted(services.items()):
        print(
            f"  line {e['line']} dir{e['direction']}: {len(e['stops_in_bbox'])} stops | "
            f"real headway AM={e['headway_am_peak_min']}min allday={e['headway_allday_min']}min"
        )
    print(
        f"  SUMO loads bus stops: {load['sumo_loads_busstops']}   verdict: {report['verdict']}   -> {args.out}"
    )
    if report["verdict"] != "pass":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
