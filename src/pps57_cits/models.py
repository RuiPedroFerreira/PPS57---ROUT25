#!/usr/bin/env python3
"""Modelos internos para observações SUMO e estado de interseções."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass(frozen=True)
class VehicleObservation:
    vehicle_id: str
    vehicle_class: str
    type_id: str
    line_id: str
    route_id: str
    edge_id: str
    lane_id: str
    lane_position_m: float
    lane_length_m: float
    speed_mps: float
    waiting_time_s: float = 0.0
    accumulated_waiting_time_s: float = 0.0
    schedule_delay_s: float = 0.0
    headway_deviation_s: float = 0.0

    @property
    def distance_to_stopline_m(self) -> float:
        return max(self.lane_length_m - self.lane_position_m, 0.0)

    @property
    def eta_to_stopline_s(self) -> float:
        speed = max(self.speed_mps, 0.5)
        return self.distance_to_stopline_m / speed

    @property
    def is_bus_like(self) -> bool:
        lower = " ".join([self.vehicle_id, self.vehicle_class, self.type_id, self.line_id]).lower()
        return "bus" in lower or "stcp" in lower


@dataclass(frozen=True)
class SignalState:
    intersection_id: str
    tls_id: str
    rsu_id: str
    timestamp_s: float
    current_phase_index: Optional[int]
    current_program_id: Optional[str]
    red_yellow_green_state: Optional[str]
    next_switch_s: Optional[float]
    spent_duration_s: Optional[float]
    controlled_lanes: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class QueueState:
    detector_id: str
    lane_id: str
    vehicle_count: int = 0
    jam_length_m: float = 0.0
    occupancy: float = 0.0


@dataclass(frozen=True)
class EmulationSummary:
    steps: int
    mapem_messages: int
    spatem_messages: int
    srem_messages: int
    ssem_messages: int
    acknowledged_requests: int
    rejected_requests: int
