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


# Fonte de verdade única para o conjunto de ações que de facto atuam o
# semáforo (TraCI setPhaseDuration). Antes este literal estava duplicado em
# engine/optimizer/rl_trainer/event_dataset/policy_runtime, onde concordavam
# apenas por coincidência. Consumidores com acesso a config devem preferir
# TSPConfig.actuating_actions() (config-driven com este conjunto como default).
DEFAULT_ACTUATING_ACTIONS: frozenset[str] = frozenset(
    {TSPAction.GREEN_EXTENSION.value, TSPAction.EARLY_GREEN.value}
)


class DecisionStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    BLOCKED_BY_SAFETY = "blocked_by_safety"
    NOT_ACTUABLE = "not_actuable"
    APPLIED = "applied"


class ReasonCode(str, Enum):
    """Registo único dos códigos de motivo emitidos no caminho de decisão TSP.

    `.value` é exactamente igual à string (ou ao prefixo, para motivos
    dinâmicos com operandos) emitida hoje por engine/safety/controller, pelo
    que agregações por valor (p.ex. demonstrator.safety_block_by_reason)
    permanecem inalteradas. Motivos dinâmicos (PRIORITY_SCORE_BELOW_THRESHOLD,
    MIN_GREEN_NOT_SATISFIED) são emitidos como f"{code.value}:{operandos}". Um
    teste de drift (tests/test_explainability) verifica que todo o literal de
    motivo em engine/safety/controller está registado aqui.
    """

    # --- engine (decisão) ---
    PRIORITY_REQUEST_CANCELLATION = "priority_request_cancellation_no_tsp_actuation"
    REQUEST_EXPIRED = "request_expired_before_tsp_decision"
    PRIORITY_SCORE_BELOW_THRESHOLD = "priority_score_below_threshold"  # dinâmico: :score<min
    GREEN_WINDOW_ALREADY_SUFFICIENT = "green_window_already_sufficient"
    EXTEND_CURRENT_GREEN = "extend_current_green_to_cover_bus_eta"
    BUS_TOO_CLOSE_FOR_SAFE_RED_TRUNCATION = "bus_too_close_for_safe_red_truncation"
    TRUNCATE_CONFLICTING_PHASE = "truncate_conflicting_phase_to_anticipate_priority_movement_green"

    # --- engine v2: prioridade condicional e decisão cost-aware ---
    PRIORITY_NEED_NOT_MET = "priority_need_not_met"  # dinâmico: :delay/headway vs mínimos
    NETWORK_PRESSURE_DEFER = "network_pressure_defer_intervention"  # dinâmico: :sinal observado
    EARLY_GREEN_DEFERRED_MIN_GREEN = "early_green_deferred_until_min_green_served"  # dinâmico: :spent<min
    EARLY_GREEN_PRECHECK_DEFER = "early_green_precheck_defer"  # dinâmico: :problema de sequência
    GREEN_EXTENSION_PRECHECK_DEFER = "green_extension_precheck_defer"  # dinâmico: :dado em falta
    INTERVENTION_BENEFIT_TOO_SMALL = "intervention_benefit_too_small"  # dinâmico: :saving<min
    GREEN_COMPENSATION_PAYBACK = "green_compensation_payback"
    # v2.2: recuperação de coordenação — devolve o desvio de ciclo causado por
    # extensões de verde, encurtando a fase estendida na ativação seguinte.
    COORDINATION_RECOVERY_PAYBACK = "coordination_recovery_cycle_resync_payback"

    # --- safety: cooldown / consecutivas / amarelo / recovery ---
    CURRENT_PHASE_IS_YELLOW = "current_phase_is_yellow_wait_for_next_cycle"
    COOLDOWN_AFTER_PRIORITY_ACTIVE = "cooldown_after_priority_active"
    RECOVERY_DEBT_LIMIT_ACTIVE = "recovery_debt_limit_active"
    SAFETY_CONSTRAINT_MISSING_MAX_CONSECUTIVE = "safety_constraint_missing:max_consecutive_priority_interventions_per_tls"
    MAX_CONSECUTIVE_INTERVENTIONS_REACHED = "max_consecutive_priority_interventions_reached"
    UNSUPPORTED_TSP_ACTION = "unsupported_tsp_action"

    # --- safety: green extension ---
    GREEN_EXTENSION_DISABLED_BY_CONFIG = "green_extension_disabled_by_config"
    SIGNAL_GROUP_CONTRACT_MISSING = "signal_group_contract_missing"
    GREEN_EXTENSION_DISABLED_FOR_SIGNAL_GROUP = "green_extension_disabled_for_signal_group"
    GREEN_EXTENSION_NOT_POSITIVE = "green_extension_not_positive"
    GREEN_EXTENSION_UNKNOWN_TARGET_PHASE = "green_extension_unknown_target_phase"
    GREEN_EXTENSION_REQUIRES_PRIORITY_MOVEMENT_GREEN_PHASE = "green_extension_requires_priority_movement_green_phase"
    GREEN_EXTENSION_REQUIRES_PROTECTED_GREEN = "green_extension_requires_protected_green"
    SAFETY_CONSTRAINT_MISSING_MAX_GREEN_EXTENSION_S = "safety_constraint_missing:max_green_extension_s"
    SAFETY_CONSTRAINT_MISSING_MAX_TOTAL_GREEN_S = "safety_constraint_missing:max_total_green_s"
    GREEN_EXTENSION_UNKNOWN_REMAINING_PHASE_TIME = "green_extension_unknown_remaining_phase_time"
    GREEN_EXTENSION_UNKNOWN_SPENT_PHASE_TIME = "green_extension_unknown_spent_phase_time"
    MAX_TOTAL_GREEN_ALREADY_REACHED = "max_total_green_already_reached"
    GREEN_EXTENSION_CLIPPED_TO_ZERO = "green_extension_clipped_to_zero"
    APPROVED_GREEN_EXTENSION = "approved_green_extension"
    # v2.2: lifecycle check-in/check-out — orçamento cumulativo do evento.
    GREEN_EXTENSION_EVENT_BUDGET_EXHAUSTED = "green_extension_event_budget_exhausted"
    EXTENSION_RETURNED_AT_CHECKOUT = "green_extension_returned_at_bus_checkout"

    # --- safety: early green / red truncation ---
    RED_TRUNCATION_DISABLED_BY_CONFIG = "red_truncation_disabled_by_config"
    EARLY_GREEN_DISABLED_FOR_SIGNAL_GROUP = "early_green_disabled_for_signal_group"
    SIGNAL_GROUP_CONFLICT_MATRIX_MISSING = "signal_group_conflict_matrix_missing"
    SAFETY_CONSTRAINT_MISSING_MIN_GREEN_S = "safety_constraint_missing:min_green_s"
    EARLY_GREEN_UNKNOWN_SPENT_PHASE_TIME = "early_green_unknown_spent_phase_time"
    MIN_GREEN_NOT_SATISFIED = "min_green_not_satisfied"  # dinâmico: :spent<min
    PEDESTRIAN_CLEARANCE_UNVERIFIABLE = "pedestrian_clearance_unverifiable_signal_program_not_validated"
    PHASE_ALREADY_CLOSE_TO_SWITCH = "phase_already_close_to_switch"
    APPROVED_RED_TRUNCATION = "approved_red_truncation"

    # --- safety: verificações de sequência / conflito de fase ---
    EARLY_GREEN_PHASE_INDICES_UNKNOWN = "early_green_phase_indices_unknown"
    EARLY_GREEN_TARGET_PHASE_ALREADY_ACTIVE = "early_green_target_phase_already_active"
    EARLY_GREEN_PHASE_NOT_IN_CONFIGURED_SEQUENCE = "early_green_phase_not_in_configured_sequence"
    EARLY_GREEN_TARGET_PHASE_NOT_IN_REMAINING_SEQUENCE = "early_green_target_phase_not_in_remaining_sequence"
    EARLY_GREEN_WOULD_SKIP_CLEARANCE_PHASE = "early_green_would_skip_clearance_phase"
    EARLY_GREEN_CURRENT_PHASE_UNKNOWN = "early_green_current_phase_unknown"
    EARLY_GREEN_CURRENT_PHASE_SIGNAL_GROUP_UNKNOWN = "early_green_current_phase_signal_group_unknown"
    EARLY_GREEN_CURRENT_PHASE_NOT_CONFIGURED_AS_CONFLICT = "early_green_current_phase_not_configured_as_conflict"

    # --- controller (orquestração) ---
    SUPERSEDED_BY_EARLIER_INTERVENTION_SAME_STEP = "superseded_by_earlier_intervention_same_step"
    NETWORK_STATE_DEGRADED_DETECTOR_READ_FAILURE = "network_state_degraded_detector_read_failure"

    # --- corridor arbiter (P6) — downgrade-only, pré-Safety, opt-in ---
    DEFERRED_CORRIDOR_RECOVERY_DEBT_EXHAUSTED = "deferred_corridor_recovery_debt_exhausted"
    DEFERRED_DOWNSTREAM_SPILLBACK_RISK = "deferred_downstream_spillback_risk"


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
    eta_to_stopline_s: float
    schedule_delay_s: float
    headway_deviation_s: float
    vehicle_class: str = ""
    priority_level: str = ""
    current_edge_id: str = ""
    current_lane_id: str = ""
    next_edge_id: str = ""
    priority_movement_id: str = ""
    target_signal_group_id: str = ""
    decision_id: str = field(default_factory=lambda: str(uuid4()))
    extension_s: float = 0.0
    phase_duration_s: Optional[float] = None
    target_phase_index: Optional[int] = None
    current_phase_index: Optional[int] = None
    current_signal_state: Optional[str] = None
    current_next_switch_s: Optional[float] = None
    current_spent_duration_s: Optional[float] = None
    controlled_lanes: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    correlation_id: Optional[str] = None
    # Decomposição por-termo do priority_score ({raw, normalised, weight,
    # contribution} por objetivo). engine.priority_score já a computa; antes só
    # o escalar sobrevivia. Aditivo e serializa via to_dict()/to_json().
    score_components: Dict[str, Any] = field(default_factory=dict)

    @property
    def requires_actuation(self) -> bool:
        return self.action in DEFAULT_ACTUATING_ACTIONS

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
    controller_response: Dict[str, Any] = field(default_factory=dict)
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
