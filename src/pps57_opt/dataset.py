#!/usr/bin/env python3
"""Cenários offline sintéticos para treinar políticas do Pacote 5."""
from __future__ import annotations

from typing import List

from pps57_cits.config import CITSConfig, IntersectionConfig
from pps57_cits.messages import PriorityLevel, RequestedManeuver, SREMLike
from pps57_cits.models import SignalState

from .models import OfflineScenario


def build_offline_scenarios(config: CITSConfig) -> List[OfflineScenario]:
    intersections = {item.tls_id: item for item in config.intersections}
    return [
        OfflineScenario(
            scenario_id="P5_GREEN_EXTENSION_SHORT_GREEN",
            description="Autocarro atrasado chegaria no fim do verde do corredor.",
            expected_case="green_extension",
            sim_time_s=100.0,
            request=_request(intersections["I2"], edge_id="I1_I2", lane_id="I1_I2_0", eta=16.0, distance=160.0),
            signal_state=_state(intersections["I2"], phase=0, ryg="GGrr", next_switch=102.0, spent=33.0),
        ),
        OfflineScenario(
            scenario_id="P5_NO_ACTION_GREEN_SUFFICIENT",
            description="Verde atual cobre ETA e buffer sem atuação.",
            expected_case="no_action",
            sim_time_s=100.0,
            request=_request(intersections["I4"], edge_id="I3_I4", lane_id="I3_I4_0", eta=10.0, distance=80.0),
            signal_state=_state(intersections["I4"], phase=0, ryg="GGrr", next_switch=140.0, spent=5.0),
        ),
        OfflineScenario(
            scenario_id="P5_EARLY_GREEN_SAFE_RED",
            description="Corredor está vermelho e fase conflitante já cumpriu verde mínimo.",
            expected_case="early_green",
            sim_time_s=100.0,
            request=_request(
                intersections["I6"],
                edge_id="I7_I6",
                lane_id="I7_I6_0",
                eta=18.0,
                distance=171.0,
                maneuver=RequestedManeuver.EARLY_GREEN.value,
            ),
            signal_state=_state(intersections["I6"], phase=2, ryg="rrGG", next_switch=125.0, spent=20.0),
        ),
        OfflineScenario(
            scenario_id="P5_REEVALUATE_TOO_CLOSE",
            description="Autocarro está demasiado perto para truncagem segura.",
            expected_case="reevaluate_next_cycle",
            sim_time_s=100.0,
            request=_request(
                intersections["I6"],
                edge_id="I7_I6",
                lane_id="I7_I6_0",
                eta=9.0,
                distance=85.5,
                maneuver=RequestedManeuver.EARLY_GREEN.value,
            ),
            signal_state=_state(intersections["I6"], phase=2, ryg="rrGG", next_switch=125.0, spent=20.0),
        ),
        OfflineScenario(
            scenario_id="P5_REJECT_LOW_SCORE",
            description="Pedido aceite para análise, mas score TSP é insuficiente.",
            expected_case="reject",
            sim_time_s=100.0,
            request=_request(
                intersections["I2"],
                edge_id="I1_I2",
                lane_id="I1_I2_0",
                eta=16.0,
                distance=250.0,
                delay=0.0,
                priority=PriorityLevel.PUBLIC_TRANSPORT_NOMINAL.value,
            ),
            signal_state=_state(intersections["I2"], phase=0, ryg="GGrr", next_switch=102.0, spent=33.0),
        ),
        OfflineScenario(
            scenario_id="P5_YELLOW_TRANSITION_BLOCK",
            description="Fase atual está em amarelo; qualquer atuação deve ser filtrada.",
            expected_case="safety_filter",
            sim_time_s=100.0,
            request=_request(intersections["I2"], edge_id="I1_I2", lane_id="I1_I2_0", eta=16.0, distance=160.0),
            signal_state=_state(intersections["I2"], phase=1, ryg="yyrr", next_switch=103.0, spent=1.0),
        ),
        OfflineScenario(
            scenario_id="P5_MAX_GREEN_REACHED",
            description="Verde total já está no limite; extensão deve ser filtrada.",
            expected_case="safety_filter",
            sim_time_s=100.0,
            request=_request(intersections["I2"], edge_id="I1_I2", lane_id="I1_I2_0", eta=16.0, distance=160.0),
            signal_state=_state(intersections["I2"], phase=0, ryg="GGrr", next_switch=102.0, spent=53.0),
        ),
        # M7: exercitar o caminho stateful (cooldown) da Safety Layer offline.
        OfflineScenario(
            scenario_id="P5_COOLDOWN_ACTIVE",
            description="Cooldown da intervenção anterior ainda ativo; extensão deve ser filtrada.",
            expected_case="safety_filter",
            sim_time_s=100.0,
            request=_request(intersections["I2"], edge_id="I1_I2", lane_id="I1_I2_0", eta=16.0, distance=160.0),
            signal_state=_state(intersections["I2"], phase=0, ryg="GGrr", next_switch=130.0, spent=10.0),
            initial_last_intervention_time_by_tls={"I2": 95.0},  # 5s atrás, cooldown=90s
        ),
        # M7: exercitar o limite de intervenções consecutivas (no max_consecutive=2).
        OfflineScenario(
            scenario_id="P5_MAX_CONSECUTIVE_REACHED",
            description="Já houve 2 intervenções consecutivas neste TLS; extensão deve ser filtrada.",
            expected_case="safety_filter",
            sim_time_s=100.0,
            request=_request(intersections["I2"], edge_id="I1_I2", lane_id="I1_I2_0", eta=16.0, distance=160.0),
            signal_state=_state(intersections["I2"], phase=0, ryg="GGrr", next_switch=130.0, spent=10.0),
            initial_consecutive_interventions_by_tls={"I2": 2},
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
    maneuver: str = RequestedManeuver.GREEN_EXTENSION.value,
    priority: str = PriorityLevel.PUBLIC_TRANSPORT_HIGH_DELAY.value,
) -> SREMLike:
    return SREMLike(
        source_id=f"OBU_bus_{intersection.tls_id}",
        destination_id=intersection.rsu_id,
        timestamp_s=100.0,
        vehicle_id=f"bus_{intersection.tls_id}",
        vehicle_class="bus",
        line_id="STCP500_PROXY_W",
        route_id="route_boavista_proxy",
        intersection_id=intersection.intersection_id,
        tls_id=intersection.tls_id,
        rsu_id=intersection.rsu_id,
        current_edge_id=edge_id,
        current_lane_id=lane_id,
        speed_mps=max(distance / max(eta, 0.1), 0.1),
        distance_to_stopline_m=distance,
        eta_to_stopline_s=eta,
        schedule_delay_s=delay,
        headway_deviation_s=headway,
        requested_maneuver=maneuver,
        priority_level=priority,
        expires_at_s=130.0,
    )


def _state(intersection: IntersectionConfig, *, phase: int, ryg: str, next_switch: float, spent: float) -> SignalState:
    return SignalState(
        intersection_id=intersection.intersection_id,
        tls_id=intersection.tls_id,
        rsu_id=intersection.rsu_id,
        timestamp_s=100.0,
        current_phase_index=phase,
        current_program_id="pacote5_offline_proxy",
        red_yellow_green_state=ryg,
        next_switch_s=next_switch,
        spent_duration_s=spent,
        controlled_lanes=[f"{edge}_0" for edge in intersection.controlled_approach_edges],
    )
