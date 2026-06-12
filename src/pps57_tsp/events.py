#!/usr/bin/env python3
"""Lifecycle check-in/check-out de eventos de prioridade (v2.2, opt-in).

O TSP clássico em produção não decide uma vez sobre um ETA pontual: abre um
*evento de prioridade* no check-in, estende o verde em prestações enquanto o
autocarro se aproxima, e termina a extensão no check-out (autocarro passa a
stopline), devolvendo o verde não usado à transversal. Aqui:

- O *check-in* é implícito: o primeiro green extension aplicado para um
  (tls, veículo) abre o evento e fixa o fim ORIGINAL da fase (pré-extensão).
- As *prestações* usam o loop de decisão normal: o OBU refresca o SREM
  (``request_refresh_s``), o engine propõe nova extensão limitada a
  ``green_extension_rolling_increment_s``, e a Safety valida cada prestação
  contra o estado vivo — continuações do mesmo evento não pagam cooldown nem
  contam como novas intervenções, mas o orçamento CUMULATIVO do evento nunca
  excede ``max_green_extension_s`` (e ``max_total_green_s`` continua a valer
  por prestação).
- O *check-out* vem do PriorityRequestStore ("cleared"/"expired"): o evento
  termina repondo o fim original da fase — NUNCA antes dele, pelo que a
  terminação não pode violar nada que o plano base já não satisfizesse — e o
  verde devolvido abate o recovery debt da Safety e o reclaim da recuperação
  de coordenação (esse verde nunca chegou a deslocar o ciclo).

Opt-in por config: ``actuation.priority_event_lifecycle_enabled``; com o flag
ausente o comportamento é byte-idêntico (sem eventos, decisão one-shot v2.1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, TYPE_CHECKING

from pps57_cits.config import CITSConfig
from pps57_cits.models import SignalState

from .config import TSPConfig
from .models import ActuationResult, ReasonCode, TSPAction, TSPDecision
from .request_store import PriorityRequestStore
from .signal_control import SignalControlAdapter

if TYPE_CHECKING:  # evita ciclo de imports em runtime (safety importa events)
    from .compensation import GreenCompensationManager
    from .safety import TSPSafetyLayer


@dataclass
class ActivePriorityEvent:
    tls_id: str
    vehicle_id: str
    phase_index: int
    opened_at_s: float
    # Fim da fase SEM intervenção (next_switch no momento do primeiro grant):
    # é o piso da terminação — o check-out repõe este fim, nunca antecipa.
    original_end_s: float
    granted_total_s: float = 0.0
    last_decision_id: str = ""


@dataclass
class PriorityEventManager:
    cits_config: CITSConfig
    tsp_config: TSPConfig
    events_by_key: Dict[str, ActivePriorityEvent] = field(default_factory=dict)
    opened_count: int = 0
    closed_natural_count: int = 0
    checkout_termination_count: int = 0
    returned_s_total: float = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.tsp_config.actuation.get("priority_event_lifecycle_enabled", False))

    def active_event(
        self, tls_id: str, vehicle_id: str, phase_index: Optional[int]
    ) -> Optional[ActivePriorityEvent]:
        """Evento ativo para (tls, veículo) na MESMA fase, ou None.

        A fase tem de coincidir: se a fase avançou, o evento antigo já não
        autoriza continuações (será fechado no próximo step())."""
        if not self.enabled or phase_index is None:
            return None
        event = self.events_by_key.get(self._key(tls_id, vehicle_id))
        if event is not None and event.phase_index == int(phase_index):
            return event
        return None

    def register_applied(self, decision: TSPDecision) -> None:
        """Abre o evento no primeiro grant; acumula prestações nos seguintes.

        Chamado DEPOIS de safety.mark_applied (que consulta active_event para
        distinguir abertura de continuação) — a ordem no controller importa."""
        if not self.enabled or decision.action != TSPAction.GREEN_EXTENSION.value:
            return
        if decision.current_phase_index is None:
            return
        key = self._key(decision.tls_id, decision.vehicle_id)
        event = self.events_by_key.get(key)
        if event is None or event.phase_index != int(decision.current_phase_index):
            if decision.current_next_switch_s is None:
                # Sem next_switch não há fim original mensurável -> sem evento
                # (fail-closed: a extensão fica one-shot, sem check-out).
                return
            event = ActivePriorityEvent(
                tls_id=decision.tls_id,
                vehicle_id=decision.vehicle_id,
                phase_index=int(decision.current_phase_index),
                opened_at_s=float(decision.timestamp_s),
                original_end_s=float(decision.current_next_switch_s),
            )
            self.events_by_key[key] = event
            self.opened_count += 1
        event.granted_total_s += max(0.0, float(decision.extension_s or 0.0))
        event.last_decision_id = decision.decision_id

    def step(
        self,
        signal_states: Dict[str, SignalState],
        request_store: PriorityRequestStore,
        signal_control: SignalControlAdapter,
        safety: "TSPSafetyLayer",
        compensation: "GreenCompensationManager",
        sim_time_s: float,
        *,
        apply_actuation: bool,
        skip_tls: Optional[set] = None,
    ) -> List[ActuationResult]:
        """Fecha eventos: check-out devolve verde não usado; fase avançada fecha.

        ``skip_tls``: TLS comandados neste passo (TSP ou compensação) ficam de
        fora — o signal_state foi lido antes desses comandos e o fim de fase
        que ele reporta já não é verdadeiro. O check-out reavalia-se no passo
        seguinte (o status "cleared" do request store persiste).
        """
        if not self.enabled:
            return []
        results: List[ActuationResult] = []
        for key, event in list(self.events_by_key.items()):
            state = signal_states.get(event.tls_id)
            if state is None:
                continue
            if state.current_phase_index != event.phase_index:
                # Fecho natural: a fase terminou, a extensão foi consumida.
                del self.events_by_key[key]
                self.closed_natural_count += 1
                continue
            status = request_store.status_for(event.vehicle_id, event.tls_id)
            if status not in {"cleared", "expired"}:
                continue
            if skip_tls and event.tls_id in skip_tls:
                continue  # estado de fase stale neste passo; termina no seguinte
            if state.next_switch_s is None:
                # Leitura degradada: sem fim de fase vivo a devolução não é
                # mensurável; tenta no próximo passo (o fecho natural cobre o
                # caso em que a fase entretanto termina).
                continue
            result = self._terminate_at_checkout(
                event, state, signal_control, safety, compensation, sim_time_s,
                apply_actuation=apply_actuation,
            )
            del self.events_by_key[key]
            self.checkout_termination_count += 1
            if result is not None:
                results.append(result)
        return results

    def _terminate_at_checkout(
        self,
        event: ActivePriorityEvent,
        state: SignalState,
        signal_control: SignalControlAdapter,
        safety: "TSPSafetyLayer",
        compensation: "GreenCompensationManager",
        sim_time_s: float,
        *,
        apply_actuation: bool,
    ) -> Optional[ActuationResult]:
        current_remaining_s = max(0.0, float(state.next_switch_s) - sim_time_s)
        # Invariante de segurança: repõe o fim ORIGINAL da fase, nunca antes —
        # a fase nunca fica mais curta do que o plano base já a fazia.
        restored_remaining_s = max(0.0, event.original_end_s - sim_time_s)
        returned_s = current_remaining_s - restored_remaining_s
        if returned_s < 1.0:
            return None  # devolução imaterial não justifica um comando TraCI
        if apply_actuation:
            signal_control.set_phase_duration(event.tls_id, restored_remaining_s)
        # O verde devolvido nunca foi um custo: abate o recovery debt e o
        # reclaim de coordenação que o grant tinha registado.
        safety.return_recovery_debt(event.tls_id, returned_s)
        compensation.reduce_reclaim(event.tls_id, event.phase_index, returned_s)
        self.returned_s_total += returned_s
        return ActuationResult(
            decision_id=f"checkout:{event.tls_id}:{event.vehicle_id}:{sim_time_s:.0f}",
            timestamp_s=sim_time_s,
            tls_id=event.tls_id,
            action="extension_checkout_return",
            applied=apply_actuation,
            no_actuation=not apply_actuation,
            command="set_phase_duration",
            reason=ReasonCode.EXTENSION_RETURNED_AT_CHECKOUT.value,
            parameters={
                "vehicle_id": event.vehicle_id,
                "phase_index": event.phase_index,
                "returned_s": round(returned_s, 3),
                "granted_total_s": round(event.granted_total_s, 3),
                "new_phase_duration_s": round(restored_remaining_s, 3),
            },
        )

    def summary(self) -> Dict[str, object]:
        return {
            "enabled": self.enabled,
            "opened": self.opened_count,
            "closed_natural": self.closed_natural_count,
            "checkout_terminations": self.checkout_termination_count,
            "returned_s_total": round(self.returned_s_total, 3),
            "active": len(self.events_by_key),
        }

    @staticmethod
    def _key(tls_id: str, vehicle_id: str) -> str:
        return f"{tls_id}:{vehicle_id}"
