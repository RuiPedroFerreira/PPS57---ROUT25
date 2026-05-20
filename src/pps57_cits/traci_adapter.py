#!/usr/bin/env python3
"""Adaptador TraCI para ler estado SUMO durante a emulação C-ITS."""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import shutil
import socket
import sys
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


    def set_phase_duration(self, tls_id: str, duration_s: float) -> None:
        """Define a duração restante da fase corrente do semáforo.

        Este é o comando TraCI usado no MVP para extensão de verde e
        truncagem controlada de fase. Evita manipular diretamente o estado
        vermelho/amarelo/verde.
        """
        traci = self._require_traci()
        traci.trafficlight.setPhaseDuration(tls_id, float(duration_s))

    def set_phase(self, tls_id: str, phase_index: int) -> None:
        """Muda explicitamente a fase. Deve ser usado apenas em testes controlados."""
        traci = self._require_traci()
        traci.trafficlight.setPhase(tls_id, int(phase_index))

    def _program_logic(self, tls_id: str):  # type: ignore[no-untyped-def]
        traci = self._require_traci()
        for getter in ("getAllProgramLogics", "getCompleteRedYellowGreenDefinition"):
            try:
                logics = getattr(traci.trafficlight, getter)(tls_id)
            except Exception:
                continue
            if logics:
                return logics[0]
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


def _load_traci():  # type: ignore[no-untyped-def]
    try:
        return importlib.import_module("traci")
    except ImportError:
        _bootstrap_traci_paths()
        return importlib.import_module("traci")


def _bootstrap_traci_paths() -> None:
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
