#!/usr/bin/env python3
"""C-ITS, TSP decision engine, Safety Layer, and actuation controller."""
from __future__ import annotations

from dataclasses import dataclass
import sys
from typing import Dict, Iterable, List, Optional

from pps57_cits.broker import InMemoryMessageBroker
from pps57_cits.config import CITSConfig
from pps57_cits.event_logger import CITSJsonlLogger, IncrementalCITSSummary
from pps57_cits.map_spat import build_mapem_messages, build_spatem_message_from_state
from pps57_cits.messages import CITSMessage, RequestStatus, SREMLike, SSEMLike
from pps57_cits.models import NetworkStateSnapshot, SignalState
from pps57_cits.obu import OBUEmulator
from pps57_cits.rsu import build_rsu_agents
from pps57_cits.traci_adapter import TraciSimulationAdapter, TraciUnavailableError
from pps57_cits.util import optional_int as _optional_int
from pps57_opt.policy_runtime import RuntimePolicy

from .actuator import TraciTSPActuator
from .config import TSPConfig
from .engine import TSPDecisionEngine
from .logger import TSPJsonlLogger, write_tsp_summary
from .models import ActuationResult, DecisionStatus, TSPDecision
from .safety import TSPSafetyLayer


@dataclass
class TSPControlController:
    cits_config: CITSConfig
    tsp_config: TSPConfig
    policy_mode: str = "baseline"
    policy_report_path: Optional[str] = None

    def __post_init__(self) -> None:
        self.broker = InMemoryMessageBroker()
        self.obu = OBUEmulator(self.cits_config)
        self.rsu_agents = build_rsu_agents(self.cits_config)
        self.engine = TSPDecisionEngine(self.cits_config, self.tsp_config)
        self.safety = TSPSafetyLayer(self.cits_config, self.tsp_config)
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
        verification_problems = self._verify_signal_programs(adapter)
        effective_actuation = apply_actuation and not verification_problems
        if verification_problems and apply_actuation:
            print("[SAFETY] Atuação TraCI desativada: verificação do programa semafórico falhou:")
            for problem in verification_problems:
                print(f"  - {problem}")
        # Propaga o resultado da verificação para a Safety Layer poder enforçar
        # `pedestrian_clearance_must_not_be_shortened` (fail-closed em falhas).
        self.safety.set_signal_program_verified(not verification_problems)
        actuator = TraciTSPActuator(adapter=adapter, apply_actuation=effective_actuation)

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

                    signal_states = {
                        intersection.tls_id: adapter.read_signal_state(intersection, sim_time_s)
                        for intersection in self.cits_config.intersections
                    }
                    spatem = [build_spatem_message_from_state(state) for state in signal_states.values()]
                    self._publish_log_collect(spatem, cits_logger, cits_summary)

                    observations = adapter.read_vehicle_observations()
                    requests = self.obu.generate_requests(observations, sim_time_s)
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

                    self._process_acknowledged_requests(
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
                "cits_total_messages": cits_summary_dict["total_messages"],
                "cits_by_type": cits_summary_dict["by_type"],
                "cits_acknowledged_messages": cits_summary_dict["acknowledged_messages"],
                "cits_rejected_messages": cits_summary_dict["rejected_messages"],
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
    ) -> None:
        # M6: invariante explícita "no máximo 1 intervenção por TLS por passo".
        # Até aqui isto dependia implicitamente do efeito colateral do cooldown
        # (frágil e silenciosamente quebrado em modo no-actuation/downgraded, onde
        # mark_applied não corre e a telemetria contava intervenções duplicadas).
        intervened_tls: set[str] = set()
        response_list = list(responses)
        active_requests_by_tls: Dict[str, int] = {}
        for response in response_list:
            if response.status != RequestStatus.ACKNOWLEDGED.value:
                continue
            request = requests_by_id.get(response.request_id)
            if request is not None:
                active_requests_by_tls[request.tls_id] = active_requests_by_tls.get(request.tls_id, 0) + 1

        for response in response_list:
            if response.status != RequestStatus.ACKNOWLEDGED.value:
                continue
            request = requests_by_id.get(response.request_id)
            if request is None:
                continue
            signal_state = signal_states.get(request.tls_id)
            if signal_state is None:
                continue
            baseline = self.engine.decide(request, signal_state, sim_time_s)
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
            validation = self.safety.validate(proposed, signal_state, sim_time_s)
            safe_decision = validation.safe_decision

            if safe_decision.requires_actuation and safe_decision.tls_id in intervened_tls:
                safe_decision = safe_decision.copy_with(
                    status=DecisionStatus.NOT_ACTUABLE.value,
                    reason="superseded_by_earlier_intervention_same_step",
                    notes=list(safe_decision.notes)
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
                    reason="superseded_by_earlier_intervention_same_step",
                )
            else:
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
                if validation.approved and safe_decision.requires_actuation:
                    actuation_error = (
                        not result.applied
                        and not result.no_actuation
                        and getattr(result, "severity", "info") == "error"
                    )
                    if result.applied or result.no_actuation or actuation_error:
                        self.safety.mark_applied(safe_decision, sim_time_s)
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

    def _verify_signal_programs(self, adapter: TraciSimulationAdapter) -> List[str]:
        """Verifica que o phase_mapping configurado bate certo com o programa real.

        Fail-closed: se o programa não puder ser lido, ou se algum índice de fase
        configurado estiver fora do programa, ou se o TLS for atuado (o motor
        assume tempo fixo), devolve problemas que desativam a atuação.
        """
        problems: List[str] = []
        for intersection in self.cits_config.intersections:
            tls_id = intersection.tls_id
            mapping = self.tsp_config.phase_mapping_for_tls(tls_id)
            corridor_idx = _optional_int(mapping.get("corridor_green_phase_index"))
            sequence = [
                idx
                for idx in (_optional_int(item) for item in mapping.get("phase_sequence", []))
                if idx is not None
            ]

            phase_count = adapter.read_program_phase_count(tls_id)
            if phase_count is None:
                problems.append(f"{tls_id}: programa TLS ilegível; impossível validar phase_mapping")
                continue
            if corridor_idx is None or not (0 <= corridor_idx < phase_count):
                problems.append(
                    f"{tls_id}: corridor_green_phase_index={corridor_idx} fora do programa (fases={phase_count})"
                )
            for idx in sequence:
                if not (0 <= idx < phase_count):
                    problems.append(
                        f"{tls_id}: phase_sequence índice {idx} fora do programa (fases={phase_count})"
                    )

            # Item 2: verificar que as fases *fora* do par (corridor, minor) são
            # de facto intergreen (sem 'g'/'G'). Fecha o resíduo de C1: sem isto
            # confiávamos cegamente que o programa SUMO continha amarelo/all-red
            # nas posições intermédias da phase_sequence.
            minor_idx = _optional_int(mapping.get("minor_green_phase_index"))
            green_phases = {idx for idx in (corridor_idx, minor_idx) if idx is not None}
            states = adapter.read_program_phase_states(tls_id)
            if states is None:
                problems.append(f"{tls_id}: estados de fase ilegíveis (fail-closed)")
            else:
                for idx, state in enumerate(states):
                    if idx in green_phases:
                        continue
                    if "g" in state.lower():
                        problems.append(
                            f"{tls_id}: fase {idx} ('{state}') é intermédia mas contém "
                            "verde — clearance amarelo/all-red não garantida"
                        )

            is_fixed = adapter.read_program_is_fixed_time(tls_id)
            if is_fixed is None:
                problems.append(
                    f"{tls_id}: não foi possível confirmar programa de tempo fixo (fail-closed)"
                )
            elif is_fixed is False:
                problems.append(
                    f"{tls_id}: programa atuado/adaptativo (tipo='{adapter.read_program_type(tls_id)}'); "
                    "o motor TSP assume controlo de tempo fixo"
                )
        return problems

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
            if response.status != RequestStatus.ACKNOWLEDGED.value:
                continue
            request = requests_by_id.get(response.request_id)
            if request is not None:
                active_requests_by_tls[request.tls_id] = active_requests_by_tls.get(request.tls_id, 0) + 1

        snapshots: Dict[str, NetworkStateSnapshot] = {}
        for intersection in self.cits_config.intersections:
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
        f"spillback_risk:{state.spillback_risk}"
    )
