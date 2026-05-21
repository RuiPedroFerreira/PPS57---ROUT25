#!/usr/bin/env python3
"""Geração de MAPEM-like e SPATEM-like para as RSUs do corredor."""
from __future__ import annotations

from typing import List

from .config import CITSConfig
from .messages import Approach, MAPEMLike, SPATEMLike
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
    messages: List[MAPEMLike] = []
    for intersection in config.intersections:
        approaches = [
            Approach(
                approach_id=f"{intersection.intersection_id}:{edge_id}",
                edge_id=edge_id,
                direction=direction_for_edge(edge_id),
                priority_movement_ids=[
                    movement.movement_id
                    for movement in intersection.priority_movements
                    if edge_id in movement.approach_edges
                ],
                lane_ids=[f"{edge_id}_0"],
            )
            for edge_id in intersection.controlled_approach_edges
        ]
        messages.append(
            MAPEMLike(
                source_id=intersection.rsu_id,
                destination_id="BROADCAST",
                timestamp_s=sim_time_s,
                intersection_id=intersection.intersection_id,
                tls_id=intersection.tls_id,
                rsu_id=intersection.rsu_id,
                intersection_name=intersection.name,
                approaches=approaches,
            )
        )
    return messages


def build_spatem_message_from_state(state: SignalState) -> SPATEMLike:
    return SPATEMLike(
        source_id=state.rsu_id,
        destination_id="BROADCAST",
        timestamp_s=state.timestamp_s,
        intersection_id=state.intersection_id,
        tls_id=state.tls_id,
        current_phase_index=state.current_phase_index,
        current_program_id=state.current_program_id,
        red_yellow_green_state=state.red_yellow_green_state,
        next_switch_s=state.next_switch_s,
        spent_duration_s=state.spent_duration_s,
        controlled_lanes=state.controlled_lanes,
    )
