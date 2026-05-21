#!/usr/bin/env python3
"""Load and index C-ITS/V2X emulation configuration."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class PriorityMovementConfig:
    movement_id: str
    direction: str
    approach_edges: List[str]
    vehicle_classes: List[str]
    target_signal_group_id: str
    allowed_actions: List[str]
    objectives: List[str]


@dataclass(frozen=True)
class IntersectionConfig:
    intersection_id: str
    tls_id: str
    rsu_id: str
    name: str
    controlled_approach_edges: List[str]
    priority_movements: List[PriorityMovementConfig]


@dataclass(frozen=True)
class CITSConfig:
    root: Path
    raw: Dict[str, Any]
    intersections: List[IntersectionConfig]
    edge_to_intersection: Dict[str, IntersectionConfig]
    edge_to_priority_movements: Dict[str, List[PriorityMovementConfig]]
    movement_by_id: Dict[str, PriorityMovementConfig]
    rsu_to_intersection: Dict[str, IntersectionConfig]
    tls_to_intersection: Dict[str, IntersectionConfig]

    @property
    def obu_policy(self) -> Dict[str, Any]:
        return self.raw.get("obu_policy", {})

    @property
    def rsu_policy(self) -> Dict[str, Any]:
        return self.raw.get("rsu_policy", {})

    @property
    def safety_constraints(self) -> Dict[str, Any]:
        return self.raw.get("safety_constraints", {})

    @property
    def logging(self) -> Dict[str, Any]:
        return self.raw.get("logging", {})

    @property
    def sumo(self) -> Dict[str, Any]:
        return self.raw.get("sumo", {})

    def path_from_root(self, relative: str | Path) -> Path:
        path = Path(relative)
        return path if path.is_absolute() else self.root / path

    def priority_movements_for_edge(self, edge_id: str) -> List[PriorityMovementConfig]:
        return list(self.edge_to_priority_movements.get(edge_id, []))

    def priority_movement_for_request(
        self,
        *,
        movement_id: str = "",
        edge_id: str = "",
        vehicle_class: str = "",
    ) -> Optional[PriorityMovementConfig]:
        if movement_id:
            return self.movement_by_id.get(movement_id)
        vehicle_class_norm = normalise_vehicle_class(vehicle_class)
        for movement in self.priority_movements_for_edge(edge_id):
            allowed = {normalise_vehicle_class(item) for item in movement.vehicle_classes}
            if not allowed or vehicle_class_norm in allowed or "*" in allowed:
                return movement
        movements = self.priority_movements_for_edge(edge_id)
        return movements[0] if movements else None


def load_cits_config(path: str | Path, root: Optional[str | Path] = None) -> CITSConfig:
    config_path = Path(path)
    if root is None:
        root_path = config_path.resolve().parents[1]
    else:
        root_path = Path(root).resolve()

    with config_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    def parse_priority_movements(item: Dict[str, Any], section_name: str) -> List[PriorityMovementConfig]:
        movements: List[PriorityMovementConfig] = []
        raw_movements = item.get("priority_movements", [])
        if isinstance(raw_movements, dict):
            # Legacy shape: {"westbound": "I1_I2"}. Kept only to provide a
            # clear migration path for older local files.
            raw_movements = [
                {
                    "movement_id": f"{item['intersection_id']}_{direction}_public_transport",
                    "direction": direction,
                    "approach_edges": [edge_id],
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
            require_keys(movement, ("movement_id", "approach_edges", "target_signal_group_id"), movement_section)
            movements.append(
                PriorityMovementConfig(
                    movement_id=str(movement["movement_id"]),
                    direction=str(movement.get("direction", "")),
                    approach_edges=list(movement.get("approach_edges", [])),
                    vehicle_classes=list(movement.get("vehicle_classes", ["public_transport"])),
                    target_signal_group_id=str(movement["target_signal_group_id"]),
                    allowed_actions=list(movement.get("allowed_actions", ["green_extension", "early_green"])),
                    objectives=list(movement.get("objectives", ["schedule_delay", "headway_recovery"])),
                )
            )
        return movements

    intersections: List[IntersectionConfig] = []
    for index, item in enumerate(raw.get("intersections", [])):
        # Erro claro em vez de um KeyError nu vindo do fundo de uma list
        # comprehension quando um bloco de intersection está malformado.
        section_name = f"intersections[{index}]"
        require_keys(item, ("intersection_id", "tls_id", "rsu_id"), section_name)
        intersections.append(
            IntersectionConfig(
                intersection_id=item["intersection_id"],
                tls_id=item["tls_id"],
                rsu_id=item["rsu_id"],
                name=item.get("name", item["intersection_id"]),
                controlled_approach_edges=list(item.get("controlled_approach_edges", [])),
                priority_movements=parse_priority_movements(item, section_name),
            )
        )

    edge_to_intersection: Dict[str, IntersectionConfig] = {}
    edge_to_priority_movements: Dict[str, List[PriorityMovementConfig]] = {}
    movement_by_id: Dict[str, PriorityMovementConfig] = {}
    rsu_to_intersection: Dict[str, IntersectionConfig] = {}
    tls_to_intersection: Dict[str, IntersectionConfig] = {}

    for intersection in intersections:
        rsu_to_intersection[intersection.rsu_id] = intersection
        tls_to_intersection[intersection.tls_id] = intersection
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
    )


def require_keys(mapping: Dict[str, Any], keys: Iterable[str], section_name: str) -> None:
    missing = [key for key in keys if key not in mapping]
    if missing:
        raise ValueError(f"Missing required keys in {section_name}: {', '.join(missing)}")


def normalise_vehicle_class(vehicle_class: str) -> str:
    value = (vehicle_class or "").lower()
    if value in {"bus", "coach", "public_transport_bus"}:
        return "public_transport"
    return value
