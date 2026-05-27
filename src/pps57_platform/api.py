#!/usr/bin/env python3
"""FastAPI app for the scenario dashboard."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pps57_sumo.scenarios import apply_scenario_profile, load_catalog, scenario_summary, validate_scenario_catalog

from .dashboard import STATIC_DIR, dashboard_html
from .runner import (
    RUN_TYPES,
    RunnerBusyError,
    RunnerError,
    RunnerUnsupportedError,
    ScenarioRunOptions,
    ScenarioRunner,
)


ROOT = Path(__file__).resolve().parents[2]
ALGORITHM_RUNS = ("baseline", "tsp_no_actuation", "tsp_actuation")
SCENARIO_DISPLAY_NAMES = {
    "baseline_am_peak": "Ponta da manha",
    "baseline_off_peak": "Fora de ponta",
    "baseline_pm_peak": "Ponta da tarde",
    "high_demand_corridor": "Procura elevada no corredor",
    "cross_traffic_pressure": "Pressao nas transversais",
    "delayed_bus_westbound": "Autocarro atrasado para o mar",
    "delayed_bus_eastbound": "Autocarro atrasado para a cidade",
    "bunched_buses": "Autocarros agrupados",
    "long_dwell_stop": "Paragem com dwell elevado",
    "incident_minor_road_queue": "Fila anomala em via secundaria",
    "emergency_vehicle_conflict": "Conflito com emergencia",
    "baseline_am_peak_low": "Ponta da manha — envelope inferior",
    "baseline_am_peak_high": "Ponta da manha — envelope superior",
    "baseline_pm_peak_low": "Ponta da tarde — envelope inferior",
    "baseline_pm_peak_high": "Ponta da tarde — envelope superior",
    "congested_am_peak": "Ponta da manha em saturacao",
    "baseline_rainy_am_peak": "Ponta da manha com chuva",
    "baseline_foggy_am_peak": "Ponta da manha com nevoeiro",
    "baseline_winter_morning_am_peak": "Manha de inverno com piso frio",
    "av_penetration_low": "Penetracao baixa de AVs (~10%)",
    "av_penetration_medium": "Penetracao media de AVs (~30%)",
    "av_penetration_high": "Penetracao alta de AVs (~60%)",
    "stochastic_incidents_am_peak": "Ponta da manha com incidentes estocasticos",
}


class ScenarioRunRequest(BaseModel):
    scenario_id: Optional[str] = "baseline_am_peak"
    run_type: Literal["baseline", "cits", "tsp_no_actuation", "tsp_actuation", "comparison", "all"] = "comparison"
    steps: Optional[int] = Field(default=None, ge=1)
    gui: bool = False
    generate_only: bool = False

    def to_options(self) -> ScenarioRunOptions:
        return ScenarioRunOptions(
            scenario_id=self.scenario_id,
            run_type=self.run_type,
            steps=self.steps,
            gui=self.gui,
            generate_only=self.generate_only,
        )


def create_app(root: Path = ROOT) -> FastAPI:
    root = Path(root).resolve()
    runner = ScenarioRunner(root)
    app = FastAPI(
        title="PPS57 Scenario Dashboard",
        version="1.0.0",
        description="Local scenario execution and KPI comparison dashboard.",
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(dashboard_html())

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard() -> HTMLResponse:
        return HTMLResponse(dashboard_html())

    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        state = runner.get_state()
        return {"status": "ok", "runner_status": state["status"], "root": str(root)}

    @app.get("/api/scenarios")
    def scenarios() -> Dict[str, Any]:
        return load_scenarios(root)

    @app.get("/api/runs/current")
    def current_run() -> Dict[str, Any]:
        return runner.get_state()

    @app.get("/api/runs/current/logs")
    def current_run_logs() -> Dict[str, Any]:
        state = runner.get_state()
        return {
            "run_id": state.get("run_id"),
            "stdout": _tail_text(root, state.get("stdout_log")),
            "stderr": _tail_text(root, state.get("stderr_log")),
        }

    @app.post("/api/runs/start", status_code=202)
    def start_run(request: ScenarioRunRequest) -> Dict[str, Any]:
        try:
            return runner.start_run(request.to_options())
        except RunnerBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except RunnerUnsupportedError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RunnerError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/runs/stop")
    def stop_run() -> Dict[str, Any]:
        return runner.stop_run()

    @app.get("/api/reports")
    def reports() -> Dict[str, Any]:
        scenario_payload = load_scenarios(root)
        scenario_ids = [item["scenario_id"] for item in scenario_payload["scenarios"]]
        return {
            "suite": _read_json(root / "reports" / "scenarios" / "scenario_suite_summary.json"),
            "reports": [load_scenario_report(root, scenario_id) for scenario_id in scenario_ids],
        }

    @app.get("/api/reports/{scenario_id}")
    def report(scenario_id: str) -> Dict[str, Any]:
        known = {item["scenario_id"] for item in load_scenarios(root)["scenarios"]}
        if scenario_id not in known:
            raise HTTPException(status_code=404, detail=f"Unknown scenario_id: {scenario_id}")
        return load_scenario_report(root, scenario_id)

    return app


def load_scenarios(root: Path) -> Dict[str, Any]:
    base_config = json.loads((root / "configs" / "sumo_scenario_base.json").read_text(encoding="utf-8"))
    catalog = load_catalog(root / "configs" / "scenario_catalog.yaml")
    summaries = {item["scenario_id"]: item for item in validate_scenario_catalog(base_config, catalog)}
    scenarios = []
    for scenario_id, entry in catalog.get("scenarios", {}).items():
        config = apply_scenario_profile(base_config, scenario_id)
        summary = summaries.get(scenario_id, scenario_summary(config))
        scenarios.append(
            {
                **summary,
                "display_name": entry.get("display_name") or SCENARIO_DISPLAY_NAMES.get(scenario_id, scenario_id),
                "description": entry.get("description", ""),
                "realism_basis": entry.get("realism_basis", ""),
                "kpi_focus": entry.get("kpi_focus", summary.get("kpi_focus", [])),
                "tags": entry.get("tags", []),
                "has_report": (root / "reports" / "scenarios" / scenario_id / "scenario_summary.json").exists(),
            }
        )
    return {
        "scenario_set": catalog.get("scenario_set"),
        "run_types": sorted(RUN_TYPES),
        "comparison_run_types": list(ALGORITHM_RUNS),
        "scenarios": scenarios,
    }


def load_scenario_report(root: Path, scenario_id: str) -> Dict[str, Any]:
    path = root / "reports" / "scenarios" / scenario_id / "scenario_summary.json"
    summary = _read_json(path)
    if not summary:
        return {
            "scenario_id": scenario_id,
            "exists": False,
            "path": str(path.relative_to(root)),
            "verdict": {"status": "not_run", "reasons": []},
            "runs": {},
            "comparison": build_kpi_comparison(root, {}),
        }
    return {
        "scenario_id": scenario_id,
        "exists": True,
        "path": str(path.relative_to(root)),
        "mtime": path.stat().st_mtime,
        "verdict": summary.get("verdict", {"status": "unknown", "reasons": []}),
        "runs": summary.get("runs", {}),
        "comparisons": summary.get("comparisons", {}),
        "comparison": build_kpi_comparison(root, summary.get("runs", {})),
    }


def build_kpi_comparison(root: Path, runs: Dict[str, Any]) -> Dict[str, Any]:
    kpis = {run_type: _load_run_kpis(root, runs.get(run_type, {})) for run_type in ALGORITHM_RUNS}
    rows = [
        _comparison_row("all_vehicles.vehicles", "Veículos concluídos", "n", kpis, lower_is_better=False),
        _comparison_row("buses.vehicles", "Autocarros concluídos", "n", kpis, lower_is_better=False),
        _comparison_row("buses.mean_time_loss_s", "Perda média dos autocarros", "s", kpis, lower_is_better=True),
        _comparison_row("buses.p95_time_loss_s", "P95 perda dos autocarros", "s", kpis, lower_is_better=True),
        _comparison_row("buses.mean_waiting_time_s", "Espera média dos autocarros", "s", kpis, lower_is_better=True),
        _comparison_row("general_traffic.mean_time_loss_s", "Perda média tráfego geral", "s", kpis, lower_is_better=True),
        _comparison_row("general_traffic.mean_waiting_time_s", "Espera média tráfego geral", "s", kpis, lower_is_better=True),
        _comparison_row(
            "detectors.network_queue.max_queue_vehicles",
            "Fila máxima na rede",
            "veh",
            kpis,
            lower_is_better=True,
        ),
        _comparison_row(
            "detectors.network_queue.mean_queue_vehicles",
            "Fila média na rede",
            "veh",
            kpis,
            lower_is_better=True,
        ),
    ]
    return {
        "labels": {
            "baseline": "Baseline",
            "tsp_no_actuation": "Shadow mode",
            "tsp_actuation": "TSP ativo",
        },
        "rows": rows,
    }


def _comparison_row(label_path: str, label: str, unit: str, kpis: Dict[str, Dict[str, Any]], *, lower_is_better: bool) -> Dict[str, Any]:
    baseline = _nested(kpis["baseline"], label_path)
    without_algorithm = _nested(kpis["tsp_no_actuation"], label_path)
    with_algorithm = _nested(kpis["tsp_actuation"], label_path)
    return {
        "metric": label,
        "source": label_path,
        "unit": unit,
        "lower_is_better": lower_is_better,
        "baseline": baseline,
        "without_algorithm": without_algorithm,
        "with_algorithm": with_algorithm,
        "delta_without_vs_baseline": _delta(without_algorithm, baseline),
        "delta_with_vs_baseline": _delta(with_algorithm, baseline),
        "delta_with_vs_without": _delta(with_algorithm, without_algorithm),
    }


def _load_run_kpis(root: Path, run: Dict[str, Any]) -> Dict[str, Any]:
    rel_path = run.get("kpis")
    if not rel_path:
        return {}
    return _read_json(root / rel_path)


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _tail_text(root: Path, raw_path: Any, max_chars: int = 6000) -> Dict[str, Any]:
    if not raw_path:
        return {"path": None, "exists": False, "content": ""}
    try:
        path = Path(str(raw_path))
        if not path.is_absolute():
            path = root / path
        path = path.resolve()
        path.relative_to(root)
    except (OSError, ValueError):
        return {"path": str(raw_path), "exists": False, "content": "", "error": "invalid_path"}
    if not path.exists() or not path.is_file():
        return {"path": str(path), "exists": False, "content": ""}
    content = path.read_text(encoding="utf-8", errors="replace")
    truncated = len(content) > max_chars
    return {
        "path": str(path),
        "exists": True,
        "content": content[-max_chars:] if truncated else content,
        "truncated": truncated,
    }


def _nested(payload: Dict[str, Any], dotted_path: str) -> Any:
    current: Any = payload
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _delta(candidate: Any, reference: Any) -> Optional[float]:
    if not isinstance(candidate, (int, float)) or not isinstance(reference, (int, float)):
        return None
    return round(float(candidate) - float(reference), 3)


app = create_app()
