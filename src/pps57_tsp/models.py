#!/usr/bin/env python3
"""Modelos internos para decisões TSP, validação de segurança e atuação."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
from typing import Any, Dict, List, Optional
from uuid import uuid4


class TSPAction(str, Enum):
    NO_ACTION = "no_action"
    GREEN_EXTENSION = "green_extension"
    EARLY_GREEN = "early_green"
    REEVALUATE_NEXT_CYCLE = "reevaluate_next_cycle"
    REJECT = "reject"


class DecisionStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    BLOCKED_BY_SAFETY = "blocked_by_safety"
    NOT_ACTUABLE = "not_actuable"
    APPLIED = "applied"
    DRY_RUN_APPLIED = "dry_run_applied"


@dataclass
class TSPDecision:
    timestamp_s: float
    request_id: str
    vehicle_id: str
    intersection_id: str
    tls_id: str
    rsu_id: str
    action: str
    status: str
    reason: str
    priority_score: float
    requested_maneuver: str
    eta_to_stopline_s: float
    schedule_delay_s: float
    headway_deviation_s: float
    decision_id: str = field(default_factory=lambda: str(uuid4()))
    extension_s: float = 0.0
    phase_duration_s: Optional[float] = None
    target_phase_index: Optional[int] = None
    current_phase_index: Optional[int] = None
    current_signal_state: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    correlation_id: Optional[str] = None

    @property
    def requires_actuation(self) -> bool:
        return self.action in {TSPAction.GREEN_EXTENSION.value, TSPAction.EARLY_GREEN.value}

    def copy_with(self, **changes: Any) -> "TSPDecision":
        payload = self.to_dict()
        payload.update(changes)
        # Remove fields that are properties or not constructor compatible.
        return TSPDecision(**payload)

    def to_dict(self) -> Dict[str, Any]:
        return _normalise(asdict(self))

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


@dataclass
class SafetyValidationResult:
    decision_id: str
    approved: bool
    status: str
    reason: str
    safe_decision: TSPDecision
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _normalise(asdict(self))


@dataclass
class ActuationResult:
    decision_id: str
    timestamp_s: float
    tls_id: str
    action: str
    applied: bool
    dry_run: bool
    command: str
    reason: str
    parameters: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _normalise(asdict(self))

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


def _normalise(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_normalise(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalise(item) for key, item in value.items()}
    return value
