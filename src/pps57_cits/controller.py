#!/usr/bin/env python3
"""C-ITS/V2X emulation controller."""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Dict, Iterable, List, Optional

from .broker import InMemoryMessageBroker
from .config import CITSConfig
from .event_logger import CITSJsonlLogger, IncrementalCITSSummary, write_summary_dict
from .map_spat import build_mapem_messages, build_spatem_message_from_state
from .messages import CITSMessage, SSEMLike
from .obu import OBUEmulator
from .rsu import build_rsu_agents
from .traci_adapter import TraciSimulationAdapter, TraciUnavailableError


@dataclass
class CITSEmulationController:
    config: CITSConfig

    def __post_init__(self) -> None:
        self.broker = InMemoryMessageBroker(
            transport_config=dict(self.config.raw.get("message_transport", {}))
        )
        self.obu = OBUEmulator(self.config)
        self.rsu_agents = build_rsu_agents(self.config)

    def run_with_sumo(self, steps: Optional[int] = None, sumo_binary: str = "sumo", gui: bool = False) -> Dict[str, object]:
        """Executa a emulação ligada ao SUMO via TraCI.

        Este método exige SUMO instalado e a rede já compilada com netconvert.
        """
        adapter = TraciSimulationAdapter(self.config, sumo_binary=sumo_binary, gui=gui)
        summary = IncrementalCITSSummary()
        log_path = self.config.path_from_root(self.config.logging.get("message_log", "outputs/cits_messages.jsonl"))

        try:
            adapter.start()
        except TraciUnavailableError:
            raise

        with CITSJsonlLogger(log_path) as logger:
            mapem = build_mapem_messages(self.config, sim_time_s=0.0)
            self._publish_log_collect(mapem, logger, summary)
            self._write_snapshots(mapem, [])
            latest_spatem: List[CITSMessage] = []

            step_count = 0
            try:
                while adapter.min_expected_number() > 0:
                    if steps is not None and step_count >= steps:
                        break
                    sim_time_s = adapter.simulation_step()
                    step_count += 1

                    # M2: drena mensagens do tick anterior antes de publicar as
                    # novas. NÃO drenar no fim do tick: isso destruía SSEMs
                    # publicados imediatamente antes de qualquer consumidor OBU
                    # poder vê-los, esvaziando arquiteturalmente o loop
                    # OBU->RSU->OBU. Memória continua limitada: no fim do tick
                    # apenas as filas deste tick estão preenchidas.
                    self.broker.drain_all_except([])
                    self.broker.advance_time(step_count)

                    signal_states = [
                        adapter.read_signal_state(intersection, sim_time_s)
                        for intersection in self.config.signal_controlled_intersections
                    ]
                    spatem = [build_spatem_message_from_state(state) for state in signal_states]
                    latest_spatem = list(spatem)
                    self._publish_log_collect(spatem, logger, summary)

                    observations = adapter.read_vehicle_observations()
                    requests = self.obu.generate_requests(observations, sim_time_s)
                    self._publish_log_collect(requests, logger, summary)

                    responses = self._process_rsu_queues(sim_time_s)
                    self._publish_log_collect(responses, logger, summary)
            finally:
                adapter.close()
            self._write_snapshots(mapem, latest_spatem)

        summary_path = self.config.path_from_root(self.config.logging.get("summary_report", "reports/cits_emulation_summary.json"))
        return write_summary_dict(
            summary_path,
            summary.to_dict(),
            extra={
                "mode": "sumo-traci",
                "steps": step_count,
                "scenario_id": self.config.raw.get("scenario_id"),
                "message_transport": self.broker.transport_stats(),
            },
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
