#!/usr/bin/env python3
"""State bucketing shared by offline training and runtime policy inference."""

from __future__ import annotations

from typing import Any

from pps57_cits.messages import SREMLike
from pps57_cits.models import SignalState
from pps57_cits.util import optional_int as _optional_int
from pps57_tsp.config import TSPConfig
from pps57_tsp.engine import TSPDecisionEngine


def state_bucket_for_context(
    tsp_config: TSPConfig,
    bucket_config: dict[str, Any],
    request: SREMLike,
    signal_state: SignalState,
    sim_time_s: float,
    *,
    active_request_count: int = 1,
    queue_vehicle_count: int = 0,
    halted_vehicle_count: int = 0,
    mean_speed_mps: float = 0.0,
    waiting_time_s: float = 0.0,
    occupancy: float = 0.0,
    spillback_risk: bool = False,
    seconds_since_last_intervention_s: float | None = None,
) -> str:
    eta_close = float(bucket_config.get("eta_close_s", 10))
    eta_far = float(bucket_config.get("eta_far_s", 25))
    delay_medium = float(bucket_config.get("medium_delay_s", 60))
    delay_high = float(bucket_config.get("high_delay_s", 90))
    switch_close = float(bucket_config.get("phase_switch_close_s", 5))
    high_active_requests = int(bucket_config.get("high_active_requests", 2))
    high_queue = int(bucket_config.get("high_queue_vehicle_count", 8))
    high_halted = int(bucket_config.get("high_halted_vehicle_count", high_queue))
    low_speed = float(bucket_config.get("low_mean_speed_mps", 2.0))
    high_waiting = float(bucket_config.get("high_waiting_time_s", 120))
    high_occupancy = float(bucket_config.get("high_occupancy", 0.6))
    cooldown_recent = float(bucket_config.get("cooldown_recent_s", 90))

    remaining = TSPDecisionEngine.remaining_phase_time_s(signal_state, sim_time_s)
    eta_bucket = "eta_close" if request.eta_to_stopline_s <= eta_close else "eta_mid"
    if request.eta_to_stopline_s >= eta_far:
        eta_bucket = "eta_far"

    if request.schedule_delay_s >= delay_high:
        delay_bucket = "delay_high"
    elif request.schedule_delay_s >= delay_medium:
        delay_bucket = "delay_medium"
    else:
        delay_bucket = "delay_low"

    phase_bucket = _movement_phase_bucket(request, signal_state)
    if phase_bucket == "phase_unknown":
        mapping = tsp_config.phase_mapping_for_movement(
            request.priority_movement_id, signal_state.tls_id
        )
        target_phase = _optional_int(mapping.get("target_phase_index"))
        if target_phase is not None and signal_state.current_phase_index == target_phase:
            phase_bucket = "priority_movement_green"
        elif signal_state.current_phase_index is not None:
            phase_bucket = "priority_movement_not_green"

    if (
        phase_bucket == "phase_unknown"
        and signal_state.red_yellow_green_state
        and "y" in signal_state.red_yellow_green_state.lower()
    ):
        phase_bucket = "yellow"

    switch_bucket = (
        "switch_close" if remaining is not None and remaining <= switch_close else "switch_open"
    )
    pressure_bucket = (
        "traffic_pressure_high"
        if (
            active_request_count >= high_active_requests
            or queue_vehicle_count >= high_queue
            or halted_vehicle_count >= high_halted
            or (mean_speed_mps > 0.0 and mean_speed_mps <= low_speed)
            or waiting_time_s >= high_waiting
            or occupancy >= high_occupancy
            or spillback_risk
        )
        else "traffic_pressure_low"
    )
    if seconds_since_last_intervention_s is None:
        intervention_bucket = "intervention_unknown"
    elif seconds_since_last_intervention_s < cooldown_recent:
        intervention_bucket = "intervention_recent"
    else:
        intervention_bucket = "intervention_clear"

    return "|".join(
        [
            phase_bucket,
            eta_bucket,
            delay_bucket,
            switch_bucket,
            pressure_bucket,
            intervention_bucket,
        ]
    )


def _movement_phase_bucket(request: SREMLike, signal_state: SignalState) -> str:
    ryg = signal_state.red_yellow_green_state or ""
    controlled_links = signal_state.controlled_links or []
    next_edge = getattr(request, "next_edge_id", "") or ""
    for index, links_for_signal in enumerate(controlled_links):
        if index >= len(ryg):
            continue
        if _controlled_links_match_request(links_for_signal, request.current_lane_id, next_edge):
            return _bucket_for_signal_char(ryg[index])

    controlled_lanes = signal_state.controlled_lanes or []
    for index, lane_id in enumerate(controlled_lanes):
        if index >= len(ryg):
            continue
        if lane_id == request.current_lane_id:
            return _bucket_for_signal_char(ryg[index])
    return "phase_unknown"


def _bucket_for_signal_char(char: str) -> str:
    if char.lower() == "y":
        return "yellow"
    if char == "G":
        return "priority_movement_green"
    if char == "g":
        return "priority_movement_permissive_green"
    if char.lower() == "r":
        return "priority_movement_not_green"
    return "phase_unknown"


def _controlled_links_match_request(
    links_for_signal: object, lane_id: str, next_edge_id: str
) -> bool:
    if not lane_id or not isinstance(links_for_signal, list):
        return False
    for link in links_for_signal:
        if not isinstance(link, (list, tuple)) or len(link) < 2:
            continue
        incoming_lane = str(link[0])
        outgoing_lane = str(link[1])
        if incoming_lane != lane_id:
            continue
        if not next_edge_id:
            return True
        if outgoing_lane == next_edge_id or outgoing_lane.startswith(f"{next_edge_id}_"):
            return True
    return False
