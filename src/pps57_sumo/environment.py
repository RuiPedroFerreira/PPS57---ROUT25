#!/usr/bin/env python3
"""SUMO environment discovery shared by subprocess and TraCI entrypoints."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path


def resolve_sumo_home() -> Path | None:
    """Return a valid SUMO_HOME path, ignoring stale environment values."""
    current = os.environ.get("SUMO_HOME")
    if current:
        current_path = Path(current)
        if is_valid_sumo_home(current_path):
            return current_path.resolve()

    spec = importlib.util.find_spec("sumo")
    if spec and spec.origin:
        package_home = Path(spec.origin).resolve().parent
        if is_valid_sumo_home(package_home):
            return package_home

    for candidate in _standard_sumo_home_candidates():
        if is_valid_sumo_home(candidate):
            return candidate.resolve()
    return None


def ensure_sumo_environment() -> dict[str, str]:
    """Return an environment with SUMO_HOME repaired when possible."""
    env = os.environ.copy()
    home = resolve_sumo_home()
    if home is not None:
        env["SUMO_HOME"] = str(home)
        bin_dir = home / "bin"
        if bin_dir.is_dir():
            env["PATH"] = _prepend_path(env.get("PATH", ""), bin_dir)
    return env


def apply_sumo_environment() -> Path | None:
    """Repair os.environ in-place for APIs that do not accept an env mapping."""
    home = resolve_sumo_home()
    if home is None:
        return None
    os.environ["SUMO_HOME"] = str(home)
    bin_dir = home / "bin"
    if bin_dir.is_dir():
        os.environ["PATH"] = _prepend_path(os.environ.get("PATH", ""), bin_dir)
    return home


def is_valid_sumo_home(path: Path) -> bool:
    return (path / "tools").is_dir() and (path / "data" / "xsd").is_dir()


def _standard_sumo_home_candidates() -> list[Path]:
    return [
        Path("/opt/homebrew/opt/sumo/share/sumo"),
        Path("/opt/homebrew/share/sumo"),
        Path("/usr/local/opt/sumo/share/sumo"),
        Path("/usr/local/share/sumo"),
        Path("/usr/share/sumo"),
    ]


def _prepend_path(current: str, entry: Path) -> str:
    entry_text = str(entry)
    parts = [part for part in current.split(os.pathsep) if part]
    if entry_text in parts:
        return current
    return os.pathsep.join([entry_text, *parts])
