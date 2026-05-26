#!/usr/bin/env python3
"""Scenario dashboard process runner."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
from typing import Any, Dict, Optional
from uuid import uuid4

from pps57_sumo.scenarios import load_catalog


RUN_TYPES = {"baseline", "cits", "tsp_no_actuation", "tsp_actuation", "comparison", "all"}


class RunnerError(RuntimeError):
    """Base error for dashboard runner operations."""


class RunnerBusyError(RunnerError):
    """Raised when a scenario run is already active."""


class RunnerUnsupportedError(RunnerError):
    """Raised when the requested scenario run is not supported."""


@dataclass(frozen=True)
class ScenarioRunOptions:
    scenario_id: Optional[str] = "baseline_am_peak"
    run_type: str = "comparison"
    steps: Optional[int] = None
    gui: bool = False
    generate_only: bool = False


@dataclass
class ScenarioRunner:
    root: Path
    state_path: Path = field(init=False)
    command_log_path: Path = field(init=False)
    process: Optional[subprocess.Popen] = field(default=None, init=False)
    current_state: Optional[Dict[str, Any]] = field(default=None, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        self.state_path = self.root / "outputs" / "dashboard_runtime_state.json"
        self.command_log_path = self.root / "outputs" / "dashboard_commands.jsonl"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def start_run(self, options: ScenarioRunOptions) -> Dict[str, Any]:
        with self._lock:
            self._refresh_locked()
            if self.process is not None and self.process.poll() is None:
                raise RunnerBusyError("A scenario run is already active.")
            self._validate_options(options)

            run_id = str(uuid4())
            stdout_log = self.root / "outputs" / f"dashboard_runner_{run_id}.out.log"
            stderr_log = self.root / "outputs" / f"dashboard_runner_{run_id}.err.log"
            command = self._command_for(options)

            stdout_log.parent.mkdir(parents=True, exist_ok=True)
            stdout_handle = stdout_log.open("w", encoding="utf-8")
            stderr_handle = stderr_log.open("w", encoding="utf-8")
            try:
                process = subprocess.Popen(
                    command,
                    cwd=self.root,
                    env=self._environment_for(options),
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    text=True,
                    start_new_session=os.name != "nt",
                )
            finally:
                stdout_handle.close()
                stderr_handle.close()

            self.process = process
            self.current_state = {
                "run_id": run_id,
                "status": "running",
                "scenario_id": options.scenario_id,
                "run_type": options.run_type,
                "steps": options.steps,
                "gui": options.gui,
                "generate_only": options.generate_only,
                "root": str(self.root),
                "returncode": None,
                "stdout_log": str(stdout_log),
                "stderr_log": str(stderr_log),
                "message": "Scenario run started.",
                "managed_by_current_process": True,
            }
            self._write_state_locked()
            self._append_command_locked("start", {"options": options.__dict__})
            return dict(self.current_state)

    def stop_run(self) -> Dict[str, Any]:
        with self._lock:
            self._refresh_locked()
            if self.process is None or self.process.poll() is not None:
                state = self._idle_state("No active managed scenario run.")
                self._write_state_payload(state)
                return state
            self._terminate_process_group(self.process)
            self._refresh_locked(force=True)
            if self.current_state is not None:
                self.current_state["message"] = "Scenario run stopped by dashboard command."
                self._write_state_locked()
            self._append_command_locked("stop", {})
            return dict(self.current_state) if self.current_state is not None else self._idle_state("Scenario run stopped.")

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            self._refresh_locked()
            if self.current_state is not None:
                return dict(self.current_state)
            if self.state_path.exists():
                try:
                    payload = json.loads(self.state_path.read_text(encoding="utf-8"))
                    if isinstance(payload, dict):
                        payload.setdefault("managed_by_current_process", False)
                        return payload
                except json.JSONDecodeError:
                    pass
            return self._idle_state("No scenario run has been started.")

    def _validate_options(self, options: ScenarioRunOptions) -> None:
        if options.run_type not in RUN_TYPES:
            raise RunnerUnsupportedError(f"Unsupported scenario run type: {options.run_type}")
        if options.steps is not None and options.steps < 1:
            raise RunnerUnsupportedError("steps must be >= 1.")
        catalog = load_catalog(self.root / "configs" / "scenario_catalog.yaml")
        known_scenarios = set(catalog.get("scenarios", {}))
        if not options.scenario_id:
            raise RunnerUnsupportedError("scenario_id is required.")
        if options.scenario_id not in known_scenarios:
            raise RunnerUnsupportedError(f"Unknown scenario_id: {options.scenario_id}")

    def _command_for(self, options: ScenarioRunOptions) -> list[str]:
        python = _project_python(self.root)
        command = [
            python,
            "scripts/run_sumo_scenario.py",
            "--run-type",
            options.run_type,
            "--scenario",
            str(options.scenario_id),
        ]
        if options.steps is not None:
            command.extend(["--steps", str(int(options.steps))])
        if options.gui:
            command.append("--gui")
        if options.generate_only:
            command.append("--generate-only")
        return command

    def _environment_for(self, options: ScenarioRunOptions) -> dict[str, str]:
        return os.environ.copy()

    def _refresh_locked(self, force: bool = False) -> None:
        if self.process is None or self.current_state is None:
            return
        returncode = self.process.poll()
        if returncode is None and not force:
            return
        if returncode is None:
            try:
                returncode = self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                return
        self.current_state["returncode"] = returncode
        self.current_state["status"] = "completed" if returncode == 0 else "failed"
        self.current_state["message"] = (
            "Scenario run completed successfully." if returncode == 0 else f"Scenario run exited with code {returncode}."
        )
        self.current_state["managed_by_current_process"] = True
        self._write_state_locked()
        self.process = None

    def _terminate_process_group(self, process: subprocess.Popen) -> None:
        try:
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except ProcessLookupError:
                return
            process.wait(timeout=5)

    def _idle_state(self, message: str) -> Dict[str, Any]:
        return {
            "run_id": None,
            "status": "idle",
            "scenario_id": None,
            "run_type": None,
            "steps": None,
            "gui": False,
            "generate_only": False,
            "root": str(self.root),
            "returncode": None,
            "stdout_log": None,
            "stderr_log": None,
            "message": message,
            "managed_by_current_process": True,
        }

    def _write_state_locked(self) -> None:
        assert self.current_state is not None
        self._write_state_payload(self.current_state)

    def _write_state_payload(self, payload: Dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        tmp.replace(self.state_path)

    def _append_command_locked(self, action: str, payload: Dict[str, Any]) -> None:
        event = {
            "timestamp": _now(),
            "action": action,
            "run_id": self.current_state.get("run_id") if self.current_state else None,
            "payload": payload,
        }
        self.command_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.command_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def _project_python(root: Path) -> str:
    candidate = root / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
