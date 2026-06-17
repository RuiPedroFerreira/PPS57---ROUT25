#!/usr/bin/env python3
"""Configuration for policy optimization and reinforcement-learning training."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class OptimizationConfig:
    root: Path
    raw: dict[str, Any]

    @property
    def logging(self) -> dict[str, Any]:
        return self.raw.get("logging", {})

    @property
    def offline_training(self) -> dict[str, Any]:
        return self.raw.get("offline_training", {})

    @property
    def reward(self) -> dict[str, Any]:
        return self.raw.get("reward", {})

    @property
    def safety(self) -> dict[str, Any]:
        return self.raw.get("safety", {})

    @property
    def reinforcement_learning(self) -> dict[str, Any]:
        return self.raw.get("reinforcement_learning", {})

    def path_from_root(self, relative: str | Path) -> Path:
        path = Path(relative)
        return path if path.is_absolute() else self.root / path


def load_optimization_config(
    path: str | Path, root: str | Path | None = None
) -> OptimizationConfig:
    config_path = Path(path)
    root_path = Path(root).resolve() if root is not None else config_path.resolve().parents[1]
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    return OptimizationConfig(root=root_path, raw=raw)


def load_policy_optimization_config(
    path: str | Path, root: str | Path | None = None
) -> OptimizationConfig:
    return load_optimization_config(path, root=root)
