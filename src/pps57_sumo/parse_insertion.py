"""Parse SUMO summary/statistic outputs to surface insertion-failure KPIs.

A non-zero backlog (waiting vehicles) indicates the network rejected insertions
because of congestion at the entry edges. Tracking this matters because heavy
TSP intervention can starve cross-streets enough to back up insertions; without
this KPI, capacity loss would be invisible in the tripinfo-derived metrics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# M4: defusedxml em vez do stdlib — summary/statistics vêm de simulações
# externas e podem conter DTD/entidades maliciosas (XXE/billion-laughs).
try:
    from defusedxml import ElementTree as ET  # type: ignore[import-untyped]
    from defusedxml.common import DefusedXmlException  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - exercised in minimal CI images.
    from xml.etree import ElementTree as ET  # type: ignore[no-redef]

    class DefusedXmlException(Exception):  # type: ignore[no-redef]
        """Unreachable stub — defusedxml not installed, so its exceptions cannot fire."""


def _int_attr(elem: Any, name: str, default: str = "0") -> int:
    """Coerce an XML attribute to int, tolerating SUMO's decimal strings ("123.0").

    SUMO occasionally emits integer-valued counters as decimal strings; a bare
    int("123.0") raises ValueError, so go through float() first. Callers run inside
    a try that flags a parse error, so a genuinely malformed value still propagates
    (ValueError/TypeError) to be surfaced rather than silently treated as 0.
    """
    return int(float(elem.attrib.get(name, default)))


def parse_insertion_kpis(summary_path: Path | None, statistics_path: Path | None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "summary_available": False,
        "statistics_available": False,
        "safety_statistics_complete": False,
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
                waiting = _int_attr(elem, "waiting")
                last_waiting = waiting
                if waiting > max_waiting:
                    max_waiting = waiting
                    max_waiting_t = float(elem.attrib.get("time", "0"))
                if waiting > 0:
                    backlog_intervals += 1
                last_loaded = _int_attr(elem, "loaded")
                last_inserted = _int_attr(elem, "inserted")
                last_running = _int_attr(elem, "running")
                last_step = float(elem.attrib.get("time", last_step))
                elem.clear()
        except (ET.ParseError, DefusedXmlException):
            # B18: flag the summary failure but DON'T return — the statistics block
            # below carries the safety telemetry and must still be parsed even when
            # summary.xml is malformed (the early return dropped all safety KPIs).
            out["parse_error"] = True
        else:
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
                out["vehicles_loaded"] = _int_attr(vehicles, "loaded")
                out["vehicles_inserted"] = _int_attr(vehicles, "inserted")
                out["vehicles_running"] = _int_attr(vehicles, "running")
                out["vehicles_waiting"] = _int_attr(vehicles, "waiting")
            teleports = root.find("teleports")
            if teleports is not None:
                out["teleports_total"] = _int_attr(teleports, "total")
                out["teleports_jam"] = _int_attr(teleports, "jam")
                out["teleports_yield"] = _int_attr(teleports, "yield")
                out["teleports_wrongLane"] = _int_attr(teleports, "wrongLane")
            safety = root.find("safety")
            if safety is not None:
                out["collisions"] = _int_attr(safety, "collisions")
                out["emergency_stops"] = _int_attr(safety, "emergencyStops")
                out["emergency_braking"] = _int_attr(safety, "emergencyBraking")
            # Fail-closed completeness (B4): run_verdict's safety gates read
            # collisions/emergency_braking (<safety>), teleports_total/jam
            # (<teleports>) and vehicles_waiting (<vehicles>). A present-but-empty
            # statistics.xml (aborted/short TraCI run) parses cleanly yet carries
            # none of these, so signal completeness explicitly instead of letting the
            # verdict read the missing counters as 0 via `or 0` and pass silently.
            out["safety_statistics_complete"] = (
                vehicles is not None and teleports is not None and safety is not None
            )
        # _int_attr goes through float() for SUMO's decimal-string counters, which
        # can raise ValueError on a malformed value; the old `except (ParseError, ...)`
        # did NOT catch that, leaving an unhandled crash instead of a flagged parse
        # error (B4). ValueError/TypeError are now caught and surfaced as
        # statistics_parse_error so the verdict treats the safety telemetry as missing
        # rather than silently passing.
        except (ET.ParseError, DefusedXmlException, ValueError, TypeError):
            out["statistics_parse_error"] = True

    return out
