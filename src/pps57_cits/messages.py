#!/usr/bin/env python3
"""Modelos C-ITS alinhados com a camada *facilities* de ETSI TS 103 301.

A intenção desta versão é que o **shape** das mensagens (MAPEM, SPATEM, SREM,
SSEM) seja isomórfico aos módulos ASN.1 públicos da norma, mesmo permanecendo
codificadas em JSON para o ambiente de simulação. Isso permite a um integrador
substituir o broker em memória por um stack ITS-G5/PC5 real, ou trocar o
encoding por OER/UPER, sem alterar a lógica de domínio (OBU/RSU/TSP).

Campos que **não** pertencem à norma — telemetria operacional do operador,
metadados do broker em memória — vivem em sub-objetos explicitamente
marcados como extensão (`operator_telemetry`, `source_id`/`destination_id`).

Convenções de mapeamento ao CDD (ETSI TS 102 894-2):
- `station_id`        uint32 derivado deterministicamente do vehicle_id SUMO.
- `intersection_ref_id` uint16 derivado do alias "I1" -> 1.
- `request_id`        uint8 0..255, sequencial por requestor.
- `signal_group_id`   uint8 1..255, indexado a partir do link SUMO.
- `position.lat/lon`  em 1e-7 graus (CDD ReferencePosition).
- `*_time_ms`         milissegundos (CDD TimeMark/MilliSecond).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
import hashlib
import json
from typing import Any, Dict, List, Optional
from uuid import uuid4
import zlib


# ---------------------------------------------------------------------------
# Enums alinhados com ETSI TS 103 301 / TS 102 894-2 / ISO 19091.
# ---------------------------------------------------------------------------


class MessageType(str, Enum):
    """Tipos de PDU emitidos. Nomes sem o sufixo `_like` da v0.3 para reforçar
    que o *shape* segue agora os módulos ASN.1 da norma — só o encoding (JSON)
    é que difere."""

    MAPEM = "MAPEM"
    SPATEM = "SPATEM"
    SREM = "SREM"
    SSEM = "SSEM"


class StationType(int, Enum):
    """CDD ETSI TS 102 894-2 §A.91 (subset relevante para corredor BRT)."""

    UNKNOWN = 0
    PEDESTRIAN = 1
    CYCLIST = 2
    MOPED = 3
    MOTORCYCLE = 4
    PASSENGER_CAR = 5
    BUS = 6
    LIGHT_TRUCK = 7
    HEAVY_TRUCK = 8
    TRAILER = 9
    SPECIAL_VEHICLE = 10
    TRAM = 11
    ROAD_SIDE_UNIT = 15


class BasicVehicleRole(str, Enum):
    """ETSI TS 103 301-3 / J2735 BasicVehicleRole (subset)."""

    DEFAULT = "default"
    PUBLIC_TRANSPORT = "publicTransport"
    SPECIAL_TRANSPORT = "specialTransport"
    DANGEROUS_GOODS = "dangerousGoods"
    ROAD_WORK = "roadWork"
    ROAD_RESCUE = "roadRescue"
    EMERGENCY = "emergency"
    SAFETY_CAR = "safetyCar"
    NONE_UNKNOWN = "none-unknown"


class RequestType(str, Enum):
    """ETSI TS 103 301-3 PriorityRequestType."""

    PRIORITY_REQUEST = "priorityRequest"
    PRIORITY_REQUEST_UPDATE = "priorityRequestUpdate"
    PRIORITY_CANCELLATION = "priorityCancellation"


class EventState(str, Enum):
    """ETSI TS 103 301-2 MovementPhaseState (ISO 19091)."""

    UNAVAILABLE = "unavailable"
    DARK = "dark"
    STOP_THEN_PROCEED = "stop-Then-Proceed"
    STOP_AND_REMAIN = "stop-And-Remain"
    PRE_MOVEMENT = "pre-Movement"
    PERMISSIVE_MOVEMENT_ALLOWED = "permissive-Movement-Allowed"
    PROTECTED_MOVEMENT_ALLOWED = "protected-Movement-Allowed"
    PERMISSIVE_CLEARANCE = "permissive-clearance"
    PROTECTED_CLEARANCE = "protected-clearance"
    CAUTION_CONFLICTING_TRAFFIC = "caution-Conflicting-Traffic"


class ResponseStatus(str, Enum):
    """ETSI TS 103 301-3 PrioritizationResponseStatus."""

    UNKNOWN = "unknown"
    REQUESTED = "requested"
    PROCESSING = "processing"
    WATCH_OTHER_TRAFFIC = "watchOtherTraffic"
    GRANTED = "granted"
    REJECTED = "rejected"
    MAX_PRESENCE = "maxPresence"
    RESERVICE_LOCKED = "reserviceLocked"


class GrantedStrategy(str, Enum):
    """Estratégia aplicada pela infraestrutura quando o pedido é concedido.

    NOTA: este enum **não** faz parte do SSEM standard, que apenas reporta
    `responseStatus`. Aqui é exposto como extensão `audit.granted_strategy`
    para tornar os KPIs do simulador comparáveis. Um integrador real só
    precisa de o ler para auditoria; o tracking primário deve usar o
    `responseStatus` standard.
    """

    NONE = "none"
    GREEN_EXTENSION = "greenExtension"
    EARLY_GREEN = "earlyGreen"
    PHASE_INSERTION = "phaseInsertion"


class OperatorPriorityClass(str, Enum):
    """Classe operacional do pedido — afeta scoring no TSP.

    Não-standard: é uma extensão do operador, transportada em
    `operator_telemetry`. Numa pilha real chegaria via APC/AVL ao TMC e seria
    correlacionada com o SRM, ou viria num campo de extensão proprietária do
    SRM (a CDD permite extensibilidade).
    """

    NOMINAL = "nominal"
    HIGH_DELAY = "high_delay"
    HEADWAY_RECOVERY = "headway_recovery"
    EMERGENCY = "emergency"


# Valores standard do bitmap `IntersectionStatusObject` (ETSI TS 103 301-2)
INTERSECTION_STATUS_FLAGS: tuple[str, ...] = (
    "manualControlIsEnabled",
    "stopTimeIsActivated",
    "failureFlash",
    "preemptIsActive",
    "signalPriorityIsActive",
    "fixedTimeOperation",
    "trafficDependentOperation",
    "standbyOperation",
    "failureMode",
    "off",
    "recentMAPmessageDelivered",
    "recentChangeInMAPassignedLanesIdsUsed",
    "noValidMAPisAvailableAtThisTime",
    "noValidSPATisAvailableAtThisTime",
)


# ---------------------------------------------------------------------------
# Tipos auxiliares (CDD).
# ---------------------------------------------------------------------------


@dataclass
class Position3D:
    """ETSI CDD ReferencePosition (forma compacta)."""

    latitude_e7: int = 0
    longitude_e7: int = 0
    elevation_dm: int = 0


@dataclass
class SecurityEnvelope:
    """ETSI TS 103 097 — envelope estrutural.

    Em simulação, `signature_b64` é `None` (placeholder). A RSU **executa o
    caminho de verificação** (certificate_id conhecido, validade temporal),
    o que mantém o ponto de inserção limpo para PKI real (`cryptography`
    ECDSA P-256) num piloto. A presença do envelope é mandatória — cada PDU
    deve transportá-lo, como na norma.
    """

    signer_id: str
    certificate_id: str
    signature_b64: Optional[str]
    generation_time_ms: int
    valid_until_ms: int


@dataclass
class MovementEvent:
    """ETSI TS 103 301-2 MovementEvent.

    A janela de timing é (min, max, likely). Para programas de tempo fixo,
    min == max == likely. Para programas atuados, max-min reflecte a folga
    extensível da fase.
    """

    signal_group_id: int
    event_state: str            # EventState value
    min_end_time_ms: int        # ms a partir do `generation_time_ms`
    max_end_time_ms: int
    likely_time_ms: Optional[int] = None
    confidence: int = 0         # 0..15 (CDD TimeConfidence)


@dataclass
class Approach:
    """Approach (subset de IntersectionGeometry da MAP).

    `lane_id` (SUMO) é o alias operacional usado pelos componentes do
    simulador; numa MAP standard a lane é identificada por `LaneID` uint8
    relativa ao Approach.
    """

    approach_id: str
    edge_id: str
    direction: str
    priority_movement_ids: List[str] = field(default_factory=list)
    lane_ids: List[str] = field(default_factory=list)


@dataclass
class Requestor:
    """ETSI TS 103 301-3 RequestorDescription.

    `operational_vehicle_id` é uma extensão (string id SUMO) — não faz parte
    da norma, mas é necessária para correlacionar com TraCI no simulador.
    """

    station_id: int
    station_type: int                    # StationType
    basic_vehicle_role: str              # BasicVehicleRole
    position: Position3D
    heading_deg: float
    speed_mps: float
    route_name: Optional[str] = None     # GTFS line, ex.: "STCP500"
    operational_vehicle_id: str = ""     # extensão simulador


@dataclass
class SignalRequest:
    """ETSI TS 103 301-3 SignalRequest.

    `in_bound_lane_id` / `out_bound_lane_id` aqui são strings SUMO (operacional).
    Num MAP standard seriam uint8 referenciando a tabela de lanes da MAP.
    """

    intersection_ref_id: int
    request_id: int                # CDD uint8 0..255
    request_type: str              # RequestType
    in_bound_lane_id: str
    out_bound_lane_id: str
    eta_min_minute: int            # minute-of-year do ETA min
    eta_min_second_ms: int         # ms no minuto
    duration_ms: int               # TTL do pedido


@dataclass
class PrioritizationResponse:
    """ETSI TS 103 301-3 SignalStatusPackage.PrioritizationResponse."""

    request_id: int
    sequence_number: int
    requestor_station_id: int
    response_status: str                       # ResponseStatus
    granted_signal_group: Optional[int] = None
    valid_until_ms: int = 0


@dataclass
class SSEMAudit:
    """Bloco de auditoria não-standard — separado do payload SSEM normativo.

    Carrega o motivo de rejeição em texto e a estratégia concedida (quando
    `response_status = granted`). Estas duas dimensões não pertencem ao
    SSEM standard, mas são cruciais para KPI/auditoria no simulador. Numa
    instalação real vivem num log interno do TMC, não no PDU.
    """

    granted_strategy: str = GrantedStrategy.NONE.value
    rejection_reason: Optional[str] = None
    confidence: float = 1.0
    notes: List[str] = field(default_factory=list)


@dataclass
class OperatorTelemetry:
    """Extensão operacional do operador — não faz parte do SRM standard.

    Carrega o estado de schedule/headway que conduz o weighting do TSP.
    Numa pilha real chegaria via canal AVL/APC do operador para o TMC, ou
    seria embebido num campo de extensão proprietária do SRM. Aqui é
    explicitamente fora do envelope normativo para que um auditor consiga
    distinguir o que é ETSI do que é operador.
    """

    schedule_delay_s: float = 0.0
    headway_deviation_s: float = 0.0
    distance_to_stopline_m: float = 0.0
    eta_to_stopline_s: float = 0.0
    operator_priority_class: str = OperatorPriorityClass.NOMINAL.value
    line_id: str = ""
    route_id: str = ""
    intersection_alias: str = ""           # "I1" — referência operacional
    tls_id: str = ""                       # SUMO TLS id
    rsu_id: str = ""                       # broker alias
    priority_movement_id: str = ""         # entrada no catálogo do operador
    target_signal_group_id_hint: str = ""  # hint do OBU; RSU não fica vinculado
    cancellation_reason: str = ""          # extensão, só usada em priorityCancellation


# ---------------------------------------------------------------------------
# Base de mensagens.
# ---------------------------------------------------------------------------


@dataclass
class CITSMessage:
    """Cabeçalho comum a todas as ITS-PDUs.

    `station_id`/`station_type`/`generation_delta_time_ms` provêm do CDD ETSI
    TS 102 894-2. `source_id`/`destination_id` são metadados do broker em
    memória — explicitamente fora do envelope standard, mas necessários para
    encaminhamento no simulador (substituível por broadcast geocast num stack
    ITS-G5 real).
    """

    message_type: str
    station_id: int
    station_type: int
    source_id: str                # extensão broker
    destination_id: str           # extensão broker
    generation_delta_time_ms: int
    moy: int                      # minute of year
    timestamp_ms: int             # ms no minuto corrente
    security: SecurityEnvelope
    message_id: str = field(default_factory=lambda: str(uuid4()))
    protocol_version: str = "0.4.0"
    correlation_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return normalise_for_json(asdict(self))

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# PDUs alinhados com ETSI TS 103 301.
# ---------------------------------------------------------------------------


@dataclass
class MAPEMLike(CITSMessage):
    """MapData / MAPEM (ETSI TS 103 301-2 / ISO 19091)."""

    intersection_ref_id: int = 0
    intersection_alias: str = ""             # operacional, ex. "I1"
    intersection_name: str = ""
    tls_id: str = ""                         # SUMO TLS id
    rsu_id: str = ""
    revision: int = 0                        # campo obrigatório na MAP standard
    ref_point: Optional[Position3D] = None   # ETSI ReferencePosition
    approaches: List[Approach] = field(default_factory=list)


@dataclass
class SPATEMLike(CITSMessage):
    """SPaT / SPATEM (ETSI TS 103 301-2 / ISO 19091).

    O estado por **movimento** (signalGroup) substitui a antiga string de
    estados-por-link do SUMO, que sobrevive apenas em `debug_sumo_state` para
    correlação visual em traces.
    """

    intersection_ref_id: int = 0
    intersection_alias: str = ""
    tls_id: str = ""
    revision: int = 0
    movement_events: List[MovementEvent] = field(default_factory=list)
    intersection_status: Dict[str, bool] = field(default_factory=dict)
    debug_sumo_state: Optional[str] = None   # extensão simulador, não-standard


@dataclass
class SREMLike(CITSMessage):
    """SREM / SignalRequestMessage (ETSI TS 103 301-3).

    Standard: `requests[]` + `requestor` + `sequence_number`.
    Extensão simulador: `operator_telemetry` (schedule/headway) e
    `expires_at_s` (TTL absoluto em segundos de simulação).

    As propriedades em baixo são **acessos ergonómicos** que delegam em
    subobjetos da forma standard. Não fazem parte do JSON serializado — só
    existem para que o código Python a jusante não tenha de fazer
    `request.operator_telemetry.schedule_delay_s` em todo o lado.
    """

    sequence_number: int = 0
    requests: List[SignalRequest] = field(default_factory=list)
    requestor: Optional[Requestor] = None
    operator_telemetry: Optional[OperatorTelemetry] = None
    expires_at_s: Optional[float] = None

    # ------------------------------------------------------------------
    # Acessos ergonómicos (não-serializados; propriedades, não campos).
    # ------------------------------------------------------------------
    @property
    def correlation_token(self) -> str:
        """Token estável (`station:request:seq`) usado como chave para correlacionar
        SREM ↔ SSEM ↔ TSPDecision em logs/dicts. Preserva unicidade global."""
        request_id = self.requests[0].request_id if self.requests else 0
        return f"{self.station_id}:{request_id}:{self.sequence_number}"

    @property
    def request_id(self) -> str:
        """Alias compatível para uso como dict-key. Use `correlation_token` quando
        for explícito sobre o significado."""
        return self.correlation_token

    @property
    def request_type(self) -> str:
        return self.requests[0].request_type if self.requests else ""

    @property
    def is_cancellation(self) -> bool:
        return self.request_type == RequestType.PRIORITY_CANCELLATION.value

    @property
    def intersection_id(self) -> str:
        return self.operator_telemetry.intersection_alias if self.operator_telemetry else ""

    @property
    def tls_id(self) -> str:
        return self.operator_telemetry.tls_id if self.operator_telemetry else ""

    @property
    def rsu_id(self) -> str:
        return self.operator_telemetry.rsu_id if self.operator_telemetry else ""

    @property
    def vehicle_id(self) -> str:
        return self.requestor.operational_vehicle_id if self.requestor else ""

    @property
    def vehicle_class(self) -> str:
        return self.requestor.basic_vehicle_role if self.requestor else ""

    @property
    def line_id(self) -> str:
        return self.operator_telemetry.line_id if self.operator_telemetry else ""

    @property
    def route_id(self) -> str:
        return self.operator_telemetry.route_id if self.operator_telemetry else ""

    @property
    def schedule_delay_s(self) -> float:
        return self.operator_telemetry.schedule_delay_s if self.operator_telemetry else 0.0

    @property
    def headway_deviation_s(self) -> float:
        return self.operator_telemetry.headway_deviation_s if self.operator_telemetry else 0.0

    @property
    def distance_to_stopline_m(self) -> float:
        return self.operator_telemetry.distance_to_stopline_m if self.operator_telemetry else 0.0

    @property
    def eta_to_stopline_s(self) -> float:
        return self.operator_telemetry.eta_to_stopline_s if self.operator_telemetry else 0.0

    @property
    def priority_level(self) -> str:
        return self.operator_telemetry.operator_priority_class if self.operator_telemetry else ""

    @property
    def priority_movement_id(self) -> str:
        return self.operator_telemetry.priority_movement_id if self.operator_telemetry else ""

    @property
    def target_signal_group_id(self) -> str:
        return self.operator_telemetry.target_signal_group_id_hint if self.operator_telemetry else ""

    @property
    def current_lane_id(self) -> str:
        return self.requests[0].in_bound_lane_id if self.requests else ""

    @property
    def current_edge_id(self) -> str:
        lane_id = self.current_lane_id
        if not lane_id:
            return ""
        # SUMO convention: lane id is "<edge>_<index>".
        idx = lane_id.rfind("_")
        return lane_id[:idx] if idx >= 0 else lane_id

    @property
    def next_edge_id(self) -> str:
        return self.requests[0].out_bound_lane_id if self.requests else ""


@dataclass
class SSEMLike(CITSMessage):
    """SSEM / SignalStatusMessage (ETSI TS 103 301-3).

    Standard: `intersection_ref_id` + `response`.
    Extensão simulador: `audit` (motivo rejeição / estratégia concedida).

    Propriedades adiantam o estado do `response` para acesso direto.
    """

    intersection_ref_id: int = 0
    intersection_alias: str = ""
    tls_id: str = ""
    rsu_id: str = ""
    response: Optional[PrioritizationResponse] = None
    audit: SSEMAudit = field(default_factory=SSEMAudit)

    @property
    def correlation_token(self) -> str:
        if self.response is None:
            return ""
        return f"{self.response.requestor_station_id}:{self.response.request_id}:{self.response.sequence_number}"

    @property
    def request_id(self) -> str:
        """Alias para uso como dict-key. Espelha o `correlation_token` do SREM."""
        return self.correlation_token

    @property
    def status(self) -> str:
        return self.response.response_status if self.response else ""

    @property
    def reason(self) -> str:
        return self.audit.rejection_reason or ""

    @property
    def confidence(self) -> float:
        return self.audit.confidence


# ---------------------------------------------------------------------------
# Validacao estrutural centralizada.
# ---------------------------------------------------------------------------


def validate_cits_message(message: CITSMessage) -> List[str]:
    """Return structural validation errors for a simulated C-ITS PDU.

    This is intentionally a simulation-profile validator, not an ASN.1 or PKI
    validator. It checks the common envelope, standard-like enums, CDD ranges
    and payload shape so producers/consumers share one contract before the RSU
    applies operational policy such as TTL, identity and eligibility.
    """
    errors: List[str] = []
    _validate_common_message(message, errors)

    if message.message_type == MessageType.MAPEM.value:
        if isinstance(message, MAPEMLike):
            _validate_mapem(message, errors)
        else:
            errors.append("mapem.type_mismatch")
    elif message.message_type == MessageType.SPATEM.value:
        if isinstance(message, SPATEMLike):
            _validate_spatem(message, errors)
        else:
            errors.append("spatem.type_mismatch")
    elif message.message_type == MessageType.SREM.value:
        if isinstance(message, SREMLike):
            _validate_srem(message, errors)
        else:
            errors.append("srem.type_mismatch")
    elif message.message_type == MessageType.SSEM.value:
        if isinstance(message, SSEMLike):
            _validate_ssem(message, errors)
        else:
            errors.append("ssem.type_mismatch")

    return errors


def ensure_cits_message_valid(message: CITSMessage) -> None:
    """Raise ValueError when a message violates the simulation protocol."""
    errors = validate_cits_message(message)
    if errors:
        raise ValueError("; ".join(errors))


def _validate_common_message(message: CITSMessage, errors: List[str]) -> None:
    if message.message_type not in _enum_values(MessageType):
        errors.append("message_type_unsupported")
    if message.protocol_version != "0.4.0":
        errors.append("protocol_version_unsupported")
    if not message.message_id:
        errors.append("message_id_missing")
    if message.correlation_id == "":
        errors.append("correlation_id_empty")
    if not _is_uint(message.station_id, 32):
        errors.append("station_id_out_of_range")
    if message.station_type not in _enum_values(StationType):
        errors.append("station_type_unsupported")
    if not message.source_id:
        errors.append("source_id_missing")
    if not message.destination_id:
        errors.append("destination_id_missing")
    if not _in_range(message.generation_delta_time_ms, 0, 65535):
        errors.append("generation_delta_time_ms_out_of_range")
    if not _in_range(message.moy, 0, 527040):
        errors.append("moy_out_of_range")
    if not _in_range(message.timestamp_ms, 0, 59999):
        errors.append("timestamp_ms_out_of_range")
    _validate_security(message.security, errors)


def _validate_security(security: Optional[SecurityEnvelope], errors: List[str]) -> None:
    if security is None:
        errors.append("security_missing")
        return
    if not security.signer_id:
        errors.append("security.signer_id_missing")
    if not security.certificate_id:
        errors.append("security.certificate_id_missing")
    if not isinstance(security.generation_time_ms, int) or security.generation_time_ms < 0:
        errors.append("security.generation_time_ms_invalid")
    if not isinstance(security.valid_until_ms, int) or security.valid_until_ms < 0:
        errors.append("security.valid_until_ms_invalid")


def _validate_mapem(message: MAPEMLike, errors: List[str]) -> None:
    _validate_intersection_fields(message.intersection_ref_id, message.intersection_alias, message.tls_id, errors)
    if message.revision < 0:
        errors.append("mapem.revision_negative")
    if message.ref_point is not None:
        _validate_position(message.ref_point, "mapem.ref_point", errors)
    if not message.approaches:
        errors.append("mapem.approaches_missing")
    for index, approach in enumerate(message.approaches):
        prefix = f"mapem.approaches[{index}]"
        if not approach.approach_id:
            errors.append(f"{prefix}.approach_id_missing")
        if not approach.edge_id:
            errors.append(f"{prefix}.edge_id_missing")
        if not approach.direction:
            errors.append(f"{prefix}.direction_missing")


def _validate_spatem(message: SPATEMLike, errors: List[str]) -> None:
    _validate_intersection_fields(message.intersection_ref_id, message.intersection_alias, message.tls_id, errors)
    if message.revision < 0:
        errors.append("spatem.revision_negative")
    if not message.movement_events:
        errors.append("spatem.movement_events_missing")
    for index, event in enumerate(message.movement_events):
        prefix = f"spatem.movement_events[{index}]"
        if not _in_range(event.signal_group_id, 1, 255):
            errors.append(f"{prefix}.signal_group_id_out_of_range")
        if event.event_state not in _enum_values(EventState):
            errors.append(f"{prefix}.event_state_unsupported")
        if event.min_end_time_ms < 0 or event.max_end_time_ms < 0:
            errors.append(f"{prefix}.end_time_negative")
        if event.max_end_time_ms < event.min_end_time_ms:
            errors.append(f"{prefix}.end_time_inverted")
        if event.likely_time_ms is not None:
            if event.likely_time_ms < 0:
                errors.append(f"{prefix}.likely_time_negative")
            elif not (event.min_end_time_ms <= event.likely_time_ms <= event.max_end_time_ms):
                errors.append(f"{prefix}.likely_time_outside_window")
        if not _in_range(event.confidence, 0, 15):
            errors.append(f"{prefix}.confidence_out_of_range")
    unknown_flags = set(message.intersection_status) - set(INTERSECTION_STATUS_FLAGS)
    if unknown_flags:
        errors.append("spatem.intersection_status_unknown_flags")


def _validate_srem(message: SREMLike, errors: List[str]) -> None:
    if not _in_range(message.sequence_number, 0, 65535):
        errors.append("srem.sequence_number_out_of_range")
    if not message.requests:
        errors.append("srem.requests_missing")
    for index, request in enumerate(message.requests):
        prefix = f"srem.requests[{index}]"
        if not _in_range(request.intersection_ref_id, 0, 65535):
            errors.append(f"{prefix}.intersection_ref_id_out_of_range")
        if not _in_range(request.request_id, 1, 255):
            errors.append(f"{prefix}.request_id_out_of_range")
        if request.request_type not in _enum_values(RequestType):
            errors.append(f"{prefix}.request_type_unsupported")
        if request.request_type != RequestType.PRIORITY_CANCELLATION.value and not request.in_bound_lane_id:
            errors.append(f"{prefix}.in_bound_lane_id_missing")
        if not _in_range(request.eta_min_minute, 0, 527040):
            errors.append(f"{prefix}.eta_min_minute_out_of_range")
        if not _in_range(request.eta_min_second_ms, 0, 59999):
            errors.append(f"{prefix}.eta_min_second_ms_out_of_range")
        if request.duration_ms < 0:
            errors.append(f"{prefix}.duration_ms_negative")

    requestor = message.requestor
    if requestor is None:
        errors.append("srem.requestor_missing")
    else:
        if requestor.station_id != message.station_id:
            errors.append("srem.requestor_station_id_mismatch")
        if requestor.station_type not in _enum_values(StationType):
            errors.append("srem.requestor_station_type_unsupported")
        if requestor.basic_vehicle_role not in _enum_values(BasicVehicleRole):
            errors.append("srem.requestor_basic_vehicle_role_unsupported")
        if not requestor.operational_vehicle_id:
            errors.append("srem.requestor_operational_vehicle_id_missing")
        if requestor.speed_mps < 0:
            errors.append("srem.requestor_speed_negative")
        if not (0.0 <= requestor.heading_deg < 360.0):
            errors.append("srem.requestor_heading_out_of_range")
        _validate_position(requestor.position, "srem.requestor.position", errors)

    telemetry = message.operator_telemetry
    if telemetry is None:
        errors.append("srem.operator_telemetry_missing")
    else:
        if telemetry.distance_to_stopline_m < 0:
            errors.append("srem.operator_telemetry.distance_negative")
        if telemetry.eta_to_stopline_s < 0:
            errors.append("srem.operator_telemetry.eta_negative")
        if telemetry.operator_priority_class not in _enum_values(OperatorPriorityClass):
            errors.append("srem.operator_telemetry.priority_class_unsupported")
        if not telemetry.intersection_alias:
            errors.append("srem.operator_telemetry.intersection_alias_missing")
        if not telemetry.tls_id:
            errors.append("srem.operator_telemetry.tls_id_missing")
        if not telemetry.rsu_id:
            errors.append("srem.operator_telemetry.rsu_id_missing")
    if message.expires_at_s is not None and message.expires_at_s < 0:
        errors.append("srem.expires_at_s_negative")


def _validate_ssem(message: SSEMLike, errors: List[str]) -> None:
    _validate_intersection_fields(message.intersection_ref_id, message.intersection_alias, message.tls_id, errors)
    if not message.rsu_id:
        errors.append("ssem.rsu_id_missing")
    response = message.response
    if response is None:
        errors.append("ssem.response_missing")
    else:
        if not _in_range(response.request_id, 0, 255):
            errors.append("ssem.response.request_id_out_of_range")
        if not _in_range(response.sequence_number, 0, 65535):
            errors.append("ssem.response.sequence_number_out_of_range")
        if not _is_uint(response.requestor_station_id, 32):
            errors.append("ssem.response.requestor_station_id_out_of_range")
        if response.response_status not in _enum_values(ResponseStatus):
            errors.append("ssem.response.response_status_unsupported")
        if response.granted_signal_group is not None and not _in_range(response.granted_signal_group, 1, 255):
            errors.append("ssem.response.granted_signal_group_out_of_range")
        if response.valid_until_ms < 0:
            errors.append("ssem.response.valid_until_ms_negative")
        if response.response_status == ResponseStatus.REJECTED.value and not message.audit.rejection_reason:
            errors.append("ssem.audit.rejection_reason_missing_for_rejected")
    if message.audit.granted_strategy not in _enum_values(GrantedStrategy):
        errors.append("ssem.audit.granted_strategy_unsupported")
    if not (0.0 <= message.audit.confidence <= 1.0):
        errors.append("ssem.audit.confidence_out_of_range")


def _validate_intersection_fields(
    intersection_ref_id: int,
    intersection_alias: str,
    tls_id: str,
    errors: List[str],
) -> None:
    if not _in_range(intersection_ref_id, 0, 65535):
        errors.append("intersection_ref_id_out_of_range")
    if not intersection_alias:
        errors.append("intersection_alias_missing")
    if not tls_id:
        errors.append("tls_id_missing")


def _validate_position(position: Position3D, prefix: str, errors: List[str]) -> None:
    if not _in_range(position.latitude_e7, -900000000, 900000000):
        errors.append(f"{prefix}.latitude_e7_out_of_range")
    if not _in_range(position.longitude_e7, -1800000000, 1800000000):
        errors.append(f"{prefix}.longitude_e7_out_of_range")


def _enum_values(enum_type: type[Enum]) -> set[Any]:
    return {item.value for item in enum_type}


def _in_range(value: Any, lower: int, upper: int) -> bool:
    return isinstance(value, int) and lower <= value <= upper


def _is_uint(value: Any, bits: int) -> bool:
    return _in_range(value, 0, (1 << bits) - 1)


# ---------------------------------------------------------------------------
# Helpers de derivação CDD (alias string -> id numérico standard-aligned).
# ---------------------------------------------------------------------------


def parse_intersection_ref_id(alias: str) -> int:
    """Deriva o `intersection_ref_id` uint16 a partir do alias operacional.

    Apenas aliases canónicos da forma estrita "I<n>" mapeiam para o número
    ("I1" -> 1, "I12" -> 12). Qualquer outro alias (ex.: "TLS_1_2",
    "cluster_1_2") usa um hash CRC32 determinístico de 16 bits — concatenar
    dígitos faria "I12", "TLS_1_2" e "cluster_1_2" colidirem todos em 12.
    Produtores (OBU/MAPEM/SPATEM) e consumidores (RSU) usam esta mesma função
    para que o endereçamento legítimo continue a corresponder.
    """
    if len(alias) > 1 and alias[0] == "I" and alias[1:].isdigit():
        return int(alias[1:]) & 0xFFFF
    return zlib.crc32(alias.encode("utf-8")) & 0xFFFF


def derive_station_id(vehicle_or_rsu_id: str) -> int:
    """Deriva um `station_id` uint32 estável a partir do id operacional.

    Determinístico (mesma string -> mesmo id), suficiente para o simulador.
    Num piloto real, o station_id chega via certificado ETSI TS 102 941.
    """
    digest = hashlib.blake2b(vehicle_or_rsu_id.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big")


def sim_time_to_cdd(sim_time_s: float) -> tuple[int, int, int]:
    """Converte `sim_time_s` em (moy, timestamp_ms, generation_delta_time_ms).

    `moy` é minuto-do-ano (CDD MinuteOfTheYear, 0..527040). `timestamp_ms` é
    ms dentro do minuto. `generation_delta_time_ms` é o usado pela CAM/DENM
    (ms módulo 65536) — aqui derivado de `sim_time_s` para coerência.
    """
    total_ms = int(round(sim_time_s * 1000))
    moy = (total_ms // 60000) % 527040
    timestamp_ms = total_ms % 60000
    generation_delta = total_ms % 65536
    return moy, timestamp_ms, generation_delta


def build_security_envelope(
    signer_id: str,
    sim_time_s: float,
    *,
    validity_s: float = 60.0,
    signed: bool = False,
) -> SecurityEnvelope:
    """Constrói um envelope TS 103 097 estrutural.

    Em simulação `signature_b64=None` significa "não assinada", mas a RSU
    deve rejeitar quando `signed=True` é esperado. O `certificate_id` é
    derivado deterministicamente do `signer_id` para que a RSU possa
    construir uma cache simples de certificados conhecidos.
    """
    generation_ms = int(round(sim_time_s * 1000))
    valid_until_ms = generation_ms + int(round(validity_s * 1000))
    certificate_id = hashlib.blake2b(signer_id.encode("utf-8"), digest_size=8).hexdigest()
    return SecurityEnvelope(
        signer_id=signer_id,
        certificate_id=certificate_id,
        signature_b64=("PLACEHOLDER_SIGNATURE" if signed else None),
        generation_time_ms=generation_ms,
        valid_until_ms=valid_until_ms,
    )


# ---------------------------------------------------------------------------
# Mapeamento SUMO -> EventState.
# ---------------------------------------------------------------------------


# SUMO link state char -> EventState ETSI.
# Referências: SUMO TLS state characters
# (https://sumo.dlr.de/docs/Simulation/Traffic_Lights.html#tllogic_attributes)
_SUMO_TO_EVENT_STATE: Dict[str, str] = {
    "r": EventState.STOP_AND_REMAIN.value,
    "R": EventState.STOP_AND_REMAIN.value,
    "y": EventState.PERMISSIVE_CLEARANCE.value,
    "Y": EventState.PROTECTED_CLEARANCE.value,
    "g": EventState.PERMISSIVE_MOVEMENT_ALLOWED.value,
    "G": EventState.PROTECTED_MOVEMENT_ALLOWED.value,
    "s": EventState.STOP_THEN_PROCEED.value,           # right-turn-on-red: parar e avançar com cautela
    "u": EventState.PRE_MOVEMENT.value,                # red-yellow, transição p/ verde
    "O": EventState.DARK.value,
    "o": EventState.CAUTION_CONFLICTING_TRAFFIC.value, # amarelo pisca
}


def sumo_link_char_to_event_state(char: str) -> str:
    return _SUMO_TO_EVENT_STATE.get(char, EventState.UNAVAILABLE.value)


# ---------------------------------------------------------------------------
# Serialização.
# ---------------------------------------------------------------------------


def synth_srem(
    *,
    sim_time_s: float = 0.0,
    vehicle_id: str = "bus_synth",
    intersection_alias: str,
    tls_id: str,
    rsu_id: str,
    lane_id: str = "",
    next_edge_id: str = "",
    line_id: str = "",
    route_id: str = "",
    eta_to_stopline_s: float = 15.0,
    distance_to_stopline_m: float = 150.0,
    speed_mps: float = 10.0,
    schedule_delay_s: float = 120.0,
    headway_deviation_s: float = 0.0,
    operator_priority_class: str = "high_delay",
    basic_vehicle_role: str = "publicTransport",
    station_type: int = StationType.BUS.value,
    priority_movement_id: str = "",
    target_signal_group_id_hint: str = "",
    request_type: str = "priorityRequest",
    request_id: int = 1,
    sequence_number: int = 1,
    expires_at_s: Optional[float] = None,
    ttl_s: float = 30.0,
    signed: bool = False,
) -> SREMLike:
    """Constrói um SREM ETSI-aligned a partir de parâmetros operacionais.

    Pensado para fixtures sintéticas / testes. Em runtime, `OBUEmulator.generate_requests`
    constrói SREMs equivalentes a partir de observações TraCI.

    Os defaults colocam o pedido na zona elegível (delay alto + ETA dentro da
    janela 8..45 s) para que o fluxo padrão `REQUEST -> PROCESSING` aconteça.
    Os testes que querem rejeição devem violar uma condição explicitamente.
    """
    signer_id = f"OBU_{vehicle_id}"
    station_id = derive_station_id(vehicle_id)

    moy, timestamp_ms, generation_delta = sim_time_to_cdd(sim_time_s)
    security = build_security_envelope(signer_id, sim_time_s, validity_s=ttl_s, signed=signed)

    eta_arrival_s = sim_time_s + eta_to_stopline_s
    eta_moy, eta_ms, _ = sim_time_to_cdd(eta_arrival_s)
    signal_request = SignalRequest(
        intersection_ref_id=parse_intersection_ref_id(intersection_alias),
        request_id=request_id,
        request_type=request_type,
        in_bound_lane_id=lane_id,
        out_bound_lane_id=next_edge_id,
        eta_min_minute=eta_moy,
        eta_min_second_ms=eta_ms,
        duration_ms=int(round(ttl_s * 1000)),
    )
    requestor = Requestor(
        station_id=station_id,
        station_type=station_type,
        basic_vehicle_role=basic_vehicle_role,
        position=Position3D(),
        heading_deg=0.0,
        speed_mps=speed_mps,
        route_name=line_id or None,
        operational_vehicle_id=vehicle_id,
    )
    operator_telemetry = OperatorTelemetry(
        schedule_delay_s=schedule_delay_s,
        headway_deviation_s=headway_deviation_s,
        distance_to_stopline_m=distance_to_stopline_m,
        eta_to_stopline_s=eta_to_stopline_s,
        operator_priority_class=operator_priority_class,
        line_id=line_id,
        route_id=route_id,
        intersection_alias=intersection_alias,
        tls_id=tls_id,
        rsu_id=rsu_id,
        priority_movement_id=priority_movement_id,
        target_signal_group_id_hint=target_signal_group_id_hint,
    )
    return SREMLike(
        message_type=MessageType.SREM.value,
        station_id=station_id,
        station_type=station_type,
        source_id=signer_id,
        destination_id=rsu_id,
        generation_delta_time_ms=generation_delta,
        moy=moy,
        timestamp_ms=timestamp_ms,
        security=security,
        sequence_number=sequence_number,
        requests=[signal_request],
        requestor=requestor,
        operator_telemetry=operator_telemetry,
        expires_at_s=expires_at_s if expires_at_s is not None else (sim_time_s + ttl_s),
    )


def normalise_for_json(value: Any) -> Any:
    """L6: helper partilhado para serialização — converte Enum.value, anda em
    dict/list recursivamente. Reutilizado por `pps57_tsp.models`."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [normalise_for_json(item) for item in value]
    if isinstance(value, tuple):
        return [normalise_for_json(item) for item in value]
    if isinstance(value, dict):
        return {key: normalise_for_json(item) for key, item in value.items()}
    return value
