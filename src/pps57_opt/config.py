#!/usr/bin/env python3
"""Configuration for policy optimization and reinforcement-learning training."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class OptimizationConfig:
    root: Path
    raw: Dict[str, Any]

    @property
    def logging(self) -> Dict[str, Any]:
        return self.raw.get("logging", {})

    @property
    def offline_training(self) -> Dict[str, Any]:
        return self.raw.get("offline_training", {})

    @property
    def reward(self) -> Dict[str, Any]:
        return self.raw.get("reward", {})

    @property
    def safety(self) -> Dict[str, Any]:
        return self.raw.get("safety", {})

    @property
    def reinforcement_learning(self) -> Dict[str, Any]:
        return self.raw.get("reinforcement_learning", {})

    def path_from_root(self, relative: str | Path) -> Path:
        path = Path(relative)
        return path if path.is_absolute() else self.root / path


def load_optimization_config(path: str | Path, root: Optional[str | Path] = None) -> OptimizationConfig:
    config_path = Path(path)
    root_path = Path(root).resolve() if root is not None else config_path.resolve().parents[1]
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    return OptimizationConfig(root=root_path, raw=raw)


def load_policy_optimization_config(path: str | Path, root: Optional[str | Path] = None) -> OptimizationConfig:
    return load_optimization_config(path, root=root)
