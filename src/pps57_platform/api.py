#!/usr/bin/env python3
"""FastAPI control plane for the PPS57 local platform."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from .runner import (
    RUN_KINDS,
    PlatformRunner,
    RunOptions,
    RunnerBusyError,
    RunnerError,
    RunnerUnsupportedError,
)


ROOT = Path(__file__).resolve().parents[2]


class RunStartRequest(BaseModel):
    kind: str = Field(..., description=f"One of: {', '.join(sorted(RUN_KINDS))}")
    steps: Optional[int] = Field(default=None, ge=1)
    gui: bool = False
    no_actuation: bool = False
    sumo_binary: str = "sumo"
    max_records: int = Field(default=5000, ge=1)
    strict: bool = False
    config: str = "configs/cits_config.json"
    tsp_config: str = "configs/tsp_config.json"
    policy_config: str = "configs/policy_optimization_config.json"
    policy_mode: str = Field(default="baseline", description="baseline, optimized or rl")
    policy_report: Optional[str] = None

    def to_options(self) -> RunOptions:
        return RunOptions(
            steps=self.steps,
            gui=self.gui,
            no_actuation=self.no_actuation,
            sumo_binary=self.sumo_binary,
            max_records=self.max_records,
            strict=self.strict,
            config=self.config,
            tsp_config=self.tsp_config,
            policy_config=self.policy_config,
            policy_mode=self.policy_mode,
            policy_report=self.policy_report,
        )


def create_app(root: Path = ROOT) -> FastAPI:
    runner = PlatformRunner(root)
    app = FastAPI(
        title="PPS57 ROUT25 Platform API",
        version="0.1.0",
        description="Local control API for PPS57 simulations and artifacts.",
    )

    @app.get("/")
    def index() -> Dict[str, Any]:
        return {
            "service": "pps57-platform-api",
            "status": "ok",
            "run_kinds": sorted(RUN_KINDS),
        }

    @app.get("/health")
    def health() -> Dict[str, Any]:
        state = runner.get_state()
        return {"status": "ok", "runner_status": state["status"], "root": str(root)}

    @app.get("/runs/current")
    def current_run() -> Dict[str, Any]:
        return runner.get_state()

    @app.post("/runs/start", status_code=202)
    def start_run(request: RunStartRequest) -> Dict[str, Any]:
        try:
            return runner.start_run(request.kind, request.to_options())
        except RunnerBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RunnerUnsupportedError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RunnerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/runs/stop")
    def stop_run() -> Dict[str, Any]:
        return runner.stop_run()

    @app.post("/runs/pause")
    def pause_run() -> Dict[str, Any]:
        try:
            return runner.pause_run()
        except RunnerUnsupportedError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except RunnerError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/runs/resume")
    def resume_run() -> Dict[str, Any]:
        try:
            return runner.resume_run()
        except RunnerUnsupportedError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        except RunnerError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/artifacts/snapshot")
    def artifacts_snapshot(max_records: int = Query(default=5000, ge=1)) -> Dict[str, Any]:
        return runner.snapshot(max_records=max_records)

    @app.get("/events/recent")
    def recent_events(
        artifact: str = Query(default="tsp_decisions"),
        limit: int = Query(default=50, ge=1, le=1000),
    ) -> Dict[str, Any]:
        try:
            return runner.recent_events(artifact, limit=limit)
        except RunnerUnsupportedError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


app = create_app()
