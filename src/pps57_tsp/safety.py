#!/usr/bin/env python3
"""Safety Layer para bloquear ou ajustar decisões TSP antes da atuação TraCI."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

from pps57_cits.config import CITSConfig
from pps57_cits.models import SignalState

from .config import TSPConfig
from .engine import TSPDecisionEngine
from .models import DecisionStatus, SafetyValidationResult, TSPAction, TSPDecision


@dataclass
class TSPSafetyLayer:
    cits_config: CITSConfig
    tsp_config: TSPConfig
    last_intervention_time_by_tls: Dict[str, float] = field(default_factory=dict)
    consecutive_interventions_by_tls: Dict[str, int] = field(default_factory=dict)

    def validate(self, decision: TSPDecision, signal_state: SignalState, sim_time_s: float) -> SafetyValidationResult:
        safety = self.cits_config.safety_constraints
        notes = list(decision.notes)

        if decision.action in {TSPAction.NO_ACTION.value, TSPAction.REJECT.value, TSPAction.REEVALUATE_NEXT_CYCLE.value}:
            safe = decision.copy_with(status=DecisionStatus.NOT_ACTUABLE.value)
            return SafetyValidationResult(
                decision_id=decision.decision_id,
                approved=False,
                status=safe.status,
                reason=decision.reason,
                safe_decision=safe,
                notes=notes + ["Sem atuação semafórica requerida."],
            )

        if self._cooldown_active(decision.tls_id, sim_time_s):
            return self._blocked(decision, "cooldown_after_priority_active", notes)

        max_consecutive = int(safety.get("max_consecutive_priority_interventions_per_tls", 2))
        if self.consecutive_interventions_by_tls.get(decision.tls_id, 0) >= max_consecutive:
            return self._blocked(decision, "max_consecutive_priority_interventions_reached", notes)

        if decision.action == TSPAction.GREEN_EXTENSION.value:
            return self._validate_green_extension(decision, signal_state, sim_time_s, notes)

        if decision.action == TSPAction.EARLY_GREEN.value:
            return self._validate_early_green(decision, signal_state, sim_time_s, notes)

        return self._blocked(decision, "unsupported_tsp_action", notes)

    def mark_applied(self, decision: TSPDecision, sim_time_s: float) -> None:
        if decision.action not in {TSPAction.GREEN_EXTENSION.value, TSPAction.EARLY_GREEN.value}:
            return
        self.last_intervention_time_by_tls[decision.tls_id] = sim_time_s
        self.consecutive_interventions_by_tls[decision.tls_id] = self.consecutive_interventions_by_tls.get(decision.tls_id, 0) + 1

    def reset_intervention_count(self, tls_id: str) -> None:
        self.consecutive_interventions_by_tls[tls_id] = 0

    def _validate_green_extension(
        self,
        decision: TSPDecision,
        signal_state: SignalState,
        sim_time_s: float,
        notes: list[str],
    ) -> SafetyValidationResult:
        safety = self.cits_config.safety_constraints
        policy = self.tsp_config.decision_policy
        actuation = self.tsp_config.actuation

        if not bool(actuation.get("allow_green_extension", True)):
            return self._blocked(decision, "green_extension_disabled_by_config", notes)

        if decision.extension_s <= 0:
            return self._blocked(decision, "green_extension_not_positive", notes)

        max_extension = float(safety.get("max_green_extension_s", policy.get("green_extension_max_s", 12)))
        extension_s = min(decision.extension_s, max_extension)
        if extension_s < decision.extension_s:
            notes.append(f"Extensão reduzida pela safety layer: {decision.extension_s:.1f}s -> {extension_s:.1f}s.")

        remaining_s = TSPDecisionEngine.remaining_phase_time_s(signal_state, sim_time_s)
        spent_s = float(signal_state.spent_duration_s or 0.0)
        max_total_green = float(safety.get("max_total_green_s", 55))
        if remaining_s is not None:
            allowed_by_total = max_total_green - spent_s - remaining_s
            if allowed_by_total <= 0:
                return self._blocked(decision, "max_total_green_already_reached", notes)
            if extension_s > allowed_by_total:
                notes.append(f"Extensão limitada por max_total_green: {extension_s:.1f}s -> {allowed_by_total:.1f}s.")
                extension_s = max(0.0, allowed_by_total)

        if extension_s <= 0:
            return self._blocked(decision, "green_extension_clipped_to_zero", notes)

        safe = decision.copy_with(status=DecisionStatus.APPROVED.value, extension_s=round(extension_s, 3), notes=notes)
        return SafetyValidationResult(
            decision_id=decision.decision_id,
            approved=True,
            status=safe.status,
            reason="approved_green_extension",
            safe_decision=safe,
            notes=notes + ["Safety Layer aprovou extensão de verde."],
        )

    def _validate_early_green(
        self,
        decision: TSPDecision,
        signal_state: SignalState,
        sim_time_s: float,
        notes: list[str],
    ) -> SafetyValidationResult:
        safety = self.cits_config.safety_constraints
        actuation = self.tsp_config.actuation
        policy = self.tsp_config.decision_policy

        if not bool(actuation.get("allow_red_truncation", True)):
            return self._blocked(decision, "red_truncation_disabled_by_config", notes)

        if bool(actuation.get("allow_direct_phase_jump", False)):
            notes.append("Config permite salto direto de fase, mas o MVP usa setPhaseDuration por defeito.")

        min_green = float(safety.get("min_green_s", 8))
        spent_s = float(signal_state.spent_duration_s or 0.0)
        if spent_s < min_green:
            return self._blocked(decision, f"min_green_not_satisfied:{spent_s:.1f}<{min_green:.1f}", notes)

        if signal_state.red_yellow_green_state and any(ch.lower() == "y" for ch in signal_state.red_yellow_green_state):
            return self._blocked(decision, "current_phase_is_yellow_wait_for_next_cycle", notes)

        requested_duration = decision.phase_duration_s
        if requested_duration is None:
            requested_duration = float(policy.get("red_truncation_to_s", 2))

        remaining_s = TSPDecisionEngine.remaining_phase_time_s(signal_state, sim_time_s)
        if remaining_s is not None and remaining_s <= requested_duration:
            safe = decision.copy_with(
                action=TSPAction.REEVALUATE_NEXT_CYCLE.value,
                status=DecisionStatus.NOT_ACTUABLE.value,
                reason="phase_already_close_to_switch",
                phase_duration_s=None,
                notes=notes + ["Fase já está perto da transição; não é necessário truncar."],
            )
            return SafetyValidationResult(
                decision_id=decision.decision_id,
                approved=False,
                status=safe.status,
                reason=safe.reason,
                safe_decision=safe,
                notes=safe.notes,
            )

        safe = decision.copy_with(
            status=DecisionStatus.APPROVED.value,
            phase_duration_s=round(max(0.1, float(requested_duration)), 3),
            notes=notes,
        )
        return SafetyValidationResult(
            decision_id=decision.decision_id,
            approved=True,
            status=safe.status,
            reason="approved_red_truncation",
            safe_decision=safe,
            notes=notes + ["Safety Layer aprovou early green por truncagem da fase corrente."],
        )

    def _cooldown_active(self, tls_id: str, sim_time_s: float) -> bool:
        last = self.last_intervention_time_by_tls.get(tls_id)
        if last is None:
            return False
        cooldown = float(self.cits_config.safety_constraints.get("cooldown_after_priority_s", 90))
        return sim_time_s - last < cooldown

    def _blocked(self, decision: TSPDecision, reason: str, notes: Optional[list[str]] = None) -> SafetyValidationResult:
        safe = decision.copy_with(status=DecisionStatus.BLOCKED_BY_SAFETY.value, reason=reason, notes=list(notes or []))
        return SafetyValidationResult(
            decision_id=decision.decision_id,
            approved=False,
            status=safe.status,
            reason=reason,
            safe_decision=safe,
            notes=list(notes or []) + [f"Safety Layer bloqueou decisão: {reason}."],
        )
