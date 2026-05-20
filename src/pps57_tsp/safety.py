#!/usr/bin/env python3
"""Safety Layer para bloquear ou ajustar decisões TSP antes da atuação TraCI.

Garantias *efetivamente verificadas* por esta camada (proxy):
  - verde mínimo da fase truncada (min_green_s);
  - extensão máxima e verde total máximo (max_green_extension_s, max_total_green_s);
  - bloqueio em transição amarela;
  - cooldown e número máximo de intervenções consecutivas por TLS;
  - extensão só na fase de corredor verde configurada;
  - a sequência de fases configurada coloca pelo menos uma fase intermédia
    (clearance) entre a fase conflituante e o verde do corredor
    (never_skip_yellow_or_all_red).

Garantias *delegadas e NÃO verificáveis no proxy* (declaradas explicitamente,
não silenciosamente assumidas):
  - que a fase intermédia da sequência é de facto amarelo/all-red e respeita a
    clearance pedonal — depende do plano semafórico SUMO real. Esta dependência
    é reconciliada no arranque (controller._verify_signal_programs) e, em caso de
    dúvida, a atuação é desativada (fail-closed).

Princípio geral: na ausência de dados que provem a segurança de uma atuação, a
decisão é BLOQUEADA (fail-closed), nunca aprovada com defaults permissivos.
"""
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

        if self._is_yellow_transition(signal_state):
            return self._blocked(decision, "current_phase_is_yellow_wait_for_next_cycle", notes)

        if self._cooldown_active(decision.tls_id, sim_time_s):
            return self._blocked(decision, "cooldown_after_priority_active", notes)

        self._reset_count_after_cooldown(decision.tls_id, sim_time_s)

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
        policy = self.tsp_config.decision_policy
        actuation = self.tsp_config.actuation

        if not bool(actuation.get("allow_green_extension", True)):
            return self._blocked(decision, "green_extension_disabled_by_config", notes)

        if decision.extension_s <= 0:
            return self._blocked(decision, "green_extension_not_positive", notes)

        mapping = self.tsp_config.phase_mapping_for_tls(decision.tls_id)
        corridor_phase = _optional_int(mapping.get("corridor_green_phase_index"))
        if corridor_phase is not None and signal_state.current_phase_index != corridor_phase:
            return self._blocked(decision, "green_extension_requires_corridor_green_phase", notes)

        # Bound de segurança obrigatório: sem ele, não é possível provar que a
        # extensão é segura -> fail-closed.
        max_extension = self._required_safety_value("max_green_extension_s")
        if max_extension is None:
            return self._blocked(decision, "safety_constraint_missing:max_green_extension_s", notes)
        # O teto de política só pode *reduzir* a extensão, nunca substituir o
        # bound de segurança.
        max_extension = min(max_extension, float(policy.get("green_extension_max_s", max_extension)))
        extension_s = min(decision.extension_s, max_extension)
        if extension_s < decision.extension_s:
            notes.append(f"Extensão reduzida pela safety layer: {decision.extension_s:.1f}s -> {extension_s:.1f}s.")

        max_total_green = self._required_safety_value("max_total_green_s")
        if max_total_green is None:
            return self._blocked(decision, "safety_constraint_missing:max_total_green_s", notes)

        remaining_s = TSPDecisionEngine.remaining_phase_time_s(signal_state, sim_time_s)
        if remaining_s is None:
            # Sem o tempo restante de fase não é possível garantir o bound de
            # verde total -> fail-closed.
            return self._blocked(decision, "green_extension_unknown_remaining_phase_time", notes)
        if signal_state.spent_duration_s is None:
            return self._blocked(decision, "green_extension_unknown_spent_phase_time", notes)
        spent_s = float(signal_state.spent_duration_s)

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

        min_green = self._required_safety_value("min_green_s")
        if min_green is None:
            return self._blocked(decision, "safety_constraint_missing:min_green_s", notes)
        if signal_state.spent_duration_s is None:
            # Não sabemos há quanto tempo a fase conflituante está verde -> não
            # é possível garantir o verde mínimo -> fail-closed.
            return self._blocked(decision, "early_green_unknown_spent_phase_time", notes)
        spent_s = float(signal_state.spent_duration_s)
        if spent_s < min_green:
            return self._blocked(decision, f"min_green_not_satisfied:{spent_s:.1f}<{min_green:.1f}", notes)

        requested_duration = decision.phase_duration_s
        if requested_duration is None:
            requested_duration = float(policy.get("red_truncation_to_s", 2))

        sequence_problem = self._phase_sequence_clearance_check(signal_state, decision)
        if sequence_problem is not None:
            return self._blocked(decision, sequence_problem, notes)

        # Disclosure honesto: a clearance pedonal não tem modelo no proxy.
        if bool(safety.get("pedestrian_clearance_must_not_be_shortened", True)):
            notes.append(
                "Nota: pedestrian_clearance_must_not_be_shortened depende do plano "
                "SUMO conter a fase de clearance configurada na phase_sequence; "
                "não existe modelo pedonal no proxy (ver _verify_signal_programs)."
            )
        else:
            notes.append("Aviso: pedestrian_clearance_must_not_be_shortened=false na config.")

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

    def _reset_count_after_cooldown(self, tls_id: str, sim_time_s: float) -> None:
        last = self.last_intervention_time_by_tls.get(tls_id)
        if last is None:
            return
        cooldown = float(self.cits_config.safety_constraints.get("cooldown_after_priority_s", 90))
        if sim_time_s - last >= cooldown:
            self.reset_intervention_count(tls_id)

    @staticmethod
    def _is_yellow_transition(signal_state: SignalState) -> bool:
        return bool(signal_state.red_yellow_green_state and any(ch.lower() == "y" for ch in signal_state.red_yellow_green_state))

    def _phase_sequence_clearance_check(
        self, signal_state: SignalState, decision: TSPDecision
    ) -> Optional[str]:
        """Devolve None se a transição é estruturalmente segura, ou o motivo do bloqueio.

        Verifica (a) que o verde-alvo é alcançável a partir da fase atual segundo
        a sequência configurada e (b) — quando never_skip_yellow_or_all_red está
        ativo (default estrito) — que existe pelo menos uma fase intermédia entre
        a fase conflituante atual e o verde-alvo, para o programa SUMO poder
        executar a clearance amarelo/all-red. Fail-closed em dados em falta.
        """
        current = signal_state.current_phase_index
        target = decision.target_phase_index
        if current is None or target is None:
            return "early_green_phase_indices_unknown"

        mapping = self.tsp_config.phase_mapping_for_tls(decision.tls_id)
        sequence = [_optional_int(item) for item in mapping.get("phase_sequence", [0, 1, 2, 3])]
        sequence = [item for item in sequence if item is not None]
        if current not in sequence or target not in sequence:
            return "early_green_phase_not_in_configured_sequence"

        current_pos = sequence.index(current)
        next_phase = sequence[(current_pos + 1) % len(sequence)]
        after_transition = sequence[(current_pos + 2) % len(sequence)]
        if target not in {next_phase, after_transition}:
            return "early_green_target_phase_not_next_after_transition"

        never_skip = bool(self.cits_config.safety_constraints.get("never_skip_yellow_or_all_red", True))
        if never_skip and target == next_phase:
            # Ir diretamente da fase conflituante para o verde-alvo, sem fase
            # intermédia, saltaria a clearance amarelo/all-red que o programa
            # tem de executar entre movimentos em conflito.
            return "early_green_would_skip_clearance_phase"
        return None

    def _required_safety_value(self, key: str) -> Optional[float]:
        """Lê um bound de segurança numérico; devolve None se ausente/inválido.

        O chamador deve tratar None como motivo de bloqueio (fail-closed): um
        bound de segurança em falta nunca é substituído por um default.
        """
        value = self.cits_config.safety_constraints.get(key)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

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


def _optional_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
