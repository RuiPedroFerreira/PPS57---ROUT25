#!/usr/bin/env python3
"""Adaptador TraCI para ler estado SUMO durante a emulação C-ITS."""
from __future__ import annotations

import shutil
from typing import Iterable, List, Optional

from .config import CITSConfig, IntersectionConfig
from .models import SignalState, VehicleObservation


class TraciUnavailableError(RuntimeError):
    pass


class TraciSimulationAdapter:
    def __init__(self, config: CITSConfig, sumo_binary: str = "sumo", gui: bool = False) -> None:
        self.config = config
        self.sumo_binary = sumo_binary
        self.gui = gui
        self.traci = None

    def start(self, extra_args: Optional[List[str]] = None) -> None:
        try:
            import traci  # type: ignore
        except ImportError as exc:
            raise TraciUnavailableError(
                "Python package 'traci' is not available. Install SUMO and ensure SUMO tools are on PYTHONPATH."
            ) from exc

        binary = self.sumo_binary
        if self.gui:
            binary = self.config.sumo.get("default_gui_binary", "sumo-gui")
        if shutil.which(binary) is None:
            raise TraciUnavailableError(f"SUMO binary '{binary}' not found in PATH.")

        sumocfg = self.config.path_from_root(self.config.sumo.get("sumocfg", "sumo/corredor.sumocfg"))
        cmd = [binary, "-c", str(sumocfg), "--duration-log.statistics"]
        if extra_args:
            cmd.extend(extra_args)
        traci.start(cmd)
        self.traci = traci

    def close(self) -> None:
        if self.traci is not None:
            self.traci.close()
            self.traci = None

    def simulation_step(self) -> float:
        traci = self._require_traci()
        traci.simulationStep()
        return float(traci.simulation.getTime())

    def min_expected_number(self) -> int:
        traci = self._require_traci()
        return int(traci.simulation.getMinExpectedNumber())

    def read_vehicle_observations(self) -> List[VehicleObservation]:
        traci = self._require_traci()
        observations: List[VehicleObservation] = []
        for vehicle_id in traci.vehicle.getIDList():
            edge_id = traci.vehicle.getRoadID(vehicle_id)
            if edge_id not in self.config.edge_to_intersection:
                continue
            lane_id = traci.vehicle.getLaneID(vehicle_id)
            if not lane_id:
                continue
            try:
                lane_length = float(traci.lane.getLength(lane_id))
            except Exception:
                lane_length = 0.0
            try:
                line_id = traci.vehicle.getLine(vehicle_id)
            except Exception:
                line_id = ""
            try:
                vehicle_class = traci.vehicle.getVehicleClass(vehicle_id)
            except Exception:
                vehicle_class = ""
            try:
                type_id = traci.vehicle.getTypeID(vehicle_id)
            except Exception:
                type_id = ""
            try:
                accumulated_waiting_time = float(traci.vehicle.getAccumulatedWaitingTime(vehicle_id))
            except Exception:
                accumulated_waiting_time = 0.0

            observations.append(
                VehicleObservation(
                    vehicle_id=vehicle_id,
                    vehicle_class=vehicle_class,
                    type_id=type_id,
                    line_id=line_id,
                    route_id=traci.vehicle.getRouteID(vehicle_id),
                    edge_id=edge_id,
                    lane_id=lane_id,
                    lane_position_m=float(traci.vehicle.getLanePosition(vehicle_id)),
                    lane_length_m=lane_length,
                    speed_mps=float(traci.vehicle.getSpeed(vehicle_id)),
                    waiting_time_s=float(traci.vehicle.getWaitingTime(vehicle_id)),
                    accumulated_waiting_time_s=accumulated_waiting_time,
                )
            )
        return observations

    def read_signal_state(self, intersection: IntersectionConfig, sim_time_s: float) -> SignalState:
        traci = self._require_traci()
        tls_id = intersection.tls_id
        try:
            controlled_lanes = list(traci.trafficlight.getControlledLanes(tls_id))
        except Exception:
            controlled_lanes = []
        return SignalState(
            intersection_id=intersection.intersection_id,
            tls_id=tls_id,
            rsu_id=intersection.rsu_id,
            timestamp_s=sim_time_s,
            current_phase_index=_safe_call(lambda: traci.trafficlight.getPhase(tls_id)),
            current_program_id=_safe_call(lambda: traci.trafficlight.getProgram(tls_id)),
            red_yellow_green_state=_safe_call(lambda: traci.trafficlight.getRedYellowGreenState(tls_id)),
            next_switch_s=_safe_call(lambda: float(traci.trafficlight.getNextSwitch(tls_id))),
            spent_duration_s=_safe_call(lambda: float(traci.trafficlight.getSpentDuration(tls_id))),
            controlled_lanes=controlled_lanes,
        )

    def _require_traci(self):  # type: ignore[no-untyped-def]
        if self.traci is None:
            raise TraciUnavailableError("TraCI simulation has not been started.")
        return self.traci


def _safe_call(callable_):  # type: ignore[no-untyped-def]
    try:
        return callable_()
    except Exception:
        return None
