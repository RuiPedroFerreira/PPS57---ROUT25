#!/usr/bin/env python3
"""Motor de decisão TSP multiobjetivo para pedidos SREM-like aceites pela RSU."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from pps57_cits.config import CITSConfig
from pps57_cits.messages import PriorityLevel, RequestedManeuver, SREMLike
from pps57_cits.models import SignalState

from .config import TSPConfig
from .models import DecisionStatus, TSPAction, TSPDecision


@dataclass
class TSPDecisionEngine:
    cits_config: CITSConfig
    tsp_config: TSPConfig

    def decide(self, request: SREMLike, signal_state: SignalState, sim_time_s: float) -> TSPDecision:
        score = self.priority_score(request)
        policy = self.tsp_config.decision_policy
        min_score = float(policy.get("min_priority_score", 0.35))
        if score < min_score:
            return self._decision(
                request,
                signal_state,
                sim_time_s,
                action=TSPAction.REJECT.value,
                reason=f"priority_score_below_threshold:{score:.3f}<{min_score:.3f}",
                score=score,
                notes=["Pedido aceite pela RSU, mas insuficiente para intervenção semafórica."],
            )

        if request.expires_at_s and sim_time_s > request.expires_at_s:
            return self._decision(
                request,
                signal_state,
                sim_time_s,
                action=TSPAction.REJECT.value,
                reason="request_expired_before_tsp_decision",
                score=score,
            )

        is_green = self.is_priority_movement_green(request, signal_state)
        remaining_s = self.remaining_phase_time_s(signal_state, sim_time_s)
        arrival_buffer_s = float(policy.get("eta_arrival_buffer_s", 4))

        if is_green:
            required_green_s = request.eta_to_stopline_s + arrival_buffer_s
            if remaining_s is not None and remaining_s >= required_green_s:
                return self._decision(
                    request,
                    signal_state,
                    sim_time_s,
                    action=TSPAction.NO_ACTION.value,
                    reason="green_window_already_sufficient",
                    score=score,
                    notes=[
                        f"remaining_green_s={remaining_s:.1f}",
                        f"required_green_s={required_green_s:.1f}",
                    ],
                )

            needed_extension = (
                required_green_s - remaining_s
                if remaining_s is not None
                else float(policy.get("green_extension_default_s", 8))
            )
            extension_s = max(
                float(policy.get("green_extension_min_s", 3)),
                min(float(policy.get("green_extension_max_s", 12)), needed_extension),
            )
            return self._decision(
                request,
                signal_state,
                sim_time_s,
                action=TSPAction.GREEN_EXTENSION.value,
                reason="extend_current_green_to_cover_bus_eta",
                score=score,
                extension_s=round(extension_s, 3),
                notes=[
                    "Ação proposta: extensão de verde da fase atual.",
                    f"remaining_green_s={remaining_s}",
                    f"bus_eta_s={request.eta_to_stopline_s:.1f}",
                ],
            )

        # Movimento prioritário não está em verde: propor early green/red truncation.
        if request.eta_to_stopline_s < float(policy.get("early_green_min_eta_s", 10)):
            return self._decision(
                request,
                signal_state,
                sim_time_s,
                action=TSPAction.REEVALUATE_NEXT_CYCLE.value,
                reason="bus_too_close_for_safe_red_truncation",
                score=score,
                notes=["Pedido será reavaliado no ciclo seguinte para evitar transição insegura."],
            )

        mapping = self.tsp_config.phase_mapping_for_tls(request.tls_id)
        target_phase = _optional_int(mapping.get("corridor_green_phase_index"))
        return self._decision(
            request,
            signal_state,
            sim_time_s,
            action=TSPAction.EARLY_GREEN.value,
            reason="truncate_conflicting_phase_to_anticipate_corridor_green",
            score=score,
            phase_duration_s=float(policy.get("red_truncation_to_s", 2)),
            target_phase_index=target_phase,
            notes=[
                "Ação proposta: early green através de redução da duração da fase corrente.",
                "Não é feito salto direto de fase; a sequência SUMO mantém amarelo/all-red se o plano os contiver.",
            ],
        )

    def priority_score(self, request: SREMLike) -> float:
        policy = self.tsp_config.decision_policy
        weights = policy.get("weights", {})
        delay_norm = _clip01(request.schedule_delay_s / float(policy.get("delay_normalisation_s", 180)))
        headway_norm = _clip01(abs(request.headway_deviation_s) / float(policy.get("headway_normalisation_s", 240)))
        proximity_norm = _clip01(1 - request.distance_to_stopline_m / float(policy.get("distance_normalisation_m", 250)))
        priority_norm = self._priority_level_weight(request.priority_level)
        score = (
            float(weights.get("schedule_delay", 0.45)) * delay_norm
            + float(weights.get("headway_deviation", 0.20)) * headway_norm
            + float(weights.get("proximity", 0.20)) * proximity_norm
            + float(weights.get("priority_level", 0.15)) * priority_norm
        )
        return round(_clip01(score), 4)

    def is_priority_movement_green(self, request: SREMLike, signal_state: SignalState) -> bool:
        ryg = signal_state.red_yellow_green_state or ""
        controlled_lanes = signal_state.controlled_lanes or []
        candidate_lane = request.current_lane_id
        candidate_edge = request.current_edge_id

        # Preferir mapeamento por lane/control link quando disponível.
        for index, lane_id in enumerate(controlled_lanes):
            if index >= len(ryg):
                continue
            if lane_id == candidate_lane or lane_id.startswith(candidate_edge):
                return ryg[index].lower() == "g"

        # Fallback: fase 0 representa corredor verde no proxy Boavista.
        mapping = self.tsp_config.phase_mapping_for_tls(request.tls_id)
        corridor_phase = _optional_int(mapping.get("corridor_green_phase_index"))
        return corridor_phase is not None and signal_state.current_phase_index == corridor_phase

    @staticmethod
    def remaining_phase_time_s(signal_state: SignalState, sim_time_s: float) -> Optional[float]:
        if signal_state.next_switch_s is None:
            return None
        return max(0.0, float(signal_state.next_switch_s) - sim_time_s)

    def _priority_level_weight(self, priority_level: str) -> float:
        if priority_level == PriorityLevel.EMERGENCY_VEHICLE.value:
            return 1.0
        if priority_level == PriorityLevel.PUBLIC_TRANSPORT_HIGH_DELAY.value:
            return 0.85
        if priority_level == PriorityLevel.PUBLIC_TRANSPORT_HEADWAY_RECOVERY.value:
            return 0.70
        if priority_level == PriorityLevel.PUBLIC_TRANSPORT_NOMINAL.value:
            return 0.45
        return 0.0

    def _decision(
        self,
        request: SREMLike,
        signal_state: SignalState,
        sim_time_s: float,
        *,
        action: str,
        reason: str,
        score: float,
        extension_s: float = 0.0,
        phase_duration_s: Optional[float] = None,
        target_phase_index: Optional[int] = None,
        notes: Optional[list[str]] = None,
    ) -> TSPDecision:
        return TSPDecision(
            timestamp_s=sim_time_s,
            request_id=request.request_id,
            vehicle_id=request.vehicle_id,
            intersection_id=request.intersection_id,
            tls_id=request.tls_id,
            rsu_id=request.rsu_id,
            action=action,
            status=DecisionStatus.PROPOSED.value,
            reason=reason,
            priority_score=score,
            requested_maneuver=request.requested_maneuver or RequestedManeuver.PRIORITY_CANDIDATE.value,
            eta_to_stopline_s=request.eta_to_stopline_s,
            schedule_delay_s=request.schedule_delay_s,
            headway_deviation_s=request.headway_deviation_s,
            extension_s=extension_s,
            phase_duration_s=phase_duration_s,
            target_phase_index=target_phase_index,
            current_phase_index=signal_state.current_phase_index,
            current_signal_state=signal_state.red_yellow_green_state,
            notes=list(notes or []),
            correlation_id=request.message_id,
        )


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _optional_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
