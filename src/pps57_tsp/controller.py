#!/usr/bin/env python3
"""Controlador do Pacote 4: C-ITS + motor TSP + Safety Layer + atuação."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

from pps57_cits.broker import InMemoryMessageBroker
from pps57_cits.config import CITSConfig, IntersectionConfig
from pps57_cits.event_logger import CITSJsonlLogger, summarise_messages
from pps57_cits.map_spat import build_mapem_messages, build_spatem_message_from_state
from pps57_cits.messages import CITSMessage, RequestStatus, SREMLike, SSEMLike
from pps57_cits.models import SignalState, VehicleObservation
from pps57_cits.obu import OBUEmulator
from pps57_cits.rsu import build_rsu_agents
from pps57_cits.traci_adapter import TraciSimulationAdapter, TraciUnavailableError

from .actuator import DryRunTSPActuator, TraciTSPActuator
from .config import TSPConfig
from .engine import TSPDecisionEngine
from .logger import TSPJsonlLogger, write_tsp_summary
from .models import ActuationResult, DecisionStatus, TSPDecision
from .safety import TSPSafetyLayer


@dataclass
class TSPControlController:
    cits_config: CITSConfig
    tsp_config: TSPConfig

    def __post_init__(self) -> None:
        self.broker = InMemoryMessageBroker()
        self.obu = OBUEmulator(self.cits_config)
        self.rsu_agents = build_rsu_agents(self.cits_config)
        self.engine = TSPDecisionEngine(self.cits_config, self.tsp_config)
        self.safety = TSPSafetyLayer(self.cits_config, self.tsp_config)

    def run_dry_run(self, steps: Optional[int] = None) -> Dict[str, object]:
        max_steps = int(steps or self.tsp_config.dry_run.get("steps", 90))
        cits_messages: List[CITSMessage] = []
        decisions: List[TSPDecision] = []
        actuations: List[ActuationResult] = []

        cits_log_path = self.cits_config.path_from_root(self.cits_config.logging.get("message_log", "outputs/cits_messages.jsonl"))
        decision_log_path = self.tsp_config.path_from_root(self.tsp_config.logging.get("decision_log", "outputs/tsp_decisions.jsonl"))
        actuation_log_path = self.tsp_config.path_from_root(self.tsp_config.logging.get("actuation_log", "outputs/tsp_actuation.jsonl"))
        actuator = DryRunTSPActuator()

        with CITSJsonlLogger(cits_log_path) as cits_logger, TSPJsonlLogger(decision_log_path) as decision_logger, TSPJsonlLogger(actuation_log_path) as actuation_logger:
            mapem = build_mapem_messages(self.cits_config, sim_time_s=0.0)
            self._publish_log_collect(mapem, cits_logger, cits_messages)

            for step in range(max(1, max_steps)):
                sim_time_s = float(step)
                signal_states = self._dry_run_signal_states(sim_time_s)
                spatem = [build_spatem_message_from_state(state) for state in signal_states.values()]
                self._publish_log_collect(spatem, cits_logger, cits_messages)

                observations = self._dry_run_observations(sim_time_s)
                requests = self.obu.generate_requests(observations, sim_time_s)
                requests_by_id = {request.request_id: request for request in requests}
                self._publish_log_collect(requests, cits_logger, cits_messages)

                responses = self._process_rsu_queues(sim_time_s)
                self._publish_log_collect(responses, cits_logger, cits_messages)

                self._process_acknowledged_requests(
                    responses=responses,
                    requests_by_id=requests_by_id,
                    signal_states=signal_states,
                    actuator=actuator,
                    sim_time_s=sim_time_s,
                    decision_logger=decision_logger,
                    actuation_logger=actuation_logger,
                    decisions=decisions,
                    actuations=actuations,
                )

        summary_path = self.tsp_config.path_from_root(self.tsp_config.logging.get("summary_report", "reports/tsp_emulation_summary.json"))
        cits_summary = summarise_messages(cits_messages)
        return write_tsp_summary(
            summary_path,
            decisions,
            actuations,
            extra={
                "mode": "dry-run",
                "steps": max_steps,
                "scenario_id": self.tsp_config.raw.get("scenario_id"),
                "cits_total_messages": cits_summary["total_messages"],
                "cits_by_type": cits_summary["by_type"],
                "cits_acknowledged_messages": cits_summary["acknowledged_messages"],
                "cits_rejected_messages": cits_summary["rejected_messages"],
                "note": "Pacote 4 dry-run valida decisões TSP e Safety Layer sem lançar SUMO/TraCI.",
            },
        )

    def run_with_sumo(
        self,
        steps: Optional[int] = None,
        sumo_binary: str = "sumo",
        gui: bool = False,
        apply_actuation: bool = True,
    ) -> Dict[str, object]:
        adapter = TraciSimulationAdapter(self.cits_config, sumo_binary=sumo_binary, gui=gui)
        cits_messages: List[CITSMessage] = []
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
        actuator = TraciTSPActuator(adapter=adapter, apply_actuation=effective_actuation)

        step_count = 0
        with CITSJsonlLogger(cits_log_path) as cits_logger, TSPJsonlLogger(decision_log_path) as decision_logger, TSPJsonlLogger(actuation_log_path) as actuation_logger:
            mapem = build_mapem_messages(self.cits_config, sim_time_s=0.0)
            self._publish_log_collect(mapem, cits_logger, cits_messages)
            try:
                while adapter.min_expected_number() > 0:
                    if steps is not None and step_count >= steps:
                        break
                    sim_time_s = adapter.simulation_step()
                    step_count += 1

                    signal_states = {
                        intersection.tls_id: adapter.read_signal_state(intersection, sim_time_s)
                        for intersection in self.cits_config.intersections
                    }
                    spatem = [build_spatem_message_from_state(state) for state in signal_states.values()]
                    self._publish_log_collect(spatem, cits_logger, cits_messages)

                    observations = adapter.read_vehicle_observations()
                    requests = self.obu.generate_requests(observations, sim_time_s)
                    requests_by_id = {request.request_id: request for request in requests}
                    self._publish_log_collect(requests, cits_logger, cits_messages)

                    responses = self._process_rsu_queues(sim_time_s)
                    self._publish_log_collect(responses, cits_logger, cits_messages)

                    self._process_acknowledged_requests(
                        responses=responses,
                        requests_by_id=requests_by_id,
                        signal_states=signal_states,
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
        cits_summary = summarise_messages(cits_messages)
        return write_tsp_summary(
            summary_path,
            decisions,
            actuations,
            extra={
                "mode": "sumo-traci",
                "steps": step_count,
                "scenario_id": self.tsp_config.raw.get("scenario_id"),
                "actuation_requested": apply_actuation,
                "actuation_enabled": effective_actuation,
                "signal_program_verification": {
                    "problems": verification_problems,
                    "actuation_downgraded": bool(verification_problems and apply_actuation),
                },
                "cits_total_messages": cits_summary["total_messages"],
                "cits_by_type": cits_summary["by_type"],
                "cits_acknowledged_messages": cits_summary["acknowledged_messages"],
                "cits_rejected_messages": cits_summary["rejected_messages"],
            },
        )

    def _process_acknowledged_requests(
        self,
        *,
        responses: Iterable[SSEMLike],
        requests_by_id: Dict[str, SREMLike],
        signal_states: Dict[str, SignalState],
        actuator: DryRunTSPActuator | TraciTSPActuator,
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
        for response in responses:
            if response.status != RequestStatus.ACKNOWLEDGED.value:
                continue
            request = requests_by_id.get(response.request_id)
            if request is None:
                continue
            signal_state = signal_states.get(request.tls_id)
            if signal_state is None:
                continue
            proposed = self.engine.decide(request, signal_state, sim_time_s)
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
                    dry_run=not getattr(actuator, "apply_actuation", False),
                    command="none",
                    reason="superseded_by_earlier_intervention_same_step",
                )
            else:
                result = actuator.apply(safe_decision, signal_state, sim_time_s)
                if result.applied:
                    self.safety.mark_applied(safe_decision, sim_time_s)
                # Marca o TLS como já intervindo neste passo se uma atuação foi
                # aprovada — mesmo sem aplicação real — para a telemetria de
                # no-actuation/downgraded refletir a intervenção única efetiva.
                if validation.approved and safe_decision.requires_actuation:
                    intervened_tls.add(safe_decision.tls_id)

            decision_logger.write(safe_decision)
            actuation_logger.write(result)
            decisions.append(safe_decision)
            actuations.append(result)

    def _publish_log_collect(
        self,
        messages: Iterable[CITSMessage],
        logger: CITSJsonlLogger,
        all_messages: List[CITSMessage],
    ) -> None:
        for message in messages:
            self.broker.publish(message)
            logger.write(message)
            all_messages.append(message)

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

    def _process_rsu_queues(self, sim_time_s: float) -> List[SSEMLike]:
        responses: List[SSEMLike] = []
        for rsu_id, agent in self.rsu_agents.items():
            messages = self.broker.consume(rsu_id)
            responses.extend(agent.handle_messages(messages, sim_time_s))
        return responses

    def _dry_run_observations(self, sim_time_s: float) -> List[VehicleObservation]:
        scenarios = [
            # Gera green extension em I2: corredor está verde, mas a fase termina em breve.
            ("bus_STCP500_W_TSP_001", "STCP500_PROXY_W", "route_boavista_east_to_west", "I1_I2", 650.0, 10.0),
            # Gera early green em I6: aproximação prioritária está em vermelho.
            ("bus_STCP500_E_TSP_002", "STCP500_PROXY_E", "route_boavista_west_to_east", "I7_I6", 830.0, 9.5),
            # Gera no_action em I4: verde já é suficiente para atravessar.
            ("bus_STCP502_W_TSP_003", "STCP502_PROXY_W", "route_boavista_east_to_west", "I3_I4", 800.0, 8.0),
        ]
        observations: List[VehicleObservation] = []
        for vehicle_id, line_id, route_id, edge_id, lane_length, speed in scenarios:
            distance = max(0.0, 280.0 - sim_time_s * speed)
            lane_position = max(0.0, lane_length - distance)
            observations.append(
                VehicleObservation(
                    vehicle_id=vehicle_id,
                    vehicle_class="bus",
                    type_id="bus_12m",
                    line_id=line_id,
                    route_id=route_id,
                    edge_id=edge_id,
                    lane_id=f"{edge_id}_0",
                    lane_position_m=lane_position,
                    lane_length_m=lane_length,
                    speed_mps=speed,
                    waiting_time_s=0.0,
                    accumulated_waiting_time_s=0.0,
                    schedule_delay_s=90.0,
                )
            )
        return observations

    def _dry_run_signal_states(self, sim_time_s: float) -> Dict[str, SignalState]:
        states: Dict[str, SignalState] = {}
        for intersection in self.cits_config.intersections:
            if intersection.tls_id == "I2":
                # Corredor verde quase a terminar: força caso de green extension.
                states[intersection.tls_id] = self._signal_state(
                    intersection,
                    sim_time_s,
                    phase_index=0,
                    ryg="GGrr",
                    next_switch_s=sim_time_s + 2.0,
                    spent_s=33.0,
                )
            elif intersection.tls_id == "I6":
                # Corredor vermelho, fase conflitante já cumpriu verde mínimo: early green seguro.
                states[intersection.tls_id] = self._signal_state(
                    intersection,
                    sim_time_s,
                    phase_index=2,
                    ryg="rrGG",
                    next_switch_s=sim_time_s + 20.0,
                    spent_s=20.0,
                )
            elif intersection.tls_id == "I4":
                # Corredor verde com tempo suficiente: no action.
                states[intersection.tls_id] = self._signal_state(
                    intersection,
                    sim_time_s,
                    phase_index=0,
                    ryg="GGrr",
                    next_switch_s=sim_time_s + 40.0,
                    spent_s=5.0,
                )
            else:
                states[intersection.tls_id] = self._signal_state(
                    intersection,
                    sim_time_s,
                    phase_index=0,
                    ryg="GGrr",
                    next_switch_s=sim_time_s + 35.0,
                    spent_s=5.0,
                )
        return states

    @staticmethod
    def _signal_state(
        intersection: IntersectionConfig,
        sim_time_s: float,
        *,
        phase_index: int,
        ryg: str,
        next_switch_s: float,
        spent_s: float,
    ) -> SignalState:
        return SignalState(
            intersection_id=intersection.intersection_id,
            tls_id=intersection.tls_id,
            rsu_id=intersection.rsu_id,
            timestamp_s=sim_time_s,
            current_phase_index=phase_index,
            current_program_id="tsp_dry_run_program",
            red_yellow_green_state=ryg,
            next_switch_s=next_switch_s,
            spent_duration_s=spent_s,
            controlled_lanes=[f"{edge}_0" for edge in intersection.controlled_approach_edges],
        )


def _optional_int(value: object) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
