#!/usr/bin/env python3
"""Unit-test fixture scenarios for TSP policy optimization and RL training.

Runtime optimization/training loads event-derived scenarios from SUMO/TraCI logs.
These fixtures are injected explicitly by tests and are not a production data
source.
"""

from __future__ import annotations

from pps57_cits.config import CITSConfig, IntersectionConfig
from pps57_cits.messages import OperatorPriorityClass, SREMLike, synth_srem
from pps57_cits.models import SignalState

from .models import OfflineScenario


def build_offline_scenarios(config: CITSConfig) -> list[OfflineScenario]:
    intersections = {item.tls_id: item for item in config.intersections}
    return [
        OfflineScenario(
            scenario_id="OPT_GREEN_EXTENSION_SHORT_GREEN",
            description="Delayed public-transport vehicle reaches the stop line near the end of the priority movement green.",
            expected_case="green_extension",
            sim_time_s=100.0,
            request=_request(
                intersections["I2"], edge_id="I1_I2", lane_id="I1_I2_0", eta=16.0, distance=160.0
            ),
            signal_state=_state(
                intersections["I2"], phase=0, ryg="GGrr", next_switch=102.0, spent=33.0
            ),
        ),
        OfflineScenario(
            scenario_id="OPT_NO_ACTION_GREEN_SUFFICIENT",
            description="Current green covers ETA plus buffer without intervention.",
            expected_case="no_action",
            sim_time_s=100.0,
            request=_request(
                intersections["I4"], edge_id="I3_I4", lane_id="I3_I4_0", eta=10.0, distance=80.0
            ),
            signal_state=_state(
                intersections["I4"], phase=0, ryg="GGrr", next_switch=140.0, spent=5.0
            ),
        ),
        OfflineScenario(
            scenario_id="OPT_EARLY_GREEN_SAFE_RED",
            description="Corridor is red and the conflicting phase has already met minimum green.",
            expected_case="early_green",
            sim_time_s=100.0,
            request=_request(
                intersections["I6"],
                edge_id="I7_I6",
                lane_id="I7_I6_0",
                eta=18.0,
                distance=171.0,
            ),
            signal_state=_state(
                intersections["I6"], phase=2, ryg="rrGG", next_switch=125.0, spent=20.0
            ),
        ),
        OfflineScenario(
            scenario_id="OPT_REEVALUATE_TOO_CLOSE",
            description="Bus is too close for safe red truncation.",
            expected_case="reevaluate_next_cycle",
            sim_time_s=100.0,
            request=_request(
                intersections["I6"],
                edge_id="I7_I6",
                lane_id="I7_I6_0",
                eta=9.0,
                distance=85.5,
            ),
            signal_state=_state(
                intersections["I6"], phase=2, ryg="rrGG", next_switch=125.0, spent=20.0
            ),
        ),
        OfflineScenario(
            scenario_id="OPT_REJECT_LOW_SCORE",
            description="Request is valid, but the TSP priority score is insufficient.",
            expected_case="reject",
            sim_time_s=100.0,
            request=_request(
                intersections["I2"],
                edge_id="I1_I2",
                lane_id="I1_I2_0",
                eta=16.0,
                distance=250.0,
                delay=0.0,
                priority=OperatorPriorityClass.NOMINAL.value,
            ),
            signal_state=_state(
                intersections["I2"], phase=0, ryg="GGrr", next_switch=102.0, spent=33.0
            ),
        ),
        OfflineScenario(
            scenario_id="OPT_YELLOW_TRANSITION_BLOCK",
            description="Current phase is yellow; every actuation must be filtered.",
            expected_case="safety_filter",
            sim_time_s=100.0,
            request=_request(
                intersections["I2"], edge_id="I1_I2", lane_id="I1_I2_0", eta=16.0, distance=160.0
            ),
            signal_state=_state(
                intersections["I2"], phase=1, ryg="yyrr", next_switch=103.0, spent=1.0
            ),
        ),
        OfflineScenario(
            scenario_id="OPT_MAX_GREEN_REACHED",
            description="Total green is already at the limit; extension must be filtered.",
            expected_case="safety_filter",
            sim_time_s=100.0,
            request=_request(
                intersections["I2"], edge_id="I1_I2", lane_id="I1_I2_0", eta=16.0, distance=160.0
            ),
            signal_state=_state(
                intersections["I2"], phase=0, ryg="GGrr", next_switch=102.0, spent=53.0
            ),
        ),
        OfflineScenario(
            scenario_id="OPT_COOLDOWN_ACTIVE",
            description="Previous intervention cooldown is still active; extension must be filtered.",
            expected_case="safety_filter",
            sim_time_s=100.0,
            request=_request(
                intersections["I2"], edge_id="I1_I2", lane_id="I1_I2_0", eta=16.0, distance=160.0
            ),
            signal_state=_state(
                intersections["I2"], phase=0, ryg="GGrr", next_switch=130.0, spent=10.0
            ),
            initial_last_intervention_time_by_tls={"I2": 95.0},
        ),
        OfflineScenario(
            scenario_id="OPT_MAX_CONSECUTIVE_REACHED",
            description="The TLS has reached its consecutive intervention limit; extension must be filtered.",
            expected_case="safety_filter",
            sim_time_s=100.0,
            request=_request(
                intersections["I2"], edge_id="I1_I2", lane_id="I1_I2_0", eta=16.0, distance=160.0
            ),
            signal_state=_state(
                intersections["I2"], phase=0, ryg="GGrr", next_switch=130.0, spent=10.0
            ),
            initial_consecutive_interventions_by_tls={"I2": 2},
        ),
        OfflineScenario(
            scenario_id="OPT_HIGH_TRAFFIC_PRESSURE_REEVALUATE",
            description="Baseline would extend green, but high conflicting traffic pressure makes reevaluation preferable.",
            expected_case="traffic_pressure_reevaluate",
            sim_time_s=100.0,
            request=_request(
                intersections["I5"],
                edge_id="I4_I5",
                lane_id="I4_I5_0",
                eta=18.0,
                distance=180.0,
                delay=90.0,
            ),
            signal_state=_state(
                intersections["I5"], phase=0, ryg="GGrr", next_switch=102.0, spent=33.0
            ),
            active_request_count=3,
            queue_vehicle_count=14,
            halted_vehicle_count=10,
            mean_speed_mps=1.2,
            waiting_time_s=180.0,
            occupancy=0.82,
            spillback_risk=True,
        ),
    ]


def _request(
    intersection: IntersectionConfig,
    *,
    edge_id: str,
    lane_id: str,
    eta: float,
    distance: float,
    delay: float = 120.0,
    headway: float = 0.0,
    priority: str = OperatorPriorityClass.HIGH_DELAY.value,
) -> SREMLike:
    movement = next(
        (item for item in intersection.priority_movements if edge_id in item.approach_edges),
        None,
    )
    return synth_srem(
        sim_time_s=100.0,
        vehicle_id=f"bus_{intersection.tls_id}",
        intersection_alias=intersection.intersection_id,
        tls_id=intersection.tls_id,
        rsu_id=intersection.rsu_id,
        lane_id=lane_id,
        line_id="STCP500_PROXY_W",
        route_id="route_boavista_proxy",
        eta_to_stopline_s=eta,
        distance_to_stopline_m=distance,
        speed_mps=max(distance / max(eta, 0.1), 0.1),
        schedule_delay_s=delay,
        headway_deviation_s=headway,
        operator_priority_class=priority,
        priority_movement_id=movement.movement_id if movement is not None else "",
        target_signal_group_id_hint=movement.target_signal_group_id if movement is not None else "",
        expires_at_s=130.0,
    )


def _state(
    intersection: IntersectionConfig, *, phase: int, ryg: str, next_switch: float, spent: float
) -> SignalState:
    return SignalState(
        intersection_id=intersection.intersection_id,
        tls_id=intersection.tls_id,
        rsu_id=intersection.rsu_id,
        timestamp_s=100.0,
        current_phase_index=phase,
        current_program_id="policy_optimization_offline",
        red_yellow_green_state=ryg,
        next_switch_s=next_switch,
        spent_duration_s=spent,
        controlled_lanes=[f"{edge}_0" for edge in intersection.controlled_approach_edges],
    )
