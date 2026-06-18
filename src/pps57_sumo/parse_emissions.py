"""Aggregate per-vehicle emission/fuel KPIs from a SUMO emission-output XML.

SUMO emits per-step `<vehicle>` entries within `<timestep>` elements whose
CO2/NOx/PMx/fuel values are **per-step rates** (mg or ml emitted during that
step), NOT cumulative — the per-vehicle series is non-monotone. Total emissions
over a vehicle's trip are therefore the SUM of its per-step observations.
Sustainability claims (TSP reduces CO2, fuel use, etc.) are only verifiable once
this file is parsed — otherwise the KPIs are absent from the run summary.
"""

from __future__ import annotations

from pathlib import Path
from statistics import mean
from typing import Any

try:
    from defusedxml import ElementTree as ET  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - exercised in minimal CI images.
    from xml.etree import ElementTree as ET  # type: ignore[no-redef]


METRICS = ("CO2", "CO", "NOx", "PMx", "HC", "fuel", "electricity")
BUS_ID_PREFIXES = ("bus_", "Bus")
BUS_TYPE_NAMES = {"stcp_bus", "transit_bus"}


def parse_emissions(path: Path | None) -> dict[str, Any]:
    """Return aggregated emission/fuel KPIs for vehicles in a SUMO emission file.

    Missing or empty file => ``{"available": False}`` (callers can skip cleanly).
    """
    out: dict[str, Any] = {"available": False, "source": str(path) if path else None}
    if path is None or not Path(path).exists():
        return out

    per_vehicle: dict[str, dict[str, float]] = {}
    per_vehicle_type: dict[str, str] = {}

    try:
        for _event, elem in ET.iterparse(str(path), events=("end",)):
            if elem.tag != "vehicle":
                continue
            vid = elem.attrib.get("id", "")
            if not vid:
                elem.clear()
                continue
            bucket = per_vehicle.setdefault(vid, {})
            for metric in METRICS:
                value = elem.attrib.get(metric)
                if value is None:
                    continue
                try:
                    # Valores por-step (não cumulativos): o total da viagem é a soma.
                    bucket[metric] = bucket.get(metric, 0.0) + float(value)
                except ValueError:
                    continue
            v_type = elem.attrib.get("type") or elem.attrib.get("eclass")
            if v_type and vid not in per_vehicle_type:
                per_vehicle_type[vid] = v_type
            elem.clear()
    except ET.ParseError:
        out["parse_error"] = True
        return out

    if not per_vehicle:
        return out

    out["available"] = True
    out["vehicle_count"] = len(per_vehicle)

    totals: dict[str, float] = dict.fromkeys(METRICS, 0.0)
    samples: dict[str, list[float]] = {metric: [] for metric in METRICS}
    for _vid, values in per_vehicle.items():
        for metric in METRICS:
            value = values.get(metric)
            if value is None:
                continue
            totals[metric] += value
            samples[metric].append(value)

    out["totals_mg"] = {metric: round(totals[metric], 3) for metric in METRICS if samples[metric]}
    out["mean_per_vehicle_mg"] = {
        metric: round(mean(samples[metric]), 3) for metric in METRICS if samples[metric]
    }

    bus_ids = [
        vid
        for vid in per_vehicle
        if _is_bus_like(vid, per_vehicle_type.get(vid, ""))
    ]
    if bus_ids:
        bus_totals = dict.fromkeys(METRICS, 0.0)
        for vid in bus_ids:
            for metric in METRICS:
                bus_totals[metric] += per_vehicle[vid].get(metric, 0.0)
        out["bus_totals_mg"] = {
            metric: round(bus_totals[metric], 3) for metric in METRICS if bus_totals[metric] > 0
        }
        out["bus_count"] = len(bus_ids)
    return out


def _is_bus_like(vehicle_id: str, vehicle_type: str) -> bool:
    vehicle_type_lc = vehicle_type.lower()
    return (
        vehicle_id.startswith(BUS_ID_PREFIXES)
        or vehicle_type_lc.startswith("bus")
        or vehicle_type_lc in BUS_TYPE_NAMES
    )
