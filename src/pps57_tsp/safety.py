#!/usr/bin/env python3
"""Safety Layer para bloquear ou ajustar decisões TSP antes da atuação TraCI.

Garantias *efetivamente verificadas* por esta camada (proxy):
  - verde mínimo da fase truncada (min_green_s);
  - extensão máxima e verde total máximo (max_green_extension_s, max_total_green_s);
  - bloqueio em transição amarela;
  - cooldown e número máximo de intervenções consecutivas por TLS;
  - extensão só na fase verde do movimento prioritário configurado;
  - a sequência de fases configurada coloca pelo menos uma fase intermédia
    (clearance) entre a fase conflituante e o verde do movimento prioritário
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
from pps57_cits.messages import OperatorPriorityClass
from pps57_cits.models import SignalState

from .config import TSPConfig
from .engine import TSPDecisionEngine
from .models import DecisionStatus, SafetyValidationResult, TSPAction, TSPDecision
from .signal_control import ControllerContract, SignalGroupContract, build_controller_contract


@dataclass
class TSPSafetyLayer:
    cits_config: CITSConfig
    tsp_config: TSPConfig
    last_intervention_time_by_tls: Dict[str, float] = field(default_factory=dict)
    consecutive_interventions_by_tls: Dict[str, int] = field(default_factory=dict)
    recovery_debt_by_tls: Dict[str, float] = field(default_factory=dict)
    recovery_debt_update_time_by_tls: Dict[str, float] = field(default_factory=dict)
    # True após o controller reconciliar o controller contract com o programa
    # real e confirmar que as fases intergreen são intergreen (sem 'g').
    # Sem isto, não há prova de que truncar a fase corrente não compromete a
    # clearance pedonal -> fail-closed quando o flag de safety estiver ligado.
    signal_program_verified: bool = False

    def set_signal_program_verified(self, verified: bool) -> None:
        self.signal_program_verified = bool(verified)

    def validate(self, decision: TSPDecision, signal_state: SignalState, sim_time_s: float) -> SafetyValidationResult:
        notes = list(decision.notes)
        emergency_preemption = decision.priority_level == OperatorPriorityClass.EMERGENCY.value

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

        if self._is_yellow_transition(signal_state, decision):
            return self._blocked(decision, "current_phase_is_yellow_wait_for_next_cycle", notes)

        if not emergency_preemption and self._cooldown_active(decision.tls_id, sim_time_s):
            return self._blocked(decision, "cooldown_after_priority_active", notes)
        if emergency_preemption:
            notes.append("Emergency preemption: cooldown/recovery-debt rationing bypassed; clearance checks still enforced.")

        self._reset_count_after_cooldown(decision.tls_id, sim_time_s)
        if not emergency_preemption:
            self._recover_debt(decision.tls_id, sim_time_s)
            max_recovery_debt = self._optional_safety_value("max_recovery_debt_s")
            if max_recovery_debt is not None and self.recovery_debt_by_tls.get(decision.tls_id, 0.0) >= max_recovery_debt:
                return self._blocked(decision, "recovery_debt_limit_active", notes)

        # Fail-closed: max_consecutive em falta significa que não temos bound
        # de segurança para limitar intervenções repetidas no mesmo TLS.
        max_consecutive_raw = self._required_safety_value("max_consecutive_priority_interventions_per_tls")
        if max_consecutive_raw is None:
            return self._blocked(
                decision,
                "safety_constraint_missing:max_consecutive_priority_interventions_per_tls",
                notes,
            )
        max_consecutive = int(max_consecutive_raw)
        if not emergency_preemption and self.consecutive_interventions_by_tls.get(decision.tls_id, 0) >= max_consecutive:
            return self._blocked(decision, "max_consecutive_priority_interventions_reached", notes)

        if decision.action == TSPAction.GREEN_EXTENSION.value:
            return self._validate_green_extension(decision, signal_state, sim_time_s, notes)

        if decision.action == TSPAction.EARLY_GREEN.value:
            return self._validate_early_green(decision, signal_state, sim_time_s, notes)

        return self._blocked(decision, "unsupported_tsp_action", notes)

    def mark_applied(self, decision: TSPDecision, sim_time_s: float) -> None:
        if decision.action not in {TSPAction.GREEN_EXTENSION.value, TSPAction.EARLY_GREEN.value}:
            return
        self._recover_debt(decision.tls_id, sim_time_s)
        self.last_intervention_time_by_tls[decision.tls_id] = sim_time_s
        self.consecutive_interventions_by_tls[decision.tls_id] = self.consecutive_interventions_by_tls.get(decision.tls_id, 0) + 1
        added_debt = max(0.0, float(decision.extension_s or 0.0))
        if decision.action == TSPAction.EARLY_GREEN.value:
            added_debt = max(added_debt, float(decision.phase_duration_s or 0.0))
        self.recovery_debt_by_tls[decision.tls_id] = self.recovery_debt_by_tls.get(decision.tls_id, 0.0) + added_debt
        self.recovery_debt_update_time_by_tls[decision.tls_id] = sim_time_s

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
        contract = self._controller_contract(decision)
        signal_group = self._signal_group(decision, contract)

        if not bool(actuation.get("allow_green_extension", True)):
            return self._blocked(decision, "green_extension_disabled_by_config", notes)
        if signal_group is None:
            return self._blocked(decision, "signal_group_contract_missing", notes)
        if decision.action not in signal_group.allowed_actions:
            return self._blocked(decision, "green_extension_disabled_for_signal_group", notes)

        if decision.extension_s <= 0:
            return self._blocked(decision, "green_extension_not_positive", notes)

        target_phase = signal_group.phase_index
        if target_phase is None:
            return self._blocked(decision, "green_extension_unknown_target_phase", notes)
        if signal_state.current_phase_index != target_phase:
            return self._blocked(decision, "green_extension_requires_priority_movement_green_phase", notes)
        if signal_group.requires_protected_green and not _request_has_protected_green(signal_state, decision, signal_group):
            return self._blocked(decision, "green_extension_requires_protected_green", notes)

        # Bound de segurança obrigatório: sem ele, não é possível provar que a
        # extensão é segura -> fail-closed.
        max_extension = self._required_safety_value("max_green_extension_s")
        if max_extension is None:
            return self._blocked(decision, "safety_constraint_missing:max_green_extension_s", notes)
        if signal_group.max_extension_s is not None:
            max_extension = min(max_extension, signal_group.max_extension_s)
        # O teto de política só pode *reduzir* a extensão, nunca substituir o
        # bound de segurança.
        max_extension = min(max_extension, _positive_float(policy, "green_extension_max_s", max_extension))
        extension_s = min(decision.extension_s, max_extension)
        if extension_s < decision.extension_s:
            notes.append(f"Extensão reduzida pela safety layer: {decision.extension_s:.1f}s -> {extension_s:.1f}s.")

        max_total_green = self._required_safety_value("max_total_green_s")
        if max_total_green is None:
            return self._blocked(decision, "safety_constraint_missing:max_total_green_s", notes)
        if signal_group.max_green_s is not None:
            max_total_green = min(max_total_green, signal_group.max_green_s)

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
        contract = self._controller_contract(decision)
        signal_group = self._signal_group(decision, contract)

        if not bool(actuation.get("allow_red_truncation", True)):
            return self._blocked(decision, "red_truncation_disabled_by_config", notes)
        if signal_group is None:
            return self._blocked(decision, "signal_group_contract_missing", notes)
        if decision.action not in signal_group.allowed_actions:
            return self._blocked(decision, "early_green_disabled_for_signal_group", notes)
        if not signal_group.conflicts_with:
            return self._blocked(decision, "signal_group_conflict_matrix_missing", notes)

        if bool(actuation.get("allow_direct_phase_jump", False)):
            notes.append("Config permite salto direto de fase, mas o MVP usa setPhaseDuration por defeito.")

        min_green = self._required_safety_value("min_green_s")
        if min_green is None:
            return self._blocked(decision, "safety_constraint_missing:min_green_s", notes)
        phase_min_green = contract.min_green_for_phase(signal_state.current_phase_index)
        if phase_min_green is not None:
            min_green = max(min_green, phase_min_green)
        if signal_state.spent_duration_s is None:
            # Não sabemos há quanto tempo a fase conflituante está verde -> não
            # é possível garantir o verde mínimo -> fail-closed.
            return self._blocked(decision, "early_green_unknown_spent_phase_time", notes)
        spent_s = float(signal_state.spent_duration_s)
        if spent_s < min_green:
            return self._blocked(decision, f"min_green_not_satisfied:{spent_s:.1f}<{min_green:.1f}", notes)

        requested_duration = decision.phase_duration_s
        if requested_duration is None:
            requested_duration = _positive_float(policy, "red_truncation_to_s", 2.0)

        sequence_problem = self._phase_sequence_clearance_check(signal_state, decision)
        if sequence_problem is not None:
            return self._blocked(decision, sequence_problem, notes)
        current_conflict_problem = self._current_phase_conflict_check(signal_state, signal_group, contract)
        if current_conflict_problem is not None:
            return self._blocked(decision, current_conflict_problem, notes)

        # Enforcement: o early_green encurta a fase conflituante actual via
        # setPhaseDuration; só *não* compromete a clearance pedonal se o
        # programa SUMO tiver fases intermédias intergreen genuínas (sem 'g')
        # entre conflito e verde-alvo. Isso é validado uma vez por
        # `controller._verify_signal_programs` e propagado via
        # `signal_program_verified`. Sem verificação -> fail-closed.
        if bool(safety.get("pedestrian_clearance_must_not_be_shortened", True)):
            if not self.signal_program_verified:
                return self._blocked(
                    decision,
                    "pedestrian_clearance_unverifiable_signal_program_not_validated",
                    notes,
                )
            notes.append(
                "Clearance pedonal preservada: fases intermédias intergreen confirmadas "
                "pelo programa SUMO (signal_program_verified=True)."
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
        cooldown = self._required_safety_value("cooldown_after_priority_s")
        if cooldown is None:
            # Sem cooldown configurado não há como provar que o intervalo de
            # segurança decorreu -> assumir cooldown ativo (fail-closed).
            return True
        return sim_time_s - last < cooldown

    def _reset_count_after_cooldown(self, tls_id: str, sim_time_s: float) -> None:
        last = self.last_intervention_time_by_tls.get(tls_id)
        if last is None:
            return
        cooldown = self._required_safety_value("cooldown_after_priority_s")
        if cooldown is None:
            return  # fail-closed: sem cooldown configurado, nunca reseta o contador
        if sim_time_s - last >= cooldown:
            self.reset_intervention_count(tls_id)

    def _recover_debt(self, tls_id: str, sim_time_s: float) -> None:
        debt = self.recovery_debt_by_tls.get(tls_id, 0.0)
        last = self.recovery_debt_update_time_by_tls.get(tls_id)
        if debt <= 0 or last is None:
            self.recovery_debt_update_time_by_tls[tls_id] = sim_time_s
            return
        rate = self._optional_safety_value("recovery_debt_payback_rate_s_per_s")
        if rate is None:
            rate = 1.0
        recovered = max(0.0, sim_time_s - last) * max(0.0, rate)
        self.recovery_debt_by_tls[tls_id] = max(0.0, debt - recovered)
        self.recovery_debt_update_time_by_tls[tls_id] = sim_time_s

    def _is_yellow_transition(self, signal_state: SignalState, decision: TSPDecision) -> bool:
        """L1: bloqueio em amarelo passa a ser por-movimento (não global).

        Default conservador: qualquer 'y' no estado bloqueia (fail-safe). MAS
        para green_extension, se conseguirmos resolver as posições do movimento
        prioritário e nenhuma delas estiver em amarelo, libertamos — um amarelo
        noutra aproximação não conflitua necessariamente com extender esse verde.
        """
        ryg = signal_state.red_yellow_green_state or ""
        if not any(ch.lower() == "y" for ch in ryg):
            return False
        if decision.action == TSPAction.GREEN_EXTENSION.value:
            controlled_links = signal_state.controlled_links or []
            link_positions = [
                i for i, links in enumerate(controlled_links)
                if i < len(ryg) and _controlled_links_match_request(links, decision.current_lane_id, decision.next_edge_id)
            ]
            if link_positions and not any(ryg[i].lower() == "y" for i in link_positions):
                return False
            movement = self.cits_config.priority_movement_for_request(
                movement_id=decision.priority_movement_id,
                edge_id=decision.current_edge_id,
                next_edge_id=decision.next_edge_id,
                vehicle_class=decision.vehicle_class,
            )
            if movement is not None:
                movement_edges = set(movement.approach_edges)
                controlled = signal_state.controlled_lanes or []
                # Match por edge exata via lane-suffix stripping em vez de
                # startswith(edge+"_"): o sufixo "_" protege contra colisões
                # "I1_I2" vs "I1_I20", mas o rsplit é estruturalmente mais
                # robusto e independente do esquema de nomes das edges.
                movement_positions = [
                    i for i, lane in enumerate(controlled)
                    if i < len(ryg) and _lane_belongs_to_edge_set(lane, movement_edges)
                ]
                if movement_positions and not any(ryg[i].lower() == "y" for i in movement_positions):
                    return False
        return True

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
        if current == target:
            return "early_green_target_phase_already_active"

        contract = self._controller_contract(decision)
        sequence = list(contract.phase_sequence)
        if current not in sequence or target not in sequence:
            return "early_green_phase_not_in_configured_sequence"

        never_skip = bool(self.cits_config.safety_constraints.get("never_skip_yellow_or_all_red", True))
        current_pos = sequence.index(current)
        phases_until_target = []
        pos = current_pos
        for _ in range(1, len(sequence) + 1):
            pos = (pos + 1) % len(sequence)
            phase = sequence[pos]
            if phase == target:
                break
            phases_until_target.append(phase)
        else:
            return "early_green_target_phase_not_in_remaining_sequence"

        if not phases_until_target:
            return "early_green_would_skip_clearance_phase"
        if never_skip and not any(phase in contract.intergreen_phase_indices for phase in phases_until_target):
            return "early_green_would_skip_clearance_phase"
        return None

    def _current_phase_conflict_check(
        self,
        signal_state: SignalState,
        target_group: SignalGroupContract,
        contract: ControllerContract,
    ) -> Optional[str]:
        current = signal_state.current_phase_index
        if current is None:
            return "early_green_current_phase_unknown"
        current_groups = [
            group
            for group in contract.signal_groups.values()
            if group.phase_index == current and group.signal_group_id != target_group.signal_group_id
        ]
        if not current_groups:
            return "early_green_current_phase_signal_group_unknown"
        for current_group in current_groups:
            if (
                current_group.signal_group_id in target_group.conflicts_with
                or target_group.signal_group_id in current_group.conflicts_with
            ):
                return None
        return "early_green_current_phase_not_configured_as_conflict"

    def _controller_contract(self, decision: TSPDecision) -> ControllerContract:
        return build_controller_contract(self.cits_config, self.tsp_config, decision.tls_id)

    def _signal_group(
        self,
        decision: TSPDecision,
        contract: ControllerContract,
    ) -> Optional[SignalGroupContract]:
        if decision.target_signal_group_id:
            group = contract.signal_group_for_id(decision.target_signal_group_id)
            if group is not None:
                return group
        if decision.priority_movement_id:
            return contract.signal_group_for_movement(decision.priority_movement_id)
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

    def _optional_safety_value(self, key: str) -> Optional[float]:
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


def _lane_belongs_to_edge_set(lane_id: Optional[str], edges: set[str]) -> bool:
    """Lane SUMO `<edge>_<index>` pertence a `edges` sse extracted-edge ∈ edges."""
    if not lane_id or not edges:
        return False
    edge, _, suffix = lane_id.rpartition("_")
    if not edge or not suffix.isdigit():
        return False
    return edge in edges


def _controlled_links_match_request(links_for_signal: object, lane_id: str, next_edge_id: str) -> bool:
    if not lane_id or not isinstance(links_for_signal, list):
        return False
    for link in links_for_signal:
        if not isinstance(link, (list, tuple)) or len(link) < 2:
            continue
        incoming_lane = str(link[0])
        outgoing_lane = str(link[1])
        if incoming_lane != lane_id:
            continue
        if not next_edge_id:
            return True
        if outgoing_lane == next_edge_id or outgoing_lane.startswith(f"{next_edge_id}_"):
            return True
    return False


def _request_has_protected_green(
    signal_state: SignalState,
    decision: TSPDecision,
    signal_group: SignalGroupContract,
) -> bool:
    ryg = signal_state.red_yellow_green_state or ""
    controlled_links = signal_state.controlled_links or []
    for index, links in enumerate(controlled_links):
        if index < len(ryg) and _controlled_links_match_request(links, decision.current_lane_id, decision.next_edge_id):
            return ryg[index] == "G"

    controlled_lanes = signal_state.controlled_lanes or []
    for index, lane_id in enumerate(controlled_lanes):
        if index >= len(ryg):
            continue
        if lane_id == decision.current_lane_id:
            return ryg[index] == "G"
    if not signal_group.allow_edge_state_fallback:
        return False
    for index, lane_id in enumerate(controlled_lanes):
        if index >= len(ryg):
            continue
        if _lane_belongs_to_edge_set(lane_id, {decision.current_edge_id}):
            return ryg[index] == "G"
    return False


def _positive_float(mapping: dict, key: str, default: float) -> float:
    try:
        value = float(mapping.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default
