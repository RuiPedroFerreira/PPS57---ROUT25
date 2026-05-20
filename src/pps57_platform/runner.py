#!/usr/bin/env python3
"""Local orchestration layer for the PPS57 platform API.

The runner owns process lifecycle for local simulations and utilities. FastAPI
and Streamlit call this layer; neither one talks directly to TraCI.
"""
from __future__ import annotations

from collections import deque
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

from .data_loader import JSONL_ARTIFACTS, collect_snapshot, load_platform_config


RUN_KINDS = {
    "build-event-training-dataset",
    "cits-sumo",
    "compare-tsp-rl",
    "evaluate-decision-outcomes",
    "kpis",
    "tsp-sumo",
    "tsp-sumo-no-actuation",
    "optimize-offline",
    "train-rl-policy",
    "platform-check",
}


class RunnerError(RuntimeError):
    """Base error for runner operations."""


class RunnerBusyError(RunnerError):
    """Raised when a run is already active."""


class RunnerUnsupportedError(RunnerError):
    """Raised when a command cannot be executed by this platform."""


@dataclass(frozen=True)
class RunOptions:
    steps: Optional[int] = None
    gui: bool = False
    no_actuation: bool = False
    sumo_binary: str = "sumo"
    max_records: int = 5000
    strict: bool = False
    config: str = "configs/cits_config.json"
    tsp_config: str = "configs/tsp_config.json"
    policy_config: str = "configs/policy_optimization_config.json"
    policy_mode: str = "baseline"
    policy_report: Optional[str] = None


@dataclass
class PlatformRunner:
    root: Path
    state_path: Path = field(init=False)
    command_log_path: Path = field(init=False)
    process: Optional[subprocess.Popen] = field(default=None, init=False)
    current_state: Optional[Dict[str, Any]] = field(default=None, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        self.state_path = self.root / "outputs" / "platform_runtime_state.json"
        self.command_log_path = self.root / "outputs" / "platform_commands.jsonl"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    def start_run(self, kind: str, options: Optional[RunOptions] = None) -> Dict[str, Any]:
        options = options or RunOptions()
        with self._lock:
            self._refresh_locked()
            if self.process is not None and self.process.poll() is None:
                raise RunnerBusyError("A platform run is already active.")
            if kind not in RUN_KINDS:
                raise RunnerUnsupportedError(f"Unsupported run kind: {kind}")
            # Path-traversal guard: as opções `config/tsp_config/policy_config/
            # policy_report` são interpoladas em argumentos de subprocesso. Sem
            # validação, um POST com "../../etc/passwd" ou um absoluto fora do
            # projecto seria forwarded directamente. Apesar de a API correr em
            # loopback, isto é defense-in-depth.
            self._validate_project_path(options.config, "config")
            self._validate_project_path(options.tsp_config, "tsp_config")
            self._validate_project_path(options.policy_config, "policy_config")
            if options.policy_report is not None:
                self._validate_project_path(options.policy_report, "policy_report")

            run_id = str(uuid4())
            started_at = _now()
            stdout_log = self.root / "outputs" / f"platform_runner_{run_id}.out.log"
            stderr_log = self.root / "outputs" / f"platform_runner_{run_id}.err.log"
            command = self._command_for(kind, options)

            stdout_log.parent.mkdir(parents=True, exist_ok=True)
            stdout_handle = stdout_log.open("w", encoding="utf-8")
            stderr_handle = stderr_log.open("w", encoding="utf-8")
            try:
                process = subprocess.Popen(
                    command,
                    cwd=self.root,
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
                "kind": kind,
                "status": "running",
                "pid": process.pid,
                "command": command,
                "root": str(self.root),
                "started_at": started_at,
                "ended_at": None,
                "returncode": None,
                "stdout_log": str(stdout_log),
                "stderr_log": str(stderr_log),
                "message": "Run started.",
                "managed_by_current_process": True,
            }
            self._write_state_locked()
            self._append_command_locked("start", {"kind": kind, "options": options.__dict__})
            return dict(self.current_state)

    def stop_run(self) -> Dict[str, Any]:
        with self._lock:
            self._refresh_locked()
            if self.process is None or self.process.poll() is not None:
                state = self._idle_state("No active managed run.")
                self._write_state_payload(state)
                return state
            self._terminate_process_group(self.process)
            self._refresh_locked(force=True)
            if self.current_state is not None:
                self.current_state["message"] = "Run stopped by API command."
                self._write_state_locked()
            self._append_command_locked("stop", {})
            return dict(self.current_state) if self.current_state is not None else self._idle_state("Run stopped.")

    def pause_run(self) -> Dict[str, Any]:
        with self._lock:
            self._refresh_locked()
            if self.process is None or self.process.poll() is not None:
                raise RunnerError("No active managed run to pause.")
            if not hasattr(signal, "SIGSTOP"):
                raise RunnerUnsupportedError("Pause is not supported on this platform.")
            os.killpg(os.getpgid(self.process.pid), signal.SIGSTOP)
            assert self.current_state is not None
            self.current_state["status"] = "paused"
            self.current_state["message"] = "Run paused by API command."
            self._write_state_locked()
            self._append_command_locked("pause", {})
            return dict(self.current_state)

    def resume_run(self) -> Dict[str, Any]:
        with self._lock:
            if self.process is None or self.process.poll() is not None:
                raise RunnerError("No active managed run to resume.")
            if not hasattr(signal, "SIGCONT"):
                raise RunnerUnsupportedError("Resume is not supported on this platform.")
            os.killpg(os.getpgid(self.process.pid), signal.SIGCONT)
            assert self.current_state is not None
            self.current_state["status"] = "running"
            self.current_state["message"] = "Run resumed by API command."
            self._write_state_locked()
            self._append_command_locked("resume", {})
            return dict(self.current_state)

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
            return self._idle_state("No run has been started.")

    def snapshot(self, max_records: int = 5000) -> Dict[str, Any]:
        return collect_snapshot(self.root, max_records=max_records)

    def recent_events(self, artifact: str, limit: int = 50) -> Dict[str, Any]:
        config = load_platform_config(self.root)
        artifact_paths = {**config.get("artifacts", {})}
        if artifact not in JSONL_ARTIFACTS:
            raise RunnerUnsupportedError(f"Artifact is not JSONL: {artifact}")
        rel_path = artifact_paths.get(artifact)
        if rel_path is None:
            raise RunnerUnsupportedError(f"Unknown artifact: {artifact}")
        path = self.root / rel_path
        return {
            "artifact": artifact,
            "path": str(path),
            "events": _read_recent_jsonl(path, max(0, limit)),
        }

    def _validate_project_path(self, value: str, field_name: str) -> None:
        """Garante que `value` é um path relativo que, depois de resolvido
        contra `self.root`, ainda fica dentro de `self.root`. Bloqueia tanto
        absolutos como ``..`` que escapem do projecto."""
        if not value:
            raise RunnerUnsupportedError(f"{field_name} is empty.")
        candidate = Path(value)
        if candidate.is_absolute():
            raise RunnerUnsupportedError(
                f"{field_name}={value!r} must be a path relative to the project root."
            )
        resolved = (self.root / candidate).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise RunnerUnsupportedError(
                f"{field_name}={value!r} resolves outside the project root."
            ) from exc

    def _command_for(self, kind: str, options: RunOptions) -> list[str]:
        python = _project_python(self.root)
        if kind == "cits-sumo":
            cmd = _with_steps(
                [
                    python,
                    "scripts/run_cits_emulation.py",
                    "--config",
                    options.config,
                    "--mode",
                    "sumo",
                    "--sumo-binary",
                    options.sumo_binary,
                ],
                options.steps,
            )
            return cmd + (["--gui"] if options.gui else [])
        if kind in {"tsp-sumo", "tsp-sumo-no-actuation"}:
            cmd = _with_steps(
                [
                    python,
                    "scripts/run_tsp_control.py",
                    "--config",
                    options.config,
                    "--tsp-config",
                    options.tsp_config,
                    "--mode",
                    "sumo",
                    "--sumo-binary",
                    options.sumo_binary,
                ],
                options.steps,
            )
            if options.gui:
                cmd.append("--gui")
            if options.no_actuation or kind == "tsp-sumo-no-actuation":
                cmd.append("--no-actuation")
            return _with_policy(cmd, options)
        if kind == "optimize-offline":
            return _with_config_files([python, "scripts/run_policy_optimization.py"], options)
        if kind == "train-rl-policy":
            return _with_config_files([python, "scripts/run_rl_training.py"], options)
        if kind == "build-event-training-dataset":
            return [python, "scripts/build_event_training_dataset.py"]
        if kind == "compare-tsp-rl":
            cmd = _with_steps(_with_config_files([python, "scripts/compare_tsp_baseline_rl.py"], options), options.steps)
            cmd.extend(["--sumo-binary", options.sumo_binary])
            if options.no_actuation:
                cmd.append("--no-actuation")
            return cmd
        if kind == "evaluate-decision-outcomes":
            cmd = _with_steps(_with_config_files([python, "scripts/evaluate_decision_outcomes.py"], options), options.steps)
            cmd.extend(["--sumo-binary", options.sumo_binary])
            if options.no_actuation:
                cmd.append("--no-actuation")
            return cmd
        if kind == "kpis":
            return [
                python,
                "src/pps57_sumo/parse_tripinfo.py",
                "--tripinfo",
                "outputs/tripinfo.xml",
                "--out",
                "reports/baseline_kpis.json",
            ]
        if kind == "platform-check":
            cmd = [
                python,
                "scripts/check_platform_data.py",
                "--max-records",
                str(options.max_records),
            ]
            if options.strict:
                cmd.append("--strict")
            return cmd
        raise RunnerUnsupportedError(f"Unsupported run kind: {kind}")

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
        self.current_state["ended_at"] = _now()
        self.current_state["status"] = "completed" if returncode == 0 else "failed"
        self.current_state["message"] = (
            "Run completed successfully." if returncode == 0 else f"Run exited with code {returncode}."
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
            "kind": None,
            "status": "idle",
            "pid": None,
            "command": [],
            "root": str(self.root),
            "started_at": None,
            "ended_at": None,
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


def _with_steps(command: list[str], steps: Optional[int]) -> list[str]:
    if steps is not None:
        command.extend(["--steps", str(int(steps))])
    return command


def _with_config_files(command: list[str], options: RunOptions) -> list[str]:
    command.extend(["--config", options.config, "--tsp-config", options.tsp_config, "--policy-config", options.policy_config])
    return command


def _with_policy(command: list[str], options: RunOptions) -> list[str]:
    command.extend(["--policy-mode", options.policy_mode])
    if options.policy_report:
        command.extend(["--policy-report", options.policy_report])
    return command


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_recent_jsonl(path: Path, limit: int) -> list[Dict[str, Any]]:
    if limit <= 0 or not path.exists():
        return []
    rows: deque[Dict[str, Any]] = deque(maxlen=limit)
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
                rows.append(payload if isinstance(payload, dict) else {"value": payload})
            except json.JSONDecodeError as exc:
                rows.append({"__parse_error__": str(exc), "__line_number__": line_number, "raw": raw})
    return list(rows)
