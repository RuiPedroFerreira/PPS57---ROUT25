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
        active_count = 0
        max_active = int(self.config.rsu_policy.get("max_active_requests_per_rsu", 4))

        for message in messages:
            if message.message_type != MessageType.SREM_LIKE.value:
                continue
            request = message  # type: ignore[assignment]
            if not isinstance(request, SREMLike):
                continue
            active_count += 1
            responses.append(self.evaluate_request(request, sim_time_s, active_count > max_active))
        return responses

    def evaluate_request(self, request: SREMLike, sim_time_s: float, too_many_active: bool = False) -> SSEMLike:
        status = RequestStatus.ACKNOWLEDGED.value
        action = ResponseAction.FORWARD_TO_DECISION_ENGINE.value
        reason = "accepted_for_tsp_decision_engine"
        safety_notes = [
            "Pacote 3 only emulates C-ITS messages; signal actuation is reserved for Pacote 4.",
            "Safety constraints loaded but not yet applied to TraCI commands.",
        ]

        if request.expires_at_s and sim_time_s > request.expires_at_s:
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

        if status == RequestStatus.ACKNOWLEDGED.value:
            self.last_grant_time_by_vehicle[request.vehicle_id] = sim_time_s

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


def build_rsu_agents(config: CITSConfig) -> Dict[str, RSUAgent]:
    return {intersection.rsu_id: RSUAgent(config=config, intersection=intersection) for intersection in config.intersections}
