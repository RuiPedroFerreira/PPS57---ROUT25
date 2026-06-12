#!/usr/bin/env python3
"""C-ITS, TSP decision engine, Safety Layer, and actuation controller."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Dict, Iterable, List, Optional

from pps57_cits.broker import InMemoryMessageBroker
from pps57_cits.config import CITSConfig
from pps57_cits.event_logger import CITSJsonlLogger, IncrementalCITSSummary
from pps57_cits.map_spat import build_mapem_messages, build_spatem_message_from_state
from pps57_cits.messages import (
    CITSMessage,
    GrantedStrategy,
    MessageType,
    OperatorPriorityClass,
    PrioritizationResponse,
    ResponseStatus,
    SSEMAudit,
    SREMLike,
    SSEMLike,
    StationType,
    build_security_envelope,
    derive_station_id,
    parse_intersection_ref_id,
    sim_time_to_cdd,
)
from pps57_cits.models import NetworkStateSnapshot, SignalState
from pps57_cits.obu import OBUEmulator
from pps57_cits.rsu import build_rsu_agents
from pps57_cits.traci_adapter import TraciSimulationAdapter, TraciUnavailableError
from pps57_opt.policy_runtime import RuntimePolicy
from pps57_sumo.network_binding import NetworkBinding, build_network_binding

from .actuator import TraciTSPActuator
from .config import TSPConfig
from .compensation import GreenCompensationManager
from .corridor_arbiter import CorridorArbiter
from .engine import TSPDecisionEngine
from .logger import TSPJsonlLogger, write_tsp_summary
from .models import ActuationResult, DecisionStatus, ReasonCode, TSPAction, TSPDecision
from .request_store import PriorityRequestStore
from .safety import TSPSafetyLayer
from .signal_control import (
    ControllerContract,
    SignalControlAdapter,
    SimulatedControllerAdapter,
    TraciSignalControlAdapter,
    _network_profile_enabled,
    apply_network_binding,
    build_controller_contracts,
    network_binding_aliases,
)


@dataclass
class TSPControlController:
    cits_config: CITSConfig
    tsp_config: TSPConfig
    policy_mode: str = "baseline"
    policy_report_path: Optional[str] = None

    def __post_init__(self) -> None:
        self.broker = InMemoryMessageBroker(
            transport_config=dict(self.cits_config.raw.get("message_transport", {}))
        )
        self.obu = OBUEmulator(self.cits_config)
        self.rsu_agents = build_rsu_agents(self.cits_config)
        self.engine = TSPDecisionEngine(self.cits_config, self.tsp_config)
        self.safety = TSPSafetyLayer(self.cits_config, self.tsp_config)
        # NetworkBinding autoritativo (junction <request foes>) construído UMA
        # vez aqui e partilhado por contratos, verificação e Safety Layer — uma
        # única fonte de verdade, como em scripts/run_network_binding_check.py.
        self.network_binding: Optional[NetworkBinding] = None
        self.network_binding_aliases: Optional[Dict[str, Dict[str, str]]] = None
        self.network_binding_error: Optional[str] = None
        self._bind_network()
        self.safety.set_network_binding(
            self.network_binding, aliases_by_tls=self.network_binding_aliases
        )
        # P6: árbitro de corredor (cross-TLS). No-op quando o bloco `corridor`
        # está ausente da config; corre pré-Safety e só desce decisões.
        self.corridor_arbiter = CorridorArbiter(self.cits_config, self.tsp_config)
        self.compensation = GreenCompensationManager(self.cits_config, self.tsp_config)
        self._intervened_tls_this_step: set[str] = set()
        self.request_store = PriorityRequestStore(
            ttl_s=float(self.cits_config.obu_policy.get("request_lifecycle_ttl_s", 30.0))
        )
        self.runtime_policy: Optional[RuntimePolicy] = None
        if self.policy_mode in {"optimized", "rl"}:
            raw_path = self.policy_report_path or self._default_policy_report_path()
            if raw_path:
                path = self.tsp_config.path_from_root(raw_path)
                if path.exists():
                    self.runtime_policy = RuntimePolicy.load(self.tsp_config, path)

    def run_with_sumo(
        self,
        steps: Optional[int] = None,
        sumo_binary: str = "sumo",
        gui: bool = False,
        apply_actuation: bool = True,
    ) -> Dict[str, object]:
        adapter = TraciSimulationAdapter(self.cits_config, sumo_binary=sumo_binary, gui=gui)
        cits_summary = IncrementalCITSSummary()
        decisions: List[TSPDecision] = []
        actuations: List[ActuationResult] = []
        compensations: List[ActuationResult] = []

        try:
            adapter.start()
        except TraciUnavailableError:
            raise

        cits_log_path = self.cits_config.path_from_root(self.cits_config.logging.get("message_log", "outputs/cits_messages.jsonl"))
        decision_log_path = self.tsp_config.path_from_root(self.tsp_config.logging.get("decision_log", "outputs/tsp_decisions.jsonl"))
        actuation_log_path = self.tsp_config.path_from_root(self.tsp_config.logging.get("actuation_log", "outputs/tsp_actuation.jsonl"))

        # C3/C4: reconciliar o mapeamento de fases configurado com o programa
        # semafórico realmente carregado e detetar TLS atuados (o motor assume
        # controlo de tempo fixo). Em qualquer divergência, fail-closed:
        # desativa a atuação mas mantém a observação/decisão.
        contracts = self._signal_controlled_contracts()
        signal_control: SignalControlAdapter = TraciSignalControlAdapter(adapter)
        controller_simulation_cfg = self.tsp_config.raw.get("controller_simulation", {})
        if bool(controller_simulation_cfg.get("enabled", False)):
            signal_control = SimulatedControllerAdapter(
                base=signal_control,
                contracts=contracts,
                config=controller_simulation_cfg,
            )
        verification_problems = self._verify_signal_programs(signal_control)
        effective_actuation = apply_actuation and not verification_problems
        if verification_problems and apply_actuation:
            print("[SAFETY] Atuação TraCI desativada: verificação do programa semafórico falhou:")
            for problem in verification_problems:
                print(f"  - {problem}")
        # Propaga o resultado da verificação para a Safety Layer poder enforçar
        # `pedestrian_clearance_must_not_be_shortened` (fail-closed em falhas).
        self.safety.set_signal_program_verified(not verification_problems)
        actuator = TraciTSPActuator(adapter=signal_control, apply_actuation=effective_actuation)

        step_count = 0
        with CITSJsonlLogger(cits_log_path) as cits_logger, TSPJsonlLogger(decision_log_path) as decision_logger, TSPJsonlLogger(actuation_log_path) as actuation_logger:
            mapem = build_mapem_messages(self.cits_config, sim_time_s=0.0)
            self._publish_log_collect(mapem, cits_logger, cits_summary)
            try:
                while adapter.min_expected_number() > 0:
                    if steps is not None and step_count >= steps:
                        break
                    sim_time_s = adapter.simulation_step()
                    step_count += 1

                    # M2: drena mensagens do tick anterior antes de publicar
                    # as novas. Drenar no fim do tick destruía SSEMs
                    # publicados imediatamente antes de o OBU poder consumir,
                    # esvaziando o loop OBU->RSU->OBU. Memória continua
                    # limitada: cada tick só acumula as suas próprias filas.
                    self.broker.drain_all_except([])
                    self.broker.advance_time(step_count)

                    signal_states = {
                        intersection.tls_id: adapter.read_signal_state(intersection, sim_time_s)
                        for intersection in self.cits_config.signal_controlled_intersections
                    }
                    spatem = [build_spatem_message_from_state(state) for state in signal_states.values()]
                    self._publish_log_collect(spatem, cits_logger, cits_summary)

                    observations = adapter.read_vehicle_observations()
                    self.request_store.update_from_observations(observations, sim_time_s)
                    requests = self.obu.generate_requests(observations, sim_time_s)
                    self.request_store.ingest_requests(requests, sim_time_s)
                    self.request_store.expire_old(sim_time_s)
                    requests_by_id = {request.request_id: request for request in requests}
                    self._publish_log_collect(requests, cits_logger, cits_summary)

                    responses = self._process_rsu_queues(sim_time_s)
                    self._publish_log_collect(responses, cits_logger, cits_summary)
                    network_states = self._read_network_states(
                        adapter,
                        signal_states,
                        responses,
                        requests_by_id,
                        sim_time_s,
                    )

                    final_responses = self._process_acknowledged_requests(
                        responses=responses,
                        requests_by_id=requests_by_id,
                        signal_states=signal_states,
                        network_states=network_states,
                        actuator=actuator,
                        sim_time_s=sim_time_s,
                        decision_logger=decision_logger,
                        actuation_logger=actuation_logger,
                        decisions=decisions,
                        actuations=actuations,
                    )
                    self._publish_log_collect(final_responses, cits_logger, cits_summary)

                    # v2.1: paga compensações nas transições de fase. Lista
                    # separada de `actuations` para não inflacionar os KPIs de
                    # atuação TSP (applied_events); o log JSONL partilhado
                    # mantém a rastreabilidade completa.
                    for comp_result in self.compensation.step(
                        signal_states,
                        signal_control,
                        sim_time_s,
                        apply_actuation=effective_actuation,
                        skip_tls=self._intervened_tls_this_step,
                    ):
                        actuation_logger.write(comp_result)
                        compensations.append(comp_result)
            finally:
                adapter.close()

        summary_path = self.tsp_config.path_from_root(self.tsp_config.logging.get("summary_report", "reports/tsp_emulation_summary.json"))
        cits_summary_dict = cits_summary.to_dict()
        return write_tsp_summary(
            summary_path,
            decisions,
            actuations,
            extra={
                "mode": "sumo-traci",
                "steps": step_count,
                "scenario_id": self.tsp_config.raw.get("scenario_id"),
                "policy_mode": self.policy_mode,
                "runtime_policy_loaded": self.runtime_policy is not None,
                "runtime_policy": self._runtime_policy_summary(),
                "actuation_requested": apply_actuation,
                "actuation_enabled": effective_actuation,
                "signal_program_verification": {
                    "problems": verification_problems,
                    "actuation_downgraded": bool(verification_problems and apply_actuation),
                },
                "message_transport": self.broker.transport_stats(),
                "cits_total_messages": cits_summary_dict["total_messages"],
                "cits_by_type": cits_summary_dict["by_type"],
                "cits_processing_messages": cits_summary_dict.get("processing_messages", 0),
                "cits_acknowledged_messages": cits_summary_dict.get(
                    "acknowledged_messages",
                    cits_summary_dict.get("processing_messages", 0),
                ),
                "cits_rejected_messages": cits_summary_dict["rejected_messages"],
                "priority_request_lifecycle": self.request_store.to_summary(),
                "recovery_debt_by_tls_s": {
                    tls_id: round(value, 3)
                    for tls_id, value in sorted(self.safety.recovery_debt_by_tls.items())
                    if value > 0
                },
                "green_compensation": {
                    **self.compensation.summary(),
                    "events": len(compensations),
                },
            },
        )

    def _process_acknowledged_requests(
        self,
        *,
        responses: Iterable[SSEMLike],
        requests_by_id: Dict[str, SREMLike],
        signal_states: Dict[str, SignalState],
        network_states: Optional[Dict[str, NetworkStateSnapshot]] = None,
        actuator: TraciTSPActuator,
        sim_time_s: float,
        decision_logger: TSPJsonlLogger,
        actuation_logger: TSPJsonlLogger,
        decisions: List[TSPDecision],
        actuations: List[ActuationResult],
    ) -> List[SSEMLike]:
        # M6: invariante explícita "no máximo 1 intervenção por TLS por passo".
        # Até aqui isto dependia implicitamente do efeito colateral do cooldown
        # (frágil e silenciosamente quebrado em modo no-actuation/downgraded, onde
        # mark_applied não corre e a telemetria contava intervenções duplicadas).
        intervened_tls: set[str] = set()
        # Exposto para o GreenCompensationManager saltar TLS intervencionados
        # neste passo: pagar compensação com o signal_state pré-atuação
        # reinstalaria o verde que o early green acabou de cortar.
        self._intervened_tls_this_step = intervened_tls
        response_list = list(responses)
        resolved_requests_by_id = dict(requests_by_id)
        for response in response_list:
            if response.request_id not in resolved_requests_by_id:
                request = self.request_store.get_by_request_id(response.request_id)
                if request is not None:
                    resolved_requests_by_id[response.request_id] = request
        active_requests_by_tls: Dict[str, int] = {}
        response_list = sorted(
            response_list,
            key=lambda response: _response_priority_sort_key(response, resolved_requests_by_id, self.cits_config),
        )
        final_responses: List[SSEMLike] = []

        for response in response_list:
            if response.status != ResponseStatus.PROCESSING.value:
                continue
            request = resolved_requests_by_id.get(response.request_id)
            if request is not None:
                active_requests_by_tls[request.tls_id] = active_requests_by_tls.get(request.tls_id, 0) + 1

        for response in response_list:
            if response.status != ResponseStatus.PROCESSING.value:
                continue
            request = resolved_requests_by_id.get(response.request_id)
            if request is None:
                continue
            if request.is_cancellation:
                self.request_store.mark_cancelled(request, sim_time_s)
            signal_state = signal_states.get(request.tls_id)
            if signal_state is None:
                continue
            baseline = self.engine.decide(
                request,
                signal_state,
                sim_time_s,
                network_state=network_states.get(request.tls_id) if network_states else None,
            )
            # Tempo desde a última intervenção neste TLS (None se nunca houve):
            # alimenta o eixo intervention_* do state bucket no runtime policy
            # para coerência treino<->inferência.
            last_intervention = self.safety.last_intervention_time_by_tls.get(request.tls_id)
            seconds_since_last_intervention_s = (
                None if last_intervention is None else max(0.0, sim_time_s - last_intervention)
            )
            proposed = (
                self.runtime_policy.decide(
                    request,
                    signal_state,
                    sim_time_s,
                    baseline,
                    active_request_count=active_requests_by_tls.get(request.tls_id, 1),
                    network_state=network_states.get(request.tls_id) if network_states else None,
                    seconds_since_last_intervention_s=seconds_since_last_intervention_s,
                )
                if self.runtime_policy is not None
                else baseline
            )
            if self.runtime_policy is None and network_states and request.tls_id in network_states:
                proposed = proposed.copy_with(
                    notes=list(proposed.notes) + [_network_state_note(network_states[request.tls_id])]
                )
            # P6: árbitro de corredor (cross-TLS), pré-Safety e downgrade-only.
            # Só corre para ações atuáveis; no-op completo se o bloco `corridor`
            # estiver ausente. allow=True com nota -> flag green-wave (não muda
            # a ação); allow=False -> defer mais abaixo (mirror da supressão).
            arbiter_outcome = (
                self.corridor_arbiter.arbitrate(
                    proposed,
                    recovery_debt_by_tls=self.safety.recovery_debt_by_tls,
                    network_states=network_states,
                )
                if proposed.requires_actuation
                else None
            )
            if arbiter_outcome is not None and arbiter_outcome.note:
                proposed = proposed.copy_with(notes=list(proposed.notes) + [arbiter_outcome.note])
            # Supressão de mesmo-passo ANTES da Safety Layer: se este TLS já
            # interveio neste passo, o pedido é superseded (não atuável) — e não
            # bloqueado por cooldown, que a intervenção anterior deste mesmo passo
            # acabou de marcar (mesmo timestamp). A ordem importa: validar
            # primeiro faria o cooldown disparar e mascararia a deduplicação.
            if proposed.requires_actuation and request.tls_id in intervened_tls:
                safe_decision = proposed.copy_with(
                    status=DecisionStatus.NOT_ACTUABLE.value,
                    reason=ReasonCode.SUPERSEDED_BY_EARLIER_INTERVENTION_SAME_STEP.value,
                    notes=list(proposed.notes)
                    + ["TLS já interveio neste passo; pedido suprimido para evitar atuação dupla."],
                )
                result = ActuationResult(
                    decision_id=safe_decision.decision_id,
                    timestamp_s=sim_time_s,
                    tls_id=safe_decision.tls_id,
                    action=safe_decision.action,
                    applied=False,
                    no_actuation=not getattr(actuator, "apply_actuation", False),
                    command="none",
                    reason=ReasonCode.SUPERSEDED_BY_EARLIER_INTERVENTION_SAME_STEP.value,
                )
            elif proposed.requires_actuation and arbiter_outcome is not None and not arbiter_outcome.allow:
                # Defer do árbitro de corredor: desce para NOT_ACTUABLE e salta a
                # atuação (como a supressão). A Safety Layer continua a ser o
                # portão final — um pedido deferido nunca chega a APPROVED.
                safe_decision = proposed.copy_with(
                    status=DecisionStatus.NOT_ACTUABLE.value,
                    reason=arbiter_outcome.reason_code,
                    notes=list(proposed.notes)
                    + ["Corridor arbiter deferiu o pedido antes da Safety Layer (downgrade-only; nunca aprova)."],
                )
                result = ActuationResult(
                    decision_id=safe_decision.decision_id,
                    timestamp_s=sim_time_s,
                    tls_id=safe_decision.tls_id,
                    action=safe_decision.action,
                    applied=False,
                    no_actuation=not getattr(actuator, "apply_actuation", False),
                    command="none",
                    reason=arbiter_outcome.reason_code,
                )
            else:
                validation = self.safety.validate(proposed, signal_state, sim_time_s)
                safe_decision = validation.safe_decision
                network_state = network_states.get(request.tls_id) if network_states else None
                if validation.approved and network_state is not None and network_state.degraded:
                    safe_decision = safe_decision.copy_with(
                        status=DecisionStatus.BLOCKED_BY_SAFETY.value,
                        reason=ReasonCode.NETWORK_STATE_DEGRADED_DETECTOR_READ_FAILURE.value,
                        notes=list(safe_decision.notes)
                        + [
                            "Safety Layer bloqueou atuação: leitura detector/lane degradada "
                            f"({network_state.detector_read_failures} falhas)."
                        ],
                    )

                approved_for_actuation = safe_decision.status == DecisionStatus.APPROVED.value
                result = actuator.apply(safe_decision, signal_state, sim_time_s)
                # H5: avança contadores da Safety Layer (cooldown,
                # consecutive_interventions) mesmo em modo no-actuation /
                # downgraded, para a telemetria refletir o que aconteceria com
                # atuação real.
                # Failure-mode (severity=error): TraCI levantou exceção a meio
                # do setPhaseDuration -> TLS num estado potencialmente
                # intermédio. Força cooldown (mark_applied) para não martelar
                # o TLS com retries que poderiam agravar a inconsistência, e
                # emite stderr proeminente para a operação detetar.
                if approved_for_actuation and safe_decision.requires_actuation:
                    actuation_error = (
                        not result.applied
                        and not result.no_actuation
                        and getattr(result, "severity", "info") == "error"
                    )
                    if result.applied or result.no_actuation or actuation_error:
                        # H5: em modo no-actuation a intervenção "aconteceria",
                        # por isso o cooldown/consecutive têm de avançar — caso
                        # contrário pedidos subsequentes no mesmo TLS não seriam
                        # bloqueados e a telemetria não refletiria a atuação real.
                        self.safety.mark_applied(safe_decision, sim_time_s)
                        # v2.1: a fase truncada por um early green ganha direito
                        # a verde de compensação no ciclo seguinte (NEMA-style).
                        self.compensation.register_applied(safe_decision)
                    if result.applied:
                        self.request_store.mark_granted(request, sim_time_s)
                        rsu = self.rsu_agents.get(request.rsu_id)
                        if rsu is not None:
                            rsu.mark_priority_granted(request.vehicle_id, sim_time_s)
                    if result.applied or result.no_actuation or actuation_error:
                        intervened_tls.add(safe_decision.tls_id)
                    if actuation_error:
                        print(
                            f"[ACTUATION_ERROR] tls={safe_decision.tls_id} "
                            f"decision={safe_decision.decision_id} reason={result.reason}",
                            file=sys.stderr,
                            flush=True,
                        )

            decision_logger.write(safe_decision)
            actuation_logger.write(result)
            decisions.append(safe_decision)
            actuations.append(result)
            final_responses.append(
                self._build_final_ssem(request, response, safe_decision, result, sim_time_s)
            )
        return final_responses

    def _build_final_ssem(
        self,
        request: SREMLike,
        gateway_response: SSEMLike,
        decision: TSPDecision,
        actuation: ActuationResult,
        sim_time_s: float,
    ) -> SSEMLike:
        response_ttl_ms = int(round(float(self.cits_config.rsu_policy.get("response_ttl_s", 15)) * 1000))
        moy, timestamp_ms, generation_delta = sim_time_to_cdd(sim_time_s)
        status = _final_response_status(decision, actuation)
        rejection_reason = None if status == ResponseStatus.GRANTED.value else _final_rejection_reason(decision, actuation)
        primary = request.requests[0] if request.requests else None
        response = PrioritizationResponse(
            request_id=primary.request_id if primary is not None else 0,
            sequence_number=request.sequence_number,
            requestor_station_id=request.station_id,
            response_status=status,
            granted_signal_group=None,
            valid_until_ms=int(round(sim_time_s * 1000)) + response_ttl_ms,
        )
        audit = SSEMAudit(
            granted_strategy=_granted_strategy_for_decision(decision, status),
            rejection_reason=rejection_reason,
            confidence=0.95 if status == ResponseStatus.GRANTED.value else 1.0,
            notes=[
                f"gateway_ssem_id={gateway_response.message_id}",
                f"tsp_decision_id={decision.decision_id}",
                f"tsp_action={decision.action}",
                f"tsp_status={decision.status}",
                f"actuation_reason={actuation.reason}",
            ]
            + (["tsp_would_grant_not_applied=true"] if actuation.no_actuation else []),
        )
        return SSEMLike(
            message_type=MessageType.SSEM.value,
            station_id=derive_station_id(request.rsu_id),
            station_type=StationType.ROAD_SIDE_UNIT.value,
            source_id=request.rsu_id,
            destination_id=request.source_id,
            generation_delta_time_ms=generation_delta,
            moy=moy,
            timestamp_ms=timestamp_ms,
            security=build_security_envelope(request.rsu_id, sim_time_s),
            intersection_ref_id=parse_intersection_ref_id(request.intersection_id),
            intersection_alias=request.intersection_id,
            tls_id=request.tls_id,
            rsu_id=request.rsu_id,
            response=response,
            audit=audit,
            correlation_id=request.message_id,
        )

    def _publish_log_collect(
        self,
        messages: Iterable[CITSMessage],
        logger: CITSJsonlLogger,
        summary: IncrementalCITSSummary,
    ) -> None:
        for message in messages:
            self.broker.publish(message)
            logger.write(message)
            summary.add(message)

    def _verify_signal_programs(self, adapter: SignalControlAdapter) -> List[str]:
        """Verifica contratos de controlador contra o programa semafórico real.

        Fail-closed: se o programa não puder ser lido, ou se algum índice de fase
        configurado estiver fora do programa, se não houver matriz de conflitos
        ou se o TLS for atuado quando o contrato exige tempo fixo, devolve
        problemas que desativam a atuação.
        """
        contracts = self._signal_controlled_contracts()
        if hasattr(adapter, "verify_controller_contracts"):
            return adapter.verify_controller_contracts(contracts)
        return TraciSignalControlAdapter(adapter).verify_controller_contracts(contracts)  # type: ignore[arg-type]

    def _signal_controlled_contracts(self) -> List[ControllerContract]:
        signal_controlled_tls = {
            intersection.tls_id
            for intersection in self.cits_config.signal_controlled_intersections
        }
        contracts = [
            contract
            for contract in build_controller_contracts(self.cits_config, self.tsp_config)
            if contract.tls_id in signal_controlled_tls
        ]
        if self.network_binding is not None:
            contracts = apply_network_binding(
                contracts, self.network_binding, aliases_by_tls=self.network_binding_aliases
            )
        return contracts

    def _bind_network(self) -> None:
        """Constrói o NetworkBinding a partir da rede SUMO configurada.

        A matriz de conflitos passa a vir dos ``<request foes>`` da própria
        rede, em vez da heurística de disjunção de fases — é isto que permite
        a redes reais (OSM, interseções joined) passarem a verificação sem o
        bypass global de ``set_signal_program_verified(True)``. Rede ausente
        ou ilegível não é fatal: regista o erro (fail-loud) e os contratos
        seguem sem matriz autoritativa, continuando fail-closed.
        """
        if not _network_profile_enabled(self.cits_config, self.tsp_config):
            return
        network = self.cits_config.sumo.get("network")
        if not network:
            return
        network_path = Path(str(network))
        if not network_path.is_absolute():
            network_path = self.cits_config.root / network_path
        try:
            self.network_binding = build_network_binding(network_path)
        except (FileNotFoundError, OSError, ValueError) as exc:
            self.network_binding_error = f"{network_path}: {exc}"
            print(
                f"[SAFETY] NetworkBinding indisponível ({self.network_binding_error}); "
                "contratos seguem sem matriz de conflitos autoritativa (fail-closed).",
                file=sys.stderr,
                flush=True,
            )
            return
        self.network_binding_aliases = network_binding_aliases(self.cits_config, self.tsp_config)

    def _read_network_states(
        self,
        adapter: TraciSimulationAdapter,
        signal_states: Dict[str, SignalState],
        responses: Iterable[SSEMLike],
        requests_by_id: Dict[str, SREMLike],
        sim_time_s: float,
    ) -> Dict[str, NetworkStateSnapshot]:
        active_requests_by_tls: Dict[str, int] = {}
        for response in responses:
            if response.status != ResponseStatus.PROCESSING.value:
                continue
            request = requests_by_id.get(response.request_id) or self.request_store.get_by_request_id(response.request_id)
            if request is not None:
                active_requests_by_tls[request.tls_id] = active_requests_by_tls.get(request.tls_id, 0) + 1

        snapshots: Dict[str, NetworkStateSnapshot] = {}
        for intersection in self.cits_config.signal_controlled_intersections:
            signal_state = signal_states.get(intersection.tls_id)
            if signal_state is None:
                continue
            snapshots[intersection.tls_id] = adapter.read_network_state(
                intersection,
                signal_state,
                sim_time_s,
                active_request_count=active_requests_by_tls.get(intersection.tls_id, 0),
            )
        return snapshots

    def _default_policy_report_path(self) -> Optional[str]:
        runtime_cfg = self.tsp_config.raw.get("policy_runtime", {})
        if self.policy_mode == "rl":
            return runtime_cfg.get("rl_policy_report", "reports/tabular_q_policy_report.json")
        return runtime_cfg.get("policy_report", "reports/policy_report.json")

    def _runtime_policy_summary(self) -> Dict[str, object]:
        if self.runtime_policy is None:
            return {
                "loaded": False,
                "mode": self.policy_mode,
                "source_path": self.policy_report_path or self._default_policy_report_path(),
            }
        return {
            "loaded": True,
            "mode": self.policy_mode,
            "policy_id": self.runtime_policy.policy_id,
            "algorithm": self.runtime_policy.algorithm,
            "is_reinforcement_learning": self.runtime_policy.is_reinforcement_learning,
            "training_environment": self.runtime_policy.training_environment,
            "safety_filter_required": self.runtime_policy.safety_filter_required,
            "rule_count": len(self.runtime_policy.rules),
            "source_path": str(self.runtime_policy.source_path) if self.runtime_policy.source_path else None,
        }

    def _process_rsu_queues(self, sim_time_s: float) -> List[SSEMLike]:
        responses: List[SSEMLike] = []
        for rsu_id, agent in self.rsu_agents.items():
            messages = self.broker.consume(rsu_id)
            responses.extend(agent.handle_messages(messages, sim_time_s))
        return responses


def _network_state_note(state: NetworkStateSnapshot) -> str:
    return (
        "network_state="
        f"active_requests:{state.active_request_count},"
        f"queue:{state.queue_vehicle_count},"
        f"halted:{state.halted_vehicle_count},"
        f"mean_speed_mps:{state.mean_speed_mps:.3f},"
        f"waiting_time_s:{state.waiting_time_s:.3f},"
        f"occupancy:{state.occupancy:.3f},"
        f"spillback_risk:{state.spillback_risk},"
        f"degraded:{state.degraded},"
        f"detector_read_failures:{state.detector_read_failures}"
    )


def _final_response_status(decision: TSPDecision, actuation: ActuationResult) -> str:
    if decision.action == TSPAction.NO_ACTION.value:
        return ResponseStatus.GRANTED.value
    if decision.action in {TSPAction.REJECT.value, TSPAction.REEVALUATE_NEXT_CYCLE.value}:
        return ResponseStatus.REJECTED.value
    if decision.status != DecisionStatus.APPROVED.value:
        return ResponseStatus.REJECTED.value
    if actuation.applied:
        return ResponseStatus.GRANTED.value
    return ResponseStatus.REJECTED.value


def _final_rejection_reason(decision: TSPDecision, actuation: ActuationResult) -> str:
    if actuation.reason and actuation.reason != "decision_not_actuable_or_not_approved":
        return actuation.reason
    return decision.reason


def _granted_strategy_for_decision(decision: TSPDecision, status: str) -> str:
    if status != ResponseStatus.GRANTED.value:
        return GrantedStrategy.NONE.value
    if decision.action == TSPAction.GREEN_EXTENSION.value:
        return GrantedStrategy.GREEN_EXTENSION.value
    if decision.action == TSPAction.EARLY_GREEN.value:
        return GrantedStrategy.EARLY_GREEN.value
    return GrantedStrategy.NONE.value


def _response_priority_sort_key(
    response: SSEMLike,
    requests_by_id: Dict[str, SREMLike],
    cits_config: CITSConfig,
) -> tuple[int, float, float, str]:
    request = requests_by_id.get(response.request_id)
    if response.status != ResponseStatus.PROCESSING.value or request is None:
        return (999, float("inf"), 0.0, response.request_id)
    hierarchy = cits_config.raw.get("priority_hierarchy", {})
    priority_rank = int(hierarchy.get(request.priority_level, 999))
    if request.priority_level == OperatorPriorityClass.EMERGENCY.value:
        priority_rank = min(priority_rank, 0)
    return (
        priority_rank,
        float(request.eta_to_stopline_s),
        -float(request.schedule_delay_s),
        request.request_id,
    )
