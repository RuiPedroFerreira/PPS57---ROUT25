#!/usr/bin/env python3
"""Load and index C-ITS/V2X emulation configuration."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pps57_sumo.network_profile import NetworkProfile, TLSProfile, load_network_profile


@dataclass(frozen=True)
class PriorityMovementConfig:
    movement_id: str
    direction: str
    approach_edges: list[str]
    egress_edges: list[str]
    vehicle_classes: list[str]
    target_signal_group_id: str
    allowed_actions: list[str]
    objectives: list[str]


@dataclass(frozen=True)
class IntersectionConfig:
    intersection_id: str
    tls_id: str
    rsu_id: str
    name: str
    controlled_approach_edges: list[str]
    priority_movements: list[PriorityMovementConfig]
    signal_controlled: bool = True


@dataclass(frozen=True)
class CITSConfig:
    root: Path
    raw: dict[str, Any]
    intersections: list[IntersectionConfig]
    edge_to_intersection: dict[str, IntersectionConfig]
    edge_to_priority_movements: dict[str, list[PriorityMovementConfig]]
    movement_by_id: dict[str, PriorityMovementConfig]
    rsu_to_intersection: dict[str, IntersectionConfig]
    tls_to_intersection: dict[str, IntersectionConfig]
    intersection_by_alias: dict[str, IntersectionConfig]

    @property
    def obu_policy(self) -> dict[str, Any]:
        return self.raw.get("obu_policy", {})

    @property
    def rsu_policy(self) -> dict[str, Any]:
        return self.raw.get("rsu_policy", {})

    @property
    def safety_constraints(self) -> dict[str, Any]:
        return self.raw.get("safety_constraints", {})

    @property
    def schedule_plan(self) -> dict[str, Any]:
        return self.raw.get("schedule_plan", {})

    @property
    def state_estimation(self) -> dict[str, Any]:
        return self.raw.get("state_estimation", {})

    @property
    def logging(self) -> dict[str, Any]:
        return self.raw.get("logging", {})

    @property
    def sumo(self) -> dict[str, Any]:
        return self.raw.get("sumo", {})

    @property
    def signal_controlled_intersections(self) -> list[IntersectionConfig]:
        return [
            intersection for intersection in self.intersections if intersection.signal_controlled
        ]

    def path_from_root(self, relative: str | Path) -> Path:
        path = Path(relative)
        return path if path.is_absolute() else self.root / path

    def priority_movements_for_edge(self, edge_id: str) -> list[PriorityMovementConfig]:
        return list(self.edge_to_priority_movements.get(edge_id, []))

    def priority_movement_for_request(
        self,
        *,
        movement_id: str = "",
        edge_id: str = "",
        next_edge_id: str = "",
        vehicle_class: str = "",
    ) -> PriorityMovementConfig | None:
        if movement_id:
            return self.movement_by_id.get(movement_id)
        vehicle_class_norm = normalise_vehicle_class(vehicle_class)
        movements = self.priority_movements_for_edge(edge_id)
        if next_edge_id:
            egress_matches = [
                movement for movement in movements if next_edge_id in movement.egress_edges
            ]
            if egress_matches:
                movements = egress_matches
            else:
                movements = [movement for movement in movements if not movement.egress_edges]
        for movement in movements:
            allowed = {normalise_vehicle_class(item) for item in movement.vehicle_classes}
            if not allowed or vehicle_class_norm in allowed or "*" in allowed:
                return movement
        # Todos os movimentos restringem vehicle_classes e nenhum cobre esta
        # classe: não há movimento elegível. Devolver um movimento restrito a
        # outra classe daria prioridade a veículos fora do catálogo.
        return None


def load_cits_config(path: str | Path, root: str | Path | None = None) -> CITSConfig:
    config_path = Path(path)
    if root is None:
        root_path = config_path.resolve().parents[1]
    else:
        root_path = Path(root).resolve()

    with config_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    def parse_priority_movements(
        item: dict[str, Any], section_name: str
    ) -> list[PriorityMovementConfig]:
        movements: list[PriorityMovementConfig] = []
        raw_movements = item.get("priority_movements", [])
        if isinstance(raw_movements, dict):
            # Legacy shape: {"westbound": "I1_I2"}. Kept only to provide a
            # clear migration path for older local files.
            raw_movements = [
                {
                    "movement_id": f"{item['intersection_id']}_{direction}_public_transport",
                    "direction": direction,
                    "approach_edges": [edge_id],
                    "egress_edges": [],
                    "vehicle_classes": ["public_transport"],
                    "target_signal_group_id": f"{item['tls_id']}_{direction}",
                    "allowed_actions": ["green_extension", "early_green"],
                    "objectives": ["schedule_delay", "headway_recovery"],
                }
                for direction, edge_id in raw_movements.items()
            ]
        if not isinstance(raw_movements, list):
            raise ValueError(f"{section_name}.priority_movements must be a list")
        for movement_index, movement in enumerate(raw_movements):
            movement_section = f"{section_name}.priority_movements[{movement_index}]"
            require_keys(
                movement,
                ("movement_id", "approach_edges", "target_signal_group_id"),
                movement_section,
            )
            movements.append(
                PriorityMovementConfig(
                    movement_id=str(movement["movement_id"]),
                    direction=str(movement.get("direction", "")),
                    approach_edges=list(movement.get("approach_edges", [])),
                    egress_edges=list(movement.get("egress_edges", [])),
                    vehicle_classes=list(movement.get("vehicle_classes", ["public_transport"])),
                    target_signal_group_id=str(movement["target_signal_group_id"]),
                    allowed_actions=list(
                        movement.get("allowed_actions", ["green_extension", "early_green"])
                    ),
                    objectives=list(
                        movement.get("objectives", ["schedule_delay", "headway_recovery"])
                    ),
                )
            )
        return movements

    intersections: list[IntersectionConfig] = []
    for index, item in enumerate(raw.get("intersections", [])):
        # Erro claro em vez de um KeyError nu vindo do fundo de uma list
        # comprehension quando um bloco de intersection está malformado.
        section_name = f"intersections[{index}]"
        require_keys(item, ("intersection_id", "tls_id", "rsu_id"), section_name)
        signal_controlled = item.get("signal_controlled", True)
        if not isinstance(signal_controlled, bool):
            raise ValueError(f"{section_name}.signal_controlled must be a boolean")
        intersections.append(
            IntersectionConfig(
                intersection_id=item["intersection_id"],
                tls_id=item["tls_id"],
                rsu_id=item["rsu_id"],
                name=item.get("name", item["intersection_id"]),
                controlled_approach_edges=list(item.get("controlled_approach_edges", [])),
                priority_movements=parse_priority_movements(item, section_name),
                signal_controlled=signal_controlled,
            )
        )

    intersections.extend(
        _auto_intersections_from_network(
            raw,
            root_path,
            configured_tls_ids={intersection.tls_id for intersection in intersections},
            configured_aliases={intersection.intersection_id for intersection in intersections},
        )
    )

    edge_to_intersection: dict[str, IntersectionConfig] = {}
    edge_to_priority_movements: dict[str, list[PriorityMovementConfig]] = {}
    movement_by_id: dict[str, PriorityMovementConfig] = {}
    rsu_to_intersection: dict[str, IntersectionConfig] = {}
    tls_to_intersection: dict[str, IntersectionConfig] = {}
    intersection_by_alias: dict[str, IntersectionConfig] = {}

    for intersection in intersections:
        rsu_to_intersection[intersection.rsu_id] = intersection
        tls_to_intersection[intersection.tls_id] = intersection
        intersection_by_alias[intersection.intersection_id] = intersection
        for edge_id in intersection.controlled_approach_edges:
            edge_to_intersection[edge_id] = intersection
        for movement in intersection.priority_movements:
            if movement.movement_id in movement_by_id:
                raise ValueError(f"Duplicate priority movement id: {movement.movement_id}")
            movement_by_id[movement.movement_id] = movement
            for edge_id in movement.approach_edges:
                edge_to_priority_movements.setdefault(edge_id, []).append(movement)

    return CITSConfig(
        root=root_path,
        raw=raw,
        intersections=intersections,
        edge_to_intersection=edge_to_intersection,
        edge_to_priority_movements=edge_to_priority_movements,
        movement_by_id=movement_by_id,
        rsu_to_intersection=rsu_to_intersection,
        tls_to_intersection=tls_to_intersection,
        intersection_by_alias=intersection_by_alias,
    )


def require_keys(mapping: dict[str, Any], keys: Iterable[str], section_name: str) -> None:
    missing = [key for key in keys if key not in mapping]
    if missing:
        raise ValueError(f"Missing required keys in {section_name}: {', '.join(missing)}")


def normalise_vehicle_class(vehicle_class: str) -> str:
    value = (vehicle_class or "").lower()
    if value in {"bus", "coach", "public_transport_bus"}:
        return "public_transport"
    return value


def _auto_intersections_from_network(
    raw: dict[str, Any],
    root_path: Path,
    *,
    configured_tls_ids: set[str],
    configured_aliases: set[str],
) -> list[IntersectionConfig]:
    discovery = raw.get("network_discovery", {})
    if not isinstance(discovery, dict) or not bool(discovery.get("enabled", False)):
        return []
    augment = bool(discovery.get("augment_missing_intersections", True))
    if configured_tls_ids and not augment:
        return []

    profile = _load_network_profile_for_config(raw, root_path)
    if profile is None:
        return []

    classes = [
        str(item) for item in discovery.get("priority_vehicle_classes", ["public_transport"])
    ]
    rsu_prefix = str(discovery.get("rsu_id_prefix", "RSU_AUTO_"))
    auto_movements = bool(discovery.get("auto_generate_priority_movements", True))
    generated: list[IntersectionConfig] = []
    for tls_id in profile.tls_ids():
        if tls_id in configured_tls_ids or tls_id in configured_aliases:
            continue
        tls = profile.tls_profile(tls_id)
        if tls is None:
            continue
        generated.append(
            _intersection_from_tls_profile(
                tls,
                classes=classes,
                rsu_prefix=rsu_prefix,
                auto_generate_priority_movements=auto_movements,
            )
        )
    return generated


def _load_network_profile_for_config(
    raw: dict[str, Any], root_path: Path
) -> NetworkProfile | None:
    sumo_cfg = raw.get("sumo", {})
    if not isinstance(sumo_cfg, dict):
        return None
    network = sumo_cfg.get("network")
    if not network:
        return None
    network_path = Path(str(network))
    if not network_path.is_absolute():
        network_path = root_path / network_path
    try:
        return load_network_profile(network_path)
    except (FileNotFoundError, OSError, ValueError):
        return None


def _intersection_from_tls_profile(
    tls: TLSProfile,
    *,
    classes: list[str],
    rsu_prefix: str,
    auto_generate_priority_movements: bool,
) -> IntersectionConfig:
    movements = [
        PriorityMovementConfig(
            movement_id=movement.movement_id,
            direction=movement.direction,
            approach_edges=list(movement.approach_edges),
            egress_edges=list(movement.egress_edges),
            vehicle_classes=list(classes),
            target_signal_group_id=movement.signal_group_id,
            allowed_actions=["green_extension", "early_green"],
            objectives=["schedule_delay", "headway_recovery"],
        )
        for movement in tls.movements
        if auto_generate_priority_movements and movement.target_phase_index is not None
    ]
    return IntersectionConfig(
        intersection_id=tls.tls_id,
        tls_id=tls.tls_id,
        rsu_id=f"{rsu_prefix}{_safe_id(tls.tls_id).upper()}",
        name=f"Auto-discovered TLS {tls.tls_id}",
        controlled_approach_edges=list(tls.incoming_edges),
        priority_movements=movements,
        signal_controlled=True,
    )


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value).strip("_") or "TLS"
