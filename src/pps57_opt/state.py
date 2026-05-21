#!/usr/bin/env python3
"""State bucketing shared by offline training and runtime policy inference."""
from __future__ import annotations

from typing import Any, Dict, Optional

from pps57_cits.messages import SREMLike
from pps57_cits.models import SignalState
from pps57_cits.util import optional_int as _optional_int
from pps57_tsp.config import TSPConfig
from pps57_tsp.engine import TSPDecisionEngine


def state_bucket_for_context(
    tsp_config: TSPConfig,
    bucket_config: Dict[str, Any],
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
    seconds_since_last_intervention_s: Optional[float] = None,
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

    corridor_phase = _optional_int(tsp_config.phase_mapping_for_tls(signal_state.tls_id).get("corridor_green_phase_index"))
    phase_bucket = "phase_unknown"
    if signal_state.red_yellow_green_state and "y" in signal_state.red_yellow_green_state.lower():
        phase_bucket = "yellow"
    elif corridor_phase is not None and signal_state.current_phase_index == corridor_phase:
        phase_bucket = "corridor_green"
    elif signal_state.current_phase_index is not None:
        phase_bucket = "corridor_red"

    switch_bucket = "switch_close" if remaining is not None and remaining <= switch_close else "switch_open"
    pressure_bucket = "traffic_pressure_high" if (
        active_request_count >= high_active_requests
        or queue_vehicle_count >= high_queue
        or halted_vehicle_count >= high_halted
        or (mean_speed_mps > 0.0 and mean_speed_mps <= low_speed)
        or waiting_time_s >= high_waiting
        or occupancy >= high_occupancy
        or spillback_risk
    ) else "traffic_pressure_low"
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
