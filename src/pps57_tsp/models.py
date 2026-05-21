#!/usr/bin/env python3
"""Modelos internos para decisões TSP, validação de segurança e atuação."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from enum import Enum
import json
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pps57_cits.messages import normalise_for_json  # L6: helper partilhado


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
        # `dataclasses.replace` preserva tipos (sem o round-trip JSON de
        # `to_dict()`). `notes` é o único campo mutável: copia-se a lista
        # quando o chamador não a substitui, para o novo objeto não partilhar
        # a lista do original (mantém a semântica do antigo asdict deep-copy).
        if "notes" not in changes:
            changes["notes"] = list(self.notes)
        return replace(self, **changes)

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
    no_actuation: bool
    command: str
    reason: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    # "info" = normal applied/skipped; "warning" = decisão chegou ao atuador
    # mas a ação não é suportada; "error" = TraCI levantou exceção a meio
    # de uma atuação — auditoria deve filtrar por severity para detetar
    # falhas em vez de fazer match de substrings do `reason`.
    severity: str = "info"

    def to_dict(self) -> Dict[str, Any]:
        return _normalise(asdict(self))

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


# L6: factorizado. Mantém alias local para minimizar diff em call-sites.
_normalise = normalise_for_json
