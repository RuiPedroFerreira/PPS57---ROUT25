#!/usr/bin/env python3
"""Agente RSU para processamento de pedidos C-ITS SREM-like."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List

from .config import CITSConfig, IntersectionConfig
from .messages import (
    CITSMessage,
    MessageType,
    RequestStatus,
    ResponseAction,
    SSEMLike,
    SREMLike,
)


@dataclass
class RSUAgent:
    config: CITSConfig
    intersection: IntersectionConfig
    last_grant_time_by_vehicle: Dict[str, float] = field(default_factory=dict)

    @property
    def rsu_id(self) -> str:
        return self.intersection.rsu_id

    def handle_messages(self, messages: Iterable[CITSMessage], sim_time_s: float) -> List[SSEMLike]:
        responses: List[SSEMLike] = []
        max_active = int(self.config.rsu_policy.get("max_active_requests_per_rsu", 4))
        active_count = 0  # apenas pedidos elegíveis contam para o limite (não SREMs rejeitados/dup)
        seen_request_ids: set[str] = set()  # dedupe in-batch (mesma chamada handle_messages)

        for message in messages:
            if message.message_type != MessageType.SREM_LIKE.value:
                continue
            if not isinstance(message, SREMLike):
                # Mensagem com message_type=SREM_LIKE mas que não é SREMLike — provavelmente
                # foi reconstruída sem dataclass_from_dict. Rejeita explicitamente em vez
                # de descartar em silêncio (o silêncio escondia bugs de serialização).
                responses.append(
                    self._make_response_for_malformed(message, sim_time_s, "request_type_mismatch")
                )
                continue
            request = message

            if request.request_id and request.request_id in seen_request_ids:
                responses.append(self.evaluate_request(request, sim_time_s, replay_in_batch=True))
                continue
            seen_request_ids.add(request.request_id)

            response = self.evaluate_request(
                request, sim_time_s, too_many_active=active_count >= max_active
            )
            if response.status == RequestStatus.ACKNOWLEDGED.value:
                active_count += 1
            responses.append(response)
        return responses

    def evaluate_request(
        self,
        request: SREMLike,
        sim_time_s: float,
        too_many_active: bool = False,
        *,
        replay_in_batch: bool = False,
    ) -> SSEMLike:
        status = RequestStatus.ACKNOWLEDGED.value
        action = ResponseAction.FORWARD_TO_DECISION_ENGINE.value
        reason = "accepted_for_tsp_decision_engine"
        safety_notes = [
            "Pedido aceite pela RSU para avaliação pelo motor TSP.",
            "A atuação semafórica, quando existir, deve passar pela TSP Safety Layer.",
        ]

        # Verificações de identidade ANTES das de elegibilidade: um pedido com
        # `rsu_id`/`source_id`/`vehicle_id` inconsistentes nunca deve consumir
        # o quota de pedidos ativos nem ser tratado como replay legítimo.
        identity_problem = self._validate_identity(request)
        if replay_in_batch:
            status, action, reason = self._reject("duplicate_request_id_in_batch")
        elif identity_problem is not None:
            status, action, reason = self._reject(identity_problem)
        elif request.expires_at_s is not None and sim_time_s > request.expires_at_s:
            status, action, reason = self._reject("request_expired")
        elif request.intersection_id != self.intersection.intersection_id:
            status, action, reason = self._reject("request_not_for_this_intersection")
        elif too_many_active:
            status, action, reason = self._reject("rsu_active_request_limit_exceeded")
        elif self._cooldown_active(request.vehicle_id, sim_time_s):
            status, action, reason = self._reject("cooldown_active_for_vehicle")
        elif not self._eta_in_window(request):
            status, action, reason = self._reject("request_eta_out_of_window")
        elif not self._priority_condition_met(request):
            status, action, reason = self._reject("not_eligible_for_priority")

        return SSEMLike(
            source_id=self.rsu_id,
            destination_id=request.source_id,
            timestamp_s=sim_time_s,
            request_id=request.request_id,
            vehicle_id=request.vehicle_id,
            intersection_id=self.intersection.intersection_id,
            tls_id=self.intersection.tls_id,
            rsu_id=self.rsu_id,
            status=status,
            action=action,
            reason=reason,
            valid_until_s=sim_time_s + float(self.config.rsu_policy.get("response_ttl_s", 15)),
            confidence=0.95 if status == RequestStatus.ACKNOWLEDGED.value else 1.0,
            safety_notes=safety_notes,
            correlation_id=request.message_id,
        )

    def _reject(self, reason: str) -> tuple[str, str, str]:
        return RequestStatus.REJECTED.value, ResponseAction.REJECT_WITH_REASON.value, reason

    def _validate_identity(self, request: SREMLike) -> str | None:
        """Verifica que o pedido é endereçado a esta RSU e que a identidade
        do emissor é consistente. Devolve um motivo de rejeição ou None.
        """
        if request.rsu_id and request.rsu_id != self.intersection.rsu_id:
            return "request_rsu_id_mismatch"
        if request.vehicle_id and request.source_id != f"OBU_{request.vehicle_id}":
            # Convenção OBU emite SREMs com source_id=f"OBU_{vehicle_id}"; mismatch
            # indica spoofing/forge ou bug de produtor.
            return "source_id_does_not_match_vehicle"
        if not request.vehicle_id:
            return "vehicle_id_missing"
        return None

    def _make_response_for_malformed(
        self, message: CITSMessage, sim_time_s: float, reason: str
    ) -> SSEMLike:
        """Constrói SSEM de rejeição para mensagens marcadas como SREM mas que
        não são SREMLike (ex.: dict reconstruído incorretamente)."""
        return SSEMLike(
            source_id=self.rsu_id,
            destination_id=getattr(message, "source_id", "UNKNOWN"),
            timestamp_s=sim_time_s,
            request_id=getattr(message, "request_id", "") or "",
            vehicle_id=getattr(message, "vehicle_id", "") or "",
            intersection_id=self.intersection.intersection_id,
            tls_id=self.intersection.tls_id,
            rsu_id=self.rsu_id,
            status=RequestStatus.REJECTED.value,
            action=ResponseAction.REJECT_WITH_REASON.value,
            reason=reason,
            valid_until_s=sim_time_s + float(self.config.rsu_policy.get("response_ttl_s", 15)),
            confidence=1.0,
            safety_notes=["Mensagem SREM-marcada mas tipo inconsistente; rejeitada."],
            correlation_id=getattr(message, "message_id", None),
        )

    def _eta_in_window(self, request: SREMLike) -> bool:
        policy = self.config.obu_policy
        return (
            float(policy.get("request_eta_min_s", 8))
            <= request.eta_to_stopline_s
            <= float(policy.get("request_eta_max_s", 45))
        )

    def _priority_condition_met(self, request: SREMLike) -> bool:
        policy = self.config.obu_policy
        return (
            request.schedule_delay_s >= float(policy.get("delay_threshold_s", 60))
            or abs(request.headway_deviation_s) >= float(policy.get("headway_deviation_threshold_s", 120))
        )

    def _cooldown_active(self, vehicle_id: str, sim_time_s: float) -> bool:
        last = self.last_grant_time_by_vehicle.get(vehicle_id)
        if last is None:
            return False
        cooldown_s = float(self.config.rsu_policy.get("cooldown_after_grant_s", 90))
        return sim_time_s - last < cooldown_s

    def mark_priority_granted(self, vehicle_id: str, sim_time_s: float) -> None:
        """Mark a real downstream priority grant, not merely RSU forwarding."""
        self.last_grant_time_by_vehicle[vehicle_id] = sim_time_s


def build_rsu_agents(config: CITSConfig) -> Dict[str, RSUAgent]:
    return {intersection.rsu_id: RSUAgent(config=config, intersection=intersection) for intersection in config.intersections}
