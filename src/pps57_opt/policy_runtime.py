#!/usr/bin/env python3
"""Runtime inference for exported TSP policies.

The runtime only proposes decisions. Every proposal still goes through the
Safety Layer inside the TSP controller before actuation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pps57_cits.messages import SREMLike
from pps57_cits.models import NetworkStateSnapshot, SignalState
from pps57_tsp.action_planner import decision_for_action
from pps57_tsp.config import TSPConfig
from pps57_tsp.models import TSPDecision

from .state import state_bucket_for_context


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
    rules: dict[str, RuntimePolicyRule]
    policy_id: str = "offline_safe_policy_comparison"
    algorithm: str = "deterministic_policy_rules"
    is_reinforcement_learning: bool = False
    training_environment: str = "offline_policy_export"
    safety_filter_required: bool = True
    source_path: Path | None = None

    @classmethod
    def load(cls, tsp_config: TSPConfig, path: str | Path) -> RuntimePolicy:
        source = Path(path)
        payload = json.loads(source.read_text(encoding="utf-8"))
        rules: dict[str, RuntimePolicyRule] = {}
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
            algorithm=str(payload.get("algorithm", "deterministic_policy_rules")),
            is_reinforcement_learning=bool(payload.get("is_reinforcement_learning", False)),
            training_environment=str(payload.get("training_environment", "offline_policy_export")),
            safety_filter_required=bool(payload.get("safety_filter_required", True)),
            source_path=source,
        )

    def decide(
        self,
        request: SREMLike,
        signal_state: SignalState,
        sim_time_s: float,
        baseline: TSPDecision,
        *,
        active_request_count: int = 1,
        queue_vehicle_count: int = 0,
        halted_vehicle_count: int = 0,
        mean_speed_mps: float = 0.0,
        waiting_time_s: float = 0.0,
        occupancy: float = 0.0,
        spillback_risk: bool = False,
        network_state: NetworkStateSnapshot | None = None,
        seconds_since_last_intervention_s: float | None = None,
    ) -> TSPDecision:
        if network_state is not None:
            active_request_count = network_state.active_request_count
            queue_vehicle_count = network_state.queue_vehicle_count
            halted_vehicle_count = network_state.halted_vehicle_count
            mean_speed_mps = network_state.mean_speed_mps
            waiting_time_s = network_state.waiting_time_s
            occupancy = network_state.occupancy
            spillback_risk = network_state.spillback_risk
        bucket = state_bucket_for(
            self.tsp_config,
            request,
            signal_state,
            sim_time_s,
            active_request_count=active_request_count,
            queue_vehicle_count=queue_vehicle_count,
            halted_vehicle_count=halted_vehicle_count,
            mean_speed_mps=mean_speed_mps,
            waiting_time_s=waiting_time_s,
            occupancy=occupancy,
            spillback_risk=spillback_risk,
            seconds_since_last_intervention_s=seconds_since_last_intervention_s,
        )
        rule = self.rules.get(bucket)
        if rule is None:
            return baseline.copy_with(
                notes=list(baseline.notes)
                + [f"Runtime policy fallback: no exported rule for state_bucket={bucket}."]
            )
        notes = [
            f"Runtime policy '{self.policy_id}' selected action '{rule.action}'.",
            f"state_bucket={bucket}",
            f"source_scenario_id={rule.source_scenario_id}",
        ]
        if network_state is not None:
            notes.append(
                "network_state="
                f"active_requests:{active_request_count},"
                f"queue:{queue_vehicle_count},"
                f"halted:{halted_vehicle_count},"
                f"mean_speed_mps:{mean_speed_mps:.3f},"
                f"waiting_time_s:{waiting_time_s:.3f},"
                f"occupancy:{occupancy:.3f},"
                f"spillback_risk:{spillback_risk}"
            )
        if (
            baseline.requires_actuation
            and rule.action not in self.tsp_config.actuating_actions()
            and not bool(
                self.tsp_config.raw.get("policy_runtime", {}).get(
                    "allow_policy_suppress_baseline_actuation",
                    False,
                )
            )
        ):
            return baseline.copy_with(
                notes=list(baseline.notes)
                + notes
                + [
                    # Token estruturado e grep-able para que overrides do guard possam
                    # vir a ser contados em logs (ainda sem consumidor dedicado).
                    "shield_guard_override:policy_non_actuating_rule_kept_baseline_actuation",
                    "Runtime policy guard: non-actuating RL/optimized rule did not suppress "
                    "baseline actuation because allow_policy_suppress_baseline_actuation=false.",
                ]
            )
        return decision_for_action(
            self.tsp_config,
            action=rule.action,
            baseline=baseline,
            reason=f"runtime_policy_rule:{bucket}",
            notes=notes,
        )


def state_bucket_for(
    tsp_config: TSPConfig,
    request: SREMLike,
    signal_state: SignalState,
    sim_time_s: float,
    *,
    active_request_count: int = 1,
    queue_vehicle_count: int = 0,
    halted_vehicle_count: int = 0,
    mean_speed_mps: float = 0.0,
    waiting_time_s: float = 0.0,
    occupancy: float = 0.0,
    spillback_risk: bool = False,
    seconds_since_last_intervention_s: float | None = None,
) -> str:
    return state_bucket_for_context(
        tsp_config,
        tsp_config.raw.get("policy_runtime", {}).get("state_buckets", {}),
        request,
        signal_state,
        sim_time_s,
        active_request_count=active_request_count,
        queue_vehicle_count=queue_vehicle_count,
        halted_vehicle_count=halted_vehicle_count,
        mean_speed_mps=mean_speed_mps,
        waiting_time_s=waiting_time_s,
        occupancy=occupancy,
        spillback_risk=spillback_risk,
        seconds_since_last_intervention_s=seconds_since_last_intervention_s,
    )
