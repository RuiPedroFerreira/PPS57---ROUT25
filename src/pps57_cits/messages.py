#!/usr/bin/env python3
"""Modelos de mensagens C-ITS emuladas.

As mensagens são deliberadamente JSON/Python-native para desenvolvimento e validação
funcional em SUMO. O objetivo da camada C-ITS é provar o fluxo OBU -> RSU -> resposta,
não implementar codificação ASN.1/UPER operacional.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from enum import Enum
import json
import time
from typing import Any, Dict, Iterable, List, Optional, Type, TypeVar
from uuid import uuid4


class MessageType(str, Enum):
    MAPEM_LIKE = "MAPEM_like"
    SPATEM_LIKE = "SPATEM_like"
    SREM_LIKE = "SREM_like"
    SSEM_LIKE = "SSEM_like"


class PriorityLevel(str, Enum):
    EMERGENCY_VEHICLE = "emergency_vehicle"
    PUBLIC_TRANSPORT_HIGH_DELAY = "public_transport_high_delay"
    PUBLIC_TRANSPORT_HEADWAY_RECOVERY = "public_transport_headway_recovery"
    PUBLIC_TRANSPORT_NOMINAL = "public_transport_nominal"
    GENERAL_TRAFFIC = "general_traffic"


class RequestStatus(str, Enum):
    REQUESTED = "requested"
    ACKNOWLEDGED = "acknowledged"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class RequestedManeuver(str, Enum):
    GREEN_EXTENSION = "green_extension"
    EARLY_GREEN = "early_green"
    PRIORITY_CANDIDATE = "priority_candidate"


class ResponseAction(str, Enum):
    FORWARD_TO_DECISION_ENGINE = "forward_to_decision_engine"
    NO_ACTION = "no_action"
    REEVALUATE_NEXT_CYCLE = "reevaluate_next_cycle"
    REJECT_WITH_REASON = "reject_with_reason"


@dataclass
class CITSMessage:
    """Base comum das mensagens emuladas."""

    message_type: str
    source_id: str
    destination_id: str
    timestamp_s: float
    message_id: str = field(default_factory=lambda: str(uuid4()))
    protocol_version: str = "0.3.0"
    correlation_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return _normalise_enums(asdict(self))

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


@dataclass
class Approach:
    approach_id: str
    edge_id: str
    direction: str
    is_priority_corridor: bool = False
    lane_ids: List[str] = field(default_factory=list)


@dataclass
class MAPEMLike(CITSMessage):
    intersection_id: str = ""
    tls_id: str = ""
    rsu_id: str = ""
    intersection_name: str = ""
    approaches: List[Approach] = field(default_factory=list)

    def __init__(
        self,
        *,
        source_id: str,
        destination_id: str,
        timestamp_s: float,
        intersection_id: str,
        tls_id: str,
        rsu_id: str,
        intersection_name: str,
        approaches: Iterable[Approach],
        message_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        super().__init__(
            message_type=MessageType.MAPEM_LIKE.value,
            source_id=source_id,
            destination_id=destination_id,
            timestamp_s=timestamp_s,
            message_id=message_id or str(uuid4()),
            correlation_id=correlation_id,
        )
        self.intersection_id = intersection_id
        self.tls_id = tls_id
        self.rsu_id = rsu_id
        self.intersection_name = intersection_name
        self.approaches = list(approaches)


@dataclass
class SPATEMLike(CITSMessage):
    intersection_id: str = ""
    tls_id: str = ""
    current_phase_index: Optional[int] = None
    current_program_id: Optional[str] = None
    red_yellow_green_state: Optional[str] = None
    next_switch_s: Optional[float] = None
    spent_duration_s: Optional[float] = None
    controlled_lanes: List[str] = field(default_factory=list)

    def __init__(
        self,
        *,
        source_id: str,
        destination_id: str,
        timestamp_s: float,
        intersection_id: str,
        tls_id: str,
        current_phase_index: Optional[int],
        current_program_id: Optional[str],
        red_yellow_green_state: Optional[str],
        next_switch_s: Optional[float],
        spent_duration_s: Optional[float],
        controlled_lanes: Optional[Iterable[str]] = None,
        message_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        super().__init__(
            message_type=MessageType.SPATEM_LIKE.value,
            source_id=source_id,
            destination_id=destination_id,
            timestamp_s=timestamp_s,
            message_id=message_id or str(uuid4()),
            correlation_id=correlation_id,
        )
        self.intersection_id = intersection_id
        self.tls_id = tls_id
        self.current_phase_index = current_phase_index
        self.current_program_id = current_program_id
        self.red_yellow_green_state = red_yellow_green_state
        self.next_switch_s = next_switch_s
        self.spent_duration_s = spent_duration_s
        self.controlled_lanes = list(controlled_lanes or [])


@dataclass
class SREMLike(CITSMessage):
    request_id: str = field(default_factory=lambda: str(uuid4()))
    vehicle_id: str = ""
    vehicle_class: str = "bus"
    line_id: str = ""
    route_id: str = ""
    intersection_id: str = ""
    tls_id: str = ""
    rsu_id: str = ""
    current_edge_id: str = ""
    current_lane_id: str = ""
    speed_mps: float = 0.0
    distance_to_stopline_m: float = 0.0
    eta_to_stopline_s: float = 0.0
    schedule_delay_s: float = 0.0
    headway_deviation_s: float = 0.0
    requested_maneuver: str = RequestedManeuver.PRIORITY_CANDIDATE.value
    priority_level: str = PriorityLevel.PUBLIC_TRANSPORT_NOMINAL.value
    # None = pedido sem expiração; um valor é o instante absoluto de expiração.
    expires_at_s: Optional[float] = None
    status: str = RequestStatus.REQUESTED.value

    def __init__(
        self,
        *,
        source_id: str,
        destination_id: str,
        timestamp_s: float,
        vehicle_id: str,
        vehicle_class: str,
        line_id: str,
        route_id: str,
        intersection_id: str,
        tls_id: str,
        rsu_id: str,
        current_edge_id: str,
        current_lane_id: str,
        speed_mps: float,
        distance_to_stopline_m: float,
        eta_to_stopline_s: float,
        schedule_delay_s: float,
        headway_deviation_s: float,
        requested_maneuver: str,
        priority_level: str,
        expires_at_s: Optional[float] = None,
        request_id: Optional[str] = None,
        message_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        super().__init__(
            message_type=MessageType.SREM_LIKE.value,
            source_id=source_id,
            destination_id=destination_id,
            timestamp_s=timestamp_s,
            message_id=message_id or str(uuid4()),
            correlation_id=correlation_id,
        )
        self.request_id = request_id or str(uuid4())
        self.vehicle_id = vehicle_id
        self.vehicle_class = vehicle_class
        self.line_id = line_id
        self.route_id = route_id
        self.intersection_id = intersection_id
        self.tls_id = tls_id
        self.rsu_id = rsu_id
        self.current_edge_id = current_edge_id
        self.current_lane_id = current_lane_id
        self.speed_mps = speed_mps
        self.distance_to_stopline_m = distance_to_stopline_m
        self.eta_to_stopline_s = eta_to_stopline_s
        self.schedule_delay_s = schedule_delay_s
        self.headway_deviation_s = headway_deviation_s
        self.requested_maneuver = requested_maneuver
        self.priority_level = priority_level
        self.expires_at_s = expires_at_s
        self.status = RequestStatus.REQUESTED.value


@dataclass
class SSEMLike(CITSMessage):
    request_id: str = ""
    vehicle_id: str = ""
    intersection_id: str = ""
    tls_id: str = ""
    rsu_id: str = ""
    status: str = RequestStatus.ACKNOWLEDGED.value
    action: str = ResponseAction.FORWARD_TO_DECISION_ENGINE.value
    reason: str = "accepted_for_tsp_decision_engine"
    valid_until_s: float = 0.0
    confidence: float = 1.0
    safety_notes: List[str] = field(default_factory=list)

    def __init__(
        self,
        *,
        source_id: str,
        destination_id: str,
        timestamp_s: float,
        request_id: str,
        vehicle_id: str,
        intersection_id: str,
        tls_id: str,
        rsu_id: str,
        status: str,
        action: str,
        reason: str,
        valid_until_s: float,
        confidence: float = 1.0,
        safety_notes: Optional[Iterable[str]] = None,
        message_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        super().__init__(
            message_type=MessageType.SSEM_LIKE.value,
            source_id=source_id,
            destination_id=destination_id,
            timestamp_s=timestamp_s,
            message_id=message_id or str(uuid4()),
            correlation_id=correlation_id,
        )
        self.request_id = request_id
        self.vehicle_id = vehicle_id
        self.intersection_id = intersection_id
        self.tls_id = tls_id
        self.rsu_id = rsu_id
        self.status = status
        self.action = action
        self.reason = reason
        self.valid_until_s = valid_until_s
        self.confidence = confidence
        self.safety_notes = list(safety_notes or [])


def normalise_for_json(value: Any) -> Any:
    """L6: helper partilhado para serialização — converte Enum.value, anda em
    dict/list recursivamente. Reutilizado por `pps57_tsp.models`."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [normalise_for_json(item) for item in value]
    if isinstance(value, dict):
        return {key: normalise_for_json(item) for key, item in value.items()}
    return value


# Alias para retrocompatibilidade dentro deste módulo.
_normalise_enums = normalise_for_json


def now_s() -> float:
    return time.time()


T = TypeVar("T", bound=CITSMessage)


def dataclass_from_dict(cls: Type[T], payload: Dict[str, Any]) -> T:
    """Reconstrói mensagens simples quando útil em testes.

    Para as subclasses com __init__ customizado, este helper filtra apenas campos
    compatíveis. Não é usado no loop principal, mas facilita testes e ingestão de
    logs JSONL.
    """
    accepted = {
        field.name
        for field in fields(cls)
        if field.name not in {"message_type", "protocol_version"}
    }
    kwargs = {key: value for key, value in payload.items() if key in accepted}
    return cls(**kwargs)  # type: ignore[arg-type]
