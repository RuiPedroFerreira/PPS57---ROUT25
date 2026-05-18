#!/usr/bin/env python3
"""Parse SUMO tripinfo output and generate baseline KPIs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from xml.etree import ElementTree as ET


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


def parse_tripinfo(path: Path) -> dict:
    tree = ET.parse(path)
    rows = []
    for node in tree.getroot().iter("tripinfo"):
        vehicle_id = node.attrib.get("id", "")
        vehicle_type = node.attrib.get("vType", "")
        is_bus = vehicle_id.startswith("bus_") or vehicle_type == "bus"
        rows.append({
            "id": vehicle_id,
            "vType": vehicle_type,
            "is_bus": is_bus,
            "duration": _num(node.attrib.get("duration")),
            "waitingTime": _num(node.attrib.get("waitingTime")),
            "timeLoss": _num(node.attrib.get("timeLoss")),
            "departDelay": _num(node.attrib.get("departDelay")),
        })

    def group(is_bus: bool | None) -> list[dict]:
        if is_bus is None:
            return rows
        return [r for r in rows if r["is_bus"] == is_bus]

    def summarize(items: list[dict]) -> dict:
        return {
            "vehicles": len(items),
            "mean_duration_s": _mean([r["duration"] for r in items]),
            "mean_waiting_time_s": _mean([r["waitingTime"] for r in items]),
            "mean_time_loss_s": _mean([r["timeLoss"] for r in items]),
            "mean_depart_delay_s": _mean([r["departDelay"] for r in items]),
        }

    return {
        "source": str(path),
        "all_vehicles": summarize(group(None)),
        "buses": summarize(group(True)),
        "general_traffic": summarize(group(False)),
    }


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
