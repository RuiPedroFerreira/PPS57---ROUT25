#!/usr/bin/env python3
"""Carregamento e indexação da configuração C-ITS do Pacote 3."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class IntersectionConfig:
    intersection_id: str
    tls_id: str
    rsu_id: str
    name: str
    controlled_approach_edges: List[str]
    main_corridor_edges: List[str]
    priority_movements: Dict[str, str]


@dataclass(frozen=True)
class CITSConfig:
    root: Path
    raw: Dict[str, Any]
    intersections: List[IntersectionConfig]
    edge_to_intersection: Dict[str, IntersectionConfig]
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


def load_cits_config(path: str | Path, root: Optional[str | Path] = None) -> CITSConfig:
    config_path = Path(path)
    if root is None:
        root_path = config_path.resolve().parents[1]
    else:
        root_path = Path(root).resolve()

    with config_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)

    intersections = [
        IntersectionConfig(
            intersection_id=item["intersection_id"],
            tls_id=item["tls_id"],
            rsu_id=item["rsu_id"],
            name=item.get("name", item["intersection_id"]),
            controlled_approach_edges=list(item.get("controlled_approach_edges", [])),
            main_corridor_edges=list(item.get("main_corridor_edges", [])),
            priority_movements=dict(item.get("priority_movements", {})),
        )
        for item in raw.get("intersections", [])
    ]

    edge_to_intersection: Dict[str, IntersectionConfig] = {}
    rsu_to_intersection: Dict[str, IntersectionConfig] = {}
    tls_to_intersection: Dict[str, IntersectionConfig] = {}

    for intersection in intersections:
        rsu_to_intersection[intersection.rsu_id] = intersection
        tls_to_intersection[intersection.tls_id] = intersection
        for edge_id in intersection.controlled_approach_edges:
            edge_to_intersection[edge_id] = intersection

    return CITSConfig(
        root=root_path,
        raw=raw,
        intersections=intersections,
        edge_to_intersection=edge_to_intersection,
        rsu_to_intersection=rsu_to_intersection,
        tls_to_intersection=tls_to_intersection,
    )


def require_keys(mapping: Dict[str, Any], keys: Iterable[str], section_name: str) -> None:
    missing = [key for key in keys if key not in mapping]
    if missing:
        raise ValueError(f"Missing required keys in {section_name}: {', '.join(missing)}")
