#!/usr/bin/env python3
"""Configuration for the TSP decision engine and Safety Layer."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import DEFAULT_ACTUATING_ACTIONS


@dataclass(frozen=True)
class TSPConfig:
    root: Path
    raw: dict[str, Any]

    @property
    def logging(self) -> dict[str, Any]:
        return self.raw.get("logging", {})

    @property
    def decision_policy(self) -> dict[str, Any]:
        return self.raw.get("decision_policy", {})

    @property
    def actuation(self) -> dict[str, Any]:
        return self.raw.get("actuation", {})

    def actuating_actions(self) -> frozenset[str]:
        """Conjunto de ações que atuam o semáforo (fonte de verdade única).

        Lido de decision_policy.actuating_actions; recai em
        DEFAULT_ACTUATING_ACTIONS quando ausente/inválido, mantendo
        comportamento idêntico ao literal que estava duplicado nos
        consumidores (optimizer/rl_trainer/event_dataset/policy_runtime).
        """
        raw = self.decision_policy.get("actuating_actions")
        if isinstance(raw, (list, tuple, set, frozenset)):
            values = {str(item) for item in raw if str(item)}
            if values:
                return frozenset(values)
        return DEFAULT_ACTUATING_ACTIONS

    @property
    def phase_mapping(self) -> dict[str, Any]:
        return self.raw.get("phase_mapping", {})

    @property
    def controller_contracts(self) -> dict[str, Any]:
        return self.raw.get("controller_contracts", {})

    def path_from_root(self, relative: str | Path) -> Path:
        path = Path(relative)
        return path if path.is_absolute() else self.root / path

    def phase_mapping_for_tls(self, tls_id: str) -> dict[str, Any]:
        mapping = dict(self.phase_mapping.get("default", {}))
        mapping.update(self.phase_mapping.get(tls_id, {}))
        return mapping

    def phase_mapping_for_movement(self, movement_id: str, tls_id: str = "") -> dict[str, Any]:
        mapping = (
            self.phase_mapping_for_tls(tls_id)
            if tls_id
            else dict(self.phase_mapping.get("default", {}))
        )
        movement_mappings = self.phase_mapping.get("priority_movements", {})
        movement_mapping = movement_mappings.get(movement_id, {})
        if isinstance(movement_mapping, dict):
            mapping.update(movement_mapping)
        return mapping

    def controller_contract_for_tls(self, tls_id: str) -> dict[str, Any]:
        contracts = self.controller_contracts
        default = dict(contracts.get("default", {}))
        controllers = contracts.get("controllers", {})
        specific = controllers.get(tls_id, {}) if isinstance(controllers, dict) else {}
        if not isinstance(specific, dict):
            return default
        merged = dict(default)
        for key, value in specific.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                nested = dict(merged[key])
                nested.update(value)
                merged[key] = nested
            else:
                merged[key] = value
        return merged


def load_tsp_config(path: str | Path, root: str | Path | None = None) -> TSPConfig:
    config_path = Path(path)
    if root is None:
        root_path = config_path.resolve().parents[1]
    else:
        root_path = Path(root).resolve()
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    return TSPConfig(root=root_path, raw=raw)
