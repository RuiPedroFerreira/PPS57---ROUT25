#!/usr/bin/env python3
"""Configuration for the TSP decision engine and Safety Layer."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class TSPConfig:
    root: Path
    raw: Dict[str, Any]

    @property
    def logging(self) -> Dict[str, Any]:
        return self.raw.get("logging", {})

    @property
    def decision_policy(self) -> Dict[str, Any]:
        return self.raw.get("decision_policy", {})

    @property
    def actuation(self) -> Dict[str, Any]:
        return self.raw.get("actuation", {})

    @property
    def phase_mapping(self) -> Dict[str, Any]:
        return self.raw.get("phase_mapping", {})

    @property
    def dry_run(self) -> Dict[str, Any]:
        return self.raw.get("dry_run", {})

    def path_from_root(self, relative: str | Path) -> Path:
        path = Path(relative)
        return path if path.is_absolute() else self.root / path

    def phase_mapping_for_tls(self, tls_id: str) -> Dict[str, Any]:
        mapping = dict(self.phase_mapping.get("default", {}))
        mapping.update(self.phase_mapping.get(tls_id, {}))
        return mapping


def load_tsp_config(path: str | Path, root: Optional[str | Path] = None) -> TSPConfig:
    config_path = Path(path)
    if root is None:
        root_path = config_path.resolve().parents[1]
    else:
        root_path = Path(root).resolve()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    return TSPConfig(root=root_path, raw=raw)
