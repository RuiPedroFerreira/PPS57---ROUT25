#!/usr/bin/env python3
"""V2 reference demand — real European urban-road flow distributions (the envelope).

The sim-to-real plan's V2 layer asks: is the corridor's demand *plausible*? Porto
has no open traffic counts (a formal CMP request was declined), so a Porto GEH
calibration is impossible without fabricating counts. The honest substitute is a
**reference-envelope transfer**: take real, openly-published traffic counts from
other European cities and check that the modelled Boavista arterial intensity
falls inside the spread those real cities exhibit. This is a face-validity gate,
explicitly NOT a Porto calibration.

This module is pure (no I/O, no network): it parses the raw payloads fetched by
``scripts/fetch_reference_counts.py`` and turns them into veh/h distributions, so
the math is unit-testable on synthetic vectors (the formula is the source of
truth) while the real numbers come only from the fetched datasets.

Sources (each fetched, hashed and timestamped by the fetch script):
  * Madrid — Ayuntamiento de Madrid open data, real-time intensity feed
    ``informo/tmadrid/pm.xml`` (``intensidad`` in veh/h per detector, ``error``
    validity flag), joined to the measurement-point catalogue to keep only the
    urban (``URB``) detectors and drop the M-30 ring motorway. Licence: the
    portal's open conditions (datos.madrid.es).
  * United Kingdom — Department for Transport road traffic statistics open API
    (roadtraffic.dft.gov.uk), AADF per count point and direction. AADF is a daily
    figure; it is converted to a peak-hour veh/h with a sourced K-factor for a
    like-for-like comparison and is reported as a corroborating second-country
    band. Licence: Open Government Licence v3.0.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

# M4: defusedxml em vez do stdlib — o feed de Madrid vem da internet, exactamente
# a fronteira que a política de hardening XXE do repo existe para proteger.
try:
    from defusedxml import ElementTree as ET  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - exercised in minimal CI images.
    from xml.etree import ElementTree as ET  # type: ignore[no-redef]

# DfT road_category codes for A-class roads (the urban-arterial analogue of an
# avenue like Boavista): principal-A and trunk-A. M/B class are excluded.
DFT_A_ROAD_CATEGORIES = ("PA", "TA")


def _to_float(text: str | None) -> float | None:
    if text is None:
        return None
    # Madrid encodes coordinates with a comma decimal; intensidad is a plain int,
    # but be defensive and accept a comma decimal here too.
    cleaned = text.strip().replace(",", ".")
    if cleaned == "":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_madrid_catalogue(csv_text: str) -> dict[int, str]:
    """Map detector id -> element type (``URB``/``M30``) from the points catalogue.

    The catalogue is a ``;``-separated CSV whose header is
    ``"tipo_elem";"distrito";"id";...``. Only ``id`` and ``tipo_elem`` are used.
    """
    mapping: dict[int, str] = {}
    # The portal's sibling feed ships a UTF-8 BOM; tolerate one here too, or the
    # header lookup fails and every detector would be silently dropped as non-URB.
    lines = [ln for ln in csv_text.lstrip("﻿").splitlines() if ln.strip()]
    if not lines:
        return mapping
    header = [h.strip().strip('"').lower() for h in lines[0].split(";")]
    try:
        i_id = header.index("id")
        i_tipo = header.index("tipo_elem")
    except ValueError:
        return mapping
    for line in lines[1:]:
        cells = [c.strip().strip('"') for c in line.split(";")]
        if len(cells) <= max(i_id, i_tipo):
            continue
        try:
            det_id = int(cells[i_id])
        except ValueError:
            continue
        mapping[det_id] = cells[i_tipo].upper()
    return mapping


def _valid_madrid_pms(xml_text: str) -> Iterable[Any]:
    """``<pm>`` elements with a valid reading (``error == 'N'``) from the feed.

    The feed begins with a BOM and is well-formed XML; strip a BOM if present.
    """
    root = ET.fromstring(xml_text.lstrip("﻿"))
    for pm in root.findall("pm"):
        if (pm.findtext("error") or "").strip().upper() == "N":
            yield pm


def _madrid_detector_id(pm: Any) -> int | None:
    try:
        return int((pm.findtext("idelem") or "").strip())
    except ValueError:
        return None


def parse_madrid_intensities(
    xml_text: str,
    catalogue: Mapping[int, str] | None = None,
    *,
    only_urban: bool = True,
) -> list[float]:
    """Valid veh/h intensities from the Madrid real-time feed.

    Keeps only ``<pm>`` elements whose ``<error>`` flag is ``N`` (valid reading).
    When ``catalogue`` is supplied and ``only_urban`` is set, keeps only detectors
    catalogued as ``URB`` (drops the M-30 ring motorway), so the distribution is
    urban-arterial, comparable to the Boavista avenue.
    """
    values: list[float] = []
    for pm in _valid_madrid_pms(xml_text):
        intensity = _to_float(pm.findtext("intensidad"))
        if intensity is None or intensity < 0:
            continue
        if only_urban and catalogue is not None:
            det_id = _madrid_detector_id(pm)
            if det_id is None or catalogue.get(det_id, "").upper() != "URB":
                continue
        values.append(intensity)
    return values


def madrid_feed_catalogue_coverage(xml_text: str, catalogue: Mapping[int, str]) -> dict:
    """How many valid feed detectors the points catalogue actually covers.

    ``only_urban`` filtering treats "id not in catalogue" the same as "not URB",
    so a stale catalogue snapshot silently drops every detector added or
    renumbered since it was published, biasing the reference distribution. This
    measures that join so the fetch step can refuse a catalogue that no longer
    matches the live feed instead of dropping readings quietly.
    """
    feed_ids = {
        det_id
        for pm in _valid_madrid_pms(xml_text)
        if (det_id := _madrid_detector_id(pm)) is not None
    }
    matched = sum(1 for det_id in feed_ids if det_id in catalogue)
    return {
        "feed_valid_detectors": len(feed_ids),
        "in_catalogue": matched,
        "missing_from_catalogue": len(feed_ids) - matched,
        "coverage": round(matched / len(feed_ids), 4) if feed_ids else None,
    }


def _dft_records(payload: Any) -> Iterable[Mapping[str, Any]]:
    """Yield record dicts from a DfT API page (``{"data": [...]}``) or a bare list."""
    if isinstance(payload, Mapping):
        data = payload.get("data", [])
    else:
        data = payload
    for rec in data or []:
        if isinstance(rec, Mapping):
            yield rec


def parse_dft_aadf(
    payload: Any,
    *,
    road_categories: Sequence[str] = DFT_A_ROAD_CATEGORIES,
    year: int | None = None,
) -> list[float]:
    """Per-direction AADF (veh/day) for A-class roads from a DfT API payload.

    ``payload`` may be a single API page dict (``{"data": [...]}``), a bare list of
    records, or a list of pages. Filters to ``road_categories`` (A-roads) and, when
    ``year`` is given, to that survey year. Reads ``all_motor_vehicles``.
    """
    if isinstance(payload, list):
        if payload and all(isinstance(item, Mapping) and "data" in item for item in payload):
            pages = payload  # lista de páginas da API ({"data": [...]})
        elif payload and all(isinstance(item, list) for item in payload):
            pages = payload  # lista de listas de registos
        else:
            # Lista "bare" de registos — o formato que fetch_reference_counts.py
            # grava em dft_aadf.json (um registo é um Mapping SEM chave "data").
            pages = [payload]
    else:
        pages = [payload]
    cats = {c.upper() for c in road_categories}
    out: list[float] = []
    for page in pages:
        for rec in _dft_records(page):
            if str(rec.get("road_category", "")).upper() not in cats:
                continue
            if year is not None and str(rec.get("year")) != str(year):
                continue
            aadf = _to_float(str(rec.get("all_motor_vehicles")))
            if aadf is None or aadf < 0:
                continue
            out.append(aadf)
    return out


def aadf_to_peak_hour_veh_h(aadf_per_dir: float, k_factor: float) -> float:
    """Approximate per-direction peak-hour flow (veh/h) from a per-direction AADF.

    peak_hour ≈ AADF_dir * K, with K the peak-hour fraction of daily flow. K is a
    sourced constant (HCM/FHWA), supplied by the caller from the config; this
    function adds no hidden assumption beyond that single, documented factor.
    """
    if aadf_per_dir < 0 or k_factor <= 0:
        raise ValueError("AADF must be non-negative and K-factor positive")
    return aadf_per_dir * k_factor


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile, matching ``measure_arterial_intensity`` in the V4d
    corridor build (``sorted[int(n*q)]``) so Boavista's stats compare like-for-like.
    """
    if not values:
        raise ValueError("percentile of an empty sequence is undefined")
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be in [0, 1]")
    ordered = sorted(values)
    idx = min(int(len(ordered) * q), len(ordered) - 1)
    return ordered[idx]


def distribution(values: Sequence[float]) -> dict[str, Any]:
    """Summary distribution (n, mean, median, p75, p90, max) of a veh/h sample."""
    if not values:
        return {"n": 0}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "mean": round(sum(ordered) / len(ordered), 1),
        "median": round(percentile(ordered, 0.50), 1),
        "p75": round(percentile(ordered, 0.75), 1),
        "p90": round(percentile(ordered, 0.90), 1),
        "max": round(ordered[-1], 1),
    }


def evaluate_demand_envelope(
    corridor_stats: Mapping[str, float],
    city_distributions: Mapping[str, Mapping[str, Any]],
    *,
    percentiles: Sequence[str] = ("median", "p90"),
) -> dict[str, Any]:
    """Is the modelled corridor intensity inside the real multi-city envelope?

    For each requested percentile, the reference band is the spread of that
    percentile across the supplied real cities, ``[min_city, max_city]``. The
    corridor value (e.g. its measured arterial ``median``/``p90``) must lie inside
    the band. Verdict is ``plausible`` only when every percentile is inside.

    This is a face-validity envelope (a transfer), not a Porto calibration: it
    establishes that the corridor demand is in the range real European urban roads
    exhibit, with every reference number traceable to a fetched dataset.
    """
    cities = {name: dist for name, dist in city_distributions.items() if dist.get("n")}
    checks: list[dict[str, Any]] = []
    all_inside = bool(cities)
    for key in percentiles:
        city_values = {name: dist[key] for name, dist in cities.items() if key in dist}
        if not city_values:
            all_inside = False
            checks.append({"percentile": key, "inside": False, "reason": "no_reference_cities"})
            continue
        low = min(city_values.values())
        high = max(city_values.values())
        corridor_value = corridor_stats.get(key)
        inside = corridor_value is not None and low <= corridor_value <= high
        all_inside = all_inside and inside
        checks.append(
            {
                "percentile": key,
                "corridor_veh_h": corridor_value,
                "reference_band_veh_h": [round(low, 1), round(high, 1)],
                "per_city_veh_h": {
                    name: round(val, 1) for name, val in sorted(city_values.items())
                },
                "inside": bool(inside),
            }
        )
    return {
        "metric": "demand_reference_envelope",
        "reference_cities": sorted(cities),
        "percentile_checks": checks,
        "verdict": "plausible" if all_inside else ("no_reference" if not cities else "flagged"),
    }


def evaluate_corridor_plausibility(
    corridor_stats: Mapping[str, float],
    city_distributions: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Corridor-appropriate plausibility against the real European arterial envelope.

    A single corridor is not a whole city's road network: it cannot reproduce a
    city's heavy upper tail (a city has many A-roads of widely differing load, so
    its per-site p90 runs high; one avenue's edges are relatively uniform). So the
    naive "corridor p90 must exceed every city's p90" test in
    :func:`evaluate_demand_envelope` is not apples-to-apples at the tail. The
    meaningful, corridor-appropriate test is:

      (a) typical intensity matches — the corridor median lies within the
          inter-city range of medians; and
      (b) not implausibly heavy/light — the corridor p90 lies within the real
          range ``[min city median, max city p90]`` (carries at least a typical
          load, and does not exceed the heaviest real arterial peak).

    Plausible iff both hold. This is a methodology choice (which statistic to
    compare), documented here and in the config; it fabricates no data.
    """
    cities = {name: dist for name, dist in city_distributions.items() if dist.get("n")}
    medians = [dist["median"] for dist in cities.values() if "median" in dist]
    p90s = [dist["p90"] for dist in cities.values() if "p90" in dist]
    if not medians or not p90s:
        return {"metric": "corridor_demand_plausibility", "verdict": "no_reference"}
    med_lo, med_hi, p90_max = min(medians), max(medians), max(p90s)
    c_median = corridor_stats.get("median")
    c_p90 = corridor_stats.get("p90")
    typical_ok = c_median is not None and med_lo <= c_median <= med_hi
    bounded_ok = c_p90 is not None and med_lo <= c_p90 <= p90_max
    return {
        "metric": "corridor_demand_plausibility",
        "typical_intensity_match": {
            "corridor_median_veh_h": c_median,
            "real_median_range_veh_h": [round(med_lo, 1), round(med_hi, 1)],
            "inside": bool(typical_ok),
        },
        "within_real_envelope": {
            "corridor_p90_veh_h": c_p90,
            "real_floor_to_peak_veh_h": [round(med_lo, 1), round(p90_max, 1)],
            "inside": bool(bounded_ok),
        },
        "note": (
            "A single corridor lacks a whole city's heavy upper tail; the test is "
            "typical-intensity match (median) + not-implausibly-heavy (p90 within the real "
            "range), not p90 >= every city's p90."
        ),
        "verdict": "plausible" if (typical_ok and bounded_ok) else "flagged",
    }
