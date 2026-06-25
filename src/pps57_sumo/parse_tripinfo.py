#!/usr/bin/env python3
"""Parse SUMO tripinfo output and generate baseline KPIs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from statistics import mean

SRC = Path(__file__).resolve().parents[1]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_sumo.vehicle_classification import DEFAULT_BUS_ID_PREFIXES, is_bus_like  # noqa: E402

# M4: defusedxml em vez do stdlib — tripinfo vem de simulações externas.
try:
    from defusedxml import ElementTree as ET  # type: ignore[import-untyped]
    from defusedxml.common import DefusedXmlException  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - exercised in minimal CI images.
    from xml.etree import ElementTree as ET  # type: ignore[no-redef]

    class DefusedXmlException(Exception):  # type: ignore[no-redef]
        """Unreachable stub — defusedxml not installed, so its exceptions cannot fire."""


def _num(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _mean(values: list[float | None]) -> float | None:
    cleaned = [v for v in values if v is not None]
    return round(mean(cleaned), 3) if cleaned else None


def _sum(values: list[float | None]) -> float | None:
    cleaned = [v for v in values if v is not None]
    return round(sum(cleaned), 3) if cleaned else None


def _percentile(values: list[float | None], percentile: float) -> float | None:
    cleaned = sorted(v for v in values if v is not None)
    if not cleaned:
        return None
    index = min(len(cleaned) - 1, max(0, round((len(cleaned) - 1) * percentile)))
    return round(cleaned[index], 3)


DEFAULT_LINE_ATTR_NAMES = ("line", "line_id", "lineID")


def parse_tripinfo(
    path: Path,
    *,
    bus_id_prefixes: tuple[str, ...] = DEFAULT_BUS_ID_PREFIXES,
    line_attr_names: tuple[str, ...] = DEFAULT_LINE_ATTR_NAMES,
) -> dict:
    rows = []
    try:
        # Stream tripinfo rows: city-wide dumps hold tens of thousands of trips, so
        # iterparse + elem.clear()/root.clear() keeps memory flat where the previous
        # ET.parse built the entire DOM up front (mirrors parse_emissions).
        context = ET.iterparse(str(path), events=("start", "end"))
        root = None
        for event, node in context:
            if event == "start":
                if root is None:
                    root = node
                continue
            if node.tag != "tripinfo":
                continue
            vehicle_id = node.attrib.get("id", "")
            vehicle_type = node.attrib.get("vType", "")
            attrs = dict(node.attrib)
            is_bus = is_bus_like(vehicle_id, vehicle_type, bus_id_prefixes=bus_id_prefixes)
            vehicle_type_lc = vehicle_type.lower()
            is_emergency = vehicle_id.startswith(("ev_", "emergency_")) or vehicle_type_lc in {
                "emergency_vehicle",
                "emergency",
            }
            is_priority = is_bus or is_emergency
            rows.append(
                {
                    "id": vehicle_id,
                    "vType": vehicle_type,
                    "is_bus": is_bus,
                    "is_emergency": is_emergency,
                    "is_priority": is_priority,
                    "line_key": _line_key(vehicle_id, attrs, line_attr_names),
                    "direction": _direction_key(vehicle_id, attrs),
                    "depart": _num(node.attrib.get("depart")),
                    "arrival": _num(node.attrib.get("arrival")),
                    "duration": _num(node.attrib.get("duration")),
                    "routeLength": _num(node.attrib.get("routeLength")),
                    "waitingTime": _num(node.attrib.get("waitingTime")),
                    "timeLoss": _num(node.attrib.get("timeLoss")),
                    "departDelay": _num(node.attrib.get("departDelay")),
                    "waitingCount": _num(node.attrib.get("waitingCount")),
                }
            )
            node.clear()
            if root is not None:
                root.clear()
    except (ET.ParseError, DefusedXmlException, OSError) as exc:
        # B12: flag a real parse failure so the verdict can fail with the right
        # reason. Without it, the mutilated {source,error} dict (no all_vehicles
        # block) was read downstream as "no completed vehicles" — the wrong reason.
        return {"source": str(path), "error": str(exc), "tripinfo_parse_error": True}

    def group(field: str | None, expected: bool | None = None) -> list[dict]:
        if field is None:
            return rows
        return [r for r in rows if r[field] == expected]

    def buses_in_direction(direction: str) -> list[dict]:
        # Split de autocarros por sentido. Cenários focados num heading (ex.
        # delayed_bus_westbound) diluem o efeito na média dos dois sentidos do grupo
        # `buses`; isto isola-o. Grupo vazio -> stats None (não rebenta), como em
        # `emergency_vehicles` nos cenários sem emergência.
        return [r for r in rows if r["is_bus"] and r["direction"] == direction]

    return {
        "source": str(path),
        "all_vehicles": _summarize_items(group(None)),
        "buses": _summarize_items(group("is_bus", True)),
        "buses_westbound": _summarize_items(buses_in_direction("W")),
        "buses_eastbound": _summarize_items(buses_in_direction("E")),
        "emergency_vehicles": _summarize_items(group("is_emergency", True)),
        "priority_vehicles": _summarize_items(group("is_priority", True)),
        "general_traffic": _summarize_items(group("is_priority", False)),
        "bus_lines": _bus_lines(rows),
        "bus_headways": _bus_headways(rows),
    }


def _line_key(
    vehicle_id: str,
    attrs: dict[str, str] | None = None,
    line_attr_names: tuple[str, ...] = DEFAULT_LINE_ATTR_NAMES,
) -> str:
    attrs = attrs or {}
    for attr_name in line_attr_names:
        value = attrs.get(attr_name)
        if value:
            return value
    parts = vehicle_id.split("_")
    if len(parts) >= 3 and parts[0] in {"bus", "Bus"}:
        return parts[1]
    return ""


def _direction_key(vehicle_id: str, attrs: dict[str, str] | None = None) -> str:
    attrs = attrs or {}
    for attr_name in ("direction", "dir"):
        value = attrs.get(attr_name)
        if value:
            return value
    parts = vehicle_id.split("_")
    if len(parts) >= 4 and parts[0] in {"bus", "Bus"}:
        return parts[2]
    return ""


def _summarize_items(items: list[dict]) -> dict:
    return {
        "vehicles": len(items),
        "mean_duration_s": _mean([row["duration"] for row in items]),
        "p95_duration_s": _percentile([row["duration"] for row in items], 0.95),
        "mean_route_length_m": _mean([row["routeLength"] for row in items]),
        # B11/B27: the true fleet distance is the SUM of per-vehicle route lengths,
        # not mean_route_length_m × vehicles (only exact when all routes are equal).
        # Consumers must normalise per-vehicle-km against this, not the mean×count.
        "total_route_length_m": _sum([row["routeLength"] for row in items]),
        # B36: journey speed = routeLength/duration INCLUDES stopped time, so it
        # diverges (lower) from instantaneous detector speeds. Labelled accordingly.
        "mean_speed_mps": _mean(
            [
                (row["routeLength"] / row["duration"])
                if row["routeLength"] is not None
                and row["duration"] is not None
                and row["duration"] > 0
                else None
                for row in items
            ]
        ),
        "mean_waiting_time_s": _mean([row["waitingTime"] for row in items]),
        "mean_time_loss_s": _mean([row["timeLoss"] for row in items]),
        "mean_depart_delay_s": _mean([row["departDelay"] for row in items]),
        "p95_time_loss_s": _percentile([row["timeLoss"] for row in items], 0.95),
        # B35: SUMO `waitingCount` counts stop/slow EPISODES (incl. congestion
        # stalls), not visits to scheduled stops — surfaced/labelled as such.
        "mean_stop_count": _mean([row["waitingCount"] for row in items]),
    }


def _bus_lines(rows: list[dict]) -> dict:
    groups: dict[str, list[dict]] = {}
    for row in rows:
        if not row["is_bus"]:
            continue
        line_key = row.get("line_key")
        if line_key:
            groups.setdefault(line_key, []).append(row)
    return {line: _summarize_items(items) for line, items in sorted(groups.items())}


def _bus_headways(rows: list[dict]) -> dict:
    groups: dict[str, list[float]] = {}
    for row in rows:
        if not row["is_bus"]:
            continue
        key = ":".join(item for item in [row.get("line_key", ""), row.get("direction", "")] if item)
        if not key:
            continue
        depart = row.get("depart")
        if depart is not None:
            groups.setdefault(key, []).append(float(depart))

    result: dict[str, dict] = {}
    for key, departures in sorted(groups.items()):
        departures = sorted(departures)
        gaps = [departures[i] - departures[i - 1] for i in range(1, len(departures))]
        result[key] = {
            "departures": len(departures),
            "mean_headway_s": _mean(gaps),
            "min_headway_s": round(min(gaps), 3) if gaps else None,
            "max_headway_s": round(max(gaps), 3) if gaps else None,
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tripinfo", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    if not args.tripinfo.exists():
        raise FileNotFoundError(f"Tripinfo file not found: {args.tripinfo}")

    kpis = parse_tripinfo(args.tripinfo)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(kpis, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(kpis, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
