"""Aggregate per-vehicle emission/fuel KPIs from a SUMO emissions source.

Two source formats are accepted and produce identical aggregates:

* **tripinfo emissions device** (preferred). With ``--device.emissions.probability
  1`` and ``--tripinfo-output`` enabled, SUMO writes one ``<emissions .../>`` child
  per ``<tripinfo>`` carrying the vehicle's *trip-total* absolutes (``CO2_abs``,
  ``fuel_abs``, ...). One row per vehicle — orders of magnitude smaller than a
  per-step dump and no summation needed.
* **raw ``emission-output``** (legacy). Per-step ``<vehicle>`` entries inside
  ``<timestep>`` elements whose CO2/NOx/PMx/fuel values are per-step *rates* (not
  cumulative); the trip total is the SUM of a vehicle's per-step observations.
  Kept so historical emission dumps still parse.

Sustainability claims (TSP reduces CO2, fuel use, etc.) are only verifiable once
this file is parsed — otherwise the KPIs are absent from the run summary.
"""

from __future__ import annotations

from pathlib import Path
from statistics import mean
from typing import Any

from pps57_sumo.vehicle_classification import is_bus_like

try:
    from defusedxml import ElementTree as ET  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - exercised in minimal CI images.
    from xml.etree import ElementTree as ET  # type: ignore[no-redef]


METRICS = ("CO2", "CO", "NOx", "PMx", "HC", "fuel", "electricity")


def _ingest_tripinfo(
    elem: Any, per_vehicle: dict[str, dict[str, float]], per_vehicle_type: dict[str, str]
) -> None:
    """Read trip-total absolutes from a ``<tripinfo>``'s ``<emissions>`` child."""
    vid = elem.attrib.get("id", "")
    if not vid:
        return
    emissions = elem.find("emissions")
    if emissions is None:
        return
    bucket = per_vehicle.setdefault(vid, {})
    for metric in METRICS:
        # The emissions device names trip totals "<METRIC>_abs" (mg; fuel in mg,
        # electricity in Wh). These are already cumulative — no per-step sum.
        value = emissions.attrib.get(f"{metric}_abs")
        if value is None:
            continue
        try:
            bucket[metric] = bucket.get(metric, 0.0) + float(value)
        except ValueError:
            continue
    v_type = elem.attrib.get("vType")
    if v_type and vid not in per_vehicle_type:
        per_vehicle_type[vid] = v_type


def _ingest_step_vehicle(
    elem: Any, per_vehicle: dict[str, dict[str, float]], per_vehicle_type: dict[str, str]
) -> None:
    """Accumulate one per-step ``<vehicle>`` row from a raw emission-output dump."""
    vid = elem.attrib.get("id", "")
    if not vid:
        return
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
        # Track the root so parsed top-level <tripinfo> rows can be dropped, keeping
        # memory flat on city-wide dumps (tens of thousands of vehicles). The legacy
        # per-step <vehicle> rows are nested in <timestep> and only `elem.clear()`
        # themselves — clearing the root there would detach the in-progress timestep.
        context = ET.iterparse(str(path), events=("start", "end"))
        root = None
        for event, elem in context:
            if event == "start":
                if root is None:
                    root = elem
                continue
            if elem.tag == "tripinfo":
                _ingest_tripinfo(elem, per_vehicle, per_vehicle_type)
                elem.clear()
                if root is not None:
                    root.clear()
            elif elem.tag == "vehicle":
                _ingest_step_vehicle(elem, per_vehicle, per_vehicle_type)
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
        if is_bus_like(vid, per_vehicle_type.get(vid, ""))
    ]
    if bus_ids:
        bus_totals = dict.fromkeys(METRICS, 0.0)
        bus_samples: dict[str, list[float]] = {metric: [] for metric in METRICS}
        for vid in bus_ids:
            for metric in METRICS:
                value = per_vehicle[vid].get(metric)
                if value is None:
                    continue
                bus_totals[metric] += value
                bus_samples[metric].append(value)
        # Same inclusion rule as totals_mg above (keep a species if any bus reported
        # it), instead of the inconsistent ">0" filter this block used before.
        out["bus_totals_mg"] = {
            metric: round(bus_totals[metric], 3) for metric in METRICS if bus_samples[metric]
        }
        out["bus_count"] = len(bus_ids)
    return out
