#!/usr/bin/env python3
"""Motor de decisão TSP multiobjetivo para pedidos SREM-like aceites pela RSU."""

from __future__ import annotations

from dataclasses import dataclass, field

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
from .util import (
    controlled_links_match_request as _controlled_links_match_request,
)
from .util import (
    lane_belongs_to_edge_set as _lane_belongs_to_edge_set,
)
from .util import (
    non_negative_float as _non_negative_float,
)
from .util import (
    positive_float as _positive_float,
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
        network_state: NetworkStateSnapshot | None = None,
    ) -> TSPDecision:
        # Breakdown calculado UMA vez por decisão: o escalar guia os portões e
        # os componentes seguem para a decisão (antes recomputava-se em
        # _decision para cada retorno).
        score, score_components = self._score_breakdown(request)
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
                score_components=score_components,
            )
        if request.expires_at_s is not None and sim_time_s > request.expires_at_s:
            return self._decision(
                request,
                signal_state,
                sim_time_s,
                action=TSPAction.REJECT.value,
                reason=ReasonCode.REQUEST_EXPIRED.value,
                score=score,
                score_components=score_components,
            )

        # --- v2.1: estado de congestão observado (afecta necessidade e score) ---
        occupancy_threshold = _non_negative_float(policy, "congested_occupancy_threshold", 0.5)
        congested = network_state is not None and network_state.occupancy >= occupancy_threshold
        congestion_notes: list[str] = []

        # --- v2: portão de necessidade (prioridade condicional) ---
        # Prática real de TSP: só veículos com necessidade material (atraso ou
        # desvio de headway) recebem prioridade; proximidade e classe nunca
        # chegam sozinhas. Emergência tem hierarquia própria e não passa aqui.
        if not emergency_request:
            need_delay_s = _non_negative_float(policy, "need_min_schedule_delay_s", 20.0)
            if congested:
                # v2.1: sob congestão o atraso exigido sobe — só autocarros
                # materialmente atrasados justificam roubar capacidade a uma
                # rede saturada. Dial alcançável, ao contrário do score-cliff
                # (o score máximo do regime proxy é ~0.485, pelo que um
                # min_score congestionado de 0.5 desligava o TSP por inteiro).
                congested_need_s = _non_negative_float(
                    policy, "need_min_schedule_delay_congested_s", 35.0
                )
                if congested_need_s > need_delay_s:
                    need_delay_s = congested_need_s
                    congestion_notes.append(
                        f"need_min_delay elevado para {need_delay_s:.0f}s: "
                        f"occupancy {network_state.occupancy:.2f}>={occupancy_threshold:.2f}"
                    )
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
                    score_components=score_components,
                    notes=[
                        "Prioridade condicional: sem atraso/desvio de headway material, "
                        "o pedido não gera intervenção semafórica.",
                        *congestion_notes,
                    ],
                )

        # --- v2: limiar de score sensível a congestão ---
        if not emergency_request and congested:
            congested_min_score = _positive_float(policy, "min_priority_score_congested", 0.4)
            if congested_min_score > min_score:
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
                score_components=score_components,
                notes=[
                    "Pedido aceite pela RSU, mas insuficiente para intervenção semafórica.",
                    *congestion_notes,
                ],
            )

        is_green = self.is_priority_movement_green(request, signal_state)
        remaining_s = self.remaining_phase_time_s(signal_state, sim_time_s)
        arrival_buffer_s = _non_negative_float(policy, "eta_arrival_buffer_s", 4.0)

        # v2.2: ETA efetiva = ETA cinemática do OBU + tempo de descarga da
        # fila parada à frente do autocarro na mesma lane. Sem isto as
        # extensões ficam sistematicamente subdimensionadas exatamente em
        # congestão (o autocarro chega depois da fila descarregar, não ao
        # ETA de velocidade livre).
        eta_s = request.eta_to_stopline_s
        eta_notes: list[str] = []
        queue_correction_s = self._queue_eta_correction_s(request, network_state)
        # O ETA do OBU pode já embutir penalização de fila (queue_ahead *
        # eta_queue_penalty_s, declarada em eta_queue_delay_s); deduzi-la
        # antes de somar a correção por detetor, senão a MESMA fila parada
        # conta duas vezes e required_green_s infla — extensões
        # sobredimensionadas exatamente nos cenários congestionados. ETAs
        # externos/raw (campo 0.0) mantêm a correção integral.
        obu_queue_delay_s = max(0.0, float(request.eta_queue_delay_s or 0.0))
        queue_correction_s = max(0.0, queue_correction_s - obu_queue_delay_s)
        if queue_correction_s > 0.0:
            eta_s += queue_correction_s
            eta_notes.append(
                f"eta corrigida pela fila: {request.eta_to_stopline_s:.1f}s"
                f"+{queue_correction_s:.1f}s descarga = {eta_s:.1f}s"
            )

        if is_green:
            # Pré-consulta (espelho dos prechecks do early green): sem
            # remaining/spent legíveis a Safety bloqueia SEMPRE a extensão
            # (unknown_remaining/unknown_spent) — propor só inflaciona
            # blocked_by_safety no funil. Defere para o ciclo seguinte.
            if remaining_s is None or signal_state.spent_duration_s is None:
                missing = "remaining" if remaining_s is None else "spent"
                return self._decision(
                    request,
                    signal_state,
                    sim_time_s,
                    action=TSPAction.REEVALUATE_NEXT_CYCLE.value,
                    reason=f"{ReasonCode.GREEN_EXTENSION_PRECHECK_DEFER.value}:unknown_{missing}",
                    score=score,
                    score_components=score_components,
                    notes=[
                        "Pré-consulta: tempo de fase ilegível torna a extensão sempre bloqueada pela Safety.",
                        *eta_notes,
                    ],
                )
            required_green_s = eta_s + arrival_buffer_s
            if remaining_s >= required_green_s:
                return self._decision(
                    request,
                    signal_state,
                    sim_time_s,
                    action=TSPAction.NO_ACTION.value,
                    reason=ReasonCode.GREEN_WINDOW_ALREADY_SUFFICIENT.value,
                    score=score,
                    score_components=score_components,
                    notes=[
                        f"remaining_green_s={remaining_s:.1f}",
                        f"required_green_s={required_green_s:.1f}",
                        *eta_notes,
                    ],
                )

            needed_extension = required_green_s - remaining_s
            extension_min_s = _positive_float(policy, "green_extension_min_s", 3.0)
            extension_max_s = max(
                extension_min_s, _positive_float(policy, "green_extension_max_s", 12.0)
            )
            # v2.2: com o lifecycle de eventos ativo, cada decisão concede só
            # uma prestação (rolling extension); o refresh do SREM traz a
            # prestação seguinte se o autocarro ainda não tiver passado, e o
            # check-out devolve o que não for usado. Robusto a erro de ETA.
            if bool(self.tsp_config.actuation.get("priority_event_lifecycle_enabled", False)):
                increment_s = _positive_float(policy, "green_extension_rolling_increment_s", 4.0)
                if increment_s < extension_max_s:
                    extension_max_s = max(extension_min_s, increment_s)
                    congestion_notes.append(
                        f"rolling extension: prestação limitada a {extension_max_s:.1f}s (priority event lifecycle)"
                    )
            # v2: sob pressão transversal o tecto da extensão encolhe — a
            # extensão continua permitida (instrumento suave), mas o verde
            # roubado às transversais carregadas fica limitado. v2.1: vítimas
            # = lanes em vermelho puro (o sentido oposto partilha o verde).
            cross_halted = self._victim_pressure_halted(
                request, signal_state, network_state, victims="red"
            )
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
                score_components=score_components,
                extension_s=round(extension_s, 3),
                notes=[
                    "Ação proposta: extensão de verde da fase atual.",
                    f"remaining_green_s={remaining_s}",
                    f"bus_eta_s={eta_s:.1f}",
                    *eta_notes,
                    *congestion_notes,
                ],
            )

        # Movimento prioritário não está em verde: propor early green/red truncation.
        early_green_min_eta_s = (
            _non_negative_float(policy, "emergency_early_green_min_eta_s", 0.0)
            if emergency_request
            else _non_negative_float(policy, "early_green_min_eta_s", 10.0)
        )
        if eta_s < early_green_min_eta_s:
            return self._decision(
                request,
                signal_state,
                sim_time_s,
                action=TSPAction.REEVALUATE_NEXT_CYCLE.value,
                reason=ReasonCode.BUS_TOO_CLOSE_FOR_SAFE_RED_TRUNCATION.value,
                score=score,
                score_components=score_components,
                notes=[
                    "Pedido será reavaliado no ciclo seguinte para evitar transição insegura.",
                    *eta_notes,
                ],
            )

        target_phase = self._target_phase_for_request(request)

        # v2: pressão de rede -> diferir a truncagem (o instrumento agressivo).
        # Spillback ou transversais carregadas significam que o verde roubado
        # custa mais do que o autocarro recupera; reavalia no próximo ciclo.
        if not emergency_request and network_state is not None:
            pressure_signals: list[str] = []
            if network_state.spillback_risk:
                pressure_signals.append("spillback_risk")
            # v2.1: vítimas do early green = lanes com verde na fase corrente
            # (é a descarga delas que a truncagem corta).
            cross_halted = self._victim_pressure_halted(
                request, signal_state, network_state, victims="green"
            )
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
                    score_components=score_components,
                    notes=[
                        "Intervenção adiada: pressão observada na rede torna a truncagem mais cara do que o benefício."
                    ],
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
                    score_components=score_components,
                    notes=[
                        "Pré-consulta de contratos: a transição proposta não é estruturalmente viável agora."
                    ],
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
                    score_components=score_components,
                    notes=[
                        "Fase conflituante ainda não serviu o verde mínimo; truncar agora seria sempre bloqueado."
                    ],
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
                    score_components=score_components,
                    notes=[
                        "Poupança potencial marginal: a truncagem não recupera tempo material para o TP."
                    ],
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
                *eta_notes,
                *congestion_notes,
                *(
                    [
                        "Pedido emergency tratado no caminho de preempção segura: sem bypass de clearance/min-green."
                    ]
                    if emergency_request
                    else []
                ),
            ],
        )

    _GREEN_CHARS = ("g", "G")
    _RED_CHARS = ("r", "R")

    def _queue_eta_correction_s(
        self, request: SREMLike, network_state: NetworkStateSnapshot | None
    ) -> float:
        """Tempo de descarga da fila à frente do autocarro (v2.2).

        halted_by_lane conta TODOS os parados na lane do autocarro (o adaptador
        não sabe quem está à frente); o clamp cinemático distance/jam_spacing
        limita a contagem aos veículos que cabem fisicamente entre o autocarro
        e a stopline — nunca inventa fila para além do espaço observável.
        queue_discharge_headway_s=0 desliga a correção; sem snapshot ou sem
        decomposição por lane devolve 0 (comportamento pré-v2.2).
        """
        policy = self.tsp_config.decision_policy
        headway_s = _non_negative_float(policy, "queue_discharge_headway_s", 2.0)
        if headway_s <= 0.0 or network_state is None or not network_state.halted_by_lane:
            return 0.0
        halted = int(network_state.halted_by_lane.get(request.current_lane_id) or 0)
        # O próprio autocarro conta como halted na sua lane quando está parado
        # na fila — descontá-lo evita sobrestimar a descarga em exatamente os
        # casos congestionados que a correção visa. 0.1 m/s é o limiar de
        # halting do SUMO; velocidade desconhecida não desconta (só se sabe
        # que o autocarro está parado quando o SREM o diz).
        bus_speed = request.requestor.speed_mps if request.requestor is not None else None
        if halted > 0 and bus_speed is not None and float(bus_speed) <= 0.1:
            halted -= 1
        if halted <= 0:
            return 0.0
        jam_spacing_m = _positive_float(policy, "queue_jam_spacing_m", 7.5)
        fits_ahead = int(float(request.distance_to_stopline_m) / jam_spacing_m)
        queue_ahead = min(halted, fits_ahead)
        if queue_ahead <= 0:
            return 0.0
        return queue_ahead * headway_s

    def _victim_pressure_halted(
        self,
        request: SREMLike,
        signal_state: SignalState,
        network_state: NetworkStateSnapshot | None,
        *,
        victims: str,
    ) -> int | None:
        """Veículos parados nas lanes realmente lesadas pela intervenção (v2.1).

        victims="red" (extensão de verde): lesadas são as aproximações sem
        qualquer link verde — o sentido oposto do corredor partilha o verde e
        não paga a extensão, por isso não conta. victims="green" (early
        green): lesadas são as lanes com link verde na fase corrente, cuja
        descarga de fila seria truncada.

        Usa o halted por lane do adaptador + a máscara RYG do signal_state
        (sem sinais novos). Lanes internas (':') e a aproximação do bus ficam
        de fora. Sem máscara alinhável (len != controlled_lanes) recai no
        comportamento v2 — contar todas as lanes externas — que é conservador
        (defere mais). Devolve None sem snapshot/decomposição.
        """
        if network_state is None or not network_state.halted_by_lane:
            return None
        bus_edge = str(request.current_edge_id or "")
        if not bus_edge:
            return None
        chars_by_lane = self._link_chars_by_lane(signal_state)
        total = 0
        for lane_id, halted in network_state.halted_by_lane.items():
            if lane_id.startswith(":"):
                continue
            if _lane_belongs_to_edge_set(lane_id, {bus_edge}):
                continue
            chars = chars_by_lane.get(lane_id)
            if chars is not None:
                has_green = any(c in self._GREEN_CHARS for c in chars)
                has_red = any(c in self._RED_CHARS for c in chars)
                if victims == "red" and (has_green or not has_red):
                    continue
                if victims == "green" and not has_green:
                    continue
            total += int(halted or 0)
        return total

    @staticmethod
    def _link_chars_by_lane(signal_state: SignalState) -> dict[str, str]:
        """Mapa lane -> caracteres RYG dos seus links (concatenados).

        controlled_lanes repete a lane por cada link controlado, alinhada com
        o índice do estado RYG; um desalinhamento devolve {} (fallback do
        caller para o comportamento sem máscara).
        """
        lanes = list(signal_state.controlled_lanes or [])
        ryg = str(signal_state.red_yellow_green_state or "")
        if not lanes or len(lanes) != len(ryg):
            return {}
        chars: dict[str, str] = {}
        for lane_id, char in zip(lanes, ryg, strict=False):
            chars[lane_id] = chars.get(lane_id, "") + char
        return chars

    def _min_green_for_current_phase(
        self, contract: ControllerContract, signal_state: SignalState
    ) -> float | None:
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

    def _controller_contract_for_request(self, request: SREMLike) -> ControllerContract | None:
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
        delay_norm = _clip01(
            request.schedule_delay_s / _positive_float(policy, "delay_normalisation_s", 180.0)
        )
        headway_norm = _clip01(
            abs(request.headway_deviation_s)
            / _positive_float(policy, "headway_normalisation_s", 240.0)
        )
        proximity_norm = _clip01(
            1
            - request.distance_to_stopline_m
            / _positive_float(policy, "distance_normalisation_m", 250.0)
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
            if _controlled_links_match_request(
                links_for_signal, request.current_lane_id, next_edge
            ):
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
    def remaining_phase_time_s(signal_state: SignalState, sim_time_s: float) -> float | None:
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
        return self._raw_group_flag(request, "requires_protected_green", default=True)

    def _allows_edge_state_fallback(self, request: SREMLike) -> bool:
        group = self._signal_group_for_request(request)
        if group is not None:
            return group.allow_edge_state_fallback
        return self._raw_group_flag(request, "allow_edge_state_fallback", default=False)

    def _raw_group_flag(self, request: SREMLike, key: str, *, default: bool) -> bool:
        """Flag de signal group lida da config crua (fallback sem contrato).

        Precedência: defaults do TLS -> grupo específico -> chave top-level do
        contrato raw -> default. Era o mesmo bloco copiado em
        _requires_protected_green e _allows_edge_state_fallback."""
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
        return bool(group_raw.get(key, raw.get(key, default)))

    def _target_phase_for_request(self, request: SREMLike) -> int | None:
        mapping = self.tsp_config.phase_mapping_for_movement(
            request.priority_movement_id, request.tls_id
        )
        target_phase = _optional_int(mapping.get("target_phase_index"))
        if target_phase is not None:
            return target_phase
        group = self._signal_group_for_request(request)
        return group.phase_index if group is not None else None

    def _signal_group_for_request(self, request: SREMLike) -> SignalGroupContract | None:
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
        score_components: dict | None = None,
        extension_s: float = 0.0,
        phase_duration_s: float | None = None,
        target_phase_index: int | None = None,
        notes: list[str] | None = None,
    ) -> TSPDecision:
        if score_components is None:
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


def _is_green_for_priority(char: str, protected_required: bool) -> bool:
    if protected_required:
        return char == "G"
    return char.lower() == "g"
