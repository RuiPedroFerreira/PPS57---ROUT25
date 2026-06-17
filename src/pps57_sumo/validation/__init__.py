#!/usr/bin/env python3
"""Sim-to-real validation harness (V0): the independent measuring instruments.

The simulator must not be its own oracle. This package provides the published,
source-traced goodness-of-fit metrics (GEH, RMSE, flow/travel-time criteria) and
config-driven acceptance gates that compare SUMO outputs against real counts,
AVL/GTFS observations and reference scenarios. It fabricates no data.
"""

from __future__ import annotations

from pps57_sumo.validation import acceptance, metrics, reference_counts
from pps57_sumo.validation.acceptance import (
    evaluate_link_flow_calibration,
    evaluate_travel_times,
    evaluate_tsp_face_validity,
    load_validation_config,
)
from pps57_sumo.validation.reference_counts import (
    distribution,
    evaluate_corridor_plausibility,
    evaluate_demand_envelope,
)

__all__ = [
    "acceptance",
    "metrics",
    "reference_counts",
    "evaluate_link_flow_calibration",
    "evaluate_travel_times",
    "evaluate_tsp_face_validity",
    "load_validation_config",
    "distribution",
    "evaluate_corridor_plausibility",
    "evaluate_demand_envelope",
]
