#!/usr/bin/env python3
"""Parse SUMO detector outputs into queue/occupancy KPIs."""

from __future__ import annotations

from pathlib import Path
from statistics import mean

try:
    from defusedxml import ElementTree as ET  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    from xml.etree import ElementTree as ET  # type: ignore[no-redef]


def parse_detector_kpis(e1_path: Path | None = None, e2_path: Path | None = None) -> dict:
    payload = {
        "e1_source": str(e1_path) if e1_path else None,
        "e2_source": str(e2_path) if e2_path else None,
        "e1": _parse_e1(e1_path) if e1_path and e1_path.exists() else {},
        "e2": _parse_e2(e2_path) if e2_path and e2_path.exists() else {},
    }
    payload["network_queue"] = _network_queue_summary(payload["e2"])
    return payload


def _parse_e1(path: Path) -> dict:
    intervals = list(_intervals(path))
    return {
        "intervals": len(intervals),
        "mean_occupancy_pct": _avg(_num(item.get("occupancy")) for item in intervals),
        "mean_speed_mps": _avg(_num(item.get("speed")) for item in intervals),
        "vehicles_seen": int(sum(_num(item.get("nVehContrib")) or 0 for item in intervals)),
    }


def _parse_e2(path: Path) -> dict:
    by_detector: dict[str, list[dict]] = {}
    by_edge: dict[str, list[dict]] = {}
    for interval in _intervals(path):
        detector_id = str(interval.get("id", "unknown"))
        edge_id = _edge_from_detector(detector_id)
        by_detector.setdefault(detector_id, []).append(interval)
        by_edge.setdefault(edge_id, []).append(interval)
    return {
        "detectors": {key: _queue_summary(items) for key, items in sorted(by_detector.items())},
        "edges": {key: _queue_summary(items) for key, items in sorted(by_edge.items())},
    }


def _network_queue_summary(e2_payload: dict) -> dict:
    edge_summaries = list(e2_payload.get("edges", {}).values())
    if not edge_summaries:
        return {
            "edge_count": 0,
            "max_queue_vehicles": None,
            "mean_queue_vehicles": None,
            "mean_occupancy_pct": None,
            "edge_intervals_above_8_veh": 0,
        }
    return {
        "edge_count": len(edge_summaries),
        "max_queue_vehicles": max(item["max_queue_vehicles"] or 0 for item in edge_summaries),
        "mean_queue_vehicles": _avg(item["mean_queue_vehicles"] for item in edge_summaries),
        "mean_occupancy_pct": _avg(item["mean_occupancy_pct"] for item in edge_summaries),
        # Network-level name is `edge_intervals_above_8_veh`: it is the SUM over edges
        # of each edge's congested-interval count, i.e. edge×interval occurrences (it
        # scales with network size), not the number of time-intervals the whole
        # network exceeded 8. The per-detector count below stays `intervals_above_8_veh`.
        "edge_intervals_above_8_veh": int(
            sum(item["intervals_above_8_veh"] for item in edge_summaries)
        ),
    }


def _queue_summary(items: list[dict]) -> dict:
    # SUMO E2 detector emits `meanMaxJamLengthInVehicles` (per-interval mean of
    # the max jam length observed during that interval); there is no plain
    # `meanJamLengthInVehicles` attribute. Using the wrong name silently
    # produced `mean_queue_vehicles: null` for every detector.
    mean_queues = [_num(item.get("meanMaxJamLengthInVehicles")) for item in items]
    max_queues = [_num(item.get("maxJamLengthInVehicles")) for item in items]
    occupancies = [_num(item.get("meanOccupancy")) for item in items]
    speeds = [_num(item.get("meanSpeed")) for item in items]
    return {
        "intervals": len(items),
        "mean_queue_vehicles": _avg(mean_queues),
        "max_queue_vehicles": _max(max_queues),
        "mean_occupancy_pct": _avg(occupancies),
        "mean_speed_mps": _avg(speeds),
        "intervals_above_8_veh": sum(1 for value in max_queues if value is not None and value >= 8),
    }


def _intervals(path: Path) -> list[dict]:
    root = ET.parse(path).getroot()
    return [dict(node.attrib) for node in root.iter("interval")]


def _edge_from_detector(detector_id: str) -> str:
    value = detector_id
    if value.startswith(("e1_", "e2_")):
        value = value[3:]
    parts = value.split("_")
    if parts and parts[-1].isdigit():
        parts = parts[:-1]
    return "_".join(parts) if parts else detector_id


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _avg(values) -> float | None:  # type: ignore[no-untyped-def]
    cleaned = [value for value in values if value is not None]
    return round(mean(cleaned), 3) if cleaned else None


def _max(values: list[float | None]) -> float | None:
    cleaned = [value for value in values if value is not None]
    return round(max(cleaned), 3) if cleaned else None
