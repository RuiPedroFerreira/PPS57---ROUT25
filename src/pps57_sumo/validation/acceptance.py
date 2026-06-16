#!/usr/bin/env python3
"""Config-driven acceptance gates for sim-to-real validation.

Thresholds are loaded from ``configs/validation_config.json`` (each carries a
``source``) and echoed into every verdict so the report is self-documenting and
auditable. Nothing here fabricates data: callers pass in ``(modelled, observed)``
pairs gathered from real counts / AVL / reference scenarios.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pps57_sumo.stats import mean_ci95
from pps57_sumo.validation import metrics

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = ROOT / "configs" / "validation_config.json"


def load_validation_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load the validation thresholds config (defaults to configs/validation_config.json)."""
    config_path = Path(path) if path is not None else DEFAULT_CONFIG
    if not config_path.exists():
        raise FileNotFoundError(f"Validation config not found: {config_path}")
    return json.loads(config_path.read_text(encoding="utf-8"))


def _fraction(passing: int, total: int) -> float:
    return (passing / total) if total else 0.0


def evaluate_link_flow_calibration(
    links: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate modelled vs observed link flows against the DMRB/FHWA criteria.

    Each link is a mapping with ``link_id``, ``modelled_veh_h``, ``observed_veh_h``
    and an optional ``source``. Returns a JSON-serialisable report whose verdict
    is ``pass`` only when every gate (GEH, flow-% band, sum-of-flows) passes.
    """
    cal = config["link_flow_calibration"]
    geh_cfg = cal["geh"]
    net_cfg = cal["network_acceptance"]
    band_cfg = cal["flow_percentage_bands"]
    sum_cfg = cal["sum_of_flows"]

    per_link: list[dict[str, Any]] = []
    pairs: list[metrics.Pair] = []
    geh_pass = 0
    band_pass = 0
    for link in links:
        modelled = float(link["modelled_veh_h"])
        observed = float(link["observed_veh_h"])
        pairs.append((modelled, observed))
        value = metrics.geh(modelled, observed)
        band = metrics.geh_band(
            value,
            good_below=geh_cfg["good_below"],
            investigate_below=geh_cfg["investigate_below"],
        )
        within_band = metrics.flow_within_band(modelled, observed, band_cfg["bands"])
        geh_pass += 1 if value < net_cfg["geh_threshold"] else 0
        band_pass += 1 if within_band else 0
        per_link.append(
            {
                "link_id": link.get("link_id", ""),
                "modelled_veh_h": modelled,
                "observed_veh_h": observed,
                "geh": round(value, 3),
                "geh_band": band,
                "within_flow_band": within_band,
                "source": link.get("source", ""),
            }
        )

    total = len(links)
    geh_fraction = _fraction(geh_pass, total)
    band_fraction = _fraction(band_pass, total)
    modelled_sum = sum(m for m, _ in pairs)
    observed_sum = sum(o for _, o in pairs)
    sum_geh = metrics.geh(modelled_sum, observed_sum)
    sum_within = (
        abs(modelled_sum - observed_sum) <= sum_cfg["within_fraction"] * observed_sum
        if observed_sum
        else modelled_sum == 0
    )
    sum_geh_ok = sum_geh < sum_cfg["geh_threshold"]

    # Strict ">" to match the documented "> 85%" criterion (exactly 85% does not pass).
    geh_ok = geh_fraction > net_cfg["min_fraction_passing"]
    band_ok = band_fraction > band_cfg["min_fraction_passing"]
    sum_ok = sum_within and sum_geh_ok

    return {
        "metric": "link_flow_calibration",
        "n_links": total,
        "per_link": per_link,
        "geh": {
            "fraction_passing": round(geh_fraction, 4),
            "min_fraction_required": net_cfg["min_fraction_passing"],
            "geh_threshold": net_cfg["geh_threshold"],
            "passed": geh_ok,
            "source": net_cfg["source"],
        },
        "flow_percentage": {
            "fraction_passing": round(band_fraction, 4),
            "min_fraction_required": band_cfg["min_fraction_passing"],
            "passed": band_ok,
            "source": band_cfg["source"],
        },
        "sum_of_flows": {
            "modelled_sum_veh_h": round(modelled_sum, 3),
            "observed_sum_veh_h": round(observed_sum, 3),
            "within_fraction_required": sum_cfg["within_fraction"],
            "within": sum_within,
            "geh_sum": round(sum_geh, 3),
            "geh_threshold": sum_cfg["geh_threshold"],
            "passed": sum_ok,
            "source": sum_cfg["source"],
        },
        "error_stats": {
            "rmse_veh_h": round(metrics.rmse(pairs), 3),
            "rmse_pct": round(metrics.rmse_pct(pairs), 3),
            "pearson_r": round(metrics.pearson_r(pairs), 4),
            "abs_pct_error_ci95": mean_ci95(metrics.abs_pct_errors(pairs)),
        },
        "verdict": "pass" if (geh_ok and band_ok and sum_ok) else "fail",
    }


def evaluate_travel_times(
    segments: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Evaluate modelled vs observed journey times against the FHWA/WisDOT criterion.

    Each segment is a mapping with ``segment_id``, ``modelled_s``, ``observed_s``
    and an optional ``source``.
    """
    tt_cfg = config["travel_time_validation"]
    per_segment: list[dict[str, Any]] = []
    passing = 0
    for segment in segments:
        modelled = float(segment["modelled_s"])
        observed = float(segment["observed_s"])
        within = metrics.travel_time_within(
            modelled,
            observed,
            within_fraction=tt_cfg["within_fraction"],
            or_absolute_s=tt_cfg["or_absolute_s"],
        )
        passing += 1 if within else 0
        per_segment.append(
            {
                "segment_id": segment.get("segment_id", ""),
                "modelled_s": modelled,
                "observed_s": observed,
                "within_tolerance": within,
                "source": segment.get("source", ""),
            }
        )
    fraction = _fraction(passing, len(segments))
    return {
        "metric": "travel_time_validation",
        "n_segments": len(segments),
        "per_segment": per_segment,
        "fraction_passing": round(fraction, 4),
        "min_fraction_required": tt_cfg["min_fraction_passing"],
        "within_fraction": tt_cfg["within_fraction"],
        "or_absolute_s": tt_cfg["or_absolute_s"],
        "passed": fraction > tt_cfg["min_fraction_passing"],
        "source": tt_cfg["source"],
        "verdict": "pass" if fraction > tt_cfg["min_fraction_passing"] else "fail",
    }


_FACE_VALIDITY_BANDS = {
    "bus_running_time_improvement_pct",
    "bus_delay_reduction_pct",
}


def evaluate_tsp_face_validity(
    measurements: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    """Check measured TSP gains against the published face-validity envelopes.

    Each measurement is a mapping with ``metric`` (one of the configured bands,
    e.g. ``bus_running_time_improvement_pct``), ``value_pct`` and an optional
    ``source``. A gain inside the band is plausible; outside is flagged. The
    configured ``corridor_travel_time_anchors_pct`` (published per-city point
    anchors, not a band) is echoed into the report as context only — it gates
    nothing.
    """
    fv_cfg = config["tsp_face_validity"]
    results: list[dict[str, Any]] = []
    all_inside = True
    for item in measurements:
        band_key = str(item["metric"])
        if band_key not in _FACE_VALIDITY_BANDS or band_key not in fv_cfg:
            raise ValueError(f"Unknown TSP face-validity band: {band_key}")
        band = fv_cfg[band_key]
        value = float(item["value_pct"])
        inside = metrics.within_envelope(value, band["min"], band["max"])
        all_inside = all_inside and inside
        results.append(
            {
                "metric": band_key,
                "value_pct": value,
                "envelope_pct": [band["min"], band["max"]],
                "inside_envelope": inside,
                "measurement_source": item.get("source", ""),
                "envelope_source": band["source"],
            }
        )
    report = {
        "metric": "tsp_face_validity",
        "n_measurements": len(measurements),
        "results": results,
        "verdict": "plausible"
        if all_inside and measurements
        else ("no_measurements" if not measurements else "flagged"),
    }
    anchors = fv_cfg.get("corridor_travel_time_anchors_pct")
    if anchors:
        report["published_corridor_anchors_pct"] = {
            "anchors": {key: value for key, value in anchors.items() if key != "source"},
            "source": anchors.get("source", ""),
            "role": "context only — published point anchors for comparison, not a pass/fail band",
        }
    return report
