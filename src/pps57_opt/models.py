#!/usr/bin/env python3
"""Modelos para avaliação offline de políticas TSP otimizadas."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Any, Dict, List

from pps57_tsp.models import TSPDecision


@dataclass(frozen=True)
class OfflineScenario:
    scenario_id: str
    description: str
    expected_case: str
    sim_time_s: float
    request: Any
    signal_state: Any

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "description": self.description,
            "expected_case": self.expected_case,
            "sim_time_s": self.sim_time_s,
            "request": self.request.to_dict(),
            "signal_state": asdict(self.signal_state),
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
