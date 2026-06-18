#!/usr/bin/env python3
"""Signal-control contract and adapter boundary.

The TSP engine and Safety Layer reason in terms of controller contracts:
signal groups, phase indices, conflicts, intergreens and action capabilities.
SUMO/TraCI is one implementation of the adapter boundary, not the contract.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

from pps57_cits.config import CITSConfig, PriorityMovementConfig
from pps57_cits.models import SignalState
from pps57_cits.traci_adapter import TraciSimulationAdapter
from pps57_cits.util import optional_int as _optional_int
from pps57_sumo.network_binding import NetworkBinding
from pps57_sumo.network_profile import MovementProfile, TLSProfile, load_network_profile

from .config import TSPConfig
from .models import TSPAction, TSPDecision


@dataclass(frozen=True)
class SignalGroupContract:
    signal_group_id: str
    phase_index: int | None
    movement_ids: list[str] = field(default_factory=list)
    allowed_actions: list[str] = field(default_factory=list)
    min_green_s: float | None = None
    max_green_s: float | None = None
    max_extension_s: float | None = None
    pedestrian_clearance_s: float | None = None
    conflicts_with: list[str] = field(default_factory=list)
    requires_protected_green: bool = True
    allow_edge_state_fallback: bool = False
    # True when an authoritative source (NetworkBinding, from SUMO junction foes)
    # has determined this group's conflict matrix — even if it is empty (the group
    # is genuinely conflict-free). Distinguishes a *known-empty* matrix from an
    # *unknown* one, so the verifier does not fail-close a safe, conflict-free group.
    conflict_matrix_known: bool = False


def signal_group_lacks_conflict_matrix(group: SignalGroupContract) -> bool:
    """Fail-close predicate: signal group com movimentos mas sem matriz de conflitos.

    É exactamente a condição "sem matriz de conflitos" aplicada por
    ``verify_controller_contracts``: o grupo serve movimentos, a lista de conflitos
    está vazia e nenhuma fonte autoritativa (NetworkBinding) marcou a matriz como
    conhecida. Exportada para que os scripts de evidência contem a mesma condição
    que o verificador aplica, em vez de copiarem o predicado.
    """
    return bool(group.movement_ids) and not group.conflicts_with and not group.conflict_matrix_known


@dataclass(frozen=True)
class ControllerContract:
    tls_id: str
    adapter_type: str
    fixed_time_required: bool
    allowed_actions: list[str]
    phase_sequence: list[int]
    service_green_phase_indices: list[int]
    intergreen_phase_indices: list[int]
    min_yellow_s: float | None
    min_all_red_s: float | None
    expected_cycle_s: float | None
    pedestrian_phase_required: bool
    pedestrian_phase_indices: list[int]
    signal_groups: dict[str, SignalGroupContract]
    # Link indices de movimentos veiculares (não-internos), topologicamente
    # estáveis entre programas WAUT. Distinguem verde veicular de verde pedonal
    # na verificação de fase pedonal exclusiva. Vazio => sem profile -> a
    # verificação recai na heurística service∪intergreen.
    vehicle_link_indices: list[int] = field(default_factory=list)
    # Matriz de conflitos ao nível do LINK (vinda do NetworkBinding / foes da
    # rede), usada pela verificação de all-red para distinguir greens
    # consecutivos conflituantes de não-conflituantes. ``None`` => sem dados
    # autoritativos -> a verificação fica fail-closed (comportamento legado:
    # exige all-red em todas as transições verde->verde). Dict (possivelmente
    # vazio) => dados presentes; ``known_conflict_links`` diz que links têm foe
    # data autoritativa (mesmo com conjunto de conflitos vazio).
    link_conflicts: dict[int, frozenset[int]] | None = None
    known_conflict_links: frozenset[int] | None = None

    def signal_group_for_id(self, signal_group_id: str) -> SignalGroupContract | None:
        return self.signal_groups.get(signal_group_id)

    def signal_group_for_movement(self, movement_id: str) -> SignalGroupContract | None:
        for group in self.signal_groups.values():
            if movement_id in group.movement_ids:
                return group
        return None

    def min_green_for_phase(self, phase_index: int | None) -> float | None:
        if phase_index is None:
            return None
        values = [
            group.min_green_s
            for group in self.signal_groups.values()
            if group.phase_index == phase_index and group.min_green_s is not None
        ]
        return max(values) if values else None


def phase_sequence_clearance_problem(
    contract: ControllerContract,
    current_phase_index: int | None,
    target_phase_index: int | None,
    *,
    never_skip_yellow_or_all_red: bool,
) -> str | None:
    """Devolve None se a transição early-green é estruturalmente segura.

    Verifica (a) que o verde-alvo é alcançável a partir da fase atual segundo
    a sequência configurada e (b) — quando never_skip_yellow_or_all_red está
    ativo (default estrito) — que existe pelo menos uma fase intermédia entre
    a fase conflituante atual e o verde-alvo, para o programa SUMO poder
    executar a clearance amarelo/all-red. Fail-closed em dados em falta.

    Partilhada entre a Safety Layer (bloqueio autoritativo) e o motor de
    decisão (pré-consulta para não propor o que seria sempre bloqueado);
    extraída de SafetyLayer._phase_sequence_clearance_check sem alterar os
    literais de motivo.
    """
    current = current_phase_index
    target = target_phase_index
    if current is None or target is None:
        return "early_green_phase_indices_unknown"
    if current == target:
        return "early_green_target_phase_already_active"

    sequence = list(contract.phase_sequence)
    if current not in sequence or target not in sequence:
        return "early_green_phase_not_in_configured_sequence"

    current_pos = sequence.index(current)
    phases_until_target = []
    pos = current_pos
    for _ in range(1, len(sequence) + 1):
        pos = (pos + 1) % len(sequence)
        phase = sequence[pos]
        if phase == target:
            break
        phases_until_target.append(phase)
    else:
        return "early_green_target_phase_not_in_remaining_sequence"

    if not phases_until_target:
        return "early_green_would_skip_clearance_phase"
    if never_skip_yellow_or_all_red and not any(
        phase in contract.intergreen_phase_indices for phase in phases_until_target
    ):
        return "early_green_would_skip_clearance_phase"
    return None


@dataclass(frozen=True)
class ControllerCommandValidation:
    accepted: bool
    reason: str
    effective_at_s: float | None = None
    adjusted_parameters: dict[str, object] = field(default_factory=dict)
    severity: str = "info"

    def to_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "effective_at_s": self.effective_at_s,
            "adjusted_parameters": dict(self.adjusted_parameters),
            "severity": self.severity,
        }


class SignalControlAdapter(Protocol):
    """Boundary for SUMO/TraCI and future real controller implementations."""

    def read_program_phase_count(self, tls_id: str) -> int | None: ...

    def read_program_phase_states(self, tls_id: str) -> list[str] | None: ...

    def read_program_phase_durations(self, tls_id: str) -> list[float] | None: ...

    def read_program_is_fixed_time(self, tls_id: str) -> bool | None: ...

    def read_program_type(self, tls_id: str) -> str | None: ...

    def set_phase_duration(self, tls_id: str, duration_s: float) -> None: ...

    def validate_actuation(
        self,
        decision: TSPDecision,
        signal_state: SignalState,
        sim_time_s: float,
        command: str,
        parameters: dict[str, object],
    ) -> ControllerCommandValidation: ...

    def verify_controller_contracts(self, contracts: Iterable[ControllerContract]) -> list[str]: ...


@dataclass
class TraciSignalControlAdapter:
    """SUMO/TraCI implementation of the signal-control boundary."""

    adapter: TraciSimulationAdapter

    def read_program_phase_count(self, tls_id: str) -> int | None:
        return self.adapter.read_program_phase_count(tls_id)

    def read_program_phase_states(self, tls_id: str) -> list[str] | None:
        return self.adapter.read_program_phase_states(tls_id)

    def read_program_phase_durations(self, tls_id: str) -> list[float] | None:
        if hasattr(self.adapter, "read_program_phase_durations"):
            return self.adapter.read_program_phase_durations(tls_id)
        return None

    def read_program_is_fixed_time(self, tls_id: str) -> bool | None:
        return self.adapter.read_program_is_fixed_time(tls_id)

    def read_program_type(self, tls_id: str) -> str | None:
        return self.adapter.read_program_type(tls_id)

    def set_phase_duration(self, tls_id: str, duration_s: float) -> None:
        self.adapter.set_phase_duration(tls_id, duration_s)

    def validate_actuation(
        self,
        decision: TSPDecision,
        signal_state: SignalState,
        sim_time_s: float,
        command: str,
        parameters: dict[str, object],
    ) -> ControllerCommandValidation:
        return ControllerCommandValidation(
            accepted=True,
            reason="traci_adapter_accepts_command",
            effective_at_s=sim_time_s,
            adjusted_parameters=dict(parameters),
        )

    def verify_controller_contracts(self, contracts: Iterable[ControllerContract]) -> list[str]:
        problems: list[str] = []
        for contract in contracts:
            tls_id = contract.tls_id
            phase_count = self.read_program_phase_count(tls_id)
            if phase_count is None:
                problems.append(
                    f"{tls_id}: programa TLS ilegível; impossível validar controller contract"
                )
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
                    problems.append(
                        f"{tls_id}: phase_sequence índice {idx} fora do programa (fases={phase_count})"
                    )
            for idx in contract.service_green_phase_indices + contract.intergreen_phase_indices:
                if not (0 <= idx < phase_count):
                    problems.append(
                        f"{tls_id}: controller_contract phase index {idx} fora do programa (fases={phase_count})"
                    )

            states = self.read_program_phase_states(tls_id)
            durations = self.read_program_phase_durations(tls_id)
            if states is None:
                problems.append(f"{tls_id}: estados de fase ilegíveis (fail-closed)")
            else:
                for group in contract.signal_groups.values():
                    if group.phase_index is None or group.phase_index >= len(states):
                        continue
                    green_ok = (
                        "G" in states[group.phase_index]
                        if group.requires_protected_green
                        else "g" in states[group.phase_index].lower()
                    )
                    if not green_ok:
                        problems.append(
                            f"{tls_id}: signal_group {group.signal_group_id} aponta para fase "
                            f"{group.phase_index} ('{states[group.phase_index]}') sem "
                            f"{'verde protegido' if group.requires_protected_green else 'verde'}"
                        )
                for idx in contract.intergreen_phase_indices:
                    if idx < len(states) and "g" in states[idx].lower():
                        problems.append(
                            f"{tls_id}: fase {idx} ('{states[idx]}') é intergreen mas contém "
                            "verde — clearance amarelo/all-red não garantida"
                        )
                    if (
                        durations is not None
                        and idx < len(durations)
                        and idx < len(states)
                        and "y" in states[idx].lower()
                    ):
                        duration_s = float(durations[idx])
                        if contract.min_yellow_s is not None and duration_s < contract.min_yellow_s:
                            problems.append(
                                f"{tls_id}: fase {idx} tem duração {duration_s:.1f}s inferior ao amarelo mínimo "
                                f"{contract.min_yellow_s:.1f}s"
                            )
                if (
                    durations is not None
                    and contract.min_all_red_s is not None
                    and contract.min_all_red_s > 0
                ):
                    missing_all_red = _missing_all_red_transitions(
                        states,
                        durations,
                        contract.phase_sequence,
                        contract.service_green_phase_indices,
                        contract.min_all_red_s,
                        link_conflicts=contract.link_conflicts,
                        known_conflict_links=contract.known_conflict_links,
                    )
                    for from_phase, to_phase in missing_all_red:
                        problems.append(
                            f"{tls_id}: all-red explícito >= {contract.min_all_red_s:.1f}s não encontrado "
                            f"na transição {from_phase}->{to_phase}"
                        )
                if durations is not None and contract.expected_cycle_s is not None:
                    cycle_s = sum(float(duration) for duration in durations)
                    if abs(cycle_s - contract.expected_cycle_s) > 0.51:
                        problems.append(
                            f"{tls_id}: ciclo SUMO {cycle_s:.1f}s difere do controller contract "
                            f"{contract.expected_cycle_s:.1f}s"
                        )
                if contract.pedestrian_phase_required and not _has_configured_pedestrian_phase(
                    states,
                    contract.service_green_phase_indices,
                    contract.intergreen_phase_indices,
                    contract.pedestrian_phase_indices,
                    vehicle_link_indices=contract.vehicle_link_indices or None,
                ):
                    problems.append(
                        f"{tls_id}: fase pedonal exclusiva configurada não encontrada; clearance pedonal não garantida"
                    )

            for group in contract.signal_groups.values():
                for conflict in group.conflicts_with:
                    if conflict not in contract.signal_groups:
                        problems.append(
                            f"{tls_id}: signal_group {group.signal_group_id} referencia conflito inexistente {conflict}"
                        )
                if signal_group_lacks_conflict_matrix(group):
                    problems.append(
                        f"{tls_id}: signal_group {group.signal_group_id} sem matriz de conflitos"
                    )

            if contract.fixed_time_required:
                is_fixed = self.read_program_is_fixed_time(tls_id)
                if is_fixed is None:
                    problems.append(
                        f"{tls_id}: não foi possível confirmar programa de tempo fixo (fail-closed)"
                    )
                elif is_fixed is False:
                    problems.append(
                        f"{tls_id}: programa atuado/adaptativo (tipo='{self.read_program_type(tls_id)}'); "
                        "o contrato TSP assume controlo de tempo fixo"
                    )
        return problems


@dataclass
class SimulatedControllerAdapter:
    """Real-controller behaviour shim over SUMO/TraCI.

    It does not replace SUMO/TraCI. It screens commands before they reach TraCI,
    simulating controller ACK/NACK, command spacing, pending-command locks,
    manual mode and deterministic pedestrian calls.
    """

    base: SignalControlAdapter
    contracts: Iterable[ControllerContract]
    config: dict[str, object]
    last_command_time_by_tls: dict[str, float] = field(default_factory=dict)
    pending_until_by_tls: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.contract_by_tls = {contract.tls_id: contract for contract in self.contracts}

    def read_program_phase_count(self, tls_id: str) -> int | None:
        return self.base.read_program_phase_count(tls_id)

    def read_program_phase_states(self, tls_id: str) -> list[str] | None:
        return self.base.read_program_phase_states(tls_id)

    def read_program_phase_durations(self, tls_id: str) -> list[float] | None:
        if hasattr(self.base, "read_program_phase_durations"):
            return self.base.read_program_phase_durations(tls_id)
        return None

    def read_program_is_fixed_time(self, tls_id: str) -> bool | None:
        return self.base.read_program_is_fixed_time(tls_id)

    def read_program_type(self, tls_id: str) -> str | None:
        return self.base.read_program_type(tls_id)

    def set_phase_duration(self, tls_id: str, duration_s: float) -> None:
        self.base.set_phase_duration(tls_id, duration_s)

    def verify_controller_contracts(self, contracts: Iterable[ControllerContract]) -> list[str]:
        return self.base.verify_controller_contracts(contracts)

    def validate_actuation(
        self,
        decision: TSPDecision,
        signal_state: SignalState,
        sim_time_s: float,
        command: str,
        parameters: dict[str, object],
    ) -> ControllerCommandValidation:
        contract = self.contract_by_tls.get(decision.tls_id)
        if contract is None:
            return ControllerCommandValidation(
                False, "controller_contract_missing", severity="warning"
            )

        mode = self._mode_for_tls(decision.tls_id)
        if mode != "automatic":
            return ControllerCommandValidation(
                False, f"controller_locked_{mode}_mode", severity="warning"
            )

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
            return ControllerCommandValidation(
                False, "controller_signal_group_unknown", severity="warning"
            )

        if (
            decision.action not in contract.allowed_actions
            or decision.action not in group.allowed_actions
        ):
            return ControllerCommandValidation(
                False, "controller_action_not_allowed", severity="warning"
            )

        if self._pedestrian_call_active(decision.tls_id) and any(
            "pedestrian" in item for item in group.conflicts_with
        ):
            return ControllerCommandValidation(
                False,
                "controller_rejected_pedestrian_call_active",
                severity="warning",
            )

        if (
            signal_state.red_yellow_green_state
            and "y" in signal_state.red_yellow_green_state.lower()
        ):
            return ControllerCommandValidation(
                False, "controller_rejected_yellow_transition", severity="warning"
            )

        if decision.action == TSPAction.EARLY_GREEN.value and not group.conflicts_with:
            return ControllerCommandValidation(
                False, "controller_conflict_matrix_missing", severity="warning"
            )

        latency = float(self.config.get("command_latency_s", 0.0))
        if latency > 0:
            return ControllerCommandValidation(
                False,
                "controller_latency_requires_command_scheduler",
                effective_at_s=sim_time_s + latency,
                severity="warning",
            )
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


def build_controller_contracts(
    cits_config: CITSConfig, tsp_config: TSPConfig
) -> list[ControllerContract]:
    return [
        build_controller_contract(cits_config, tsp_config, intersection.tls_id)
        for intersection in cits_config.signal_controlled_intersections
    ]


def network_binding_aliases(
    cits_config: CITSConfig, tsp_config: TSPConfig
) -> dict[str, dict[str, str]]:
    """Per-TLS map of profile signal-group ids -> contract signal-group ids.

    ``build_controller_contract`` renames profile groups to the config's
    ``target_signal_group_id`` (aliases); the NetworkBinding only knows the raw
    profile names. Pass this map to :func:`apply_network_binding` so lookups and
    conflict lists are translated into the contract namespace.
    """
    aliases: dict[str, dict[str, str]] = {}
    for intersection in cits_config.signal_controlled_intersections:
        tls_id = intersection.tls_id
        tls_profile = _network_tls_profile(cits_config, tsp_config, tls_id)
        profile_by_target_group = _profile_movements_by_target_group(
            intersection.priority_movements, tls_profile
        )
        aliases[tls_id] = {
            profile.signal_group_id: group_id
            for group_id, profile in profile_by_target_group.items()
            if profile is not None
        }
    return aliases


def apply_network_binding(
    contracts: Iterable[ControllerContract],
    binding: NetworkBinding,
    aliases_by_tls: dict[str, dict[str, str]] | None = None,
) -> list[ControllerContract]:
    """Replace each signal group's conflict matrix with the network's authoritative one.

    The :class:`~pps57_sumo.network_binding.NetworkBinding` reads the real conflict
    matrix from the SUMO junction ``<request foes>`` data. For every signal group
    the binding covers, this sets ``conflicts_with`` to the authoritative list and
    marks ``conflict_matrix_known=True`` — even when the list is empty, meaning the
    group is genuinely conflict-free. That stops ``verify_controller_contracts``
    from fail-closing real (OSM, joined) intersections whose conflict matrix the
    phase-disjointness heuristic could not infer.

    ``aliases_by_tls`` (see :func:`network_binding_aliases`) translates between the
    binding's raw profile group names and the contract's (possibly aliased) names —
    both for the lookup and for the conflict lists written into the contract.
    Without it, aliased groups are never bound and conflict lists can reference
    profile names absent from the contract (fail-closed, but never functional).

    Groups the binding does not cover are returned unchanged (and still fail-close
    if their matrix was unknown). This never *grants* a permission — it only
    supplies conflict information; the Safety Layer stays the final gate.
    """
    bound: list[ControllerContract] = []
    for contract in contracts:
        tls_binding = binding.binding_for_tls(contract.tls_id)
        if tls_binding is None:
            bound.append(contract)
            continue
        aliases = dict((aliases_by_tls or {}).get(contract.tls_id, {}))
        contract_to_profile = {
            contract_id: profile_id for profile_id, contract_id in aliases.items()
        }
        new_groups: dict[str, SignalGroupContract] = {}
        for group_id, group in contract.signal_groups.items():
            profile_group_id = contract_to_profile.get(group_id, group_id)
            group_binding = tls_binding.signal_groups.get(profile_group_id)
            if group_binding is None or not group_binding.conflict_matrix_known:
                new_groups[group_id] = group
                continue
            conflicts = sorted(
                {aliases.get(name, name) for name in group_binding.conflicts_with} - {group_id}
            )
            new_groups[group_id] = replace(
                group,
                conflicts_with=conflicts,
                conflict_matrix_known=True,
            )
        # Matriz de conflitos ao nível do link: índices universais dentro do TLS
        # (não precisam de tradução por alias, ao contrário dos ids de grupo).
        bound.append(
            replace(
                contract,
                signal_groups=new_groups,
                link_conflicts=dict(tls_binding.link_conflicts),
                known_conflict_links=tls_binding.known_conflict_links,
            )
        )
    return bound


def build_controller_contract(
    cits_config: CITSConfig, tsp_config: TSPConfig, tls_id: str
) -> ControllerContract:
    intersection = cits_config.tls_to_intersection[tls_id]
    raw = tsp_config.controller_contract_for_tls(tls_id)
    safety = cits_config.safety_constraints
    mapping = tsp_config.phase_mapping_for_tls(tls_id)
    tls_profile = _network_tls_profile(cits_config, tsp_config, tls_id)
    prefer_generated = _prefer_generated_contract(tsp_config, tls_id, tls_profile)

    allowed_actions = list(raw.get("allowed_actions", ["green_extension", "early_green"]))
    phase_sequence = _int_list(raw.get("phase_sequence", mapping.get("phase_sequence", [])))
    if tls_profile is not None and (prefer_generated or not phase_sequence):
        phase_sequence = list(tls_profile.phase_sequence)
    service_green_phase_indices = _int_list(
        raw.get("service_green_phase_indices", mapping.get("service_green_phase_indices", []))
    )
    if tls_profile is not None and (prefer_generated or not service_green_phase_indices):
        service_green_phase_indices = list(tls_profile.service_green_phase_indices)
    intergreen_phase_indices = _int_list(raw.get("intergreen_phase_indices", []))
    if tls_profile is not None and (prefer_generated or not intergreen_phase_indices):
        intergreen_phase_indices = list(tls_profile.intergreen_phase_indices)
    signal_groups: dict[str, SignalGroupContract] = {}

    priority_defaults = raw.get("priority_signal_group_defaults", {})
    profile_by_target_group = _profile_movements_by_target_group(
        intersection.priority_movements, tls_profile
    )
    profile_aliases = {
        profile.signal_group_id: group_id
        for group_id, profile in profile_by_target_group.items()
        if profile is not None
    }
    for movement in intersection.priority_movements:
        movement_mapping = tsp_config.phase_mapping_for_movement(movement.movement_id, tls_id)
        profile_movement = profile_by_target_group.get(movement.target_signal_group_id)
        auto_group_raw = _group_raw_from_profile(profile_movement, profile_aliases)
        specific_group_raw = raw.get("signal_groups", {}).get(movement.target_signal_group_id, {})
        if prefer_generated:
            group_raw = dict(priority_defaults)
            group_raw.update(auto_group_raw)
            group_raw.update(specific_group_raw)
        else:
            group_raw = dict(auto_group_raw)
            group_raw.update(priority_defaults)
            group_raw.update(specific_group_raw)
        phase_index = _optional_int(
            group_raw.get("phase_index", movement_mapping.get("target_phase_index"))
        )
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

    if tls_profile is not None:
        for profile_movement in tls_profile.movements:
            group_id = profile_aliases.get(
                profile_movement.signal_group_id, profile_movement.signal_group_id
            )
            if group_id in signal_groups:
                continue
            auto_group_raw = _group_raw_from_profile(profile_movement, profile_aliases)
            signal_groups[group_id] = _group_from_raw(
                group_id,
                auto_group_raw,
                tls_id=tls_id,
                default_allowed_actions=allowed_actions,
                default_phase_index=profile_movement.target_phase_index,
                default_movement_ids=[],
                default_min_green=_float_or_none(safety.get("min_green_s")),
                default_max_green=_float_or_none(safety.get("max_total_green_s")),
                default_max_extension=_float_or_none(safety.get("max_green_extension_s")),
            )

    for item in _additional_signal_group_items(tsp_config, tls_id, raw, prefer_generated):
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

    pedestrian_phase_indices = _int_list(raw.get("pedestrian_phase_indices", []))
    if tls_profile is not None and (prefer_generated or not pedestrian_phase_indices):
        pedestrian_phase_indices = _pedestrian_phase_indices(tls_profile)
    # Sem profile nem valor de config não há expectativa real de ciclo — None
    # salta o check em vez de fabricar um default (90) que fail-closava qualquer
    # plano legítimo != 90 s.
    expected_cycle_default = tls_profile.expected_cycle_s if tls_profile is not None else None
    pedestrian_required = bool(
        raw.get(
            "pedestrian_phase_required",
            bool(safety.get("pedestrian_clearance_must_not_be_shortened", True)),
        )
    )
    if prefer_generated and not pedestrian_phase_indices:
        pedestrian_required = False

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
        expected_cycle_s=_float_or_none(
            raw.get("expected_cycle_s", raw.get("cycle_s", expected_cycle_default))
        ),
        pedestrian_phase_required=pedestrian_required,
        pedestrian_phase_indices=pedestrian_phase_indices,
        signal_groups=signal_groups,
        vehicle_link_indices=(_vehicle_link_indices(tls_profile) if tls_profile is not None else []),
    )


def _runtime_green_phase(
    states: list[str],
    link_indices: list[int],
    prefer_protected: bool,
    durations: list[float] | None = None,
) -> int | None:
    """Índice da fase runtime que melhor serve o verde do grupo.

    Os link indices vêm do profile (net.xml) mas são TOPOLÓGICOS — o WAUT troca os
    *estados* das fases, não a ordem dos links — logo continuam válidos em runtime.

    Em vez de exigir TODOS os links do grupo verdes numa ÚNICA fase (que devolvia
    ``None`` em verde dividido por várias fases — protegido+permissivo, lagging-left
    — deixando o ``phase_index`` offline e excluindo o TLS na verificação), pontua
    cada fase pelo verde que dá AOS LINKS DO GRUPO e escolhe a melhor: mais links em
    verde protegido (``'G'``), depois mais links em verde (``'g'``/``'G'``), depois
    maior duração de serviço, depois índice mais cedo. Prefere verde protegido
    quando exigido (só recorre a permissivo se nenhuma fase protege o grupo).
    Devolve ``None`` só quando NENHUMA fase dá verde ao grupo (mantém o
    ``phase_index`` offline -> fail-closed).

    Não relaxa segurança: só escolhe qual a fase de serviço que o
    green_extension/early_green visa; os gates de clearance/all-red operam sobre os
    service/intergreen recomputados do runtime, não sobre este índice.
    """
    indices = [index for index in link_indices if isinstance(index, int) and index >= 0]
    if not indices:
        return None
    scored: list[tuple[int, int, float, int, int]] = []
    for phase, state in enumerate(states):
        chars = [state[index] for index in indices if index < len(state)]
        protected = sum(1 for ch in chars if ch == "G")
        green = sum(1 for ch in chars if ch.lower() == "g")
        if green == 0:
            continue
        duration = float(durations[phase]) if durations and phase < len(durations) else 0.0
        scored.append((protected, green, duration, -phase, phase))
    if not scored:
        return None
    if prefer_protected and any(item[0] > 0 for item in scored):
        scored = [item for item in scored if item[0] > 0]
    return max(scored)[-1]


def reconcile_contract_with_runtime(
    contract: ControllerContract,
    adapter: SignalControlAdapter,
    tls_profile: TLSProfile | None = None,
) -> ControllerContract:
    """Reconcilia a ESTRUTURA do contrato com o programa SUMO realmente em execução.

    O contrato é construído offline a partir do ``tlLogic`` embebido no ``net.xml``,
    mas em runtime o WAUT troca o programa por hora do dia (ex.: ciclo 90s->243s,
    4->3 fases). O contrato offline fica desatualizado e a verificação fail-closa
    com "ciclo difere"/"fase fora do programa", e o motor recusa o early_green com
    "current_phase_not_configured_as_conflict"/"would_skip_clearance_phase".

    Lê o programa em execução via TraCI e reconstrói ``phase_sequence``,
    ``service_green_phase_indices``, ``intergreen_phase_indices`` e
    ``expected_cycle_s`` a partir dos estados/durações reais. Com ``tls_profile``,
    remapeia também o ``phase_index`` de cada signal group para a fase verde REAL
    do movimento (via os seus link indices topológicos + os estados runtime) — é o
    que destrava o green_extension/early_green nos TLS WAUT cujo verde mudou de
    fase. NÃO relaxa segurança: os checks de clearance passam a operar sobre os
    intergreens VERDADEIROS e a verificação pedonal mantém-se intacta (fail-closed
    quando não confirmável). Programa ilegível -> contrato inalterado. Preserva a
    matriz de conflitos do binding e ``pedestrian_phase_indices``.
    """
    states = adapter.read_program_phase_states(contract.tls_id)
    if not states:
        return contract  # programa ilegível -> fail-closed (inalterado)
    durations = adapter.read_program_phase_durations(contract.tls_id)
    service_green = [index for index, state in enumerate(states) if "g" in state.lower()]
    intergreen = [index for index, state in enumerate(states) if "g" not in state.lower()]
    expected_cycle = (
        sum(float(duration) for duration in durations) if durations else contract.expected_cycle_s
    )

    signal_groups = contract.signal_groups
    if tls_profile is not None:
        # link indices por signal group (match directo: nos contratos
        # auto-descobertos o id do grupo == movement.signal_group_id do profile;
        # em configs com alias não há match -> phase_index mantém-se, que é o
        # correto porque aí o programa runtime == net.xml).
        link_indices_by_group = {
            movement.signal_group_id: movement.link_indices for movement in tls_profile.movements
        }
        remapped: dict[str, SignalGroupContract] = {}
        for group_id, group in contract.signal_groups.items():
            link_indices = link_indices_by_group.get(group_id)
            runtime_phase = (
                _runtime_green_phase(
                    states, link_indices, group.requires_protected_green, durations
                )
                if link_indices
                else None
            )
            remapped[group_id] = (
                replace(group, phase_index=runtime_phase) if runtime_phase is not None else group
            )
        signal_groups = remapped

    return replace(
        contract,
        phase_sequence=list(range(len(states))),
        service_green_phase_indices=service_green,
        intergreen_phase_indices=intergreen,
        expected_cycle_s=expected_cycle,
        signal_groups=signal_groups,
    )


def _network_tls_profile(
    cits_config: CITSConfig, tsp_config: TSPConfig, tls_id: str
) -> TLSProfile | None:
    if not _network_profile_enabled(cits_config, tsp_config):
        return None
    network = cits_config.sumo.get("network")
    if not network:
        return None
    network_path = Path(str(network))
    if not network_path.is_absolute():
        network_path = cits_config.root / network_path
    try:
        profile = load_network_profile(network_path)
    except (FileNotFoundError, OSError, ValueError):
        return None
    return profile.tls_profile(tls_id)


def _network_profile_enabled(cits_config: CITSConfig, tsp_config: TSPConfig) -> bool:
    tsp_profile = tsp_config.raw.get("network_profile", {})
    cits_discovery = cits_config.raw.get("network_discovery", {})
    return (isinstance(tsp_profile, dict) and bool(tsp_profile.get("enabled", False))) or (
        isinstance(cits_discovery, dict) and bool(cits_discovery.get("enabled", False))
    )


def _prefer_generated_contract(
    tsp_config: TSPConfig, tls_id: str, tls_profile: TLSProfile | None
) -> bool:
    if tls_profile is None:
        return False
    profile_cfg = tsp_config.raw.get("network_profile", {})
    if not isinstance(profile_cfg, dict):
        return False
    controllers = tsp_config.controller_contracts.get("controllers", {})
    has_specific = isinstance(controllers, dict) and isinstance(controllers.get(tls_id), dict)
    return (
        bool(profile_cfg.get("prefer_generated_contracts_for_unknown_tls", True))
        and not has_specific
    )


def _profile_movements_by_target_group(
    movements: list[PriorityMovementConfig],
    tls_profile: TLSProfile | None,
) -> dict[str, MovementProfile | None]:
    if tls_profile is None:
        return {movement.target_signal_group_id: None for movement in movements}
    return {
        movement.target_signal_group_id: _profile_movement_for_priority(tls_profile, movement)
        for movement in movements
    }


def _profile_movement_for_priority(
    tls_profile: TLSProfile,
    movement: PriorityMovementConfig,
) -> MovementProfile | None:
    for profile_movement in tls_profile.movements:
        if (
            profile_movement.movement_id == movement.movement_id
            or profile_movement.signal_group_id == movement.target_signal_group_id
        ):
            return profile_movement
    for approach_edge in movement.approach_edges:
        egress_edges = list(getattr(movement, "egress_edges", []))
        if egress_edges:
            for egress_edge in egress_edges:
                profile_movement = tls_profile.movement_for_edges(approach_edge, egress_edge)
                if profile_movement is not None:
                    return profile_movement
        profile_movement = tls_profile.movement_for_edges(approach_edge)
        if profile_movement is not None:
            return profile_movement
    return None


def _group_raw_from_profile(
    movement: MovementProfile | None,
    aliases: dict[str, str],
) -> dict[str, object]:
    if movement is None:
        return {}
    conflicts = [aliases.get(group_id, group_id) for group_id in movement.conflicts_with]
    return {
        "phase_index": movement.target_phase_index,
        "conflicts_with": conflicts,
        "requires_protected_green": bool(movement.protected_green_phase_indices),
        "allow_edge_state_fallback": False,
    }


def _additional_signal_group_items(
    tsp_config: TSPConfig,
    tls_id: str,
    raw: dict[str, object],
    prefer_generated: bool,
) -> list[dict[str, object]]:
    if not prefer_generated:
        items = raw.get("additional_signal_groups", [])
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    controllers = tsp_config.controller_contracts.get("controllers", {})
    if not isinstance(controllers, dict):
        return []
    specific = controllers.get(tls_id, {})
    if not isinstance(specific, dict):
        return []
    items = specific.get("additional_signal_groups", [])
    return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []


def _vehicle_link_indices(tls_profile: TLSProfile) -> list[int]:
    """Link indices de movimentos VEICULARES (conexões sem lanes internas ':').

    As restantes conexões (de/para lanes internas) são travessias pedonais. É a
    MESMA distinção usada por _pedestrian_phase_indices, extraída para um só
    sítio, para distinguir verde veicular de verde pedonal na verificação de
    clearance (uma fase pedonal exclusiva tem peões verdes e veículos vermelhos).
    """
    return sorted(
        {
            connection.link_index
            for connection in tls_profile.connections
            if not connection.from_edge.startswith(":")
            and not connection.to_edge.startswith(":")
        }
    )


def _pedestrian_phase_indices(tls_profile: TLSProfile) -> list[int]:
    pedestrian_indices = {
        connection.link_index
        for connection in tls_profile.connections
        if connection.from_edge.startswith(":") or connection.to_edge.startswith(":")
    }
    vehicle_indices = set(_vehicle_link_indices(tls_profile))
    result: list[int] = []
    for phase in tls_profile.phases:
        pedestrian_green = any(
            index < len(phase.state) and phase.state[index].lower() == "g"
            for index in pedestrian_indices
        )
        vehicle_green = any(
            index < len(phase.state) and phase.state[index].lower() == "g"
            for index in vehicle_indices
        )
        if pedestrian_green and not vehicle_green:
            result.append(phase.index)
    return result


def _group_from_raw(
    signal_group_id: str,
    raw: dict[str, object],
    *,
    tls_id: str,
    default_allowed_actions: list[str],
    default_phase_index: int | None,
    default_movement_ids: list[str],
    default_min_green: float | None,
    default_max_green: float | None,
    default_max_extension: float | None,
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
        requires_protected_green=bool(raw.get("requires_protected_green", True)),
        allow_edge_state_fallback=bool(raw.get("allow_edge_state_fallback", False)),
    )


def _int_list(values: object) -> list[int]:
    if not isinstance(values, list):
        return []
    result: list[int] = []
    for item in values:
        value = _optional_int(item)
        if value is not None:
            result.append(value)
    return result


def _float_or_none(value: object, default: float | None = None) -> float | None:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_all_red_state(state: str) -> bool:
    return bool(state) and all(ch.lower() == "r" for ch in state)


def _has_configured_pedestrian_phase(
    states: list[str],
    service_green_phase_indices: list[int],
    intergreen_phase_indices: list[int],
    pedestrian_phase_indices: list[int],
    vehicle_link_indices: list[int] | None = None,
) -> bool:
    """Existe uma fase pedonal EXCLUSIVA (peões verdes, veículos vermelhos)?

    É a garantia de que o early_green (truncagem de fase) não encurta a travessia
    pedonal. Com ``vehicle_link_indices`` (classificação topológica dos links
    veiculares, estável entre programas WAUT), verifica a exclusividade
    DIRETAMENTE: a fase tem verde mas NENHUM link veicular verde. Sem essa info
    (``None``) recai na heurística anterior — a fase pedonal tem de estar fora do
    conjunto curado service∪intergreen. A heurística é necessária para configs
    sem profile, mas falha quando service/intergreen são DERIVADOS (cobrem todas
    as fases e a fase pedonal, que tem 'g' no link de peão, cai em service); é
    por isso que o caminho com links é o correto para redes reais (OSM/WAUT).
    """
    if not pedestrian_phase_indices:
        return False
    vehicular_phase_indices = set(service_green_phase_indices) | set(intergreen_phase_indices)
    for index in pedestrian_phase_indices:
        if not (0 <= index < len(states)):
            continue
        state = states[index]
        if not any(ch in {"G", "g"} for ch in state):
            continue  # sem qualquer verde -> não é fase pedonal
        if vehicle_link_indices is not None:
            vehicle_green = any(
                link < len(state) and state[link] in {"G", "g"} for link in vehicle_link_indices
            )
            if not vehicle_green:
                return True  # verde presente e nenhum veicular -> pedonal exclusiva
        elif index not in vehicular_phase_indices:
            return True  # fallback: fase fora do conjunto veicular curado
    return False


def _green_link_indices(state: str) -> set[int]:
    return {index for index, ch in enumerate(state) if ch in ("g", "G")}


def _transition_needs_all_red(
    states: list[str],
    from_phase: int,
    to_phase: int,
    link_conflicts: dict[int, frozenset[int]] | None,
    known_conflict_links: frozenset[int] | None,
) -> bool:
    """Esta transição verde->verde (sem all-red) liberta um movimento conflituante?

    All-red entre dois verdes só é necessário quando um movimento que TERMINA
    (verde na fase de origem, já não na de destino) é foe de um que ARRANCA (novo
    verde na fase de destino) — só aí há tráfego a libertar que pode colidir com o
    que ainda escoa. Foes que co-existem verdes na fase de destino
    (protegido/permissivo) são uma propriedade dessa fase, não uma clearance da
    transição, e não exigem all-red entre as fases.

    Fail-closed por omissão de dados:
    - ``link_conflicts is None`` (sem matriz autoritativa): devolve ``True`` para
      TODAS as transições -> comportamento legado (exige all-red em todo o lado),
      como em redes sintéticas sem NetworkBinding.
    - matriz presente mas algum link envolvido sem foe data autoritativa: devolve
      ``True`` (não dá para provar seguro) a menos que um conflito genuíno já o
      tenha decidido.
    """
    if link_conflicts is None:
        return True
    if not (0 <= from_phase < len(states) and 0 <= to_phase < len(states)):
        return True
    green_from = _green_link_indices(states[from_phase])
    green_to = _green_link_indices(states[to_phase])
    newly = green_to - green_from  # movimentos que ARRANCAM
    if not newly:
        return False  # nada de novo libertado (mesmo verde mantido/encolhido) -> seguro
    terminating = green_from - green_to  # movimentos que TERMINAM
    for starting in newly:
        for ending in terminating:
            if starting in link_conflicts.get(ending, frozenset()) or ending in link_conflicts.get(
                starting, frozenset()
            ):
                return True  # movimento que termina é foe de um que arranca -> all-red exigível
    known = known_conflict_links or frozenset()
    # Seguro só se TODOS os links envolvidos têm foe data autoritativa (nenhum
    # conflitua, já verificado acima); caso contrário foe data em falta -> fail-closed.
    return not all(link in known for link in (newly | terminating))


def _missing_all_red_transitions(
    states: list[str],
    durations: list[float],
    phase_sequence: list[int],
    service_green_phase_indices: list[int],
    min_all_red_s: float,
    link_conflicts: dict[int, frozenset[int]] | None = None,
    known_conflict_links: frozenset[int] | None = None,
) -> list[tuple[int, int]]:
    service_set = set(service_green_phase_indices)
    # Iterate over (position, phase_index) pairs so that duplicate phase indices
    # in the sequence each get their own from_pos — using list.index() would
    # always return the first occurrence and silently skip later ones.
    service_positions = [
        (pos, phase) for pos, phase in enumerate(phase_sequence) if phase in service_set
    ]
    if len(service_positions) < 2:
        return []

    missing: list[tuple[int, int]] = []
    sequence_len = len(phase_sequence)
    for from_pos, from_phase in service_positions:
        between: list[int] = []
        pos = from_pos
        to_phase: int | None = None
        for _ in range(1, sequence_len + 1):
            pos = (pos + 1) % sequence_len
            phase = phase_sequence[pos]
            if phase in service_green_phase_indices:
                to_phase = phase
                break
            between.append(phase)
        if to_phase is None:
            continue
        has_required_all_red = any(
            idx < len(states)
            and idx < len(durations)
            and _is_all_red_state(states[idx])
            and float(durations[idx]) >= min_all_red_s
            for idx in between
        )
        if has_required_all_red:
            continue
        # Sem all-red explícito entre dois service-greens consecutivos: só é
        # problema se a transição libertar um movimento conflituante (ou se a foe
        # data não permitir prová-lo seguro). Greens não-conflituantes (mesmo
        # verde segmentado / expansão sem conflito) deixam de ser falsos-negativos.
        if _transition_needs_all_red(
            states, from_phase, to_phase, link_conflicts, known_conflict_links
        ):
            missing.append((from_phase, to_phase))
    return missing
