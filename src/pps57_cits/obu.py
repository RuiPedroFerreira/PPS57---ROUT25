#!/usr/bin/env python3
"""Emulador OBU para autocarros no cenário SUMO."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .config import CITSConfig, PriorityMovementConfig
from .messages import PriorityLevel, RequestedManeuver, SREMLike
from .models import VehicleObservation


@dataclass
class OBUEmulator:
    config: CITSConfig
    last_request_time_by_key: Dict[str, float] = field(default_factory=dict)

    def generate_requests(self, observations: Iterable[VehicleObservation], sim_time_s: float) -> List[SREMLike]:
        requests: List[SREMLike] = []
        for observation in observations:
            request = self.generate_request(observation, sim_time_s)
            if request is not None:
                requests.append(request)
        return requests

    def generate_request(self, observation: VehicleObservation, sim_time_s: float) -> Optional[SREMLike]:
        if not self._is_priority_vehicle(observation):
            return None

        intersection = self.config.edge_to_intersection.get(observation.edge_id)
        if intersection is None:
            return None
        priority_movement = self.config.priority_movement_for_request(
            edge_id=observation.edge_id,
            vehicle_class=observation.vehicle_class or observation.type_id or "bus",
        )
        if priority_movement is None:
            return None

        policy = self.config.obu_policy
        distance = observation.distance_to_stopline_m
        eta = observation.eta_to_stopline_s

        if distance > float(policy.get("request_distance_m", 250)):
            return None
        is_emergency = observation.is_emergency_like
        eta_min = 0.0 if is_emergency else float(policy.get("request_eta_min_s", 8))
        eta_max = float(policy.get("emergency_request_eta_max_s", policy.get("request_eta_max_s", 45))) if is_emergency else float(policy.get("request_eta_max_s", 45))
        if eta < eta_min or eta > eta_max:
            return None

        schedule_delay_s = self._effective_schedule_delay(observation)
        headway_deviation_s = observation.headway_deviation_s

        if not is_emergency and not self._priority_condition_met(schedule_delay_s, headway_deviation_s):
            return None

        key = f"{observation.vehicle_id}:{intersection.intersection_id}"
        refresh_s = float(policy.get("request_refresh_s", 5))
        last_sent = self.last_request_time_by_key.get(key)
        if last_sent is not None and sim_time_s - last_sent < refresh_s:
            return None

        self.last_request_time_by_key[key] = sim_time_s
        priority_level = self._priority_level(observation, schedule_delay_s, headway_deviation_s)
        requested_maneuver = self._select_requested_maneuver(observation, priority_movement)

        return SREMLike(
            source_id=f"OBU_{observation.vehicle_id}",
            destination_id=intersection.rsu_id,
            timestamp_s=sim_time_s,
            vehicle_id=observation.vehicle_id,
            vehicle_class=observation.vehicle_class or observation.type_id or "bus",
            line_id=observation.line_id,
            route_id=observation.route_id,
            intersection_id=intersection.intersection_id,
            tls_id=intersection.tls_id,
            rsu_id=intersection.rsu_id,
            current_edge_id=observation.edge_id,
            current_lane_id=observation.lane_id,
            next_edge_id=observation.next_edge_id,
            priority_movement_id=priority_movement.movement_id,
            target_signal_group_id=priority_movement.target_signal_group_id,
            speed_mps=round(observation.speed_mps, 3),
            distance_to_stopline_m=round(distance, 3),
            eta_to_stopline_s=round(eta, 3),
            schedule_delay_s=round(schedule_delay_s, 3),
            headway_deviation_s=round(headway_deviation_s, 3),
            requested_maneuver=requested_maneuver,
            priority_level=priority_level,
            expires_at_s=sim_time_s + float(policy.get("request_ttl_s", 12)),
        )

    def _is_priority_vehicle(self, observation: VehicleObservation) -> bool:
        policy = self.config.obu_policy
        bus_prefixes = tuple(policy.get("bus_id_prefixes", ["bus_"]))
        emergency_prefixes = tuple(policy.get("emergency_id_prefixes", ["ev_", "emergency_"]))
        priority_line_ids = set(policy.get("priority_line_ids", []))

        if observation.vehicle_id.startswith(emergency_prefixes):
            return True
        if observation.is_emergency_like:
            return True
        if observation.vehicle_id.startswith(bus_prefixes):
            return True
        if observation.line_id in priority_line_ids:
            return True
        return observation.is_bus_like

    def _effective_schedule_delay(self, observation: VehicleObservation) -> float:
        return max(observation.schedule_delay_s, observation.waiting_time_s, observation.accumulated_waiting_time_s)

    def _priority_condition_met(self, schedule_delay_s: float, headway_deviation_s: float) -> bool:
        policy = self.config.obu_policy
        if bool(policy.get("allow_nominal_priority_requests", False)):
            return True
        return (
            schedule_delay_s >= float(policy.get("delay_threshold_s", 60))
            or abs(headway_deviation_s) >= float(policy.get("headway_deviation_threshold_s", 120))
        )

    def _priority_level(self, observation: VehicleObservation, schedule_delay_s: float, headway_deviation_s: float) -> str:
        policy = self.config.obu_policy
        if observation.is_emergency_like:
            return PriorityLevel.EMERGENCY_VEHICLE.value
        if schedule_delay_s >= float(policy.get("delay_threshold_s", 60)):
            return PriorityLevel.PUBLIC_TRANSPORT_HIGH_DELAY.value
        if abs(headway_deviation_s) >= float(policy.get("headway_deviation_threshold_s", 120)):
            return PriorityLevel.PUBLIC_TRANSPORT_HEADWAY_RECOVERY.value
        return PriorityLevel.PUBLIC_TRANSPORT_NOMINAL.value

    def _select_requested_maneuver(self, observation: VehicleObservation, movement: PriorityMovementConfig) -> str:
        allowed = set(movement.allowed_actions)
        if RequestedManeuver.GREEN_EXTENSION.value in allowed and observation.eta_to_stopline_s <= 20:
            return RequestedManeuver.GREEN_EXTENSION.value
        if RequestedManeuver.EARLY_GREEN.value in allowed:
            return RequestedManeuver.EARLY_GREEN.value
        return RequestedManeuver.PRIORITY_CANDIDATE.value
