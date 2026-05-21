#!/usr/bin/env python3
"""Signal-control contract and adapter boundary.

The TSP engine and Safety Layer reason in terms of controller contracts:
signal groups, phase indices, conflicts, intergreens and action capabilities.
SUMO/TraCI is one implementation of the adapter boundary, not the contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Protocol

from pps57_cits.config import CITSConfig
from pps57_cits.models import SignalState
from pps57_cits.traci_adapter import TraciSimulationAdapter
from pps57_cits.util import optional_int as _optional_int

from .config import TSPConfig
from .models import TSPAction, TSPDecision


@dataclass(frozen=True)
class SignalGroupContract:
    signal_group_id: str
    phase_index: Optional[int]
    movement_ids: List[str] = field(default_factory=list)
    allowed_actions: List[str] = field(default_factory=list)
    min_green_s: Optional[float] = None
    max_green_s: Optional[float] = None
    max_extension_s: Optional[float] = None
    pedestrian_clearance_s: Optional[float] = None
    conflicts_with: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ControllerContract:
    tls_id: str
    adapter_type: str
    fixed_time_required: bool
    allowed_actions: List[str]
    phase_sequence: List[int]
    service_green_phase_indices: List[int]
    intergreen_phase_indices: List[int]
    min_yellow_s: Optional[float]
    min_all_red_s: Optional[float]
    signal_groups: Dict[str, SignalGroupContract]

    def signal_group_for_id(self, signal_group_id: str) -> Optional[SignalGroupContract]:
        return self.signal_groups.get(signal_group_id)

    def signal_group_for_movement(self, movement_id: str) -> Optional[SignalGroupContract]:
        for group in self.signal_groups.values():
            if movement_id in group.movement_ids:
                return group
        return None

    def min_green_for_phase(self, phase_index: Optional[int]) -> Optional[float]:
        if phase_index is None:
            return None
        values = [
            group.min_green_s
            for group in self.signal_groups.values()
            if group.phase_index == phase_index and group.min_green_s is not None
        ]
        return max(values) if values else None


@dataclass(frozen=True)
class ControllerCommandValidation:
    accepted: bool
    reason: str
    effective_at_s: Optional[float] = None
    adjusted_parameters: Dict[str, object] = field(default_factory=dict)
    severity: str = "info"

    def to_dict(self) -> Dict[str, object]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "effective_at_s": self.effective_at_s,
            "adjusted_parameters": dict(self.adjusted_parameters),
            "severity": self.severity,
        }


class SignalControlAdapter(Protocol):
    """Boundary for SUMO/TraCI and future real controller implementations."""

    def read_program_phase_count(self, tls_id: str) -> Optional[int]:
        ...

    def read_program_phase_states(self, tls_id: str) -> Optional[List[str]]:
        ...

    def read_program_phase_durations(self, tls_id: str) -> Optional[List[float]]:
        ...

    def read_program_is_fixed_time(self, tls_id: str) -> Optional[bool]:
        ...

    def read_program_type(self, tls_id: str) -> Optional[str]:
        ...

    def set_phase_duration(self, tls_id: str, duration_s: float) -> None:
        ...

    def validate_actuation(
        self,
        decision: TSPDecision,
        signal_state: SignalState,
        sim_time_s: float,
        command: str,
        parameters: Dict[str, object],
    ) -> ControllerCommandValidation:
        ...

    def verify_controller_contracts(self, contracts: Iterable[ControllerContract]) -> List[str]:
        ...


@dataclass
class TraciSignalControlAdapter:
    """SUMO/TraCI implementation of the signal-control boundary."""

    adapter: TraciSimulationAdapter

    def read_program_phase_count(self, tls_id: str) -> Optional[int]:
        return self.adapter.read_program_phase_count(tls_id)

    def read_program_phase_states(self, tls_id: str) -> Optional[List[str]]:
        return self.adapter.read_program_phase_states(tls_id)

    def read_program_phase_durations(self, tls_id: str) -> Optional[List[float]]:
        if hasattr(self.adapter, "read_program_phase_durations"):
            return self.adapter.read_program_phase_durations(tls_id)
        return None

    def read_program_is_fixed_time(self, tls_id: str) -> Optional[bool]:
        return self.adapter.read_program_is_fixed_time(tls_id)

    def read_program_type(self, tls_id: str) -> Optional[str]:
        return self.adapter.read_program_type(tls_id)

    def set_phase_duration(self, tls_id: str, duration_s: float) -> None:
        self.adapter.set_phase_duration(tls_id, duration_s)

    def validate_actuation(
        self,
        decision: TSPDecision,
        signal_state: SignalState,
        sim_time_s: float,
        command: str,
        parameters: Dict[str, object],
    ) -> ControllerCommandValidation:
        return ControllerCommandValidation(
            accepted=True,
            reason="traci_adapter_accepts_command",
            effective_at_s=sim_time_s,
            adjusted_parameters=dict(parameters),
        )

    def verify_controller_contracts(self, contracts: Iterable[ControllerContract]) -> List[str]:
        problems: List[str] = []
        for contract in contracts:
            tls_id = contract.tls_id
            phase_count = self.read_program_phase_count(tls_id)
            if phase_count is None:
                problems.append(f"{tls_id}: programa TLS ilegível; impossível validar controller contract")
                continue

            for group in contract.signal_groups.values():
                if group.phase_index is None:
                    continue
                if not (0 <= group.phase_index < phase_count):
                    problems.append(
                        f"{tls_id}: signal_group {group.signal_group_id} phase_index={group.phase_index} "
                        f"fora do programa (fases={phase_count})"
                    )

            for idx in contract.phase_sequence:
                if not (0 <= idx < phase_count):
                    problems.append(f"{tls_id}: phase_sequence índice {idx} fora do programa (fases={phase_count})")
            for idx in contract.service_green_phase_indices + contract.intergreen_phase_indices:
                if not (0 <= idx < phase_count):
                    problems.append(f"{tls_id}: controller_contract phase index {idx} fora do programa (fases={phase_count})")

            states = self.read_program_phase_states(tls_id)
            durations = self._read_phase_durations_if_available(tls_id)
            if states is None:
                problems.append(f"{tls_id}: estados de fase ilegíveis (fail-closed)")
            else:
                for group in contract.signal_groups.values():
                    if group.phase_index is None or group.phase_index >= len(states):
                        continue
                    if "g" not in states[group.phase_index].lower():
                        problems.append(
                            f"{tls_id}: signal_group {group.signal_group_id} aponta para fase "
                            f"{group.phase_index} ('{states[group.phase_index]}') sem verde"
                        )
                for idx in contract.intergreen_phase_indices:
                    if idx < len(states) and "g" in states[idx].lower():
                        problems.append(
                            f"{tls_id}: fase {idx} ('{states[idx]}') é intergreen mas contém "
                            "verde — clearance amarelo/all-red não garantida"
                        )
                    if durations is not None and idx < len(durations) and "y" in states[idx].lower():
                        duration_s = float(durations[idx])
                        if contract.min_yellow_s is not None and duration_s < contract.min_yellow_s:
                            problems.append(
                                f"{tls_id}: fase {idx} tem duração {duration_s:.1f}s inferior ao amarelo mínimo "
                                f"{contract.min_yellow_s:.1f}s"
                            )
                if durations is not None and contract.min_all_red_s is not None and contract.min_all_red_s > 0:
                    has_all_red = any(
                        idx < len(states)
                        and idx < len(durations)
                        and _is_all_red_state(states[idx])
                        and float(durations[idx]) >= contract.min_all_red_s
                        for idx in contract.intergreen_phase_indices
                    )
                    if not has_all_red:
                        problems.append(
                            f"{tls_id}: all-red explícito >= {contract.min_all_red_s:.1f}s não encontrado "
                            "nas fases intergreen do programa SUMO"
                        )

            for group in contract.signal_groups.values():
                for conflict in group.conflicts_with:
                    if conflict not in contract.signal_groups:
                        problems.append(
                            f"{tls_id}: signal_group {group.signal_group_id} referencia conflito inexistente {conflict}"
                        )
                if group.movement_ids and not group.conflicts_with:
                    problems.append(f"{tls_id}: signal_group {group.signal_group_id} sem matriz de conflitos")

            if contract.fixed_time_required:
                is_fixed = self.read_program_is_fixed_time(tls_id)
                if is_fixed is None:
                    problems.append(f"{tls_id}: não foi possível confirmar programa de tempo fixo (fail-closed)")
                elif is_fixed is False:
                    problems.append(
                        f"{tls_id}: programa atuado/adaptativo (tipo='{self.read_program_type(tls_id)}'); "
                        "o contrato TSP assume controlo de tempo fixo"
                    )
        return problems

    def _read_phase_durations_if_available(self, tls_id: str) -> Optional[List[float]]:
        if hasattr(self, "read_program_phase_durations"):
            return self.read_program_phase_durations(tls_id)
        return None


@dataclass
class SimulatedControllerAdapter:
    """Real-controller behaviour shim over SUMO/TraCI.

    It does not replace SUMO/TraCI. It screens commands before they reach TraCI,
    simulating controller ACK/NACK, command spacing, pending-command locks,
    manual mode and deterministic pedestrian calls.
    """

    base: SignalControlAdapter
    contracts: Iterable[ControllerContract]
    config: Dict[str, object]
    last_command_time_by_tls: Dict[str, float] = field(default_factory=dict)
    pending_until_by_tls: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.contract_by_tls = {contract.tls_id: contract for contract in self.contracts}

    def read_program_phase_count(self, tls_id: str) -> Optional[int]:
        return self.base.read_program_phase_count(tls_id)

    def read_program_phase_states(self, tls_id: str) -> Optional[List[str]]:
        return self.base.read_program_phase_states(tls_id)

    def read_program_phase_durations(self, tls_id: str) -> Optional[List[float]]:
        if hasattr(self.base, "read_program_phase_durations"):
            return self.base.read_program_phase_durations(tls_id)
        return None

    def read_program_is_fixed_time(self, tls_id: str) -> Optional[bool]:
        return self.base.read_program_is_fixed_time(tls_id)

    def read_program_type(self, tls_id: str) -> Optional[str]:
        return self.base.read_program_type(tls_id)

    def set_phase_duration(self, tls_id: str, duration_s: float) -> None:
        self.base.set_phase_duration(tls_id, duration_s)

    def verify_controller_contracts(self, contracts: Iterable[ControllerContract]) -> List[str]:
        return self.base.verify_controller_contracts(contracts)

    def validate_actuation(
        self,
        decision: TSPDecision,
        signal_state: SignalState,
        sim_time_s: float,
        command: str,
        parameters: Dict[str, object],
    ) -> ControllerCommandValidation:
        contract = self.contract_by_tls.get(decision.tls_id)
        if contract is None:
            return ControllerCommandValidation(False, "controller_contract_missing", severity="warning")

        mode = self._mode_for_tls(decision.tls_id)
        if mode != "automatic":
            return ControllerCommandValidation(False, f"controller_locked_{mode}_mode", severity="warning")

        pending_until = self.pending_until_by_tls.get(decision.tls_id)
        if pending_until is not None and sim_time_s < pending_until:
            return ControllerCommandValidation(
                False,
                "controller_command_pending",
                effective_at_s=pending_until,
                severity="warning",
            )

        last = self.last_command_time_by_tls.get(decision.tls_id)
        min_interval = float(self.config.get("min_command_interval_s", 0.0))
        if last is not None and sim_time_s - last < min_interval:
            return ControllerCommandValidation(
                False,
                "controller_min_command_interval_active",
                effective_at_s=last + min_interval,
                severity="warning",
            )

        group = contract.signal_group_for_id(decision.target_signal_group_id)
        if group is None:
            group = contract.signal_group_for_movement(decision.priority_movement_id)
        if group is None:
            return ControllerCommandValidation(False, "controller_signal_group_unknown", severity="warning")

        if decision.action not in contract.allowed_actions or decision.action not in group.allowed_actions:
            return ControllerCommandValidation(False, "controller_action_not_allowed", severity="warning")

        if self._pedestrian_call_active(decision.tls_id) and any("pedestrian" in item for item in group.conflicts_with):
            return ControllerCommandValidation(
                False,
                "controller_rejected_pedestrian_call_active",
                severity="warning",
            )

        if signal_state.red_yellow_green_state and "y" in signal_state.red_yellow_green_state.lower():
            return ControllerCommandValidation(False, "controller_rejected_yellow_transition", severity="warning")

        if decision.action == TSPAction.EARLY_GREEN.value and not group.conflicts_with:
            return ControllerCommandValidation(False, "controller_conflict_matrix_missing", severity="warning")

        latency = float(self.config.get("command_latency_s", 0.0))
        effective_at = sim_time_s + max(0.0, latency)
        pending_s = float(self.config.get("pending_lock_s", latency))
        self.last_command_time_by_tls[decision.tls_id] = sim_time_s
        if pending_s > 0:
            self.pending_until_by_tls[decision.tls_id] = sim_time_s + pending_s

        adjusted = dict(parameters)
        adjusted["controller_adapter"] = "simulated_real_controller"
        adjusted["controller_effective_at_s"] = round(effective_at, 3)
        adjusted["controller_mode"] = mode
        return ControllerCommandValidation(
            accepted=True,
            reason="controller_command_accepted",
            effective_at_s=round(effective_at, 3),
            adjusted_parameters=adjusted,
        )

    def _mode_for_tls(self, tls_id: str) -> str:
        modes = self.config.get("controller_modes", {})
        if isinstance(modes, dict):
            return str(modes.get(tls_id, self.config.get("default_mode", "automatic")))
        return str(self.config.get("default_mode", "automatic"))

    def _pedestrian_call_active(self, tls_id: str) -> bool:
        active = self.config.get("active_pedestrian_calls_by_tls", [])
        return isinstance(active, list) and tls_id in {str(item) for item in active}


def build_controller_contracts(cits_config: CITSConfig, tsp_config: TSPConfig) -> List[ControllerContract]:
    return [
        build_controller_contract(cits_config, tsp_config, intersection.tls_id)
        for intersection in cits_config.intersections
    ]


def build_controller_contract(cits_config: CITSConfig, tsp_config: TSPConfig, tls_id: str) -> ControllerContract:
    intersection = cits_config.tls_to_intersection[tls_id]
    raw = tsp_config.controller_contract_for_tls(tls_id)
    safety = cits_config.safety_constraints
    mapping = tsp_config.phase_mapping_for_tls(tls_id)

    allowed_actions = list(raw.get("allowed_actions", ["green_extension", "early_green"]))
    phase_sequence = _int_list(raw.get("phase_sequence", mapping.get("phase_sequence", [])))
    service_green_phase_indices = _int_list(
        raw.get("service_green_phase_indices", mapping.get("service_green_phase_indices", []))
    )
    intergreen_phase_indices = _int_list(raw.get("intergreen_phase_indices", []))
    signal_groups: Dict[str, SignalGroupContract] = {}

    priority_defaults = raw.get("priority_signal_group_defaults", {})
    for movement in intersection.priority_movements:
        movement_mapping = tsp_config.phase_mapping_for_movement(movement.movement_id, tls_id)
        group_raw = dict(priority_defaults)
        group_raw.update(raw.get("signal_groups", {}).get(movement.target_signal_group_id, {}))
        phase_index = _optional_int(group_raw.get("phase_index", movement_mapping.get("target_phase_index")))
        signal_groups[movement.target_signal_group_id] = _group_from_raw(
            movement.target_signal_group_id,
            group_raw,
            tls_id=tls_id,
            default_allowed_actions=allowed_actions,
            default_phase_index=phase_index,
            default_movement_ids=[movement.movement_id],
            default_min_green=_float_or_none(safety.get("min_green_s")),
            default_max_green=_float_or_none(safety.get("max_total_green_s")),
            default_max_extension=_float_or_none(safety.get("max_green_extension_s")),
        )

    for item in raw.get("additional_signal_groups", []):
        group_id = str(item.get("signal_group_id", "")).format(tls_id=tls_id)
        if not group_id:
            continue
        signal_groups[group_id] = _group_from_raw(
            group_id,
            item,
            tls_id=tls_id,
            default_allowed_actions=allowed_actions,
            default_phase_index=_optional_int(item.get("phase_index")),
            default_movement_ids=[],
            default_min_green=_float_or_none(safety.get("min_green_s")),
            default_max_green=_float_or_none(safety.get("max_total_green_s")),
            default_max_extension=_float_or_none(safety.get("max_green_extension_s")),
        )

    return ControllerContract(
        tls_id=tls_id,
        adapter_type=str(raw.get("adapter_type", "sumo_traci")),
        fixed_time_required=bool(raw.get("fixed_time_required", True)),
        allowed_actions=allowed_actions,
        phase_sequence=phase_sequence,
        service_green_phase_indices=service_green_phase_indices,
        intergreen_phase_indices=intergreen_phase_indices,
        min_yellow_s=_float_or_none(safety.get("yellow_s")),
        min_all_red_s=_float_or_none(safety.get("all_red_s")),
        signal_groups=signal_groups,
    )


def _group_from_raw(
    signal_group_id: str,
    raw: Dict[str, object],
    *,
    tls_id: str,
    default_allowed_actions: List[str],
    default_phase_index: Optional[int],
    default_movement_ids: List[str],
    default_min_green: Optional[float],
    default_max_green: Optional[float],
    default_max_extension: Optional[float],
) -> SignalGroupContract:
    return SignalGroupContract(
        signal_group_id=signal_group_id,
        phase_index=default_phase_index,
        movement_ids=[str(item) for item in raw.get("movement_ids", default_movement_ids)],  # type: ignore[arg-type]
        allowed_actions=[str(item) for item in raw.get("allowed_actions", default_allowed_actions)],  # type: ignore[arg-type]
        min_green_s=_float_or_none(raw.get("min_green_s"), default_min_green),
        max_green_s=_float_or_none(raw.get("max_green_s"), default_max_green),
        max_extension_s=_float_or_none(raw.get("max_extension_s"), default_max_extension),
        pedestrian_clearance_s=_float_or_none(raw.get("pedestrian_clearance_s")),
        conflicts_with=[str(item).format(tls_id=tls_id) for item in raw.get("conflicts_with", [])],  # type: ignore[arg-type]
    )


def _int_list(values: object) -> List[int]:
    if not isinstance(values, list):
        return []
    result: List[int] = []
    for item in values:
        value = _optional_int(item)
        if value is not None:
            result.append(value)
    return result


def _float_or_none(value: object, default: Optional[float] = None) -> Optional[float]:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_all_red_state(state: str) -> bool:
    return bool(state) and all(ch.lower() == "r" for ch in state)
