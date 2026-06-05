#!/usr/bin/env python3
"""Modelos para avaliação offline de políticas TSP otimizadas."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Any, Dict, List, Optional

from pps57_tsp.models import TSPDecision


@dataclass(frozen=True)
class OfflineScenario:
    scenario_id: str
    description: str
    expected_case: str
    sim_time_s: float
    request: Any
    signal_state: Any
    # M7: estado inicial opcional para exercitar caminhos com estado na Safety
    # Layer (cooldown, consecutive_interventions). Cada chave mapeia tls_id -> valor.
    initial_last_intervention_time_by_tls: Dict[str, float] = field(default_factory=dict)
    initial_consecutive_interventions_by_tls: Dict[str, int] = field(default_factory=dict)
    active_request_count: int = 1
    queue_vehicle_count: int = 0
    halted_vehicle_count: int = 0
    mean_speed_mps: float = 0.0
    waiting_time_s: float = 0.0
    occupancy: float = 0.0
    spillback_risk: bool = False
    seconds_since_last_intervention_s: Optional[float] = None
    # P4 (OPE inputs). behavior_policy_action: ação que a política em execução
    # de facto tomou no log (a "behavior policy"). realized_outcome: KPI por-
    # decisão observado, quando existir no event row — hoje ausente no corpus,
    # logo None e o OPE devolve honestamente "inconclusive_without_outcomes".
    behavior_policy_action: Optional[str] = None
    realized_outcome: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "description": self.description,
            "expected_case": self.expected_case,
            "sim_time_s": self.sim_time_s,
            "request": self.request.to_dict(),
            "signal_state": asdict(self.signal_state),
            "initial_last_intervention_time_by_tls": dict(self.initial_last_intervention_time_by_tls),
            "initial_consecutive_interventions_by_tls": dict(self.initial_consecutive_interventions_by_tls),
            "active_request_count": self.active_request_count,
            "queue_vehicle_count": self.queue_vehicle_count,
            "halted_vehicle_count": self.halted_vehicle_count,
            "mean_speed_mps": self.mean_speed_mps,
            "waiting_time_s": self.waiting_time_s,
            "occupancy": self.occupancy,
            "spillback_risk": self.spillback_risk,
            "seconds_since_last_intervention_s": self.seconds_since_last_intervention_s,
            "behavior_policy_action": self.behavior_policy_action,
            "realized_outcome": self.realized_outcome,
        }


@dataclass
class CandidateEvaluation:
    scenario_id: str
    state_bucket: str
    policy_id: str
    action: str
    reward: float
    safety_status: str
    safety_reason: str
    selected: bool
    safe_decision: TSPDecision
    notes: List[str] = field(default_factory=list)

    @property
    def is_safety_blocked(self) -> bool:
        return self.safety_status == "blocked_by_safety"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "state_bucket": self.state_bucket,
            "policy_id": self.policy_id,
            "action": self.action,
            "reward": self.reward,
            "safety_status": self.safety_status,
            "safety_reason": self.safety_reason,
            "selected": self.selected,
            "safe_decision": self.safe_decision.to_dict(),
            "notes": list(self.notes),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


@dataclass
class LearnedPolicyRule:
    state_bucket: str
    action: str
    reward: float
    source_scenario_id: str
    safety_status: str
    safety_reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
