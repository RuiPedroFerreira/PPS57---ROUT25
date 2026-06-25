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


# B13: SUMO emits mass pollutants/fuel absolutes in milligrams but electric energy
# (``electricity_abs``) in watt-hours. Keeping both under a single ``_mg`` suffix
# mislabels the units, so the aggregates split mass species (mg) from energy (Wh).
MASS_METRICS = ("CO2", "CO", "NOx", "PMx", "HC", "fuel")
ENERGY_METRICS = ("electricity",)
METRICS = MASS_METRICS + ENERGY_METRICS


def _ingest_tripinfo(
    elem: Any,
    per_vehicle: dict[str, dict[str, float]],
    per_vehicle_type: dict[str, str],
    duplicate_ids: set[str],
) -> None:
    """Read trip-total absolutes from a ``<tripinfo>``'s ``<emissions>`` child."""
    vid = elem.attrib.get("id", "")
    if not vid:
        return
    emissions = elem.find("emissions")
    if emissions is None:
        return
    # B15: tripinfo carries exactly one trip-total row per vehicle, so a repeated id
    # is a duplicate export. Record it and skip instead of silently double-counting
    # its emissions into the same bucket (the per-step path below legitimately sums).
    # Key on a NON-EMPTY bucket: an earlier emissions-less occurrence (e.g. <emissions/>
    # with no *_abs attrs) leaves an empty bucket, and a later real row for the same id
    # must still be ingested (not mistaken for a duplicate and dropped).
    if per_vehicle.get(vid):
        duplicate_ids.add(vid)
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
    duplicate_ids: set[str] = set()

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
                _ingest_tripinfo(elem, per_vehicle, per_vehicle_type, duplicate_ids)
                elem.clear()
                if root is not None:
                    root.clear()
            elif elem.tag == "vehicle":
                _ingest_step_vehicle(elem, per_vehicle, per_vehicle_type)
                elem.clear()
    except ET.ParseError:
        out["parse_error"] = True
        return out

    # B14: a vehicle whose id was seen but produced no parseable metric left an empty
    # bucket (setdefault). Drop those before counting so vehicle_count/bus_count and
    # the per-vehicle normalisations are not inflated by metric-less entries.
    per_vehicle = {vid: values for vid, values in per_vehicle.items() if values}
    if not per_vehicle:
        return out

    out["available"] = True
    out["vehicle_count"] = len(per_vehicle)
    if duplicate_ids:
        # B15: surface duplicate trip-total exports instead of swallowing them.
        out["duplicate_vehicle_count"] = len(duplicate_ids)

    totals: dict[str, float] = dict.fromkeys(METRICS, 0.0)
    samples: dict[str, list[float]] = {metric: [] for metric in METRICS}
    for _vid, values in per_vehicle.items():
        for metric in METRICS:
            value = values.get(metric)
            if value is None:
                continue
            totals[metric] += value
            samples[metric].append(value)

    out["totals_mg"] = {m: round(totals[m], 3) for m in MASS_METRICS if samples[m]}
    out["mean_per_vehicle_mg"] = {m: round(mean(samples[m]), 3) for m in MASS_METRICS if samples[m]}
    # B13: electricity_abs is in watt-hours, not mg — emit it under honest _wh keys.
    energy_totals = {m: round(totals[m], 3) for m in ENERGY_METRICS if samples[m]}
    if energy_totals:
        out["totals_wh"] = energy_totals
        out["mean_per_vehicle_wh"] = {
            m: round(mean(samples[m]), 3) for m in ENERGY_METRICS if samples[m]
        }

    bus_ids = [vid for vid in per_vehicle if is_bus_like(vid, per_vehicle_type.get(vid, ""))]
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
        out["bus_totals_mg"] = {m: round(bus_totals[m], 3) for m in MASS_METRICS if bus_samples[m]}
        bus_energy = {m: round(bus_totals[m], 3) for m in ENERGY_METRICS if bus_samples[m]}
        if bus_energy:
            out["bus_totals_wh"] = bus_energy
        out["bus_count"] = len(bus_ids)
    return out
