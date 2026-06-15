"""Parse SUMO summary/statistic outputs to surface insertion-failure KPIs.

A non-zero backlog (waiting vehicles) indicates the network rejected insertions
because of congestion at the entry edges. Tracking this matters because heavy
TSP intervention can starve cross-streets enough to back up insertions; without
this KPI, capacity loss would be invisible in the tripinfo-derived metrics.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

# M4: defusedxml em vez do stdlib — summary/statistics vêm de simulações
# externas e podem conter DTD/entidades maliciosas (XXE/billion-laughs).
try:
    from defusedxml import ElementTree as ET  # type: ignore[import-untyped]
    from defusedxml.common import DefusedXmlException  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - exercised in minimal CI images.
    from xml.etree import ElementTree as ET  # type: ignore[no-redef]

    class DefusedXmlException(Exception):  # type: ignore[no-redef]
        """Unreachable stub — defusedxml not installed, so its exceptions cannot fire."""


def parse_insertion_kpis(summary_path: Path | None, statistics_path: Path | None) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "summary_available": False,
        "statistics_available": False,
    }

    if summary_path is not None and Path(summary_path).exists():
        out["summary_available"] = True
        max_waiting = 0
        max_waiting_t = 0.0
        last_loaded = 0
        last_inserted = 0
        last_running = 0
        last_step = 0.0
        steps = 0
        backlog_intervals = 0
        last_waiting = 0
        try:
            for _, elem in ET.iterparse(str(summary_path), events=("end",)):
                if elem.tag != "step":
                    continue
                steps += 1
                waiting = int(float(elem.attrib.get("waiting", "0")))
                last_waiting = waiting
                if waiting > max_waiting:
                    max_waiting = waiting
                    max_waiting_t = float(elem.attrib.get("time", "0"))
                if waiting > 0:
                    backlog_intervals += 1
                last_loaded = int(float(elem.attrib.get("loaded", "0")))
                last_inserted = int(float(elem.attrib.get("inserted", "0")))
                last_running = int(float(elem.attrib.get("running", "0")))
                last_step = float(elem.attrib.get("time", last_step))
                elem.clear()
        except (ET.ParseError, DefusedXmlException):
            out["parse_error"] = True
            return out
        out["max_waiting_to_insert"] = max_waiting
        out["max_waiting_at_time_s"] = max_waiting_t
        out["steps"] = steps
        out["backlog_step_count"] = backlog_intervals
        out["final_loaded"] = last_loaded
        out["final_inserted"] = last_inserted
        out["final_running"] = last_running
        out["final_time_s"] = last_step
        # Genuine end-of-run insertion backlog = the final `waiting` count
        # (vehicles whose depart time was reached but could not be inserted).
        # `loaded - inserted` would over-report here: SUMO loads vehicles ahead
        # of their depart time (route look-ahead), so on shortened runs
        # (`--steps`/smoke) it counts not-yet-due future departures as a gap and
        # trips the strict gate even when nothing is actually stuck. The earlier
        # `- running` term was also wrong (running ⊆ inserted, double-count).
        out["insertion_gap_at_end"] = max(0, last_waiting)
        out["final_waiting"] = last_waiting

    if statistics_path is not None and Path(statistics_path).exists():
        out["statistics_available"] = True
        try:
            tree = ET.parse(str(statistics_path))
            root = tree.getroot()
            vehicles = root.find("vehicles")
            if vehicles is not None:
                out["vehicles_loaded"] = int(vehicles.attrib.get("loaded", "0"))
                out["vehicles_inserted"] = int(vehicles.attrib.get("inserted", "0"))
                out["vehicles_running"] = int(vehicles.attrib.get("running", "0"))
                out["vehicles_waiting"] = int(vehicles.attrib.get("waiting", "0"))
            teleports = root.find("teleports")
            if teleports is not None:
                out["teleports_total"] = int(teleports.attrib.get("total", "0"))
                out["teleports_jam"] = int(teleports.attrib.get("jam", "0"))
                out["teleports_yield"] = int(teleports.attrib.get("yield", "0"))
                out["teleports_wrongLane"] = int(teleports.attrib.get("wrongLane", "0"))
            safety = root.find("safety")
            if safety is not None:
                out["collisions"] = int(safety.attrib.get("collisions", "0"))
                out["emergency_stops"] = int(safety.attrib.get("emergencyStops", "0"))
                out["emergency_braking"] = int(safety.attrib.get("emergencyBraking", "0"))
        except (ET.ParseError, DefusedXmlException):
            out["statistics_parse_error"] = True

    return out
