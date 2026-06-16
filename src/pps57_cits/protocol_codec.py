#!/usr/bin/env python3
"""Protocol codec boundary for C-ITS messages.

The simulator currently uses a JSON profile of ETSI-like MAPEM/SPATEM/SREM/SSEM
Python models. This module makes that boundary explicit: domain code works with
typed internal models, while transport/persistence code uses a codec.

`JsonSimulationCodec` is intentionally not an ASN.1/UPER/OER ETSI codec. A real
field deployment should add a separate codec implementation backed by the
official ASN.1 modules, PKI validation and the selected transport stack.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Dict, List, Protocol

from .messages import (
    Approach,
    CITSMessage,
    MAPEMLike,
    MessageType,
    MovementEvent,
    OperatorTelemetry,
    Position3D,
    PrioritizationResponse,
    Requestor,
    SecurityEnvelope,
    SignalRequest,
    SPATEMLike,
    SSEMAudit,
    SSEMLike,
    SREMLike,
    ensure_cits_message_valid,
    validate_cits_message,
)


class ProtocolCodecError(ValueError):
    """Raised when encoded protocol data cannot be decoded or validated."""


class ProtocolCodec(Protocol):
    """Transport/persistence boundary for C-ITS PDUs."""

    profile_name: str
    content_type: str

    def encode(self, message: CITSMessage) -> str: ...

    def decode(self, payload: str | bytes | Dict[str, Any]) -> CITSMessage: ...

    def validate(self, message: CITSMessage) -> List[str]: ...


@dataclass(frozen=True)
class JsonSimulationCodec:
    """JSON codec for the simulator profile.

    The codec performs structural validation before writing or after reading a
    message. It preserves the existing JSON shape used by logs and datasets.
    """

    validate_on_encode: bool = True
    validate_on_decode: bool = True

    profile_name: str = "json-simulation-etsi-like"
    content_type: str = "application/vnd.pps57.cits+json;profile=simulation"

    def encode(self, message: CITSMessage) -> str:
        try:
            if self.validate_on_encode:
                ensure_cits_message_valid(message)
            return json.dumps(message.to_dict(), ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError, KeyError) as exc:
            raise ProtocolCodecError(f"Invalid {self.profile_name} message: {exc}") from exc

    def decode(self, payload: str | bytes | Dict[str, Any]) -> CITSMessage:
        try:
            if isinstance(payload, bytes):
                raw = json.loads(payload.decode("utf-8"))
            elif isinstance(payload, str):
                raw = json.loads(payload)
            elif isinstance(payload, dict):
                raw = dict(payload)
            else:
                raise ProtocolCodecError(
                    f"Unsupported JSON simulation payload type: {type(payload).__name__}"
                )
        except ProtocolCodecError:
            raise
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise ProtocolCodecError(f"Invalid {self.profile_name} payload: {exc}") from exc

        if not isinstance(raw, dict):
            raise ProtocolCodecError(f"Invalid {self.profile_name} payload: expected JSON object")

        try:
            message = message_from_dict(raw)
            if self.validate_on_decode:
                errors = validate_cits_message(message)
                if errors:
                    raise ProtocolCodecError("; ".join(errors))
            return message
        except ProtocolCodecError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise ProtocolCodecError(f"Invalid {self.profile_name} payload: {exc}") from exc

    def validate(self, message: CITSMessage) -> List[str]:
        return validate_cits_message(message)


def message_from_dict(payload: Dict[str, Any]) -> CITSMessage:
    """Build a typed C-ITS message from the simulator JSON shape."""

    message_type = str(payload.get("message_type", ""))
    common = _common_kwargs(payload)
    if message_type == MessageType.MAPEM.value:
        return MAPEMLike(
            **common,
            intersection_ref_id=int(payload.get("intersection_ref_id", 0)),
            intersection_alias=str(payload.get("intersection_alias", "")),
            intersection_name=str(payload.get("intersection_name", "")),
            tls_id=str(payload.get("tls_id", "")),
            rsu_id=str(payload.get("rsu_id", "")),
            revision=int(payload.get("revision", 0)),
            ref_point=_position_or_none(payload.get("ref_point")),
            approaches=[
                _approach(item) for item in payload.get("approaches", []) if isinstance(item, dict)
            ],
        )
    if message_type == MessageType.SPATEM.value:
        return SPATEMLike(
            **common,
            intersection_ref_id=int(payload.get("intersection_ref_id", 0)),
            intersection_alias=str(payload.get("intersection_alias", "")),
            tls_id=str(payload.get("tls_id", "")),
            revision=int(payload.get("revision", 0)),
            movement_events=[
                _movement_event(item)
                for item in payload.get("movement_events", [])
                if isinstance(item, dict)
            ],
            intersection_status=dict(payload.get("intersection_status", {})),
            debug_sumo_state=_optional_str(payload.get("debug_sumo_state")),
        )
    if message_type == MessageType.SREM.value:
        return SREMLike(
            **common,
            sequence_number=int(payload.get("sequence_number", 0)),
            requests=[
                _signal_request(item)
                for item in payload.get("requests", [])
                if isinstance(item, dict)
            ],
            requestor=_requestor_or_none(payload.get("requestor")),
            operator_telemetry=_operator_telemetry_or_none(payload.get("operator_telemetry")),
            expires_at_s=_optional_float(payload.get("expires_at_s")),
        )
    if message_type == MessageType.SSEM.value:
        return SSEMLike(
            **common,
            intersection_ref_id=int(payload.get("intersection_ref_id", 0)),
            intersection_alias=str(payload.get("intersection_alias", "")),
            tls_id=str(payload.get("tls_id", "")),
            rsu_id=str(payload.get("rsu_id", "")),
            response=_prioritization_response_or_none(payload.get("response")),
            audit=_ssem_audit(payload.get("audit")),
        )
    raise ProtocolCodecError(f"Unsupported C-ITS message_type: {message_type!r}")


def _common_kwargs(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "message_type": str(payload.get("message_type", "")),
        "station_id": int(payload.get("station_id", 0)),
        "station_type": int(payload.get("station_type", 0)),
        "source_id": str(payload.get("source_id", "")),
        "destination_id": str(payload.get("destination_id", "")),
        "generation_delta_time_ms": int(payload.get("generation_delta_time_ms", 0)),
        "moy": int(payload.get("moy", 0)),
        "timestamp_ms": int(payload.get("timestamp_ms", 0)),
        "security": _security(payload.get("security")),
        "message_id": str(payload.get("message_id", "")),
        "protocol_version": str(payload.get("protocol_version", "0.4.0")),
        "correlation_id": _optional_str(payload.get("correlation_id")),
    }


def _security(payload: object) -> SecurityEnvelope:
    if not isinstance(payload, dict):
        return SecurityEnvelope("", "", None, 0, 0)
    return SecurityEnvelope(
        signer_id=str(payload.get("signer_id", "")),
        certificate_id=str(payload.get("certificate_id", "")),
        signature_b64=_optional_str(payload.get("signature_b64")),
        generation_time_ms=int(payload.get("generation_time_ms", 0)),
        valid_until_ms=int(payload.get("valid_until_ms", 0)),
    )


def _position(payload: object) -> Position3D:
    if not isinstance(payload, dict):
        return Position3D()
    return Position3D(
        latitude_e7=int(payload.get("latitude_e7", 0)),
        longitude_e7=int(payload.get("longitude_e7", 0)),
        elevation_dm=int(payload.get("elevation_dm", 0)),
    )


def _position_or_none(payload: object) -> Position3D | None:
    return _position(payload) if isinstance(payload, dict) else None


def _approach(payload: Dict[str, Any]) -> Approach:
    return Approach(
        approach_id=str(payload.get("approach_id", "")),
        edge_id=str(payload.get("edge_id", "")),
        direction=str(payload.get("direction", "")),
        priority_movement_ids=[str(item) for item in payload.get("priority_movement_ids", [])],
        lane_ids=[str(item) for item in payload.get("lane_ids", [])],
    )


def _movement_event(payload: Dict[str, Any]) -> MovementEvent:
    likely = payload.get("likely_time_ms")
    return MovementEvent(
        signal_group_id=int(payload.get("signal_group_id", 0)),
        event_state=str(payload.get("event_state", "")),
        min_end_time_ms=int(payload.get("min_end_time_ms", 0)),
        max_end_time_ms=int(payload.get("max_end_time_ms", 0)),
        likely_time_ms=int(likely) if likely is not None else None,
        confidence=int(payload.get("confidence", 0)),
    )


def _signal_request(payload: Dict[str, Any]) -> SignalRequest:
    return SignalRequest(
        intersection_ref_id=int(payload.get("intersection_ref_id", 0)),
        request_id=int(payload.get("request_id", 0)),
        request_type=str(payload.get("request_type", "")),
        in_bound_lane_id=str(payload.get("in_bound_lane_id", "")),
        out_bound_lane_id=str(payload.get("out_bound_lane_id", "")),
        eta_min_minute=int(payload.get("eta_min_minute", 0)),
        eta_min_second_ms=int(payload.get("eta_min_second_ms", 0)),
        duration_ms=int(payload.get("duration_ms", 0)),
    )


def _requestor_or_none(payload: object) -> Requestor | None:
    if not isinstance(payload, dict):
        return None
    return Requestor(
        station_id=int(payload.get("station_id", 0)),
        station_type=int(payload.get("station_type", 0)),
        basic_vehicle_role=str(payload.get("basic_vehicle_role", "")),
        position=_position(payload.get("position")),
        heading_deg=float(payload.get("heading_deg", 0.0)),
        speed_mps=float(payload.get("speed_mps", 0.0)),
        route_name=_optional_str(payload.get("route_name")),
        operational_vehicle_id=str(payload.get("operational_vehicle_id", "")),
    )


def _operator_telemetry_or_none(payload: object) -> OperatorTelemetry | None:
    if not isinstance(payload, dict):
        return None
    return OperatorTelemetry(
        schedule_delay_s=float(payload.get("schedule_delay_s", 0.0)),
        headway_deviation_s=float(payload.get("headway_deviation_s", 0.0)),
        distance_to_stopline_m=float(payload.get("distance_to_stopline_m", 0.0)),
        eta_to_stopline_s=float(payload.get("eta_to_stopline_s", 0.0)),
        eta_queue_delay_s=float(payload.get("eta_queue_delay_s", 0.0)),
        operator_priority_class=str(payload.get("operator_priority_class", "")),
        line_id=str(payload.get("line_id", "")),
        route_id=str(payload.get("route_id", "")),
        intersection_alias=str(payload.get("intersection_alias", "")),
        tls_id=str(payload.get("tls_id", "")),
        rsu_id=str(payload.get("rsu_id", "")),
        priority_movement_id=str(payload.get("priority_movement_id", "")),
        target_signal_group_id_hint=str(payload.get("target_signal_group_id_hint", "")),
        cancellation_reason=str(payload.get("cancellation_reason", "")),
    )


def _prioritization_response_or_none(payload: object) -> PrioritizationResponse | None:
    if not isinstance(payload, dict):
        return None
    granted = payload.get("granted_signal_group")
    return PrioritizationResponse(
        request_id=int(payload.get("request_id", 0)),
        sequence_number=int(payload.get("sequence_number", 0)),
        requestor_station_id=int(payload.get("requestor_station_id", 0)),
        response_status=str(payload.get("response_status", "")),
        granted_signal_group=int(granted) if granted is not None else None,
        valid_until_ms=int(payload.get("valid_until_ms", 0)),
    )


def _ssem_audit(payload: object) -> SSEMAudit:
    if not isinstance(payload, dict):
        return SSEMAudit()
    return SSEMAudit(
        granted_strategy=str(payload.get("granted_strategy", "")),
        rejection_reason=_optional_str(payload.get("rejection_reason")),
        confidence=float(payload.get("confidence", 1.0)),
        notes=[str(item) for item in payload.get("notes", [])],
    )


def _optional_str(value: object) -> str | None:
    return None if value is None else str(value)


def _optional_float(value: object) -> float | None:
    return None if value is None else float(value)
