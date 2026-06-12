#!/usr/bin/env python3
"""Modelos internos para observações SUMO e estado de interseções."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class EtaParams:
    """Constantes físicas do cálculo de ETA à stopline (externalizadas em P2).

    Os defaults reproduzem exactamente os literais anteriores, pelo que
    eta_to_stopline_s é byte-idêntico quando state_estimation está ausente. O
    adaptador injeta valores vindos de config; construções diretas (testes)
    usam estes defaults.
    """

    free_flow_speed_mps: float = 8.0
    queue_penalty_s: float = 2.0
    waiting_cap_s: float = 15.0
    min_speed_mps: float = 0.5


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
    # True quando schedule_delay_s/headway_deviation_s vêm de um
    # SchedulePlanProvider (stand-in AVL/APC). Permite à OBU distinguir
    # "provider disse 0.0 (a horas)" de "sem provider, default 0.0" e só então
    # recair no proxy de waiting-time. Ver pps57_cits.schedule_plan.
    schedule_adherence_sourced: bool = False
    route_edges: List[str] = field(default_factory=list)
    next_edge_id: str = ""
    queue_ahead_vehicle_count: int = 0
    stop_count: int = 0
    eta_params: EtaParams = field(default_factory=EtaParams)

    # Bit 4 do getStopState do SUMO = parado numa busStop (docs TraCI,
    # Vehicle Value Retrieval, "stop state"). O campo `stop_count` guarda o
    # bitmask bruto do getStopState (nome histórico).
    _STOP_STATE_AT_BUS_STOP = 16

    @property
    def is_at_bus_stop(self) -> bool:
        """True enquanto o veículo está parado a servir uma busStop."""
        return bool(int(self.stop_count) & self._STOP_STATE_AT_BUS_STOP)

    @property
    def distance_to_stopline_m(self) -> float:
        return max(self.lane_length_m - self.lane_position_m, 0.0)

    @property
    def eta_to_stopline_s(self) -> float:
        params = self.eta_params
        distance = self.distance_to_stopline_m
        current_speed_eta = distance / max(self.speed_mps, params.min_speed_mps)
        free_flow_eta = distance / params.free_flow_speed_mps
        queue_penalty_s = self.queue_ahead_vehicle_count * params.queue_penalty_s
        if self.speed_mps < params.min_speed_mps:
            return free_flow_eta + queue_penalty_s + min(self.waiting_time_s, params.waiting_cap_s)
        return min(current_speed_eta, free_flow_eta + queue_penalty_s)

    @property
    def eta_queue_delay_included_s(self) -> float:
        """Componente de fila EFETIVAMENTE incluída em eta_to_stopline_s.

        Branch-aware: no ramo em movimento o min() pode escolher o ETA por
        velocidade actual, caso em que a penalização de fila não entrou — só
        conta a diferença que a penalização realmente acrescentou. Vai no SREM
        (operator_telemetry.eta_queue_delay_s) para o engine deduzir antes de
        somar a correção queue-aware por detetor (senão a mesma fila parada
        contava duas vezes e required_green_s inflava)."""
        params = self.eta_params
        distance = self.distance_to_stopline_m
        current_speed_eta = distance / max(self.speed_mps, params.min_speed_mps)
        free_flow_eta = distance / params.free_flow_speed_mps
        queue_penalty_s = self.queue_ahead_vehicle_count * params.queue_penalty_s
        if self.speed_mps < params.min_speed_mps:
            return queue_penalty_s
        return min(current_speed_eta, free_flow_eta + queue_penalty_s) - min(
            current_speed_eta, free_flow_eta
        )

    @property
    def is_bus_like(self) -> bool:
        lower = " ".join([self.vehicle_id, self.vehicle_class, self.type_id, self.line_id]).lower()
        return "bus" in lower or "stcp" in lower

    @property
    def is_emergency_like(self) -> bool:
        lower = " ".join([self.vehicle_id, self.vehicle_class, self.type_id, self.line_id]).lower()
        return "emergency" in lower or lower.startswith("ev_")


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
    controlled_links: List[Any] = field(default_factory=list)


@dataclass(frozen=True)
class NetworkStateSnapshot:
    tls_id: str
    timestamp_s: float
    active_request_count: int = 0
    lane_count: int = 0
    vehicle_count: int = 0
    queue_vehicle_count: int = 0
    halted_vehicle_count: int = 0
    mean_speed_mps: float = 0.0
    waiting_time_s: float = 0.0
    occupancy: float = 0.0
    spillback_risk: bool = False
    degraded: bool = False
    detector_read_failures: int = 0
    failed_lanes: List[str] = field(default_factory=list)
    # Halted por lane controlada (estado bruto do passo, não agregado): permite
    # ao motor de decisão separar pressão na aproximação prioritária da pressão
    # transversal sem inventar sinais novos — é a mesma leitura que alimenta
    # halted_vehicle_count, apenas sem a soma.
    halted_by_lane: Dict[str, int] = field(default_factory=dict)
