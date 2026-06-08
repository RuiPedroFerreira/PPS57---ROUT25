#!/usr/bin/env python3
"""Sim-to-real validation metrics (the measuring instruments).

Each formula and threshold is traceable to a published source; the numeric
limiares live in ``configs/validation_config.json`` under ``source`` fields.
This module invents no data: it consumes ``(modelled, observed)`` pairs supplied
from real counts / AVL / reference scenarios and turns them into the standard
traffic-engineering goodness-of-fit measures used to break the oracle = system-
under-test circularity.

GEH statistic
    GEH = sqrt( 2 * (M - C)^2 / (M + C) )
    M = modelled hourly volume, C = observed hourly count.
    Source: DMRB (Design Manual for Roads and Bridges); FHWA Traffic Analysis
    Toolbox Vol. III. Bands: < 5 good; 5-10 investigate; > 10 likely error.
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence, Tuple

Pair = Tuple[float, float]  # (modelled, observed)


def geh(modelled: float, observed: float) -> float:
    """GEH statistic for one (modelled, observed) hourly-flow pair.

    GEH = sqrt(2*(M-C)^2 / (M+C)). Returns 0.0 when both flows are zero
    (a perfect, trivially-matching pair). Source: DMRB / FHWA TAT Vol. III.
    """
    if modelled < 0 or observed < 0:
        raise ValueError("GEH expects non-negative flows (veh/h)")
    denom = modelled + observed
    if denom == 0:
        return 0.0
    return math.sqrt(2.0 * (modelled - observed) ** 2 / denom)


def geh_band(value: float, *, good_below: float, investigate_below: float) -> str:
    """Classify a GEH value into the published bands: good / investigate / poor."""
    if value < good_below:
        return "good"
    if value < investigate_below:
        return "investigate"
    return "poor"


def flow_within_band(modelled: float, observed: float, bands: Sequence[Mapping[str, float]]) -> bool:
    """Whether |M-C| is within the per-volume tolerance band selected by observed flow.

    Bands are the Wisconsin DOT / FHWA TAT Vol. III link-flow criteria, chosen by
    the magnitude of the observed flow (a band may carry ``tolerance_fraction`` or
    ``tolerance_abs_veh_h``). Returns False when no band matches the observed flow.
    """
    for band in bands:
        low = band.get("min_flow_veh_h")
        high = band.get("max_flow_veh_h")
        if low is not None and observed < low:
            continue
        if high is not None and observed >= high:
            continue
        if "tolerance_fraction" in band:
            return abs(modelled - observed) <= band["tolerance_fraction"] * observed
        if "tolerance_abs_veh_h" in band:
            return abs(modelled - observed) <= band["tolerance_abs_veh_h"]
        return False
    return False


def travel_time_within(
    modelled_s: float,
    observed_s: float,
    *,
    within_fraction: float,
    or_absolute_s: float,
) -> bool:
    """Whether a journey time is within the FHWA/WisDOT tolerance.

    Tolerance = max(within_fraction * observed, or_absolute_s), encoding
    "within 15% (or 1 min, if higher)". Source: FHWA TAT Vol. III (Wisconsin DOT).
    """
    tolerance = max(within_fraction * observed_s, or_absolute_s)
    return abs(modelled_s - observed_s) <= tolerance


def within_envelope(value: float, low: float, high: float) -> bool:
    """Whether a value falls inside the inclusive [low, high] face-validity band."""
    return low <= value <= high


def rmse(pairs: Sequence[Pair]) -> float:
    """Root-mean-square error of (modelled - observed) over the pairs."""
    if not pairs:
        return 0.0
    return math.sqrt(sum((m - o) ** 2 for m, o in pairs) / len(pairs))


def rmse_pct(pairs: Sequence[Pair]) -> float:
    """RMSE normalised by the mean observed value, as a percentage (%RMSE)."""
    if not pairs:
        return 0.0
    mean_observed = sum(o for _, o in pairs) / len(pairs)
    if mean_observed == 0:
        return 0.0
    return 100.0 * rmse(pairs) / mean_observed


def abs_pct_errors(pairs: Sequence[Pair]) -> list[float]:
    """Absolute percentage error per pair; pairs with observed == 0 are skipped."""
    return [100.0 * abs(m - o) / o for m, o in pairs if o != 0]


def pearson_r(pairs: Sequence[Pair]) -> float:
    """Pearson correlation between modelled and observed series (0.0 if undefined)."""
    n = len(pairs)
    if n < 2:
        return 0.0
    mean_m = sum(m for m, _ in pairs) / n
    mean_o = sum(o for _, o in pairs) / n
    s_mo = sum((m - mean_m) * (o - mean_o) for m, o in pairs)
    s_mm = sum((m - mean_m) ** 2 for m, _ in pairs)
    s_oo = sum((o - mean_o) ** 2 for _, o in pairs)
    if s_mm <= 0 or s_oo <= 0:
        return 0.0
    return s_mo / math.sqrt(s_mm * s_oo)
