#!/usr/bin/env python3
"""Emulador OBU (On-Board Unit) para autocarros no cenário SUMO.

Responsabilidade (alinhada com ETSI TS 103 301-3): a OBU declara **identidade**
(`requestor`) e **intenção** (`requests[]` com `requestType`) num SREM. A OBU
**não** propõe a estratégia (greenExtension/earlyGreen); essa decisão é da
infraestrutura (RSU/TSP). A OBU é também responsável por emitir explicitamente
o `priorityCancellation` quando o pedido em curso deixa de ser relevante
(veículo passou stopline, saiu do corredor, mudou de interseção).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from .config import CITSConfig, IntersectionConfig, PriorityMovementConfig
from .messages import (
    BasicVehicleRole,
    MessageType,
    OperatorPriorityClass,
    OperatorTelemetry,
    Position3D,
    Requestor,
    RequestType,
    SREMLike,
    SignalRequest,
    StationType,
    build_security_envelope,
    derive_station_id,
    parse_intersection_ref_id,
    sim_time_to_cdd,
)
from .models import VehicleObservation


@dataclass
class _RequestorState:
    """Estado por veículo (sequence_number monotónico, request_id em curso).

    `sequence_number` é monotónico por requestor (a norma usa-o para ordenar
    pedidos do mesmo veículo). `current_request_id` é um uint8 que identifica
    o pedido em curso para uma interseção específica; mantém-se em UPDATEs e
    fecha-se com CANCELLATION ou ao mudar de interseção.
    """

    vehicle_id: str
    sequence_number: int = 0
    current_request_id: Optional[int] = None
    current_intersection_alias: Optional[str] = None
    current_message_id: Optional[str] = None
    last_request_time_s: Optional[float] = None
    next_request_id: int = 1  # 0 reservado para "sem pedido em curso"


@dataclass
class OBUEmulator:
    config: CITSConfig
    state_by_vehicle: Dict[str, _RequestorState] = field(default_factory=dict)

    def generate_requests(
        self, observations: Iterable[VehicleObservation], sim_time_s: float
    ) -> List[SREMLike]:
        """Emite SREMs para o tick atual.

        Para cada veículo prioritário:
        - emite `priorityRequest` no primeiro envio para esta interseção,
        - emite `priorityRequestUpdate` no refresh subsequente,
        - emite `priorityCancellation` se o pedido em curso já não se aplica.
        """
        observations_list = list(observations)
        observations_by_vehicle = {obs.vehicle_id: obs for obs in observations_list}

        requests: List[SREMLike] = []

        # 1. Cancelar pedidos em curso para veículos que saíram do contexto.
        for vehicle_id, state in list(self.state_by_vehicle.items()):
            if state.current_request_id is None:
                continue
            observation = observations_by_vehicle.get(vehicle_id)
            should_cancel, reason = self._should_cancel(observation, state)
            if not should_cancel:
                continue
            cancellation = self._build_cancellation(state, sim_time_s, reason or "unknown")
            if cancellation is not None:
                requests.append(cancellation)
            state.current_request_id = None
            state.current_intersection_alias = None
            state.current_message_id = None
            state.last_request_time_s = sim_time_s

        # 2. Varre estado de veículos que partiram: sem isto o dict cresce
        # para sempre em corridas longas. A retenção pós-último-pedido cobre
        # o TTL do replay-cache da RSU, para que um regresso ao corredor não
        # reutilize sequence_numbers ainda em cache.
        self._prune_departed_vehicles(observations_by_vehicle, sim_time_s)

        # 3. Novos pedidos ou updates para os veículos elegíveis observados.
        for observation in observations_list:
            request = self._generate_request_or_update(observation, sim_time_s)
            if request is not None:
                requests.append(request)
        return requests

    def _prune_departed_vehicles(
        self,
        observations_by_vehicle: Dict[str, VehicleObservation],
        sim_time_s: float,
    ) -> None:
        retention_s = float(self.config.obu_policy.get("state_retention_s", 60.0))
        for vehicle_id, state in list(self.state_by_vehicle.items()):
            if vehicle_id in observations_by_vehicle:
                continue
            if state.current_request_id is not None:
                continue
            last_seen = state.last_request_time_s
            if last_seen is None or sim_time_s - last_seen >= retention_s:
                del self.state_by_vehicle[vehicle_id]

    def generate_request(
        self, observation: VehicleObservation, sim_time_s: float
    ) -> Optional[SREMLike]:
        """Entry-point por observação (testabilidade).

        Equivalente a uma iteração de `generate_requests` para esta observação,
        sem o passe de cancelamento para veículos desaparecidos. Em runtime,
        prefira `generate_requests(observations, sim_time_s)` que faz ambas as
        coisas e gere o ciclo de vida dos pedidos.
        """
        return self._generate_request_or_update(observation, sim_time_s)

    def _generate_request_or_update(
        self, observation: VehicleObservation, sim_time_s: float
    ) -> Optional[SREMLike]:
        if not self._is_priority_vehicle(observation):
            return None

        intersection = self.config.edge_to_intersection.get(observation.edge_id)
        if intersection is None:
            return None
        if not intersection.signal_controlled:
            return None
        priority_movement = self.config.priority_movement_for_request(
            edge_id=observation.edge_id,
            next_edge_id=observation.next_edge_id,
            vehicle_class=observation.vehicle_class or observation.type_id or "bus",
        )
        if priority_movement is None:
            return None

        policy = self.config.obu_policy
        distance = observation.distance_to_stopline_m
        eta = observation.eta_to_stopline_s

        # v2.1: supressão at-stop — enquanto o bus serve uma paragem (porta
        # aberta nos sistemas reais), o dwell restante é desconhecido e o ETA
        # do proxy é optimista; um pedido agora gasta verde sem benefício.
        # O pedido (re)nasce quando o bus retoma a marcha.
        if (
            not observation.is_emergency_like
            and bool(policy.get("suppress_requests_at_stop", True))
            and observation.is_at_bus_stop
        ):
            return None

        if distance > float(policy.get("request_distance_m", 250)):
            return None
        is_emergency = observation.is_emergency_like
        eta_min = 0.0 if is_emergency else float(policy.get("request_eta_min_s", 8))
        eta_max = (
            float(policy.get("emergency_request_eta_max_s", policy.get("request_eta_max_s", 45)))
            if is_emergency
            else float(policy.get("request_eta_max_s", 45))
        )
        if eta < eta_min or eta > eta_max:
            return None

        schedule_delay_s = self._effective_schedule_delay(observation)
        headway_deviation_s = observation.headway_deviation_s

        if not is_emergency and not self._priority_condition_met(schedule_delay_s, headway_deviation_s):
            return None

        state = self.state_by_vehicle.setdefault(
            observation.vehicle_id, _RequestorState(vehicle_id=observation.vehicle_id)
        )

        # Throttle: respeitar `request_refresh_s` entre updates ao mesmo
        # request_id na mesma interseção.
        refresh_s = float(policy.get("request_refresh_s", 5))
        same_intersection = state.current_intersection_alias == intersection.intersection_id
        if (
            same_intersection
            and state.last_request_time_s is not None
            and sim_time_s - state.last_request_time_s < refresh_s
        ):
            return None

        # Mudou de interseção: cancellation foi emitido no passo anterior via
        # _should_cancel; aqui assumimos o estado limpo e abrimos novo pedido.
        if not same_intersection:
            state.current_request_id = None
            state.current_intersection_alias = None
            state.current_message_id = None

        is_new_request = state.current_request_id is None
        if is_new_request:
            state.current_request_id = state.next_request_id
            state.next_request_id = (state.next_request_id % 255) + 1
            state.current_intersection_alias = intersection.intersection_id
            request_type = RequestType.PRIORITY_REQUEST.value
        else:
            request_type = RequestType.PRIORITY_REQUEST_UPDATE.value

        state.last_request_time_s = sim_time_s
        return self._build_message(
            observation=observation,
            state=state,
            intersection=intersection,
            sim_time_s=sim_time_s,
            request_type=request_type,
            priority_movement=priority_movement,
            schedule_delay_s=schedule_delay_s,
            headway_deviation_s=headway_deviation_s,
        )

    # ------------------------------------------------------------------
    # Construção de SREMs.
    # ------------------------------------------------------------------

    def _build_message(
        self,
        *,
        observation: VehicleObservation,
        state: _RequestorState,
        intersection: IntersectionConfig,
        sim_time_s: float,
        request_type: str,
        priority_movement: PriorityMovementConfig,
        schedule_delay_s: float,
        headway_deviation_s: float,
    ) -> SREMLike:
        policy = self.config.obu_policy
        ttl_s = float(policy.get("request_ttl_s", 12))
        state.sequence_number = (state.sequence_number + 1) % 65536

        operator_priority_class = self._operator_priority_class(
            observation, schedule_delay_s, headway_deviation_s
        )
        operator_telemetry = OperatorTelemetry(
            schedule_delay_s=round(schedule_delay_s, 3),
            headway_deviation_s=round(headway_deviation_s, 3),
            distance_to_stopline_m=round(observation.distance_to_stopline_m, 3),
            eta_to_stopline_s=round(observation.eta_to_stopline_s, 3),
            eta_queue_delay_s=round(observation.eta_queue_delay_included_s, 3),
            operator_priority_class=operator_priority_class,
            line_id=observation.line_id,
            route_id=observation.route_id,
            intersection_alias=intersection.intersection_id,
            tls_id=intersection.tls_id,
            rsu_id=intersection.rsu_id,
            priority_movement_id=priority_movement.movement_id,
            target_signal_group_id_hint=priority_movement.target_signal_group_id,
        )

        eta_arrival_s = sim_time_s + observation.eta_to_stopline_s
        eta_moy, eta_ms, _ = sim_time_to_cdd(eta_arrival_s)
        signal_request = SignalRequest(
            intersection_ref_id=parse_intersection_ref_id(intersection.intersection_id),
            request_id=state.current_request_id or 0,
            request_type=request_type,
            in_bound_lane_id=observation.lane_id,
            out_bound_lane_id=observation.next_edge_id,
            eta_min_minute=eta_moy,
            eta_min_second_ms=eta_ms,
            duration_ms=int(round(ttl_s * 1000)),
        )

        basic_role = self._basic_vehicle_role(observation)
        station_type = (
            StationType.SPECIAL_VEHICLE.value
            if basic_role == BasicVehicleRole.EMERGENCY.value
            else StationType.BUS.value
        )
        station_id = derive_station_id(observation.vehicle_id)
        signer_id = f"OBU_{observation.vehicle_id}"
        requestor = Requestor(
            station_id=station_id,
            station_type=station_type,
            basic_vehicle_role=basic_role,
            position=Position3D(),
            heading_deg=0.0,
            speed_mps=round(observation.speed_mps, 3),
            route_name=observation.line_id or None,
            operational_vehicle_id=observation.vehicle_id,
        )

        moy, timestamp_ms, generation_delta = sim_time_to_cdd(sim_time_s)
        security = build_security_envelope(signer_id, sim_time_s, validity_s=ttl_s)
        correlation_id = (
            state.current_message_id
            if request_type == RequestType.PRIORITY_REQUEST_UPDATE.value
            else None
        )
        message = SREMLike(
            message_type=MessageType.SREM.value,
            station_id=station_id,
            station_type=station_type,
            source_id=signer_id,
            destination_id=intersection.rsu_id,
            generation_delta_time_ms=generation_delta,
            moy=moy,
            timestamp_ms=timestamp_ms,
            security=security,
            sequence_number=state.sequence_number,
            requests=[signal_request],
            requestor=requestor,
            operator_telemetry=operator_telemetry,
            expires_at_s=sim_time_s + ttl_s,
            correlation_id=correlation_id,
        )
        state.current_message_id = message.message_id
        return message

    def _build_cancellation(
        self, state: _RequestorState, sim_time_s: float, reason: str
    ) -> Optional[SREMLike]:
        if state.current_intersection_alias is None or state.current_request_id is None:
            return None
        intersection = self.config.intersection_by_alias.get(state.current_intersection_alias)
        if intersection is None:
            return None

        state.sequence_number = (state.sequence_number + 1) % 65536
        signer_id = f"OBU_{state.vehicle_id}"
        station_id = derive_station_id(state.vehicle_id)
        moy, timestamp_ms, generation_delta = sim_time_to_cdd(sim_time_s)
        security = build_security_envelope(signer_id, sim_time_s, validity_s=5.0)

        signal_request = SignalRequest(
            intersection_ref_id=parse_intersection_ref_id(intersection.intersection_id),
            request_id=state.current_request_id,
            request_type=RequestType.PRIORITY_CANCELLATION.value,
            in_bound_lane_id="",
            out_bound_lane_id="",
            eta_min_minute=moy,
            eta_min_second_ms=timestamp_ms,
            duration_ms=0,
        )
        operator_telemetry = OperatorTelemetry(
            operator_priority_class=OperatorPriorityClass.NOMINAL.value,
            intersection_alias=intersection.intersection_id,
            tls_id=intersection.tls_id,
            rsu_id=intersection.rsu_id,
            line_id="",
            route_id="",
            priority_movement_id="",
            target_signal_group_id_hint="",
            cancellation_reason=reason,
        )
        requestor = Requestor(
            station_id=station_id,
            station_type=StationType.BUS.value,
            basic_vehicle_role=BasicVehicleRole.PUBLIC_TRANSPORT.value,
            position=Position3D(),
            heading_deg=0.0,
            speed_mps=0.0,
            route_name=None,
            operational_vehicle_id=state.vehicle_id,
        )
        return SREMLike(
            message_type=MessageType.SREM.value,
            station_id=station_id,
            station_type=StationType.BUS.value,
            source_id=signer_id,
            destination_id=intersection.rsu_id,
            generation_delta_time_ms=generation_delta,
            moy=moy,
            timestamp_ms=timestamp_ms,
            security=security,
            sequence_number=state.sequence_number,
            requests=[signal_request],
            requestor=requestor,
            operator_telemetry=operator_telemetry,
            expires_at_s=None,
            correlation_id=state.current_message_id,
        )

    # ------------------------------------------------------------------
    # Critérios de elegibilidade e cancelamento.
    # ------------------------------------------------------------------

    def _should_cancel(
        self,
        observation: Optional[VehicleObservation],
        state: _RequestorState,
    ) -> Tuple[bool, Optional[str]]:
        if state.current_request_id is None or state.current_intersection_alias is None:
            return False, None
        if observation is None:
            return True, "vehicle_left_observation_window"
        intersection = self.config.edge_to_intersection.get(observation.edge_id)
        if intersection is None:
            return True, "vehicle_off_corridor"
        if intersection.intersection_id != state.current_intersection_alias:
            return True, "vehicle_changed_intersection_context"
        # v2.1: bus encostou numa paragem com pedido activo -> cancela (espelho
        # do inibidor de porta-aberta; o pedido renasce ao retomar a marcha).
        if (
            bool(self.config.obu_policy.get("suppress_requests_at_stop", True))
            and not observation.is_emergency_like
            and observation.is_at_bus_stop
        ):
            return True, "vehicle_dwelling_at_stop"
        return False, None

    def _is_priority_vehicle(self, observation: VehicleObservation) -> bool:
        policy = self.config.obu_policy
        raw_bus_prefixes = policy.get("bus_id_prefixes", ["bus_"])
        if isinstance(raw_bus_prefixes, str):
            # String solta na config: tuple() iteraria os caracteres e quase
            # todos os IDs passariam a contar como autocarro.
            raw_bus_prefixes = (raw_bus_prefixes,)
        bus_prefixes = tuple(raw_bus_prefixes)
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
        # Com um SchedulePlanProvider ligado (stand-in AVL/APC), o atraso vem
        # do horário e é a fonte autoritativa — inclusive 0.0 (a horas). Sem
        # provider, recai no proxy de waiting-time do SUMO.
        if observation.schedule_adherence_sourced:
            return observation.schedule_delay_s
        return max(
            observation.schedule_delay_s,
            observation.waiting_time_s,
            observation.accumulated_waiting_time_s,
        )

    def _priority_condition_met(self, schedule_delay_s: float, headway_deviation_s: float) -> bool:
        policy = self.config.obu_policy
        if bool(policy.get("allow_nominal_priority_requests", False)):
            return True
        return (
            schedule_delay_s >= float(policy.get("delay_threshold_s", 60))
            or abs(headway_deviation_s) >= float(policy.get("headway_deviation_threshold_s", 120))
        )

    def _operator_priority_class(
        self,
        observation: VehicleObservation,
        schedule_delay_s: float,
        headway_deviation_s: float,
    ) -> str:
        policy = self.config.obu_policy
        if observation.is_emergency_like:
            return OperatorPriorityClass.EMERGENCY.value
        if schedule_delay_s >= float(policy.get("delay_threshold_s", 60)):
            return OperatorPriorityClass.HIGH_DELAY.value
        if abs(headway_deviation_s) >= float(policy.get("headway_deviation_threshold_s", 120)):
            return OperatorPriorityClass.HEADWAY_RECOVERY.value
        return OperatorPriorityClass.NOMINAL.value

    def _basic_vehicle_role(self, observation: VehicleObservation) -> str:
        if observation.is_emergency_like:
            return BasicVehicleRole.EMERGENCY.value
        return BasicVehicleRole.PUBLIC_TRANSPORT.value
