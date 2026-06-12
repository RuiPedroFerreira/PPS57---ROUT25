#!/usr/bin/env python3
"""Motor de decisão TSP multiobjetivo para pedidos SREM-like aceites pela RSU."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from pps57_cits.config import CITSConfig
from pps57_cits.messages import OperatorPriorityClass, SREMLike
from pps57_cits.models import NetworkStateSnapshot, SignalState
from pps57_cits.util import optional_int as _optional_int

from .config import TSPConfig
from .models import DecisionStatus, ReasonCode, TSPAction, TSPDecision
from .signal_control import (
    ControllerContract,
    SignalGroupContract,
    build_controller_contract,
    phase_sequence_clearance_problem,
)


@dataclass
class TSPDecisionEngine:
    cits_config: CITSConfig
    tsp_config: TSPConfig
    # Contratos de controlador são estáticos durante um run; cache por TLS para
    # a pré-consulta de sequência/min-green não duplicar custo por decisão.
    _contract_cache: dict = field(default_factory=dict, repr=False)

    def decide(
        self,
        request: SREMLike,
        signal_state: SignalState,
        sim_time_s: float,
        network_state: Optional[NetworkStateSnapshot] = None,
    ) -> TSPDecision:
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
                reason=ReasonCode.PRIORITY_REQUEST_CANCELLATION.value,
                score=score,
            )
        if request.expires_at_s is not None and sim_time_s > request.expires_at_s:
            return self._decision(
                request,
                signal_state,
                sim_time_s,
                action=TSPAction.REJECT.value,
                reason=ReasonCode.REQUEST_EXPIRED.value,
                score=score,
            )

        # --- v2: portão de necessidade (prioridade condicional) ---
        # Prática real de TSP: só veículos com necessidade material (atraso ou
        # desvio de headway) recebem prioridade; proximidade e classe nunca
        # chegam sozinhas. Emergência tem hierarquia própria e não passa aqui.
        if not emergency_request:
            need_delay_s = _non_negative_float(policy, "need_min_schedule_delay_s", 20.0)
            need_headway_s = _non_negative_float(policy, "need_min_headway_deviation_s", 120.0)
            headway_abs = abs(request.headway_deviation_s)
            if request.schedule_delay_s < need_delay_s and headway_abs < need_headway_s:
                return self._decision(
                    request,
                    signal_state,
                    sim_time_s,
                    action=TSPAction.REJECT.value,
                    reason=(
                        f"{ReasonCode.PRIORITY_NEED_NOT_MET.value}:"
                        f"delay_{request.schedule_delay_s:.1f}<{need_delay_s:.1f}"
                        f",headway_{headway_abs:.1f}<{need_headway_s:.1f}"
                    ),
                    score=score,
                    notes=[
                        "Prioridade condicional: sem atraso/desvio de headway material, "
                        "o pedido não gera intervenção semafórica."
                    ],
                )

        # --- v2: limiar de score sensível a congestão ---
        congestion_notes: list[str] = []
        if not emergency_request and network_state is not None:
            occupancy_threshold = _non_negative_float(policy, "congested_occupancy_threshold", 0.5)
            congested_min_score = _positive_float(policy, "min_priority_score_congested", 0.5)
            if network_state.occupancy >= occupancy_threshold and congested_min_score > min_score:
                min_score = congested_min_score
                congestion_notes.append(
                    f"min_score elevado para {min_score:.2f}: "
                    f"occupancy {network_state.occupancy:.2f}>={occupancy_threshold:.2f}"
                )

        if score < min_score and not emergency_request:
            return self._decision(
                request,
                signal_state,
                sim_time_s,
                action=TSPAction.REJECT.value,
                reason=f"{ReasonCode.PRIORITY_SCORE_BELOW_THRESHOLD.value}:{score:.3f}<{min_score:.3f}",
                score=score,
                notes=[
                    "Pedido aceite pela RSU, mas insuficiente para intervenção semafórica.",
                    *congestion_notes,
                ],
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
                    reason=ReasonCode.GREEN_WINDOW_ALREADY_SUFFICIENT.value,
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
            # v2: sob pressão transversal o tecto da extensão encolhe — a
            # extensão continua permitida (instrumento suave), mas o verde
            # roubado às transversais carregadas fica limitado.
            cross_halted = self._cross_pressure_halted(request, network_state)
            cross_threshold = _non_negative_float(policy, "cross_pressure_halted_threshold", 8.0)
            if (
                not emergency_request
                and cross_halted is not None
                and cross_halted >= cross_threshold
            ):
                congested_cap_s = _positive_float(policy, "green_extension_max_congested_s", 6.0)
                if congested_cap_s < extension_max_s:
                    extension_max_s = max(extension_min_s, congested_cap_s)
                    congestion_notes.append(
                        f"extensão limitada a {extension_max_s:.1f}s: "
                        f"cross_halted {int(cross_halted)}>={int(cross_threshold)}"
                    )
            extension_s = max(
                extension_min_s,
                min(extension_max_s, needed_extension),
            )
            return self._decision(
                request,
                signal_state,
                sim_time_s,
                action=TSPAction.GREEN_EXTENSION.value,
                reason=ReasonCode.EXTEND_CURRENT_GREEN.value,
                score=score,
                extension_s=round(extension_s, 3),
                notes=[
                    "Ação proposta: extensão de verde da fase atual.",
                    f"remaining_green_s={remaining_s}",
                    f"bus_eta_s={request.eta_to_stopline_s:.1f}",
                    *congestion_notes,
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
                reason=ReasonCode.BUS_TOO_CLOSE_FOR_SAFE_RED_TRUNCATION.value,
                score=score,
                notes=["Pedido será reavaliado no ciclo seguinte para evitar transição insegura."],
            )

        target_phase = self._target_phase_for_request(request)

        # v2: pressão de rede -> diferir a truncagem (o instrumento agressivo).
        # Spillback ou transversais carregadas significam que o verde roubado
        # custa mais do que o autocarro recupera; reavalia no próximo ciclo.
        if not emergency_request and network_state is not None:
            pressure_signals: list[str] = []
            if network_state.spillback_risk:
                pressure_signals.append("spillback_risk")
            cross_halted = self._cross_pressure_halted(request, network_state)
            cross_threshold = _non_negative_float(policy, "cross_pressure_halted_threshold", 8.0)
            if cross_halted is not None and cross_halted >= cross_threshold:
                pressure_signals.append(f"cross_halted_{int(cross_halted)}>={int(cross_threshold)}")
            if pressure_signals:
                return self._decision(
                    request,
                    signal_state,
                    sim_time_s,
                    action=TSPAction.REEVALUATE_NEXT_CYCLE.value,
                    reason=f"{ReasonCode.NETWORK_PRESSURE_DEFER.value}:{'+'.join(pressure_signals)}",
                    score=score,
                    notes=["Intervenção adiada: pressão observada na rede torna a truncagem mais cara do que o benefício."],
                )

        # v2: pré-consulta dos contratos — não propor o que a Safety Layer
        # bloquearia sempre (sequência inalcançável, clearance impossível,
        # min-green da fase conflituante ainda por servir). A Safety continua
        # autoritativa; isto só limpa o funil de propostas inviáveis.
        contract = self._controller_contract_for_request(request)
        if contract is not None:
            never_skip = bool(
                self.cits_config.safety_constraints.get("never_skip_yellow_or_all_red", True)
            )
            sequence_problem = phase_sequence_clearance_problem(
                contract,
                signal_state.current_phase_index,
                target_phase,
                never_skip_yellow_or_all_red=never_skip,
            )
            if sequence_problem is not None:
                return self._decision(
                    request,
                    signal_state,
                    sim_time_s,
                    action=TSPAction.REEVALUATE_NEXT_CYCLE.value,
                    reason=f"{ReasonCode.EARLY_GREEN_PRECHECK_DEFER.value}:{sequence_problem}",
                    score=score,
                    notes=["Pré-consulta de contratos: a transição proposta não é estruturalmente viável agora."],
                )
            min_green_s = self._min_green_for_current_phase(contract, signal_state)
            spent_s = signal_state.spent_duration_s
            if min_green_s is not None and spent_s is not None and float(spent_s) < min_green_s:
                return self._decision(
                    request,
                    signal_state,
                    sim_time_s,
                    action=TSPAction.REEVALUATE_NEXT_CYCLE.value,
                    reason=(
                        f"{ReasonCode.EARLY_GREEN_DEFERRED_MIN_GREEN.value}:"
                        f"{float(spent_s):.1f}<{min_green_s:.1f}"
                    ),
                    score=score,
                    notes=["Fase conflituante ainda não serviu o verde mínimo; truncar agora seria sempre bloqueado."],
                )

        # v2: truncagem proporcional — limita o verde removido por evento em
        # vez de truncar sempre para o mínimo configurado. O custo de cada
        # intervenção fica limitado mesmo quando a fase tinha muito verde pela
        # frente; o recovery debt da Safety continua a limitar a frequência.
        truncate_to_s = _positive_float(policy, "red_truncation_to_s", 2.0)
        truncation_notes: list[str] = []
        if remaining_s is not None:
            max_removed_s = _positive_float(policy, "max_green_removed_per_event_s", 10.0)
            proportional_floor_s = max(0.0, float(remaining_s) - max_removed_s)
            if proportional_floor_s > truncate_to_s:
                truncate_to_s = proportional_floor_s
                truncation_notes.append(
                    f"truncagem proporcional: remove no máximo {max_removed_s:.1f}s "
                    f"dos {float(remaining_s):.1f}s restantes"
                )
            # v2: recuperabilidade — se o verde efetivamente removido (tecto
            # do que o autocarro pode poupar) for marginal, não vale a
            # perturbação da transversal.
            saving_s = max(0.0, float(remaining_s) - truncate_to_s)
            min_useful_s = _non_negative_float(policy, "min_useful_intervention_s", 5.0)
            if not emergency_request and saving_s < min_useful_s:
                return self._decision(
                    request,
                    signal_state,
                    sim_time_s,
                    action=TSPAction.REEVALUATE_NEXT_CYCLE.value,
                    reason=f"{ReasonCode.INTERVENTION_BENEFIT_TOO_SMALL.value}:{saving_s:.1f}<{min_useful_s:.1f}",
                    score=score,
                    notes=["Poupança potencial marginal: a truncagem não recupera tempo material para o TP."],
                )

        return self._decision(
            request,
            signal_state,
            sim_time_s,
            action=TSPAction.EARLY_GREEN.value,
            reason=ReasonCode.TRUNCATE_CONFLICTING_PHASE.value,
            score=score,
            phase_duration_s=round(truncate_to_s, 3),
            target_phase_index=target_phase,
            notes=[
                "Ação proposta: early green através de redução da duração da fase corrente.",
                "Não é feito salto direto de fase; a sequência SUMO mantém amarelo/all-red se o plano os contiver.",
                *truncation_notes,
                *congestion_notes,
                *(
                    ["Pedido emergency tratado no caminho de preempção segura: sem bypass de clearance/min-green."]
                    if emergency_request
                    else []
                ),
            ],
        )

    def _cross_pressure_halted(
        self, request: SREMLike, network_state: Optional[NetworkStateSnapshot]
    ) -> Optional[int]:
        """Veículos parados nas lanes controladas que NÃO servem a aproximação do bus.

        Usa o halted por lane já lido pelo adaptador (sem sinais novos). Lanes
        internas de junção (':') ficam de fora por misturarem movimentos.
        Devolve None quando não há snapshot/decomposição — caller trata como
        'sem evidência de pressão' (a Safety mantém os seus próprios bounds).
        """
        if network_state is None or not network_state.halted_by_lane:
            return None
        bus_edge = str(request.current_edge_id or "")
        if not bus_edge:
            return None
        total = 0
        for lane_id, halted in network_state.halted_by_lane.items():
            if lane_id.startswith(":"):
                continue
            if lane_id.rsplit("_", 1)[0] == bus_edge:
                continue
            total += int(halted or 0)
        return total

    def _min_green_for_current_phase(
        self, contract: ControllerContract, signal_state: SignalState
    ) -> Optional[float]:
        """Verde mínimo aplicável à fase corrente (global vs por-fase, o maior)."""
        raw = self.cits_config.safety_constraints.get("min_green_s")
        try:
            min_green = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            min_green = None
        phase_min = contract.min_green_for_phase(signal_state.current_phase_index)
        if phase_min is None:
            return min_green
        if min_green is None:
            return float(phase_min)
        return max(min_green, float(phase_min))

    def _controller_contract_for_request(self, request: SREMLike) -> Optional[ControllerContract]:
        if request.tls_id not in self._contract_cache:
            try:
                self._contract_cache[request.tls_id] = build_controller_contract(
                    self.cits_config, self.tsp_config, request.tls_id
                )
            except (KeyError, ValueError):
                self._contract_cache[request.tls_id] = None
        return self._contract_cache[request.tls_id]

    def priority_score(self, request: SREMLike) -> float:
        return self._score_breakdown(request)[0]

    def _score_breakdown(self, request: SREMLike) -> tuple[float, dict]:
        """Score escalar + decomposição por-termo (P3 explainability).

        A soma das `contribution` é igual ao score antes do clip01/round. Com os
        pesos default (somam 1.0) não há clip, logo sum(contribution) == score;
        só se a config inflacionar os pesos acima de 1.0 é que o escalar é
        clipado a 1.0 enquanto a soma das contribuições não — divergindo na
        região saturada por construção.
        """
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
        w_delay = _non_negative_float(weights, "schedule_delay", 0.45)
        w_headway = _non_negative_float(weights, "headway_deviation", 0.20)
        w_proximity = _non_negative_float(weights, "proximity", 0.20)
        w_priority = _non_negative_float(weights, "priority_level", 0.15)
        score = round(
            _clip01(
                w_delay * delay_norm
                + w_headway * headway_norm
                + w_proximity * proximity_norm
                + w_priority * priority_norm
            ),
            4,
        )
        components = {
            "schedule_delay": _component(request.schedule_delay_s, delay_norm, w_delay),
            "headway_deviation": _component(request.headway_deviation_s, headway_norm, w_headway),
            "proximity": _component(request.distance_to_stopline_m, proximity_norm, w_proximity),
            "priority_level": _component(request.priority_level, priority_norm, w_priority),
        }
        return score, components

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
        contract = self._controller_contract_for_request(request)
        if contract is None:
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
        _, score_components = self._score_breakdown(request)
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
            score_components=score_components,
        )


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _component(raw: object, normalised: float, weight: float) -> dict:
    return {
        "raw": round(float(raw), 3) if isinstance(raw, (int, float)) else raw,
        "normalised": round(float(normalised), 4),
        "weight": round(float(weight), 4),
        "contribution": round(float(weight) * float(normalised), 4),
    }


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
