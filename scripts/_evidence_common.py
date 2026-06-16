#!/usr/bin/env python3
"""Shared auto-discovery configs for the evidence scripts.

The evidence scripts (TSP demo, NetworkBinding check, empirical network-profile
check) all probe a real SUMO net through the same path: a CITS config with full
network discovery enabled plus a generated-contract TSP config. These builders
were copy-pasted per script and had already drifted; this module is the single
canonical version. The decision-policy weights are the demo's full set — scripts
that only build contracts (no scoring) are insensitive to them, so sharing the
full config changes no behaviour.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_cits.config import load_cits_config  # noqa: E402
from pps57_tsp.config import TSPConfig  # noqa: E402


def auto_discovery_cits_config(network: Path) -> Any:
    """CITS config with full network discovery for ``network`` (no hand-written intersections)."""
    payload = {
        "sumo": {"network": str(network)},
        "network_discovery": {
            "enabled": True,
            "augment_missing_intersections": True,
            "auto_generate_priority_movements": True,
            "priority_vehicle_classes": ["public_transport"],
            "rsu_id_prefix": "RSU_AUTO_",
        },
        "obu_policy": {},
        "rsu_policy": {},
        "safety_constraints": {
            "min_green_s": 8,
            "max_green_extension_s": 12,
            "max_total_green_s": 55,
            "yellow_s": 3,
            "all_red_s": 0,
            "pedestrian_clearance_must_not_be_shortened": True,
            "never_skip_yellow_or_all_red": True,
            "max_consecutive_priority_interventions_per_tls": 2,
            "cooldown_after_priority_s": 90,
        },
        "intersections": [],
    }
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(payload, handle)
    handle.close()
    config_path = Path(handle.name)
    try:
        return load_cits_config(config_path, root=config_path.parent)
    finally:
        config_path.unlink(missing_ok=True)


def auto_tsp_config(root: Path) -> TSPConfig:
    """Generated-contract TSP config with the canonical (demo) decision policy."""
    return TSPConfig(
        root=root,
        raw={
            "decision_policy": {
                "min_priority_score": 0.0,
                "eta_arrival_buffer_s": 4,
                "green_extension_min_s": 3,
                "green_extension_default_s": 8,
                "green_extension_max_s": 12,
                "early_green_min_eta_s": 10,
                "red_truncation_to_s": 2,
                "delay_normalisation_s": 180,
                "headway_normalisation_s": 240,
                "distance_normalisation_m": 250,
                "weights": {
                    "schedule_delay": 0.45,
                    "headway_deviation": 0.2,
                    "proximity": 0.2,
                    "priority_level": 0.15,
                },
            },
            "actuation": {
                "allow_green_extension": True,
                "allow_red_truncation": True,
                "allow_direct_phase_jump": False,
            },
            "network_profile": {
                "enabled": True,
                "prefer_generated_contracts_for_unknown_tls": True,
            },
            "controller_contracts": {
                "default": {
                    "adapter_type": "sumo_traci",
                    "fixed_time_required": True,
                    "allowed_actions": ["green_extension", "early_green"],
                }
            },
            "phase_mapping": {},
        },
    )


def running_time_envelope(config: dict) -> tuple[tuple[float, float], str]:
    """TSP bus running-time face-validity envelope (min%, max%) plus its source.

    Reads ``tsp_face_validity.bus_running_time_improvement_pct`` from the loaded
    validation config so the demo's gate cannot drift from the sourced band.
    """
    band = config["tsp_face_validity"]["bus_running_time_improvement_pct"]
    return (float(band["min"]), float(band["max"])), str(band["source"])
