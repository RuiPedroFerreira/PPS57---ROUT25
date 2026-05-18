#!/usr/bin/env python3
"""Controlador de emulação C-ITS/V2X do Pacote 3."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .broker import InMemoryMessageBroker
from .config import CITSConfig
from .event_logger import CITSJsonlLogger, write_summary
from .map_spat import build_mapem_messages, build_spatem_message_from_state, build_static_spatem_messages
from .messages import CITSMessage, SREMLike, SSEMLike
from .models import VehicleObservation
from .obu import OBUEmulator
from .rsu import build_rsu_agents
from .traci_adapter import TraciSimulationAdapter, TraciUnavailableError


@dataclass
class CITSEmulationController:
    config: CITSConfig

    def __post_init__(self) -> None:
        self.broker = InMemoryMessageBroker()
        self.obu = OBUEmulator(self.config)
        self.rsu_agents = build_rsu_agents(self.config)

    def run_dry_run(self, steps: int = 60) -> Dict[str, object]:
        """Executa uma demonstração sem SUMO, útil para validar o Pacote 3."""
        all_messages: List[CITSMessage] = []
        log_path = self.config.path_from_root(self.config.logging.get("message_log", "outputs/cits_messages.jsonl"))

        with CITSJsonlLogger(log_path) as logger:
            mapem = build_mapem_messages(self.config, sim_time_s=0.0)
            spatem = build_static_spatem_messages(self.config, sim_time_s=0.0)
            self._publish_log_collect(mapem + spatem, logger, all_messages)
            self._write_snapshots(mapem, spatem)

            for step in range(max(1, steps)):
                sim_time_s = float(step)
                observations = self._dry_run_observations(sim_time_s)
                requests = self.obu.generate_requests(observations, sim_time_s)
                self._publish_log_collect(requests, logger, all_messages)

                responses = self._process_rsu_queues(sim_time_s)
                self._publish_log_collect(responses, logger, all_messages)

        summary_path = self.config.path_from_root(self.config.logging.get("summary_report", "reports/cits_emulation_summary.json"))
        return write_summary(
            summary_path,
            all_messages,
            extra={
                "mode": "dry-run",
                "steps": steps,
                "scenario_id": self.config.raw.get("scenario_id"),
                "note": "Dry-run validates message flow without launching SUMO/TraCI.",
            },
        )

    def run_with_sumo(self, steps: Optional[int] = None, sumo_binary: str = "sumo", gui: bool = False) -> Dict[str, object]:
        """Executa a emulação ligada ao SUMO via TraCI.

        Este método exige SUMO instalado e a rede já compilada com netconvert.
        """
        adapter = TraciSimulationAdapter(self.config, sumo_binary=sumo_binary, gui=gui)
        all_messages: List[CITSMessage] = []
        log_path = self.config.path_from_root(self.config.logging.get("message_log", "outputs/cits_messages.jsonl"))

        try:
            adapter.start()
        except TraciUnavailableError:
            raise

        with CITSJsonlLogger(log_path) as logger:
            mapem = build_mapem_messages(self.config, sim_time_s=0.0)
            self._publish_log_collect(mapem, logger, all_messages)
            self._write_snapshots(mapem, [])

            step_count = 0
            try:
                while adapter.min_expected_number() > 0:
                    sim_time_s = adapter.simulation_step()
                    step_count += 1
                    if steps is not None and step_count > steps:
                        break

                    signal_states = [adapter.read_signal_state(intersection, sim_time_s) for intersection in self.config.intersections]
                    spatem = [build_spatem_message_from_state(state) for state in signal_states]
                    self._publish_log_collect(spatem, logger, all_messages)

                    observations = adapter.read_vehicle_observations()
                    requests = self.obu.generate_requests(observations, sim_time_s)
                    self._publish_log_collect(requests, logger, all_messages)

                    responses = self._process_rsu_queues(sim_time_s)
                    self._publish_log_collect(responses, logger, all_messages)
            finally:
                adapter.close()

        summary_path = self.config.path_from_root(self.config.logging.get("summary_report", "reports/cits_emulation_summary.json"))
        return write_summary(
            summary_path,
            all_messages,
            extra={
                "mode": "sumo-traci",
                "steps": step_count,
                "scenario_id": self.config.raw.get("scenario_id"),
            },
        )

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

    def _process_rsu_queues(self, sim_time_s: float) -> List[SSEMLike]:
        responses: List[SSEMLike] = []
        for rsu_id, agent in self.rsu_agents.items():
            messages = self.broker.consume(rsu_id)
            responses.extend(agent.handle_messages(messages, sim_time_s))
        return responses

    def _write_snapshots(self, mapem: List[CITSMessage], spatem: List[CITSMessage]) -> None:
        mapem_path = self.config.path_from_root(self.config.logging.get("mapem_snapshot", "outputs/cits_mapem_snapshot.json"))
        spatem_path = self.config.path_from_root(self.config.logging.get("spatem_snapshot", "outputs/cits_spatem_snapshot.json"))
        mapem_path.parent.mkdir(parents=True, exist_ok=True)
        spatem_path.parent.mkdir(parents=True, exist_ok=True)
        mapem_path.write_text(json.dumps([message.to_dict() for message in mapem], indent=2, ensure_ascii=False), encoding="utf-8")
        spatem_path.write_text(json.dumps([message.to_dict() for message in spatem], indent=2, ensure_ascii=False), encoding="utf-8")

    def _dry_run_observations(self, sim_time_s: float) -> List[VehicleObservation]:
        """Gera autocarros sintéticos a aproximarem-se das RSUs do corredor.

        A lógica foi desenhada para testar SREM/SSEM antes de ligar o TraCI.
        """
        observations: List[VehicleObservation] = []
        # Deslocação simples ao longo de três aproximações para gerar pedidos em vários ciclos.
        scenarios = [
            ("bus_STCP500_W_DRY_001", "STCP500_PROXY_W", "route_boavista_east_to_west", "I1_I2", 650.0, 10.0),
            ("bus_STCP500_E_DRY_002", "STCP500_PROXY_E", "route_boavista_west_to_east", "I7_I6", 830.0, 9.5),
            ("bus_STCP502_W_DRY_003", "STCP502_PROXY_W", "route_boavista_east_to_west", "I3_I4", 800.0, 8.0),
        ]
        for vehicle_id, line_id, route_id, edge_id, lane_length, speed in scenarios:
            # O autocarro parte a 280 m da stopline e entra na janela de pedido.
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
