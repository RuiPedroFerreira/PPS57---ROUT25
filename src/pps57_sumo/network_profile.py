#!/usr/bin/env python3
"""SUMO net.xml profiling for map-agnostic C-ITS/TSP configuration.

The profile is intentionally deterministic and conservative. It extracts the
traffic-light programs, controlled connections, approach movements and a
signal-program-derived conflict matrix from a SUMO ``net.xml``. The safety
layer still owns final approval; this module only removes hard-coded phase and
lane assumptions from imported maps.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

# M4: net.xml/additional podem vir de fontes externas (OSM) -> endurece o
# parsing de input não-confiável contra XXE/expansão de entidades. A
# anotação/construção com `ET.Element` mantém-se do stdlib (defusedxml não a
# expõe e não processa input).
try:
    from defusedxml.ElementTree import fromstring as _safe_fromstring
    from defusedxml.ElementTree import parse as _safe_parse
except ImportError:  # pragma: no cover - exercised in minimal CI images.
    _safe_fromstring = ET.fromstring
    _safe_parse = ET.parse


@dataclass(frozen=True)
class PhaseProfile:
    index: int
    duration_s: float
    state: str

    @property
    def has_green(self) -> bool:
        return any(ch.lower() == "g" for ch in self.state)


@dataclass(frozen=True)
class ConnectionProfile:
    tls_id: str
    link_index: int
    from_edge: str
    to_edge: str
    from_lane_index: int | None
    to_lane_index: int | None
    direction: str = ""
    state: str = ""
    via: str = ""

    @property
    def controlled_lane(self) -> str:
        if self.from_lane_index is None:
            return self.from_edge
        return f"{self.from_edge}_{self.from_lane_index}"

    @property
    def outgoing_lane(self) -> str:
        if self.to_lane_index is None:
            return self.to_edge
        return f"{self.to_edge}_{self.to_lane_index}"


@dataclass(frozen=True)
class MovementProfile:
    movement_id: str
    signal_group_id: str
    tls_id: str
    from_edge: str
    to_edge: str
    direction: str
    controlled_lanes: list[str]
    link_indices: list[int]
    green_phase_indices: list[int]
    protected_green_phase_indices: list[int]
    permissive_green_phase_indices: list[int]
    target_phase_index: int | None
    conflicts_with: list[str] = field(default_factory=list)

    @property
    def approach_edges(self) -> list[str]:
        return [self.from_edge]

    @property
    def egress_edges(self) -> list[str]:
        return [self.to_edge]


@dataclass(frozen=True)
class TLSProfile:
    tls_id: str
    program_id: str
    tls_type: str
    offset_s: float
    junction_type: str
    phases: list[PhaseProfile]
    connections: list[ConnectionProfile]
    movements: list[MovementProfile]

    @property
    def phase_sequence(self) -> list[int]:
        return [phase.index for phase in self.phases]

    @property
    def service_green_phase_indices(self) -> list[int]:
        return [phase.index for phase in self.phases if phase.has_green]

    @property
    def intergreen_phase_indices(self) -> list[int]:
        return [phase.index for phase in self.phases if not phase.has_green]

    @property
    def expected_cycle_s(self) -> float:
        return sum(phase.duration_s for phase in self.phases)

    @property
    def controlled_lanes(self) -> list[str]:
        return sorted(
            {
                connection.controlled_lane
                for connection in self.connections
                if connection.controlled_lane
            }
        )

    @property
    def incoming_edges(self) -> list[str]:
        return sorted(
            {
                connection.from_edge
                for connection in self.connections
                if not connection.from_edge.startswith(":")
            }
        )

    def movement_for_edges(self, from_edge: str, to_edge: str = "") -> MovementProfile | None:
        candidates = [movement for movement in self.movements if movement.from_edge == from_edge]
        if to_edge:
            exact = [movement for movement in candidates if movement.to_edge == to_edge]
            if exact:
                return _best_movement(exact)
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            return _best_movement(candidates)
        return None


@dataclass(frozen=True)
class DetectorProfile:
    detector_id: str
    detector_type: str
    lane_id: str
    edge_id: str
    file: str = ""


@dataclass(frozen=True)
class NetworkProfile:
    network_file: str
    fingerprint: str
    tls_profiles: dict[str, TLSProfile]
    detectors: list[DetectorProfile] = field(default_factory=list)

    def tls_ids(self) -> list[str]:
        return sorted(self.tls_profiles)

    def tls_profile(self, tls_id: str) -> TLSProfile | None:
        return self.tls_profiles.get(tls_id)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class NetworkProfileBuilder:
    def __init__(
        self, network_file: str | Path, *, additional_files: Iterable[str | Path] | None = None
    ) -> None:
        self.network_file = Path(network_file)
        self.additional_files = [Path(path) for path in additional_files or []]

    def build(self) -> NetworkProfile:
        if not self.network_file.exists():
            raise FileNotFoundError(f"SUMO network file not found: {self.network_file}")
        xml_bytes = self.network_file.read_bytes()
        root = _safe_fromstring(xml_bytes)
        fingerprint = hashlib.sha256(xml_bytes).hexdigest()

        junction_types = {
            str(junction.get("id")): str(junction.get("type", ""))
            for junction in root.iter("junction")
            if junction.get("id")
        }
        tl_logics = self._read_tl_logics(root)
        connections_by_tls = self._read_connections(root)

        tls_profiles: dict[str, TLSProfile] = {}
        for tls_id in sorted(set(tl_logics) | set(connections_by_tls)):
            logic = tl_logics.get(tls_id, {})
            phases = list(logic.get("phases", []))
            connections = sorted(
                connections_by_tls.get(tls_id, []), key=lambda item: item.link_index
            )
            movements = _build_movements(tls_id, phases, connections)
            tls_profiles[tls_id] = TLSProfile(
                tls_id=tls_id,
                program_id=str(logic.get("program_id", "")),
                tls_type=str(logic.get("tls_type", "")),
                offset_s=float(logic.get("offset_s", 0.0)),
                junction_type=junction_types.get(tls_id, ""),
                phases=phases,
                connections=connections,
                movements=movements,
            )

        detectors = self._read_detectors()
        return NetworkProfile(
            network_file=str(self.network_file),
            fingerprint=fingerprint,
            tls_profiles=tls_profiles,
            detectors=detectors,
        )

    def _read_tl_logics(self, root: ET.Element) -> dict[str, dict[str, object]]:
        result: dict[str, dict[str, object]] = {}
        for tl_logic in root.iter("tlLogic"):
            tls_id = str(tl_logic.get("id", ""))
            if not tls_id or tls_id in result:
                continue
            phases: list[PhaseProfile] = []
            for index, phase in enumerate(tl_logic.findall("phase")):
                phases.append(
                    PhaseProfile(
                        index=index,
                        duration_s=_float(phase.get("duration"), 0.0),
                        state=str(phase.get("state", "")),
                    )
                )
            result[tls_id] = {
                "program_id": str(tl_logic.get("programID", "")),
                "tls_type": str(tl_logic.get("type", "")),
                "offset_s": _float(tl_logic.get("offset"), 0.0),
                "phases": phases,
            }
        return result

    def _read_connections(self, root: ET.Element) -> dict[str, list[ConnectionProfile]]:
        result: dict[str, list[ConnectionProfile]] = {}
        for connection in root.iter("connection"):
            tls_id = str(connection.get("tl", ""))
            link_index = _optional_int(connection.get("linkIndex"))
            from_edge = str(connection.get("from", ""))
            to_edge = str(connection.get("to", ""))
            if not tls_id or link_index is None or not from_edge or not to_edge:
                continue
            result.setdefault(tls_id, []).append(
                ConnectionProfile(
                    tls_id=tls_id,
                    link_index=link_index,
                    from_edge=from_edge,
                    to_edge=to_edge,
                    from_lane_index=_optional_int(connection.get("fromLane")),
                    to_lane_index=_optional_int(connection.get("toLane")),
                    direction=str(connection.get("dir", "")),
                    state=str(connection.get("state", "")),
                    via=str(connection.get("via", "")),
                )
            )
        return result

    def _read_detectors(self) -> list[DetectorProfile]:
        detectors: list[DetectorProfile] = []
        for path in self.additional_files:
            if not path.exists():
                continue
            root = _safe_parse(path).getroot()
            for tag, detector_type in (("inductionLoop", "e1"), ("laneAreaDetector", "e2")):
                for item in root.iter(tag):
                    detector_id = str(item.get("id", ""))
                    lane_id = str(item.get("lane", ""))
                    if not detector_id or not lane_id:
                        continue
                    detectors.append(
                        DetectorProfile(
                            detector_id=detector_id,
                            detector_type=detector_type,
                            lane_id=lane_id,
                            edge_id=edge_from_lane(lane_id),
                            file=str(item.get("file", "")),
                        )
                    )
        return detectors


def load_network_profile(
    network_file: str | Path,
    *,
    additional_files: Iterable[str | Path] | None = None,
) -> NetworkProfile:
    path = Path(network_file).resolve()
    add_paths = tuple(str(Path(item).resolve()) for item in additional_files or [])
    stat = path.stat()
    return _load_network_profile_cached(str(path), stat.st_mtime_ns, stat.st_size, add_paths)


@lru_cache(maxsize=8)
def _load_network_profile_cached(
    network_file: str,
    mtime_ns: int,
    size: int,
    additional_files: tuple[str, ...],
) -> NetworkProfile:
    del mtime_ns, size
    return NetworkProfileBuilder(network_file, additional_files=additional_files).build()


def edge_from_lane(lane_id: str) -> str:
    edge, sep, suffix = lane_id.rpartition("_")
    if sep and suffix.isdigit():
        return edge
    return lane_id


def _build_movements(
    tls_id: str,
    phases: list[PhaseProfile],
    connections: list[ConnectionProfile],
) -> list[MovementProfile]:
    grouped: dict[tuple[str, str], list[ConnectionProfile]] = {}
    for connection in connections:
        if connection.from_edge.startswith(":") or connection.to_edge.startswith(":"):
            continue
        grouped.setdefault((connection.from_edge, connection.to_edge), []).append(connection)

    draft: list[MovementProfile] = []
    for (from_edge, to_edge), group in sorted(grouped.items()):
        link_indices = sorted({connection.link_index for connection in group})
        green_phases, protected_phases, permissive_phases = _green_phase_sets(phases, link_indices)
        target_phase = _target_phase(phases, link_indices, protected_phases or green_phases)
        signal_group_id = f"{tls_id}_movement_{_safe_id(from_edge)}_to_{_safe_id(to_edge)}"
        movement_id = f"{signal_group_id}_public_transport"
        draft.append(
            MovementProfile(
                movement_id=movement_id,
                signal_group_id=signal_group_id,
                tls_id=tls_id,
                from_edge=from_edge,
                to_edge=to_edge,
                direction=_movement_direction(from_edge, to_edge, group),
                controlled_lanes=sorted({connection.controlled_lane for connection in group}),
                link_indices=link_indices,
                green_phase_indices=green_phases,
                protected_green_phase_indices=protected_phases,
                permissive_green_phase_indices=permissive_phases,
                target_phase_index=target_phase,
                conflicts_with=[],
            )
        )

    conflicts = _movement_conflicts(draft)
    return [
        MovementProfile(
            movement_id=movement.movement_id,
            signal_group_id=movement.signal_group_id,
            tls_id=movement.tls_id,
            from_edge=movement.from_edge,
            to_edge=movement.to_edge,
            direction=movement.direction,
            controlled_lanes=movement.controlled_lanes,
            link_indices=movement.link_indices,
            green_phase_indices=movement.green_phase_indices,
            protected_green_phase_indices=movement.protected_green_phase_indices,
            permissive_green_phase_indices=movement.permissive_green_phase_indices,
            target_phase_index=movement.target_phase_index,
            conflicts_with=conflicts.get(movement.signal_group_id, []),
        )
        for movement in draft
    ]


def _green_phase_sets(
    phases: list[PhaseProfile],
    link_indices: list[int],
) -> tuple[list[int], list[int], list[int]]:
    green: list[int] = []
    protected: list[int] = []
    permissive: list[int] = []
    for phase in phases:
        chars = [phase.state[index] for index in link_indices if 0 <= index < len(phase.state)]
        if any(ch.lower() == "g" for ch in chars):
            green.append(phase.index)
        if any(ch == "G" for ch in chars):
            protected.append(phase.index)
        if any(ch == "g" for ch in chars):
            permissive.append(phase.index)
    return green, protected, permissive


def _target_phase(
    phases: list[PhaseProfile],
    link_indices: list[int],
    candidate_indices: list[int],
) -> int | None:
    if not candidate_indices:
        return None
    phase_by_index = {phase.index: phase for phase in phases}

    def score(index: int) -> tuple[int, int, float, int]:
        phase = phase_by_index.get(index)
        state = phase.state if phase is not None else ""
        chars = [state[item] for item in link_indices if 0 <= item < len(state)]
        protected_count = sum(1 for char in chars if char == "G")
        green_count = sum(1 for char in chars if char.lower() == "g")
        duration = phase.duration_s if phase is not None else 0.0
        return protected_count, green_count, duration, -index

    return max(candidate_indices, key=score)


def _movement_conflicts(movements: list[MovementProfile]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {movement.signal_group_id: [] for movement in movements}
    for left in movements:
        left_green = set(left.green_phase_indices)
        for right in movements:
            if left.signal_group_id == right.signal_group_id:
                continue
            right_green = set(right.green_phase_indices)
            if not left_green or not right_green or left_green.isdisjoint(right_green):
                result[left.signal_group_id].append(right.signal_group_id)
    return {key: sorted(set(values)) for key, values in result.items()}


def _best_movement(movements: list[MovementProfile]) -> MovementProfile:
    return max(
        movements,
        key=lambda movement: (
            len(movement.protected_green_phase_indices),
            len(movement.green_phase_indices),
            -len(movement.conflicts_with),
            movement.movement_id,
        ),
    )


def _movement_direction(from_edge: str, to_edge: str, connections: list[ConnectionProfile]) -> str:
    directions = {connection.direction for connection in connections if connection.direction}
    if len(directions) == 1:
        return next(iter(directions))
    numbers = [int(item) for item in re.findall(r"\d+", f"{from_edge} {to_edge}")]
    if len(numbers) >= 2:
        return "ascending" if numbers[-1] > numbers[0] else "descending"
    return "unknown"


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_")
    return safe or "unknown"


def _optional_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _float(value: object, default: float) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default
