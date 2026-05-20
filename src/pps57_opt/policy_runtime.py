#!/usr/bin/env python3
"""Runtime inference for exported TSP policies.

The runtime only proposes decisions. Every proposal still goes through the
Safety Layer inside the TSP controller before actuation.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, Optional

from pps57_cits.messages import SREMLike
from pps57_cits.models import SignalState
from pps57_tsp.config import TSPConfig
from pps57_tsp.engine import TSPDecisionEngine
from pps57_tsp.models import DecisionStatus, TSPAction, TSPDecision


@dataclass(frozen=True)
class RuntimePolicyRule:
    state_bucket: str
    action: str
    reward: float
    source_scenario_id: str = ""
    safety_status: str = ""
    safety_reason: str = ""


@dataclass
class RuntimePolicy:
    tsp_config: TSPConfig
    rules: Dict[str, RuntimePolicyRule]
    policy_id: str = "offline_safe_policy_comparison"
    source_path: Optional[Path] = None

    @classmethod
    def load(cls, tsp_config: TSPConfig, path: str | Path) -> "RuntimePolicy":
        source = Path(path)
        payload = json.loads(source.read_text(encoding="utf-8"))
        rules: Dict[str, RuntimePolicyRule] = {}
        for item in payload.get("rules", []):
            if not isinstance(item, dict):
                continue
            state_bucket = str(item.get("state_bucket", ""))
            action = str(item.get("action", ""))
            if not state_bucket or not action:
                continue
            rules[state_bucket] = RuntimePolicyRule(
                state_bucket=state_bucket,
                action=action,
                reward=float(item.get("reward", 0.0)),
                source_scenario_id=str(item.get("source_scenario_id", "")),
                safety_status=str(item.get("safety_status", "")),
                safety_reason=str(item.get("safety_reason", "")),
            )
        return cls(
            tsp_config=tsp_config,
            rules=rules,
            policy_id=str(payload.get("policy_id", "offline_safe_policy_comparison")),
            source_path=source,
        )

    def decide(
        self,
        request: SREMLike,
        signal_state: SignalState,
        sim_time_s: float,
        baseline: TSPDecision,
    ) -> TSPDecision:
        bucket = state_bucket_for(self.tsp_config, request, signal_state, sim_time_s)
        rule = self.rules.get(bucket)
        if rule is None:
            return baseline.copy_with(
                notes=list(baseline.notes)
                + [f"Runtime policy fallback: no exported rule for state_bucket={bucket}."]
            )
        return decision_for_action(
            self.tsp_config,
            action=rule.action,
            baseline=baseline,
            reason=f"runtime_policy_rule:{bucket}",
            notes=[
                f"Runtime policy '{self.policy_id}' selected action '{rule.action}'.",
                f"state_bucket={bucket}",
                f"source_scenario_id={rule.source_scenario_id}",
            ],
        )


def decision_for_action(
    tsp_config: TSPConfig,
    *,
    action: str,
    baseline: TSPDecision,
    reason: str,
    notes: list[str],
) -> TSPDecision:
    policy = tsp_config.decision_policy
    mapping = tsp_config.phase_mapping_for_tls(baseline.tls_id)
    target_phase = _optional_int(mapping.get("corridor_green_phase_index"))

    if action == TSPAction.GREEN_EXTENSION.value:
        extension_s = baseline.extension_s if baseline.extension_s > 0 else float(policy.get("green_extension_default_s", 8))
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
            phase_duration_s=float(policy.get("red_truncation_to_s", 2)),
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


def state_bucket_for(tsp_config: TSPConfig, request: SREMLike, signal_state: SignalState, sim_time_s: float) -> str:
    buckets = tsp_config.raw.get("policy_runtime", {}).get("state_buckets", {})
    eta_close = float(buckets.get("eta_close_s", 10))
    eta_far = float(buckets.get("eta_far_s", 25))
    high_delay = float(buckets.get("high_delay_s", 90))
    switch_close = float(buckets.get("phase_switch_close_s", 5))
    remaining = TSPDecisionEngine.remaining_phase_time_s(signal_state, sim_time_s)

    eta_bucket = "eta_close" if request.eta_to_stopline_s <= eta_close else "eta_mid"
    if request.eta_to_stopline_s >= eta_far:
        eta_bucket = "eta_far"
    delay_bucket = "delay_high" if request.schedule_delay_s >= high_delay else "delay_low"
    corridor_phase = _optional_int(tsp_config.phase_mapping_for_tls(signal_state.tls_id).get("corridor_green_phase_index"))
    phase_bucket = "phase_unknown"
    if signal_state.red_yellow_green_state and "y" in signal_state.red_yellow_green_state.lower():
        phase_bucket = "yellow"
    elif corridor_phase is not None and signal_state.current_phase_index == corridor_phase:
        phase_bucket = "corridor_green"
    elif signal_state.current_phase_index is not None:
        phase_bucket = "corridor_red"
    switch_bucket = "switch_close" if remaining is not None and remaining <= switch_close else "switch_open"
    return "|".join([phase_bucket, eta_bucket, delay_bucket, switch_bucket])


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
