#!/usr/bin/env python3
"""Construção de MAPEM e SPATEM para as RSUs do corredor (ETSI TS 103 301-2)."""
from __future__ import annotations

from typing import List

from .config import CITSConfig
from .messages import (
    Approach,
    EventState,
    MAPEMLike,
    MovementEvent,
    SPATEMLike,
    StationType,
    Position3D,
    build_security_envelope,
    derive_station_id,
    parse_intersection_ref_id,
    sim_time_to_cdd,
    sumo_link_char_to_event_state,
)
from .messages import MessageType
from .models import SignalState


def direction_for_edge(edge_id: str) -> str:
    if edge_id.startswith("N_"):
        return "north_to_south"
    if edge_id.startswith("S_"):
        return "south_to_north"
    if "ATLANTIC_WEST" in edge_id or edge_id.endswith("_I7"):
        return "eastbound"
    parts = edge_id.split("_")
    if len(parts) >= 2 and parts[0].startswith("I") and parts[1].startswith("I"):
        try:
            start = int(parts[0][1:])
            end = int(parts[1][1:])
            return "westbound" if end > start else "eastbound"
        except ValueError:
            pass
    return "unknown"


def build_mapem_messages(config: CITSConfig, sim_time_s: float = 0.0) -> List[MAPEMLike]:
    """Constrói um MAPEM por interseção a partir do catálogo do operador.

    MAP standard exige `revision` (mandatório) e `refPoint` (lat/lon âncora).
    O catálogo atual não traz lat/lon — `ref_point=None` é honesto: indica
    que o âncora geográfica vem da config quando estiver disponível. Numa
    operação real isto é uma falha de integração que o operador resolve.
    """
    moy, timestamp_ms, generation_delta = sim_time_to_cdd(sim_time_s)
    messages: List[MAPEMLike] = []
    for intersection_index, intersection in enumerate(config.intersections):
        approaches = [
            Approach(
                approach_id=f"{intersection.intersection_id}:{edge_id}",
                edge_id=edge_id,
                direction=direction_for_edge(edge_id),
                priority_movement_ids=[
                    movement.movement_id
                    for movement in intersection.priority_movements
                    if intersection.signal_controlled and edge_id in movement.approach_edges
                ],
                lane_ids=[f"{edge_id}_0"],
            )
            for edge_id in intersection.controlled_approach_edges
        ]
        station_id = derive_station_id(intersection.rsu_id)
        security = build_security_envelope(intersection.rsu_id, sim_time_s)
        messages.append(
            MAPEMLike(
                message_type=MessageType.MAPEM.value,
                station_id=station_id,
                station_type=StationType.ROAD_SIDE_UNIT.value,
                source_id=intersection.rsu_id,
                destination_id="BROADCAST",
                generation_delta_time_ms=generation_delta,
                moy=moy,
                timestamp_ms=timestamp_ms,
                security=security,
                intersection_ref_id=parse_intersection_ref_id(intersection.intersection_id),
                intersection_alias=intersection.intersection_id,
                intersection_name=intersection.name,
                tls_id=intersection.tls_id,
                rsu_id=intersection.rsu_id,
                revision=1,
                ref_point=_synthetic_ref_point(config, intersection_index),
                approaches=approaches,
            )
        )
    return messages


def _synthetic_ref_point(config: CITSConfig, intersection_index: int) -> Position3D | None:
    geometry = config.raw.get("synthetic_geometry", {})
    if not bool(geometry.get("enabled", False)):
        return None
    origin_lat = int(geometry.get("origin_latitude_e7", 0))
    origin_lon = int(geometry.get("origin_longitude_e7", 0))
    spacing = int(geometry.get("intersection_spacing_e7", 0))
    lateral = int(geometry.get("lateral_offset_e7", 0))
    elevation = int(geometry.get("elevation_dm", 0))
    return Position3D(
        latitude_e7=origin_lat + (intersection_index * lateral),
        longitude_e7=origin_lon + (intersection_index * spacing),
        elevation_dm=elevation,
    )


def build_spatem_message_from_state(state: SignalState) -> SPATEMLike:
    """Constrói SPATEM com `MovementEvent` por signalGroup.

    Mapeamento: cada link controlado pelo TLS SUMO mapeia para um signalGroup
    indexado a partir de 1 (CDD requer signalGroupId >= 0 e tipicamente
    operadores começam em 1). O `event_state` deriva do char do estado SUMO
    via `sumo_link_char_to_event_state`. A janela de timing usa `next_switch`
    como `min == max == likely` (programa fixo; um TLS atuado expandiria
    a janela em max-min com a folga extensível).

    A string SUMO crua sobrevive em `debug_sumo_state` para correlação visual,
    explicitamente como extensão não-standard.
    """
    moy, timestamp_ms, generation_delta = sim_time_to_cdd(state.timestamp_s)
    ryg = state.red_yellow_green_state or ""

    if state.next_switch_s is not None:
        remaining_ms = max(0, int(round((state.next_switch_s - state.timestamp_s) * 1000)))
    else:
        remaining_ms = 0

    movement_events: List[MovementEvent] = []
    intersection_status: dict[str, bool] = {}
    for link_index, char in enumerate(ryg):
        if link_index >= 255:
            # signal_group_id = link_index + 1; ASN.1 signalGroupID range is 1–255.
            # Links beyond index 254 are silently dropped so the SPATEM remains valid
            # for all reachable junction sizes (max observed: ~25 links/TLS).
            break
        movement_events.append(
            MovementEvent(
                signal_group_id=link_index + 1,
                event_state=sumo_link_char_to_event_state(char),
                min_end_time_ms=remaining_ms,
                max_end_time_ms=remaining_ms,
                likely_time_ms=remaining_ms,
                confidence=15 if state.next_switch_s is not None else 0,
            )
        )
    if not movement_events:
        # Leitura TLS degradada neste tick (getRedYellowGreenState falhou):
        # em vez de produzir um SPATEM inválido (movement_events vazio, que o
        # codec rejeita), declara explicitamente a indisponibilidade com o
        # flag standard e um MovementEvent `unavailable` — o broadcast
        # continua e os consumidores sabem que não há SPAT válido agora.
        intersection_status["noValidSPATisAvailableAtThisTime"] = True
        movement_events.append(
            MovementEvent(
                signal_group_id=1,
                event_state=EventState.UNAVAILABLE.value,
                min_end_time_ms=0,
                max_end_time_ms=0,
                likely_time_ms=0,
                confidence=0,
            )
        )

    station_id = derive_station_id(state.rsu_id)
    security = build_security_envelope(state.rsu_id, state.timestamp_s)
    return SPATEMLike(
        message_type=MessageType.SPATEM.value,
        station_id=station_id,
        station_type=StationType.ROAD_SIDE_UNIT.value,
        source_id=state.rsu_id,
        destination_id="BROADCAST",
        generation_delta_time_ms=generation_delta,
        moy=moy,
        timestamp_ms=timestamp_ms,
        security=security,
        intersection_ref_id=parse_intersection_ref_id(state.intersection_id),
        intersection_alias=state.intersection_id,
        tls_id=state.tls_id,
        revision=1,
        movement_events=movement_events,
        intersection_status=intersection_status,
        debug_sumo_state=ryg or None,
    )
