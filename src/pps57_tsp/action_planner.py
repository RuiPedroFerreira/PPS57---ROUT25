#!/usr/bin/env python3
"""Shared construction of TSP action proposals from a baseline decision."""
from __future__ import annotations

from pps57_cits.util import optional_int as _optional_int

from .config import TSPConfig
from .models import DecisionStatus, TSPAction, TSPDecision
from .util import positive_float as _positive_float


def decision_for_action(
    tsp_config: TSPConfig,
    *,
    action: str,
    baseline: TSPDecision,
    reason: str,
    notes: list[str],
) -> TSPDecision:
    policy = tsp_config.decision_policy
    mapping = tsp_config.phase_mapping_for_movement(baseline.priority_movement_id, baseline.tls_id)
    target_phase = baseline.target_phase_index
    if target_phase is None:
        target_phase = _optional_int(mapping.get("target_phase_index"))

    if action == TSPAction.GREEN_EXTENSION.value:
        extension_s = baseline.extension_s if baseline.extension_s > 0 else _positive_float(policy, "green_extension_default_s", 8.0)
        return baseline.copy_with(
            action=action,
            status=DecisionStatus.PROPOSED.value,
            reason=reason,
            extension_s=extension_s,
            phase_duration_s=None,
            target_phase_index=None,
            notes=notes,
        )

    if action == TSPAction.EARLY_GREEN.value:
        return baseline.copy_with(
            action=action,
            status=DecisionStatus.PROPOSED.value,
            reason=reason,
            extension_s=0.0,
            phase_duration_s=_positive_float(policy, "red_truncation_to_s", 2.0),
            target_phase_index=target_phase,
            notes=notes,
        )

    return baseline.copy_with(
        action=action,
        status=DecisionStatus.PROPOSED.value,
        reason=reason,
        extension_s=0.0,
        phase_duration_s=None,
        target_phase_index=None,
        notes=notes,
    )
