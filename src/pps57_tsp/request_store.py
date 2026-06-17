#!/usr/bin/env python3
"""Lifecycle tracking for active priority requests."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from pps57_cits.messages import SREMLike
from pps57_cits.models import VehicleObservation


@dataclass
class PriorityRequestState:
    request: SREMLike
    first_seen_s: float
    last_seen_s: float
    status: str = "detected"
    granted_at_s: float | None = None
    cleared_at_s: float | None = None


@dataclass
class PriorityRequestStore:
    ttl_s: float = 30.0
    states_by_key: dict[str, PriorityRequestState] = field(default_factory=dict)
    cleared_count: int = 0
    expired_count: int = 0
    granted_count: int = 0

    def ingest_requests(self, requests: Iterable[SREMLike], sim_time_s: float) -> None:
        for request in requests:
            key = self._key(request.vehicle_id, request.tls_id)
            existing = self.states_by_key.get(key)
            if existing is None or existing.status in {"cleared", "expired"}:
                self.states_by_key[key] = PriorityRequestState(
                    request=request,
                    first_seen_s=sim_time_s,
                    last_seen_s=sim_time_s,
                )
            else:
                existing.request = request
                existing.last_seen_s = sim_time_s
                if existing.status == "detected":
                    existing.status = "active"

    def mark_granted(self, request: SREMLike, sim_time_s: float) -> None:
        key = self._key(request.vehicle_id, request.tls_id)
        state = self.states_by_key.get(key)
        if state is None:
            state = PriorityRequestState(
                request=request, first_seen_s=sim_time_s, last_seen_s=sim_time_s
            )
            self.states_by_key[key] = state
        if state.status != "granted":
            self.granted_count += 1
        state.status = "granted"
        state.granted_at_s = sim_time_s
        state.last_seen_s = sim_time_s

    def mark_cancelled(self, request: SREMLike, sim_time_s: float) -> None:
        key = self._key(request.vehicle_id, request.tls_id)
        state = self.states_by_key.get(key)
        if state is None:
            state = PriorityRequestState(
                request=request, first_seen_s=sim_time_s, last_seen_s=sim_time_s
            )
            self.states_by_key[key] = state
        state.request = request
        state.status = "cleared"
        state.cleared_at_s = sim_time_s
        state.last_seen_s = sim_time_s
        self.cleared_count += 1

    def status_for(self, vehicle_id: str, tls_id: str) -> str | None:
        """Estado do lifecycle para (veículo, TLS), ou None se nunca visto.

        Usado pelo PriorityEventManager como sinal de check-out: "cleared"
        (passou a stopline / mudou de edge) ou "expired" terminam o evento."""
        state = self.states_by_key.get(self._key(vehicle_id, tls_id))
        return state.status if state is not None else None

    def get_by_request_id(self, request_id: str) -> SREMLike | None:
        for state in self.states_by_key.values():
            if state.status in {"cleared", "expired"}:
                continue
            if state.request.request_id == request_id:
                return state.request
        return None

    def update_from_observations(
        self, observations: Iterable[VehicleObservation], sim_time_s: float
    ) -> None:
        observed_by_vehicle = {item.vehicle_id: item for item in observations}
        for key, state in list(self.states_by_key.items()):
            if state.status in {"cleared", "expired"}:
                continue
            observation = observed_by_vehicle.get(state.request.vehicle_id)
            if observation is None:
                self._expire_if_needed(key, state, sim_time_s)
                continue
            state.last_seen_s = sim_time_s
            if observation.edge_id != state.request.current_edge_id:
                state.status = "cleared"
                state.cleared_at_s = sim_time_s
                self.cleared_count += 1
                continue
            if observation.distance_to_stopline_m <= 1.0:
                state.status = "cleared"
                state.cleared_at_s = sim_time_s
                self.cleared_count += 1

    def expire_old(self, sim_time_s: float) -> None:
        for key, state in list(self.states_by_key.items()):
            if state.status not in {"cleared", "expired"}:
                self._expire_if_needed(key, state, sim_time_s)

    def to_summary(self) -> dict[str, object]:
        active = [
            state
            for state in self.states_by_key.values()
            if state.status not in {"cleared", "expired"}
        ]
        by_status: dict[str, int] = {}
        for state in self.states_by_key.values():
            by_status[state.status] = by_status.get(state.status, 0) + 1
        return {
            "tracked_requests": len(self.states_by_key),
            "active_requests": len(active),
            "granted_requests": self.granted_count,
            "cleared_requests": self.cleared_count,
            "expired_requests": self.expired_count,
            "by_status": by_status,
        }

    def _expire_if_needed(self, key: str, state: PriorityRequestState, sim_time_s: float) -> None:
        expires_at = state.request.expires_at_s
        expired_by_message = expires_at is not None and sim_time_s > expires_at
        expired_by_ttl = sim_time_s - state.last_seen_s > self.ttl_s
        if expired_by_message or expired_by_ttl:
            state.status = "expired"
            self.expired_count += 1

    @staticmethod
    def _key(vehicle_id: str, tls_id: str) -> str:
        return f"{vehicle_id}:{tls_id}"
