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
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from pps57_cits.config import CITSConfig
from pps57_cits.models import SignalState

from .config import TSPConfig
from .models import ActuationResult, ReasonCode, TSPAction, TSPDecision
from .signal_control import SignalControlAdapter


@dataclass
class GreenCompensationManager:
    cits_config: CITSConfig
    tsp_config: TSPConfig
    owed_s_by_tls_phase: Dict[str, Dict[int, float]] = field(default_factory=dict)
    granted_s_total: float = 0.0
    _last_phase_by_tls: Dict[str, Optional[int]] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return bool(self.tsp_config.actuation.get("compensation_enabled", False))

    def register_applied(self, decision: TSPDecision) -> None:
        """Acumula a dívida de um early green aplicado (verde removido)."""
        if not self.enabled or decision.action != TSPAction.EARLY_GREEN.value:
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

    def step(
        self,
        signal_states: Dict[str, SignalState],
        signal_control: SignalControlAdapter,
        sim_time_s: float,
        *,
        apply_actuation: bool,
        skip_tls: Optional[set] = None,
    ) -> List[ActuationResult]:
        """Paga prestações de compensação nas transições de fase deste passo.

        ``skip_tls``: TLS com atuação TSP neste passo ficam de fora — o
        signal_state foi lido antes da atuação, e comandar com base nele
        reinstalaria o verde que o early green acabou de cortar. A transição
        é reavaliada no passo seguinte (a memória de fase actualiza na mesma).
        """
        if not self.enabled:
            return []
        max_per_cycle_s = float(
            self.tsp_config.actuation.get("compensation_max_per_cycle_s", 8.0)
        )
        max_total_green = self.cits_config.safety_constraints.get("max_total_green_s")
        results: List[ActuationResult] = []
        for tls_id, state in signal_states.items():
            current = state.current_phase_index
            previous = self._last_phase_by_tls.get(tls_id)
            if skip_tls and tls_id in skip_tls:
                # Não actualiza a memória de fase: se a entrada na fase lesada
                # coincidiu com a atuação TSP, a transição ainda conta como
                # pendente no próximo passo (senão perdia-se a prestação).
                continue
            self._last_phase_by_tls[tls_id] = current
            if current is None or current == previous:
                continue  # paga uma vez, na entrada da fase
            owed_by_phase = self.owed_s_by_tls_phase.get(tls_id)
            if not owed_by_phase:
                continue
            owed_s = owed_by_phase.get(int(current), 0.0)
            if owed_s <= 0.0:
                continue
            if (
                state.next_switch_s is None
                or state.spent_duration_s is None
                or max_total_green is None
            ):
                continue  # fail-closed: sem bounds verificáveis não há comando
            remaining_s = max(0.0, float(state.next_switch_s) - sim_time_s)
            headroom_s = float(max_total_green) - (float(state.spent_duration_s) + remaining_s)
            grant_s = min(owed_s, max_per_cycle_s, max(0.0, headroom_s))
            if grant_s < 1.0:
                continue  # prestação imaterial não justifica um comando TraCI
            if apply_actuation:
                signal_control.set_phase_duration(tls_id, remaining_s + grant_s)
            owed_by_phase[int(current)] = owed_s - grant_s
            self.granted_s_total += grant_s
            results.append(
                ActuationResult(
                    decision_id=f"compensation:{tls_id}:phase{int(current)}:{sim_time_s:.0f}",
                    timestamp_s=sim_time_s,
                    tls_id=tls_id,
                    action="green_compensation",
                    applied=apply_actuation,
                    no_actuation=not apply_actuation,
                    command="set_phase_duration",
                    reason=ReasonCode.GREEN_COMPENSATION_PAYBACK.value,
                    parameters={
                        "phase_index": int(current),
                        "granted_s": round(grant_s, 3),
                        "owed_remaining_s": round(owed_s - grant_s, 3),
                        "new_phase_duration_s": round(remaining_s + grant_s, 3),
                    },
                )
            )
        return results

    def summary(self) -> Dict[str, object]:
        owed_remaining = {
            tls_id: {str(phase): round(value, 3) for phase, value in phases.items() if value > 0}
            for tls_id, phases in self.owed_s_by_tls_phase.items()
        }
        return {
            "enabled": self.enabled,
            "granted_s_total": round(self.granted_s_total, 3),
            "owed_remaining_s_by_tls_phase": {
                tls_id: phases for tls_id, phases in owed_remaining.items() if phases
            },
        }
