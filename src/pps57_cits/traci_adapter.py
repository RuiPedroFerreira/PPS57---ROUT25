#!/usr/bin/env python3
"""Adaptador TraCI para ler estado SUMO durante a emulação C-ITS."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import shutil
import socket
import sys
from typing import Any, List, Optional

from pps57_sumo.environment import apply_sumo_environment

from .config import CITSConfig, IntersectionConfig
from .models import NetworkStateSnapshot, SignalState, VehicleObservation


class TraciUnavailableError(RuntimeError):
    pass


class TraciSimulationAdapter:
    def __init__(self, config: CITSConfig, sumo_binary: str = "sumo", gui: bool = False) -> None:
        self.config = config
        self.sumo_binary = sumo_binary
        self.gui = gui
        self.traci = None
        self._lane_length_cache: dict[str, float] = {}
        self._edge_lanes_cache: dict[str, list[str]] = {}
        self._controlled_links_cache: dict[str, list[Any]] = {}
        self._subscribed_lanes: set[str] = set()
        self._subscription_var_ids_by_name: dict[str, object] = {}

    def start(self, extra_args: Optional[List[str]] = None) -> None:
        apply_sumo_environment()
        try:
            traci = _load_traci()
        except ImportError as exc:
            raise TraciUnavailableError(
                "Python package 'traci' is not available. Install SUMO or project requirements, and ensure SUMO tools are on PYTHONPATH or SUMO_HOME."
            ) from exc

        binary = self.sumo_binary
        if self.gui:
            binary = self.config.sumo.get("default_gui_binary", "sumo-gui")
        if shutil.which(binary) is None:
            raise TraciUnavailableError(f"SUMO binary '{binary}' not found in PATH.")

        cmd = self._sumo_command(binary, extra_args)
        # numRetries generoso: o arranque a frio do sumo-gui sob WSLg/WSLg-GL
        # pode demorar alguns segundos a abrir o socket TraCI.
        traci.start(cmd, port=_resolve_traci_port(), numRetries=60)
        self.traci = traci

    def _sumo_command(self, binary: str, extra_args: Optional[List[str]] = None) -> List[str]:
        sumocfg = self.config.path_from_root(self.config.sumo.get("sumocfg", "sumo/corredor.sumocfg"))
        cmd = [binary, "-c", str(sumocfg), "--duration-log.statistics"]
        if self.gui:
            # Sem --start o sumo-gui fica pausado à espera do botão Play do
            # utilizador e NÃO serve TraCI -> o cliente fica em
            # "Connection refused, Retrying" indefinidamente. --start arranca a
            # simulação já sob controlo TraCI; --quit-on-end fecha a janela no
            # fim do run (ex.: --steps 7200).
            cmd += ["--start", "--quit-on-end"]
        if extra_args:
            cmd.extend(extra_args)
        return cmd

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
            observation = self._read_vehicle_observation(vehicle_id)
            if observation is not None:
                observations.append(observation)
        return observations

    def _read_vehicle_observation(self, vehicle_id: str) -> Optional[VehicleObservation]:
        traci = self._require_traci()
        try:
            edge_id = traci.vehicle.getRoadID(vehicle_id)
            if edge_id not in self.config.edge_to_intersection:
                return None
            lane_id = traci.vehicle.getLaneID(vehicle_id)
            if not lane_id:
                return None
            route_edges = list(_safe_call(lambda: traci.vehicle.getRoute(vehicle_id)) or [])
            route_index = _safe_call(lambda: int(traci.vehicle.getRouteIndex(vehicle_id)))
            next_edge_id = ""
            if route_index is not None and 0 <= route_index + 1 < len(route_edges):
                next_edge_id = str(route_edges[route_index + 1])
            lane_position = float(traci.vehicle.getLanePosition(vehicle_id))
            lane_length = self._lane_length(lane_id)
            queue_ahead = self._queue_ahead_vehicle_count(lane_id, lane_position)
            accumulated_waiting_time = float(_safe_call(lambda: traci.vehicle.getAccumulatedWaitingTime(vehicle_id)) or 0.0)
            return VehicleObservation(
                vehicle_id=vehicle_id,
                vehicle_class=str(_safe_call(lambda: traci.vehicle.getVehicleClass(vehicle_id)) or ""),
                type_id=str(_safe_call(lambda: traci.vehicle.getTypeID(vehicle_id)) or ""),
                line_id=str(_safe_call(lambda: traci.vehicle.getLine(vehicle_id)) or ""),
                route_id=str(_safe_call(lambda: traci.vehicle.getRouteID(vehicle_id)) or ""),
                edge_id=edge_id,
                lane_id=lane_id,
                lane_position_m=lane_position,
                lane_length_m=lane_length,
                speed_mps=float(_safe_call(lambda: traci.vehicle.getSpeed(vehicle_id)) or 0.0),
                waiting_time_s=float(_safe_call(lambda: traci.vehicle.getWaitingTime(vehicle_id)) or 0.0),
                accumulated_waiting_time_s=accumulated_waiting_time,
                route_edges=route_edges,
                next_edge_id=next_edge_id,
                queue_ahead_vehicle_count=queue_ahead,
                stop_count=int(_safe_call(lambda: traci.vehicle.getStopState(vehicle_id)) or 0),
            )
        except Exception:
            return None

    def _lane_length(self, lane_id: str) -> float:
        if lane_id not in self._lane_length_cache:
            traci = self._require_traci()
            self._lane_length_cache[lane_id] = float(_safe_call(lambda: traci.lane.getLength(lane_id)) or 0.0)
        return self._lane_length_cache[lane_id]

    def _queue_ahead_vehicle_count(self, lane_id: str, lane_position_m: float) -> int:
        traci = self._require_traci()
        vehicle_ids = list(_safe_call(lambda: traci.lane.getLastStepVehicleIDs(lane_id)) or [])
        count = 0
        for other_id in vehicle_ids:
            other_position = _safe_call(lambda other_id=other_id: float(traci.vehicle.getLanePosition(other_id)))
            other_speed = _safe_call(lambda other_id=other_id: float(traci.vehicle.getSpeed(other_id)))
            if other_position is not None and other_speed is not None and other_position > lane_position_m and other_speed < 0.5:
                count += 1
        return count


    def set_phase_duration(self, tls_id: str, duration_s: float) -> None:
        """Define a duração restante da fase corrente do semáforo.

        Este é o comando TraCI usado no MVP para extensão de verde e
        truncagem controlada de fase. Evita manipular diretamente o estado
        vermelho/amarelo/verde.
        """
        traci = self._require_traci()
        traci.trafficlight.setPhaseDuration(tls_id, float(duration_s))

    def _program_logic(self, tls_id: str):  # type: ignore[no-untyped-def]
        traci = self._require_traci()
        current_program = _safe_call(lambda: traci.trafficlight.getProgram(tls_id))
        fallback = None
        for getter in ("getAllProgramLogics", "getCompleteRedYellowGreenDefinition"):
            try:
                logics = getattr(traci.trafficlight, getter)(tls_id)
            except Exception:
                continue
            for logic in logics or []:
                if fallback is None:
                    fallback = logic
                if current_program is not None and _logic_program_id(logic) == str(current_program):
                    return logic
            if fallback is not None and current_program is None:
                return fallback
        if fallback is not None and current_program is not None:
            return None
        return None

    def read_program_phase_count(self, tls_id: str) -> Optional[int]:
        """Número de fases do programa carregado, ou None se indeterminável."""
        logic = self._program_logic(tls_id)
        try:
            return len(logic.phases) if logic is not None else None
        except Exception:
            return None

    def read_program_type(self, tls_id: str) -> Optional[str]:
        """Tipo bruto do programa TLS (código SUMO), só para diagnóstico."""
        logic = self._program_logic(tls_id)
        if logic is None:
            return None
        try:
            return str(getattr(logic, "type"))
        except Exception:
            return None

    def read_program_phase_states(self, tls_id: str) -> Optional[List[str]]:
        """Lista os estados RYG das fases do programa atual, ou None se ilegível."""
        logic = self._program_logic(tls_id)
        if logic is None:
            return None
        try:
            return [str(getattr(phase, "state")) for phase in logic.phases]
        except Exception:
            return None

    def read_program_phase_durations(self, tls_id: str) -> Optional[List[float]]:
        """Lista as durações nominais das fases do programa atual, ou None se ilegível."""
        logic = self._program_logic(tls_id)
        if logic is None:
            return None
        try:
            return [float(getattr(phase, "duration")) for phase in logic.phases]
        except Exception:
            return None

    def read_program_is_fixed_time(self, tls_id: str) -> Optional[bool]:
        """Classifica o programa por *comportamento*, não pela etiqueta.

        Devolve True só se for possível confirmar tempo fixo, False se for
        atuado/adaptativo, None se indeterminável. O tipo SUMO é um enum inteiro
        cuja codificação não é fiável entre versões, por isso o sinal robusto é:
        num programa estático cada fase tem minDur == maxDur == duration; num
        programa atuado as fases extensíveis têm minDur < maxDur. O chamador deve
        tratar None como "não confirmado" (fail-closed).
        """
        logic = self._program_logic(tls_id)
        if logic is None:
            return None
        try:
            type_repr = str(getattr(logic, "type")).strip().lower()
        except Exception:
            type_repr = ""
        if type_repr in {"actuated", "delay_based", "delaybased", "nema"}:
            return False
        try:
            phases = list(logic.phases)
        except Exception:
            return None
        if not phases:
            return None
        for phase in phases:
            try:
                min_dur = float(getattr(phase, "minDur"))
                max_dur = float(getattr(phase, "maxDur"))
            except Exception:
                return None  # sem min/max fiável -> não confirmado
            if abs(max_dur - min_dur) > 1e-6:
                return False  # janela extensível -> atuado/adaptativo
        return True

    def read_signal_state(self, intersection: IntersectionConfig, sim_time_s: float) -> SignalState:
        traci = self._require_traci()
        tls_id = intersection.tls_id
        try:
            controlled_lanes = list(traci.trafficlight.getControlledLanes(tls_id))
        except Exception:
            controlled_lanes = []
        controlled_links = self._controlled_links(tls_id)
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
            controlled_links=controlled_links,
        )

    def _controlled_links(self, tls_id: str) -> list[Any]:
        if tls_id not in self._controlled_links_cache:
            traci = self._require_traci()
            self._controlled_links_cache[tls_id] = list(_safe_call(lambda: traci.trafficlight.getControlledLinks(tls_id)) or [])
        return self._controlled_links_cache[tls_id]

    def read_network_state(
        self,
        intersection: IntersectionConfig,
        signal_state: SignalState,
        sim_time_s: float,
        *,
        active_request_count: int = 0,
    ) -> NetworkStateSnapshot:
        """Read instantaneous SUMO/TraCI network KPIs for a TLS approach set.

        These are runtime-observable features, not end-of-trip KPIs. They can be
        safely used as RL/decision-engine inputs because they are known at the
        decision time.
        """
        traci = self._require_traci()
        lanes = self._network_state_lanes(intersection, signal_state)
        vehicle_count = 0
        halted_vehicle_count = 0
        waiting_time_s = 0.0
        occupancy_values: list[float] = []
        weighted_speed_sum = 0.0
        speed_weight = 0
        detector_read_failures = 0
        failed_lanes: list[str] = []

        for lane_id in lanes:
            self._subscribe_lane_if_possible(lane_id)
            lane_values = self._lane_subscription_results(lane_id)
            lane_failed = False
            lane_vehicle_count_raw = self._subscription_value(lane_values, "LAST_STEP_VEHICLE_NUMBER")
            if lane_vehicle_count_raw is None:
                lane_vehicle_count_raw = _safe_call(lambda lane_id=lane_id: traci.lane.getLastStepVehicleNumber(lane_id))
                if lane_vehicle_count_raw is None:
                    lane_failed = True
            lane_halted_raw = self._subscription_value(lane_values, "LAST_STEP_VEHICLE_HALTING_NUMBER")
            if lane_halted_raw is None:
                lane_halted_raw = _safe_call(lambda lane_id=lane_id: traci.lane.getLastStepHaltingNumber(lane_id))
                if lane_halted_raw is None:
                    lane_failed = True
            lane_speed_raw = self._subscription_value(lane_values, "LAST_STEP_MEAN_SPEED")
            if lane_speed_raw is None:
                lane_speed_raw = _safe_call(lambda lane_id=lane_id: traci.lane.getLastStepMeanSpeed(lane_id))
                if lane_speed_raw is None:
                    lane_failed = True
            lane_waiting_raw = self._subscription_value(lane_values, "VAR_WAITING_TIME")
            if lane_waiting_raw is None:
                lane_waiting_raw = _safe_call(lambda lane_id=lane_id: traci.lane.getWaitingTime(lane_id))
                if lane_waiting_raw is None:
                    lane_failed = True
            lane_occupancy = self._subscription_value(lane_values, "LAST_STEP_OCCUPANCY")
            if lane_occupancy is None:
                lane_occupancy = _safe_call(lambda lane_id=lane_id: traci.lane.getLastStepOccupancy(lane_id))
                if lane_occupancy is None:
                    lane_failed = True

            if lane_failed:
                detector_read_failures += 1
                failed_lanes.append(lane_id)

            lane_vehicle_count = int(lane_vehicle_count_raw or 0)
            lane_halted = int(lane_halted_raw or 0)
            lane_speed = float(lane_speed_raw or 0.0)
            lane_waiting = float(lane_waiting_raw or 0.0)

            vehicle_count += lane_vehicle_count
            halted_vehicle_count += lane_halted
            waiting_time_s += lane_waiting
            if lane_vehicle_count > 0:
                weighted_speed_sum += lane_speed * lane_vehicle_count
                speed_weight += lane_vehicle_count
            if lane_occupancy is not None:
                occupancy_values.append(float(lane_occupancy) / 100.0 if float(lane_occupancy) > 1.0 else float(lane_occupancy))

        mean_speed_mps = weighted_speed_sum / speed_weight if speed_weight else 0.0
        occupancy = sum(occupancy_values) / len(occupancy_values) if occupancy_values else 0.0
        queue_vehicle_count = halted_vehicle_count
        degraded = detector_read_failures > 0
        spillback_risk = degraded or occupancy >= 0.75 or (len(lanes) > 0 and halted_vehicle_count >= len(lanes) * 4)
        return NetworkStateSnapshot(
            tls_id=intersection.tls_id,
            timestamp_s=sim_time_s,
            active_request_count=active_request_count,
            lane_count=len(lanes),
            vehicle_count=vehicle_count,
            queue_vehicle_count=queue_vehicle_count,
            halted_vehicle_count=halted_vehicle_count,
            mean_speed_mps=round(mean_speed_mps, 3),
            waiting_time_s=round(waiting_time_s, 3),
            occupancy=round(occupancy, 4),
            spillback_risk=spillback_risk,
            degraded=degraded,
            detector_read_failures=detector_read_failures,
            failed_lanes=failed_lanes,
        )

    def _subscribe_lane_if_possible(self, lane_id: str) -> None:
        if lane_id in self._subscribed_lanes:
            return
        traci = self._require_traci()
        constants = getattr(traci, "constants", None)
        names = [
                "LAST_STEP_VEHICLE_NUMBER",
                "LAST_STEP_VEHICLE_HALTING_NUMBER",
                "LAST_STEP_MEAN_SPEED",
                "LAST_STEP_OCCUPANCY",
                "VAR_WAITING_TIME",
            ]
        for name in names:
            value = getattr(constants, name, None)
            if value is not None:
                self._subscription_var_ids_by_name[name] = value
        var_ids = [self._subscription_var_ids_by_name[name] for name in names if name in self._subscription_var_ids_by_name]
        var_ids = [value for value in var_ids if value is not None]
        if var_ids:
            _safe_call(lambda: traci.lane.subscribe(lane_id, var_ids))
        self._subscribed_lanes.add(lane_id)

    def _lane_subscription_results(self, lane_id: str) -> dict | None:
        traci = self._require_traci()
        result = _safe_call(lambda: traci.lane.getSubscriptionResults(lane_id))
        return result if isinstance(result, dict) else None

    def _subscription_value(self, values: dict | None, name: str) -> object:
        if not values:
            return None
        var_id = self._subscription_var_ids_by_name.get(name)
        if var_id is None:
            return None
        return values.get(var_id)

    def _network_state_lanes(self, intersection: IntersectionConfig, signal_state: SignalState) -> list[str]:
        controlled = [lane for lane in signal_state.controlled_lanes if lane]
        if controlled:
            return sorted(set(controlled))
        lanes: list[str] = []
        for edge_id in intersection.controlled_approach_edges:
            lanes.extend(self._edge_lanes(edge_id))
        return sorted(set(lanes))

    def _edge_lanes(self, edge_id: str) -> list[str]:
        traci = self._require_traci()
        if edge_id in self._edge_lanes_cache:
            return list(self._edge_lanes_cache[edge_id])
        lane_count = _safe_call(lambda: int(traci.edge.getLaneNumber(edge_id)))
        if lane_count is not None:
            lanes = [f"{edge_id}_{idx}" for idx in range(int(lane_count))]
            self._edge_lanes_cache[edge_id] = lanes
            return list(lanes)

        lanes: list[str] = []
        for idx in range(8):
            lane_id = f"{edge_id}_{idx}"
            if _safe_call(lambda lane_id=lane_id: traci.lane.getLength(lane_id)) is None:
                break
            lanes.append(lane_id)
        self._edge_lanes_cache[edge_id] = lanes
        return list(lanes)

    def _require_traci(self):  # type: ignore[no-untyped-def]
        if self.traci is None:
            raise TraciUnavailableError("TraCI simulation has not been started.")
        return self.traci


def _safe_call(callable_):  # type: ignore[no-untyped-def]
    try:
        return callable_()
    except Exception:
        return None


def _logic_program_id(logic) -> Optional[str]:  # type: ignore[no-untyped-def]
    for attr in ("programID", "programId", "id", "subID"):
        try:
            value = getattr(logic, attr)
        except Exception:
            continue
        if value is not None:
            return str(value)
    return None


def _load_traci():  # type: ignore[no-untyped-def]
    try:
        return importlib.import_module("traci")
    except ImportError:
        _bootstrap_traci_paths()
        return importlib.import_module("traci")


def _bootstrap_traci_paths() -> None:
    apply_sumo_environment()
    root = Path(__file__).resolve().parents[2]
    candidates = [
        root / ".venv" / "lib",
        Path(os.environ["SUMO_HOME"]) / "tools" if "SUMO_HOME" in os.environ else None,
    ]

    for base in candidates:
        if base is None or not base.exists():
            continue

        if base.name == "lib":
            for site_packages in sorted(base.glob("python*/site-packages")):
                _prepend_sys_path(site_packages)
        else:
            _prepend_sys_path(base)


def _prepend_sys_path(path: Path) -> None:
    resolved = str(path.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)


def _resolve_traci_port() -> int:
    configured = os.environ.get("TRACI_PORT")
    if configured:
        try:
            return int(configured)
        except ValueError as exc:
            raise TraciUnavailableError(f"Invalid TRACI_PORT value: {configured!r}") from exc

    try:
        from sumolib.miscutils import getFreeSocketPort  # type: ignore

        port = getFreeSocketPort()
        if port is not None:
            return int(port)
    except Exception:
        pass

    # Some restricted environments cannot probe an ephemeral port even though
    # SUMO/TraCI still work with a fixed localhost port.
    for port in range(8813, 8823):
        if _is_tcp_port_available(port):
            return port

    raise TraciUnavailableError(
        "Could not determine a usable TraCI TCP port. Set TRACI_PORT explicitly and retry."
    )


def _is_tcp_port_available(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
