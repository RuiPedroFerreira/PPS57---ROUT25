#!/usr/bin/env python3
"""Streamlit experiment cockpit for the PPS57 ROUT25 platform."""
from __future__ import annotations

import json
from html import escape
from pathlib import Path
import re
import sys
from typing import Any, Dict, Iterable, List, Mapping
from urllib import error, request

try:
    import pandas as pd
    import streamlit as st
    import yaml
except ImportError as exc:  # pragma: no cover - only used at runtime
    raise SystemExit(
        "Dashboard dependencies are missing. Install them with: python -m pip install -r requirements-dashboard.txt"
    ) from exc

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_platform.data_loader import collect_snapshot, latest_records  # noqa: E402


# TTL curto: cada widget rerun do Streamlit chamava `collect_snapshot`, que
# por sua vez re-parseia *todos* os JSONL. Com TTL de 5s a UI continua a
# parecer "live" mas os cliques rápidos partilham o mesmo snapshot. O botão
# "Recarregar" no sidebar chama `.clear()` para forçar releitura imediata
# (ex.: depois de uma corrida terminar).
@st.cache_data(ttl=5)
def cached_collect_snapshot(root_str: str, config_path: str, max_records: int) -> Dict[str, Any]:
    return collect_snapshot(Path(root_str), config_path, max_records=max_records)


RUN_JOBS = {
    "Baseline TSP": "tsp-sumo",
    "Baseline sem atuacao": "tsp-sumo-no-actuation",
    "C-ITS SUMO": "cits-sumo",
    "KPIs baseline": "kpis",
    "Dataset treino": "build-event-training-dataset",
    "Otimizar politica": "optimize-offline",
    "Treinar RL": "train-rl-policy",
    "Comparar TSP baseline vs RL": "compare-tsp-rl",
    "Avaliar outcomes": "evaluate-decision-outcomes",
    "Verificar plataforma": "platform-check",
}


def main() -> None:
    st.set_page_config(page_title="PPS57 ROUT25 Experiments", layout="wide")
    apply_theme()

    with st.sidebar:
        st.markdown("### PPS57 ROUT25")
        root_text = st.text_input("Raiz do repositório", value=str(ROOT))
        config_text = st.text_input("Configuração dashboard", value="configs/platform_config.json")
        api_url = st.text_input("API local", value="http://127.0.0.1:8000")
        max_records = st.number_input("Registos por log", min_value=100, max_value=100000, value=5000, step=100)
        st.divider()
        cits_config = st.text_input("C-ITS config", value="configs/cits_config.json")
        tsp_config = st.text_input("TSP config", value="configs/tsp_config.json")
        policy_config = st.text_input("RL/policy config", value="configs/policy_optimization_config.json")
        if st.button("Recarregar", use_container_width=True):
            # Limpa o cache do snapshot para forçar releitura dos JSONL.
            cached_collect_snapshot.clear()
            st.rerun()

    root = Path(root_text)
    snapshot = cached_collect_snapshot(str(root), config_text, int(max_records))
    api_state = api_get(api_url, "/runs/current")
    scenario_catalog = load_scenarios(root / "configs" / "scenarios.yaml")
    context = {
        "api_url": api_url,
        "root": root,
        "cits_config": cits_config,
        "tsp_config": tsp_config,
        "policy_config": policy_config,
        "scenario_catalog": scenario_catalog,
    }

    render_header(snapshot, api_state)
    render_data_warnings(snapshot)

    tabs = st.tabs(
        [
            "Cockpit",
            "Cenarios e runs",
            "Treino e inferencia",
            "Comparacao",
            "Safety e decisoes",
            "Artefactos",
        ]
    )
    with tabs[0]:
        render_cockpit(snapshot, api_state)
    with tabs[1]:
        render_scenario_runs(snapshot, api_state, context)
    with tabs[2]:
        render_training(snapshot, api_state, context)
    with tabs[3]:
        render_comparison(snapshot)
    with tabs[4]:
        render_safety(snapshot)
    with tabs[5]:
        render_artifacts(snapshot)


def apply_theme() -> None:
    st.markdown(
        """
        <style>
        :root {
          --pps-bg: #f8fafc;
          --pps-panel: #ffffff;
          --pps-border: #d7dde5;
          --pps-ink: #111827;
          --pps-muted: #5b6573;
          --pps-green: #0f766e;
          --pps-blue: #1d4ed8;
          --pps-amber: #a16207;
          --pps-red: #b91c1c;
          --pps-green-soft: #e7f5f1;
          --pps-blue-soft: #e8f0ff;
          --pps-amber-soft: #fff7df;
          --pps-red-soft: #feecec;
        }
        .block-container { padding-top: 1.1rem; padding-bottom: 2.2rem; max-width: 1500px; }
        h1, h2, h3, p { letter-spacing: 0; }
        div[data-testid="stMetric"] {
          background: var(--pps-panel);
          border: 1px solid var(--pps-border);
          border-radius: 8px;
          padding: 0.75rem 0.85rem;
          min-height: 92px;
        }
        div[data-testid="stMetricLabel"] p {
          color: var(--pps-muted);
          font-size: 0.78rem;
        }
        div[data-testid="stMetricValue"] {
          color: var(--pps-ink);
          font-size: 1.55rem;
        }
        .pps-shell {
          background: var(--pps-panel);
          border: 1px solid var(--pps-border);
          border-radius: 8px;
          padding: 1rem 1.1rem;
          margin-bottom: 1rem;
        }
        .pps-title {
          margin: 0;
          font-size: 1.5rem;
          font-weight: 750;
          color: var(--pps-ink);
        }
        .pps-subtitle {
          margin-top: 0.25rem;
          color: var(--pps-muted);
          font-size: 0.95rem;
        }
        .pps-strip {
          display: flex;
          flex-wrap: wrap;
          gap: 0.45rem;
          margin-top: 0.8rem;
        }
        .pps-pill {
          display: inline-flex;
          align-items: center;
          min-height: 28px;
          padding: 0.18rem 0.55rem;
          border: 1px solid var(--pps-border);
          border-radius: 999px;
          background: #f9fafb;
          color: #374151;
          font-size: 0.78rem;
          font-weight: 650;
          white-space: nowrap;
        }
        .pps-pill.ok { color: var(--pps-green); background: var(--pps-green-soft); border-color: #9bd8cc; }
        .pps-pill.warn { color: var(--pps-amber); background: var(--pps-amber-soft); border-color: #f4c45f; }
        .pps-pill.bad { color: var(--pps-red); background: var(--pps-red-soft); border-color: #f5a3a3; }
        .pps-pill.info { color: var(--pps-blue); background: var(--pps-blue-soft); border-color: #a9c4ff; }
        .pps-section {
          margin: 1rem 0 0.55rem;
          font-size: 1rem;
          font-weight: 750;
          color: #1f2937;
        }
        .pps-note { color: var(--pps-muted); font-size: 0.9rem; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header(snapshot: Mapping[str, Any], api_state: Mapping[str, Any]) -> None:
    config = snapshot["config"]
    experiments = snapshot["aggregates"].get("experiments", {})
    current_run = experiments.get("current_run", {})
    missing = snapshot.get("missing_critical_artifacts", [])
    api_status = "offline" if "__error__" in api_state else str(api_state.get("status", "online"))
    api_class = "bad" if api_status == "offline" else ("warn" if api_status in {"running", "paused"} else "ok")
    missing_class = "bad" if missing else "ok"
    policy_loaded = "policy loaded" if current_run.get("runtime_policy_loaded") else "baseline/fallback"
    html = f"""
    <div class="pps-shell">
      <div class="pps-title">{escape(str(config.get("title", "PPS57 ROUT25 Experiment Cockpit")))}</div>
      <div class="pps-subtitle">
        Cockpit local para configurar cenários SUMO/TraCI, treinar/inferir políticas e comparar métricas contra baseline.
      </div>
      <div class="pps-strip">
        <span class="pps-pill info">scenario: {escape(str(current_run.get("scenario_id", config.get("scenario_id", "n/a"))))}</span>
        <span class="pps-pill {api_class}">API: {escape(api_status)}</span>
        <span class="pps-pill {missing_class}">artefactos críticos: {len(missing)}</span>
        <span class="pps-pill">policy: {escape(policy_loaded)}</span>
        <span class="pps-pill">root: {escape(str(snapshot.get("root", "")))}</span>
      </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def render_data_warnings(snapshot: Mapping[str, Any]) -> None:
    if snapshot.get("config_error"):
        st.error(f"Configuração da plataforma inválida: {snapshot['config_error']}")
    missing = snapshot.get("missing_critical_artifacts", [])
    if missing:
        st.warning("Artefactos críticos em falta: " + ", ".join(missing))


def render_cockpit(snapshot: Mapping[str, Any], api_state: Mapping[str, Any]) -> None:
    aggregates = snapshot["aggregates"]
    overview = aggregates["overview"]
    experiments = aggregates.get("experiments", {})
    current_run = experiments.get("current_run", {})
    policy = experiments.get("policy", {})
    outcomes = experiments.get("decision_outcomes", {})

    render_section("Estado experimental")
    render_metrics(
        [
            ("Modo atual", current_run.get("policy_mode", "baseline")),
            ("Decisões TSP", overview.get("total_tsp_decisions", 0)),
            ("Bloqueios Safety", overview.get("blocked_by_safety", 0)),
            ("Atuações aplicadas", overview.get("applied_actuation_events", 0)),
            ("Reward delta", policy.get("optimized_reward_delta", 0)),
            ("Verdict rede", outcomes.get("network_impact_verdict", "n/a")),
        ],
        columns=6,
    )

    render_section("Matriz de simulações")
    render_table(build_run_matrix(snapshot, api_state), height=260)

    left, right = st.columns([1.1, 0.9])
    with left:
        render_section("Comparação rápida TSP")
        rows = experiments.get("tsp_rows", [])
        render_table(rows[:12], height=300)
    with right:
        render_section("Verdicts de outcomes")
        verdict_counts = outcomes.get("verdict_counts", {})
        render_bar(st, "Distribuição de verdicts", verdict_counts)


def render_scenario_runs(
    snapshot: Mapping[str, Any],
    api_state: Mapping[str, Any],
    context: Mapping[str, Any],
) -> None:
    render_section("Scenario Builder")
    scenarios = context["scenario_catalog"]
    scenario_names = list(scenarios) or ["custom"]
    left, right = st.columns([0.8, 1.2])
    with left:
        selected = st.selectbox("Preset de cenário", scenario_names)
        scenario = scenarios.get(selected, {})
        if scenario:
            st.markdown(f'<div class="pps-note">{escape(str(scenario.get("description", "")))}</div>', unsafe_allow_html=True)
            render_table([{"campo": key, "valor": value} for key, value in scenario.items()], height=180)
        else:
            st.info("Sem catálogo de cenários. Edita `configs/scenarios.yaml` para adicionar presets.")
    with right:
        render_run_form(
            api_state,
            context,
            form_key="runtime_run_form",
            job_options=[
                "Baseline TSP",
                "Baseline sem atuacao",
                "C-ITS SUMO",
                "KPIs baseline",
                "Comparar TSP baseline vs RL",
                "Avaliar outcomes",
                "Verificar plataforma",
            ],
        )

    render_section("Estado do processo")
    render_process_state(api_state, context)


def render_training(
    snapshot: Mapping[str, Any],
    api_state: Mapping[str, Any],
    context: Mapping[str, Any],
) -> None:
    experiments = snapshot["aggregates"].get("experiments", {})
    policy = experiments.get("policy", {})
    reports = snapshot.get("reports", {})

    render_section("Pipeline treino -> política -> inferência")
    render_metrics(
        [
            ("Candidatos", policy.get("optimized_candidate_count", 0)),
            ("Unsafe filtrados", policy.get("unsafe_candidates_filtered", 0)),
            ("Policy ID", policy.get("policy_id", "n/a")),
            ("Algoritmo", policy.get("algorithm", "n/a")),
            ("Regras exportadas", policy.get("rule_count", 0)),
        ],
        columns=5,
    )

    left, right = st.columns([1.05, 0.95])
    with left:
        render_run_form(
            api_state,
            context,
            form_key="training_run_form",
            job_options=["Dataset treino", "Otimizar politica", "Treinar RL", "Comparar TSP baseline vs RL"],
        )
    with right:
        render_section("Relatórios de política")
        selected_report = st.selectbox(
            "Relatório",
            ["rl_training_summary", "tabular_q_policy_report", "policy_report", "optimization_summary"],
        )
        st.json(reports.get(selected_report, {}))


def render_comparison(snapshot: Mapping[str, Any]) -> None:
    experiments = snapshot["aggregates"].get("experiments", {})
    render_section("SUMO KPIs: baseline vs RL")
    kpi_rows = experiments.get("kpi_rows", [])
    if kpi_rows:
        render_delta_table(kpi_rows)
    else:
        st.info("Ainda não existe `reports/sumo_baseline_vs_rl_kpi_comparison.json`.")

    left, right = st.columns(2)
    with left:
        render_section("Baseline KPIs")
        render_kpi_cards(experiments.get("baseline_kpis", {}))
    with right:
        render_section("RL KPIs")
        render_kpi_cards(experiments.get("rl_kpis", {}))

    render_section("TSP runtime: baseline vs RL")
    render_delta_table(experiments.get("tsp_rows", []))

    outcomes = experiments.get("decision_outcomes", {})
    render_section("Avaliação conservadora de outcomes")
    render_metrics(
        [
            ("Decisões avaliadas", outcomes.get("decision_count", 0)),
            ("Decisões emparelhadas", outcomes.get("matched_decision_count", 0)),
            ("Impacto rede", outcomes.get("network_impact_verdict", "n/a")),
        ],
        columns=3,
    )
    render_table(latest_records(list(outcomes.get("rows", [])), 150), height=420)


def render_safety(snapshot: Mapping[str, Any]) -> None:
    aggregates = snapshot["aggregates"]
    records = snapshot["records"]
    left, right = st.columns(2)
    with left:
        render_section("Decisões por estado")
        render_bar(st, "Status", aggregates["tsp"].get("by_status", {}))
    with right:
        render_section("Decisões por ação")
        render_bar(st, "Ação", aggregates["tsp"].get("by_action", {}))

    left, right = st.columns(2)
    with left:
        render_section("Atuação TraCI")
        render_bar(st, "Aplicada", aggregates["actuation"].get("by_applied", {}))
    with right:
        render_section("Candidatos por Safety status")
        render_bar(st, "Safety", aggregates["optimization"].get("candidates_by_safety_status", {}))

    render_section("Últimas decisões TSP")
    render_table(latest_records(records.get("tsp_decisions", []), 100), height=360)
    render_section("Últimas atuações")
    render_table(latest_records(records.get("tsp_actuation", []), 100), height=320)


def render_artifacts(snapshot: Mapping[str, Any]) -> None:
    render_section("Disponibilidade dos artefactos")
    artifacts = snapshot.get("artifacts", [])
    render_table(artifacts, height=460)
    with st.expander("Snapshot bruto"):
        st.json(snapshot)


def render_run_form(
    api_state: Mapping[str, Any],
    context: Mapping[str, Any],
    *,
    form_key: str,
    job_options: List[str],
) -> None:
    api_offline = "__error__" in api_state
    if api_offline:
        st.warning("API local indisponível. Arranca com `make platform-api` para executar jobs.")
        st.code(str(api_state.get("__error__", "")))
    with st.form(form_key):
        job_label = st.selectbox("Job", job_options)
        steps = st.number_input("Steps SUMO/TraCI", min_value=1, max_value=100000, value=7200, step=300)
        policy_mode = st.selectbox("Policy mode", ["baseline", "optimized", "rl"])
        policy_report = st.text_input("Policy report", value="")
        sumo_binary = st.text_input("SUMO binary", value="sumo")
        flags = st.columns(3)
        gui = flags[0].checkbox("SUMO GUI", value=False)
        no_actuation = flags[1].checkbox("Sem atuação", value=job_label == "Baseline sem atuacao")
        strict = flags[2].checkbox("Strict check", value=False)
        submitted = st.form_submit_button("Executar job", use_container_width=True, disabled=api_offline)
    if submitted:
        kind = RUN_JOBS[job_label]
        payload: Dict[str, Any] = {
            "kind": kind,
            "steps": int(steps),
            "gui": bool(gui),
            "no_actuation": bool(no_actuation),
            "sumo_binary": sumo_binary,
            "strict": bool(strict),
            "config": context["cits_config"],
            "tsp_config": context["tsp_config"],
            "policy_config": context["policy_config"],
            "policy_mode": policy_mode,
        }
        if policy_report:
            payload["policy_report"] = policy_report
        result = api_post(context["api_url"], "/runs/start", payload)
        render_api_result(result)


def render_process_state(api_state: Mapping[str, Any], context: Mapping[str, Any]) -> None:
    if "__error__" in api_state:
        st.info("Sem estado de processo porque a API está offline.")
        return
    render_metrics(
        [
            ("Estado", api_state.get("status", "unknown")),
            ("Job", api_state.get("kind") or "n/a"),
            ("PID", api_state.get("pid") or "n/a"),
            ("Return code", api_state.get("returncode") if api_state.get("returncode") is not None else "n/a"),
        ],
        columns=4,
    )
    col_a, col_b, col_c = st.columns(3)
    if col_a.button("Pausar", use_container_width=True):
        render_api_result(api_post(str(context["api_url"]), "/runs/pause", {}))
    if col_b.button("Continuar", use_container_width=True):
        render_api_result(api_post(str(context["api_url"]), "/runs/resume", {}))
    if col_c.button("Parar", use_container_width=True):
        render_api_result(api_post(str(context["api_url"]), "/runs/stop", {}))
    with st.expander("Estado bruto do runner"):
        st.json(api_state)


def render_kpi_cards(kpis: Mapping[str, Any]) -> None:
    rows: List[Dict[str, Any]] = []
    for group in ["all_vehicles", "buses", "general_traffic"]:
        payload = kpis.get(group, {})
        if not isinstance(payload, dict):
            continue
        row = {"group": group}
        row.update(payload)
        rows.append(row)
    legacy = kpis.get("legacy", {})
    if not rows and isinstance(legacy, dict):
        rows.append({"group": "legacy", **legacy})
    render_table(rows, height=220)


def render_delta_table(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        st.info("Sem comparação disponível.")
        return
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, height=360, hide_index=True)


def build_run_matrix(snapshot: Mapping[str, Any], api_state: Mapping[str, Any]) -> List[Dict[str, Any]]:
    reports = snapshot.get("reports", {})
    aggregates = snapshot.get("aggregates", {})
    experiments = aggregates.get("experiments", {})
    overview = aggregates.get("overview", {})
    outcomes = experiments.get("decision_outcomes", {})
    return [
        {
            "lane": "baseline",
            "source": "reports/tsp_emulation_summary.json + reports/baseline_kpis.json",
            "status": "available" if reports.get("tsp_summary") or reports.get("baseline_kpis") else "missing",
            "policy_mode": reports.get("tsp_summary", {}).get("policy_mode", "baseline"),
            "decisions": overview.get("total_tsp_decisions", 0),
            "blocked": overview.get("blocked_by_safety", 0),
            "network_verdict": "reference",
        },
        {
            "lane": "optimized",
            "source": "reports/policy_report.json",
            "status": "available" if reports.get("policy_report") else "missing",
            "policy_mode": "optimized",
            "decisions": "",
            "blocked": experiments.get("policy", {}).get("unsafe_candidates_filtered", 0),
            "network_verdict": f"reward_delta={experiments.get('policy', {}).get('optimized_reward_delta', 0)}",
        },
        {
            "lane": "rl",
            "source": "reports/tabular_q_policy_report.json",
            "status": "available" if reports.get("tabular_q_policy_report") else "missing",
            "policy_mode": "rl",
            "decisions": "",
            "blocked": "",
            "network_verdict": outcomes.get("network_impact_verdict", "n/a"),
        },
        {
            "lane": "runner",
            "source": "FastAPI local",
            "status": "offline" if "__error__" in api_state else api_state.get("status", "idle"),
            "policy_mode": api_state.get("kind", "n/a") if "__error__" not in api_state else "n/a",
            "decisions": "",
            "blocked": "",
            "network_verdict": api_state.get("message", "") if "__error__" not in api_state else api_state.get("__error__", ""),
        },
    ]


def render_bar(container: Any, title: str, counts: Mapping[str, Any]) -> None:
    container.markdown(f'<div class="pps-section">{escape(title)}</div>', unsafe_allow_html=True)
    if not counts:
        container.info("Sem dados disponíveis.")
        return
    df = pd.DataFrame([{"categoria": str(key), "valor": value} for key, value in counts.items()]).set_index("categoria")
    container.bar_chart(df)


def render_metrics(items: Iterable[tuple[str, Any]], columns: int = 4) -> None:
    cols = st.columns(columns)
    for index, (label, value) in enumerate(items):
        cols[index % columns].metric(label, format_metric_value(value))


def render_section(title: str) -> None:
    st.markdown(f'<div class="pps-section">{escape(title)}</div>', unsafe_allow_html=True)


def render_table(rows: Any, *, height: int = 300) -> None:
    df = pd.DataFrame(rows)
    if df.empty:
        st.info("Sem dados disponíveis.")
        return
    st.dataframe(df, use_container_width=True, height=height, hide_index=True)


def render_api_result(payload: Mapping[str, Any]) -> None:
    if "__error__" in payload:
        st.error(str(payload["__error__"]))
    else:
        st.success(str(payload.get("message", "Comando aceite.")))
        st.json(dict(payload))


def format_metric_value(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    if isinstance(value, bool):
        return "sim" if value else "não"
    return str(value)


def load_scenarios(path: Path) -> Dict[str, Dict[str, str]]:
    """Carrega `configs/scenarios.yaml` via `yaml.safe_load`.

    O parser ad-hoc anterior (regex sobre linhas com indentação 2/4 exacta)
    silenciosamente descartava valores com `:` no meio (URLs, descrições com
    "X: Y", etc.) e qualquer cenário com indentação diferente. `safe_load`
    cobre todo o standard YAML sem permitir tags arbitrárias.
    """
    if not path.exists():
        return {}
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    raw = payload.get("scenarios") if isinstance(payload, dict) else None
    if not isinstance(raw, dict):
        return {}
    scenarios: Dict[str, Dict[str, str]] = {}
    for name, fields in raw.items():
        if not isinstance(fields, dict):
            continue
        # Normaliza tudo a string para a UI (mantém compatibilidade com o
        # parser regex anterior, que devolvia sempre strings).
        scenarios[str(name)] = {str(k): _yaml_scalar_to_str(v) for k, v in fields.items()}
    return scenarios


def _yaml_scalar_to_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def api_get(base_url: str, path: str) -> Dict[str, Any]:
    payload = api_request(base_url, path, method="GET")
    if "__error__" not in payload:
        payload["api_url"] = base_url
    return payload


def api_post(base_url: str, path: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    return api_request(base_url, path, method="POST", payload=payload)


def api_request(base_url: str, path: str, *, method: str, payload: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    url = base_url.rstrip("/") + path
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=2) as response:
            body = response.read().decode("utf-8")
            parsed = json.loads(body) if body else {}
            return parsed if isinstance(parsed, dict) else {"value": parsed}
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8")
        return {"__error__": f"HTTP {exc.code}: {detail}"}
    except Exception as exc:
        return {"__error__": str(exc)}


if __name__ == "__main__":
    main()
