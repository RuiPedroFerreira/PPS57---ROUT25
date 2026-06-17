#!/usr/bin/env python3
"""Compensação NEMA-style: devolve à fase truncada o verde removido (v2.1).

Depois de um early green TSP, os controladores reais concedem verde de
compensação à fase lesada no ciclo seguinte, para a perturbação não se
propagar ciclo após ciclo — é o ataque directo ao custo residual no tráfego
geral que a evidência v1 mostrou (+20% a +44% de time loss).

Desenho:
- A dívida acumula por (tls, fase truncada) quando um early green é aplicado
  (ou "would-apply" em modo no-actuation, espelhando os contadores H5 da
  Safety Layer).
- O pagamento acontece na transição para a fase lesada, em prestações
  limitadas por ``compensation_max_per_cycle_s`` e nunca além do
  ``max_total_green_s`` da safety (fail-closed sem dados de fase).
- Opt-in por config: ``actuation.compensation_enabled``.

v2.2 — recuperação de coordenação (lado simétrico, para extensões):
o pagamento da compensação re-alinha o ciclo depois de um early green (o
verde devolvido anula o desvio), mas cada green extension desloca o ciclo
permanentemente para a frente — em corredor, a onda verde decai intervenção
após intervenção. Os controladores coordenados reais fazem "transition back
to coordination". Aqui: cada extensão aplicada regista um *reclaim* na fase
estendida; na ativação seguinte dessa fase, encurta-se a fase em prestações
(``compensation_max_per_cycle_s``) nunca abaixo do ``min_green_s`` da safety
(fail-closed sem o bound). Opt-in: ``actuation.coordination_recovery_enabled``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pps57_cits.config import CITSConfig
from pps57_cits.models import SignalState

from .config import TSPConfig
from .models import ActuationResult, ReasonCode, TSPAction, TSPDecision
from .signal_control import SignalControlAdapter
from .util import float_or_default as _float_or_default
from .util import optional_float as _optional_float


@dataclass
class GreenCompensationManager:
    cits_config: CITSConfig
    tsp_config: TSPConfig
    owed_s_by_tls_phase: dict[str, dict[int, float]] = field(default_factory=dict)
    # v2.2: verde a RECLAMAR (encurtar) à fase estendida, por (tls, fase) — o
    # simétrico do owed, para re-alinhar o ciclo depois de green extensions.
    reclaim_s_by_tls_phase: dict[str, dict[int, float]] = field(default_factory=dict)
    granted_s_total: float = 0.0
    reclaimed_s_total: float = 0.0
    _last_phase_by_tls: dict[str, int | None] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return bool(self.tsp_config.actuation.get("compensation_enabled", False))

    @property
    def recovery_enabled(self) -> bool:
        return bool(self.tsp_config.actuation.get("coordination_recovery_enabled", False))

    def register_applied(self, decision: TSPDecision) -> None:
        """Acumula dívida (early green) ou reclaim (green extension) aplicados."""
        if decision.action == TSPAction.EARLY_GREEN.value:
            self._register_early_green(decision)
        elif decision.action == TSPAction.GREEN_EXTENSION.value:
            self._register_green_extension(decision)

    def _register_early_green(self, decision: TSPDecision) -> None:
        if not self.enabled:
            return
        if decision.current_phase_index is None or decision.current_next_switch_s is None:
            # Sem next_switch o verde removido não é mensurável -> sem dívida
            # inventada (fail-closed, coerente com o recovery debt da safety).
            return
        remaining_at_decision_s = max(
            0.0, float(decision.current_next_switch_s) - float(decision.timestamp_s)
        )
        truncated_to_s = max(0.0, float(decision.phase_duration_s or 0.0))
        removed_s = max(0.0, remaining_at_decision_s - truncated_to_s)
        if removed_s <= 0.0:
            return
        phase = int(decision.current_phase_index)
        owed = self.owed_s_by_tls_phase.setdefault(decision.tls_id, {})
        owed[phase] = owed.get(phase, 0.0) + removed_s

    def _register_green_extension(self, decision: TSPDecision) -> None:
        if not self.recovery_enabled:
            return
        if decision.current_phase_index is None:
            return
        extension_s = max(0.0, float(decision.extension_s or 0.0))
        if extension_s <= 0.0:
            return
        phase = int(decision.current_phase_index)
        reclaim = self.reclaim_s_by_tls_phase.setdefault(decision.tls_id, {})
        reclaim[phase] = reclaim.get(phase, 0.0) + extension_s

    def reduce_reclaim(self, tls_id: str, phase_index: int, returned_s: float) -> None:
        """Abate reclaim quando verde estendido foi devolvido antes de consumido
        (check-out do priority event): o ciclo não chegou a deslocar-se por
        esse montante, logo não há nada a re-alinhar."""
        if returned_s <= 0.0:
            return
        reclaim = self.reclaim_s_by_tls_phase.get(tls_id)
        if not reclaim:
            return
        current = reclaim.get(int(phase_index), 0.0)
        if current <= 0.0:
            return
        reclaim[int(phase_index)] = max(0.0, current - float(returned_s))

    def step(
        self,
        signal_states: dict[str, SignalState],
        signal_control: SignalControlAdapter,
        sim_time_s: float,
        *,
        apply_actuation: bool,
        skip_tls: set | None = None,
    ) -> list[ActuationResult]:
        """Paga prestações de compensação nas transições de fase deste passo.

        ``skip_tls``: TLS com atuação TSP neste passo ficam de fora — o
        signal_state foi lido antes da atuação, e comandar com base nele
        reinstalaria o verde que o early green acabou de cortar. A transição
        é reavaliada no passo seguinte (a memória de fase actualiza na mesma).
        """
        if not self.enabled and not self.recovery_enabled:
            return []
        max_per_cycle_s = _float_or_default(
            self.tsp_config.actuation.get("compensation_max_per_cycle_s"), 8.0
        )
        max_total_green = _optional_float(
            self.cits_config.safety_constraints.get("max_total_green_s")
        )
        min_green = _optional_float(self.cits_config.safety_constraints.get("min_green_s"))
        results: list[ActuationResult] = []
        for tls_id, state in signal_states.items():
            current = state.current_phase_index
            previous = self._last_phase_by_tls.get(tls_id)
            if skip_tls and tls_id in skip_tls:
                # Não actualiza a memória de fase: se a entrada na fase lesada
                # coincidiu com a atuação TSP, a transição ainda conta como
                # pendente no próximo passo (senão perdia-se a prestação).
                # Excepção: sem memória prévia não há transição pendente a
                # preservar — semear com a fase actual, senão o próximo passo
                # trata a MESMA fase como entrada e paga/reclama na fase que a
                # atuação TSP acabou de truncar/estender, anulando-a.
                if tls_id not in self._last_phase_by_tls:
                    self._last_phase_by_tls[tls_id] = current
                continue
            self._last_phase_by_tls[tls_id] = current
            if current is None or current == previous:
                continue  # paga uma vez, na entrada da fase
            if state.next_switch_s is None or state.spent_duration_s is None:
                continue  # fail-closed: sem dados de fase verificáveis não há comando
            remaining_s = max(0.0, float(state.next_switch_s) - sim_time_s)
            spent_s = float(state.spent_duration_s)
            # Prioridade à compensação (alargar a fase truncada); o reclaim da
            # mesma fase, se existir, fica pendente para a ativação seguinte —
            # nunca se combinam dois comandos absolutos no mesmo passo.
            result = self._pay_compensation(
                tls_id,
                int(current),
                remaining_s,
                spent_s,
                max_per_cycle_s,
                max_total_green,
                sim_time_s,
                signal_control,
                apply_actuation,
            )
            if result is None:
                result = self._reclaim_extension(
                    tls_id,
                    int(current),
                    remaining_s,
                    spent_s,
                    max_per_cycle_s,
                    min_green,
                    sim_time_s,
                    signal_control,
                    apply_actuation,
                )
            if result is not None:
                results.append(result)
        return results

    def _pay_compensation(
        self,
        tls_id: str,
        phase: int,
        remaining_s: float,
        spent_s: float,
        max_per_cycle_s: float,
        max_total_green: float | None,
        sim_time_s: float,
        signal_control: SignalControlAdapter,
        apply_actuation: bool,
    ) -> ActuationResult | None:
        if not self.enabled or max_total_green is None:
            return None  # fail-closed: sem max_total_green não há bound de alargamento
        owed_by_phase = self.owed_s_by_tls_phase.get(tls_id)
        if not owed_by_phase:
            return None
        owed_s = owed_by_phase.get(phase, 0.0)
        if owed_s <= 0.0:
            return None
        headroom_s = float(max_total_green) - (spent_s + remaining_s)
        grant_s = min(owed_s, max_per_cycle_s, max(0.0, headroom_s))
        if grant_s < 1.0:
            return None  # prestação imaterial não justifica um comando TraCI
        if apply_actuation:
            signal_control.set_phase_duration(tls_id, remaining_s + grant_s)
        owed_by_phase[phase] = owed_s - grant_s
        self.granted_s_total += grant_s
        return ActuationResult(
            decision_id=f"compensation:{tls_id}:phase{phase}:{sim_time_s:.0f}",
            timestamp_s=sim_time_s,
            tls_id=tls_id,
            action="green_compensation",
            applied=apply_actuation,
            no_actuation=not apply_actuation,
            command="set_phase_duration",
            reason=ReasonCode.GREEN_COMPENSATION_PAYBACK.value,
            parameters={
                "phase_index": phase,
                "granted_s": round(grant_s, 3),
                "owed_remaining_s": round(owed_s - grant_s, 3),
                "new_phase_duration_s": round(remaining_s + grant_s, 3),
            },
        )

    def _reclaim_extension(
        self,
        tls_id: str,
        phase: int,
        remaining_s: float,
        spent_s: float,
        max_per_cycle_s: float,
        min_green: float | None,
        sim_time_s: float,
        signal_control: SignalControlAdapter,
        apply_actuation: bool,
    ) -> ActuationResult | None:
        if not self.recovery_enabled or min_green is None:
            return None  # fail-closed: sem min_green não há bound seguro de encurtamento
        reclaim_by_phase = self.reclaim_s_by_tls_phase.get(tls_id)
        if not reclaim_by_phase:
            return None
        reclaim_s = reclaim_by_phase.get(phase, 0.0)
        if reclaim_s <= 0.0:
            return None
        # Nunca encurtar abaixo do verde mínimo: spent + novo_remaining >= min_green.
        shrink_headroom_s = remaining_s - max(0.0, float(min_green) - spent_s)
        take_s = min(reclaim_s, max_per_cycle_s, max(0.0, shrink_headroom_s))
        if take_s < 1.0:
            return None
        if apply_actuation:
            signal_control.set_phase_duration(tls_id, remaining_s - take_s)
        reclaim_by_phase[phase] = reclaim_s - take_s
        self.reclaimed_s_total += take_s
        return ActuationResult(
            decision_id=f"coordination_recovery:{tls_id}:phase{phase}:{sim_time_s:.0f}",
            timestamp_s=sim_time_s,
            tls_id=tls_id,
            action="coordination_recovery",
            applied=apply_actuation,
            no_actuation=not apply_actuation,
            command="set_phase_duration",
            reason=ReasonCode.COORDINATION_RECOVERY_PAYBACK.value,
            parameters={
                "phase_index": phase,
                "reclaimed_s": round(take_s, 3),
                "reclaim_remaining_s": round(reclaim_s - take_s, 3),
                "new_phase_duration_s": round(remaining_s - take_s, 3),
            },
        )

    def summary(self) -> dict[str, object]:
        owed_remaining = {
            tls_id: {str(phase): round(value, 3) for phase, value in phases.items() if value > 0}
            for tls_id, phases in self.owed_s_by_tls_phase.items()
        }
        reclaim_remaining = {
            tls_id: {str(phase): round(value, 3) for phase, value in phases.items() if value > 0}
            for tls_id, phases in self.reclaim_s_by_tls_phase.items()
        }
        return {
            "enabled": self.enabled,
            "coordination_recovery_enabled": self.recovery_enabled,
            "granted_s_total": round(self.granted_s_total, 3),
            "reclaimed_s_total": round(self.reclaimed_s_total, 3),
            "owed_remaining_s_by_tls_phase": {
                tls_id: phases for tls_id, phases in owed_remaining.items() if phases
            },
            "reclaim_remaining_s_by_tls_phase": {
                tls_id: phases for tls_id, phases in reclaim_remaining.items() if phases
            },
        }
