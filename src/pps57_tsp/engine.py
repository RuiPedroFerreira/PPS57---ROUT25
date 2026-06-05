#!/usr/bin/env python3
"""Motor de decisão TSP multiobjetivo para pedidos SREM-like aceites pela RSU."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from pps57_cits.config import CITSConfig
from pps57_cits.messages import OperatorPriorityClass, SREMLike
from pps57_cits.models import SignalState
from pps57_cits.util import optional_int as _optional_int

from .config import TSPConfig
from .models import DecisionStatus, TSPAction, TSPDecision
from .signal_control import SignalGroupContract, build_controller_contract


@dataclass
class TSPDecisionEngine:
    cits_config: CITSConfig
    tsp_config: TSPConfig

    def decide(self, request: SREMLike, signal_state: SignalState, sim_time_s: float) -> TSPDecision:
        score = self.priority_score(request)
        policy = self.tsp_config.decision_policy
        min_score = _positive_float(policy, "min_priority_score", 0.35)
        emergency_request = request.priority_level == OperatorPriorityClass.EMERGENCY.value

        if request.is_cancellation:
            return self._decision(
                request,
                signal_state,
                sim_time_s,
                action=TSPAction.REJECT.value,
                reason="priority_request_cancellation_no_tsp_actuation",
                score=score,
            )
        if request.expires_at_s is not None and sim_time_s > request.expires_at_s:
            return self._decision(
                request,
                signal_state,
                sim_time_s,
                action=TSPAction.REJECT.value,
                reason="request_expired_before_tsp_decision",
                score=score,
            )

        if score < min_score and not emergency_request:
            return self._decision(
                request,
                signal_state,
                sim_time_s,
                action=TSPAction.REJECT.value,
                reason=f"priority_score_below_threshold:{score:.3f}<{min_score:.3f}",
                score=score,
                notes=["Pedido aceite pela RSU, mas insuficiente para intervenção semafórica."],
            )

        is_green = self.is_priority_movement_green(request, signal_state)
        remaining_s = self.remaining_phase_time_s(signal_state, sim_time_s)
        arrival_buffer_s = _non_negative_float(policy, "eta_arrival_buffer_s", 4.0)

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
                else _positive_float(policy, "green_extension_default_s", 8.0)
            )
            extension_min_s = _positive_float(policy, "green_extension_min_s", 3.0)
            extension_max_s = max(extension_min_s, _positive_float(policy, "green_extension_max_s", 12.0))
            extension_s = max(
                extension_min_s,
                min(extension_max_s, needed_extension),
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
        early_green_min_eta_s = (
            _non_negative_float(policy, "emergency_early_green_min_eta_s", 0.0)
            if emergency_request
            else _non_negative_float(policy, "early_green_min_eta_s", 10.0)
        )
        if request.eta_to_stopline_s < early_green_min_eta_s:
            return self._decision(
                request,
                signal_state,
                sim_time_s,
                action=TSPAction.REEVALUATE_NEXT_CYCLE.value,
                reason="bus_too_close_for_safe_red_truncation",
                score=score,
                notes=["Pedido será reavaliado no ciclo seguinte para evitar transição insegura."],
            )

        target_phase = self._target_phase_for_request(request)
        return self._decision(
            request,
            signal_state,
            sim_time_s,
            action=TSPAction.EARLY_GREEN.value,
            reason="truncate_conflicting_phase_to_anticipate_priority_movement_green",
            score=score,
            phase_duration_s=_positive_float(policy, "red_truncation_to_s", 2.0),
            target_phase_index=target_phase,
            notes=[
                "Ação proposta: early green através de redução da duração da fase corrente.",
                "Não é feito salto direto de fase; a sequência SUMO mantém amarelo/all-red se o plano os contiver.",
                *(
                    ["Pedido emergency tratado no caminho de preempção segura: sem bypass de clearance/min-green."]
                    if emergency_request
                    else []
                ),
            ],
        )

    def priority_score(self, request: SREMLike) -> float:
        policy = self.tsp_config.decision_policy
        weights = policy.get("weights", {})
        if not isinstance(weights, dict):
            weights = {}
        delay_norm = _clip01(request.schedule_delay_s / _positive_float(policy, "delay_normalisation_s", 180.0))
        headway_norm = _clip01(
            abs(request.headway_deviation_s) / _positive_float(policy, "headway_normalisation_s", 240.0)
        )
        proximity_norm = _clip01(
            1 - request.distance_to_stopline_m / _positive_float(policy, "distance_normalisation_m", 250.0)
        )
        priority_norm = self._priority_level_weight(request.priority_level)
        score = (
            _non_negative_float(weights, "schedule_delay", 0.45) * delay_norm
            + _non_negative_float(weights, "headway_deviation", 0.20) * headway_norm
            + _non_negative_float(weights, "proximity", 0.20) * proximity_norm
            + _non_negative_float(weights, "priority_level", 0.15) * priority_norm
        )
        return round(_clip01(score), 4)

    def is_priority_movement_green(self, request: SREMLike, signal_state: SignalState) -> bool:
        ryg = signal_state.red_yellow_green_state or ""
        controlled_links = signal_state.controlled_links or []
        next_edge = getattr(request, "next_edge_id", "") or ""
        protected_required = self._requires_protected_green(request)
        for index, links_for_signal in enumerate(controlled_links):
            if index >= len(ryg):
                continue
            if _controlled_links_match_request(links_for_signal, request.current_lane_id, next_edge):
                return _is_green_for_priority(ryg[index], protected_required)

        controlled_lanes = signal_state.controlled_lanes or []
        candidate_lane = request.current_lane_id
        candidate_edge = request.current_edge_id

        # Preferir mapeamento por lane/control link quando disponível.
        # Uma lane SUMO pertence à edge E sse for "<E>_<índice>"; usar apenas
        # startswith(E) daria falsos positivos (ex.: "I1_I20_0" começa por
        # "I1_I2"), o que faria o motor ler o estado de sinal do movimento
        # errado e propor a ação errada para a Safety Layer.
        for index, lane_id in enumerate(controlled_lanes):
            if index >= len(ryg):
                continue
            if lane_id == candidate_lane:
                return _is_green_for_priority(ryg[index], protected_required)
        if not self._allows_edge_state_fallback(request):
            return False
        edge_prefix = f"{candidate_edge}_" if candidate_edge else None
        for index, lane_id in enumerate(controlled_lanes):
            if index >= len(ryg):
                continue
            if edge_prefix is not None and lane_id.startswith(edge_prefix):
                return _is_green_for_priority(ryg[index], protected_required)

        target_phase = self._target_phase_for_request(request)
        return (
            not protected_required
            and target_phase is not None
            and signal_state.current_phase_index == target_phase
        )

    @staticmethod
    def remaining_phase_time_s(signal_state: SignalState, sim_time_s: float) -> Optional[float]:
        if signal_state.next_switch_s is None:
            return None
        return max(0.0, float(signal_state.next_switch_s) - sim_time_s)

    def _priority_level_weight(self, priority_level: str) -> float:
        # Pesos por classe de prioridade lidos de config (chaves espelham
        # OperatorPriorityClass.value); os defaults reproduzem exactamente o
        # mapa hardcoded anterior, logo o score é idêntico quando a chave está
        # ausente. Classe desconhecida -> 0.0 (igual ao antigo else).
        defaults = {
            OperatorPriorityClass.EMERGENCY.value: 1.0,
            OperatorPriorityClass.HIGH_DELAY.value: 0.85,
            OperatorPriorityClass.HEADWAY_RECOVERY.value: 0.70,
            OperatorPriorityClass.NOMINAL.value: 0.45,
        }
        if priority_level not in defaults:
            return 0.0
        weights = self.tsp_config.decision_policy.get("priority_level_weights", {})
        if not isinstance(weights, dict):
            weights = {}
        return _non_negative_float(weights, priority_level, defaults[priority_level])

    def _requires_protected_green(self, request: SREMLike) -> bool:
        group = self._signal_group_for_request(request)
        if group is not None:
            return group.requires_protected_green
        raw = self.tsp_config.controller_contract_for_tls(request.tls_id)
        group_raw: dict = {}
        defaults = raw.get("priority_signal_group_defaults", {})
        if isinstance(defaults, dict):
            group_raw.update(defaults)
        groups = raw.get("signal_groups", {})
        if isinstance(groups, dict):
            specific = groups.get(request.target_signal_group_id, {})
            if isinstance(specific, dict):
                group_raw.update(specific)
        return bool(group_raw.get("requires_protected_green", raw.get("requires_protected_green", True)))

    def _allows_edge_state_fallback(self, request: SREMLike) -> bool:
        group = self._signal_group_for_request(request)
        if group is not None:
            return group.allow_edge_state_fallback
        raw = self.tsp_config.controller_contract_for_tls(request.tls_id)
        group_raw: dict = {}
        defaults = raw.get("priority_signal_group_defaults", {})
        if isinstance(defaults, dict):
            group_raw.update(defaults)
        groups = raw.get("signal_groups", {})
        if isinstance(groups, dict):
            specific = groups.get(request.target_signal_group_id, {})
            if isinstance(specific, dict):
                group_raw.update(specific)
        return bool(group_raw.get("allow_edge_state_fallback", raw.get("allow_edge_state_fallback", False)))

    def _target_phase_for_request(self, request: SREMLike) -> Optional[int]:
        mapping = self.tsp_config.phase_mapping_for_movement(request.priority_movement_id, request.tls_id)
        target_phase = _optional_int(mapping.get("target_phase_index"))
        if target_phase is not None:
            return target_phase
        group = self._signal_group_for_request(request)
        return group.phase_index if group is not None else None

    def _signal_group_for_request(self, request: SREMLike) -> Optional[SignalGroupContract]:
        try:
            contract = build_controller_contract(self.cits_config, self.tsp_config, request.tls_id)
        except (KeyError, ValueError):
            return None
        if request.target_signal_group_id:
            group = contract.signal_group_for_id(request.target_signal_group_id)
            if group is not None:
                return group
        if request.priority_movement_id:
            return contract.signal_group_for_movement(request.priority_movement_id)
        return None

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
            eta_to_stopline_s=request.eta_to_stopline_s,
            schedule_delay_s=request.schedule_delay_s,
            headway_deviation_s=request.headway_deviation_s,
            vehicle_class=request.vehicle_class,
            priority_level=request.priority_level,
            current_edge_id=request.current_edge_id,
            current_lane_id=request.current_lane_id,
            next_edge_id=getattr(request, "next_edge_id", ""),
            priority_movement_id=request.priority_movement_id,
            target_signal_group_id=request.target_signal_group_id,
            extension_s=extension_s,
            phase_duration_s=phase_duration_s,
            target_phase_index=target_phase_index,
            current_phase_index=signal_state.current_phase_index,
            current_signal_state=signal_state.red_yellow_green_state,
            current_next_switch_s=signal_state.next_switch_s,
            current_spent_duration_s=signal_state.spent_duration_s,
            controlled_lanes=list(signal_state.controlled_lanes or []),
            notes=list(notes or []),
            correlation_id=request.message_id,
        )


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _positive_float(mapping: dict, key: str, default: float) -> float:
    try:
        value = float(mapping.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _non_negative_float(mapping: dict, key: str, default: float) -> float:
    try:
        value = float(mapping.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _is_green_for_priority(char: str, protected_required: bool) -> bool:
    if protected_required:
        return char == "G"
    return char.lower() == "g"


def _controlled_links_match_request(links_for_signal: object, lane_id: str, next_edge_id: str) -> bool:
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
