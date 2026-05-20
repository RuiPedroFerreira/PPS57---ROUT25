#!/usr/bin/env python3
"""Streamlit dashboard for the PPS57 ROUT25 platform."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, List, Mapping
from urllib import error, request
from html import escape

try:
    import pandas as pd
    import streamlit as st
except ImportError as exc:  # pragma: no cover - only used at runtime
    raise SystemExit(
        "Dashboard dependencies are missing. Install them with: python -m pip install -r requirements.txt"
    ) from exc

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_platform.data_loader import collect_snapshot, latest_records  # noqa: E402


def main() -> None:
    st.set_page_config(page_title="PPS57 — ROUT25 Platform", page_icon="🚦", layout="wide")
    apply_theme()

    with st.sidebar:
        st.markdown("### PPS57 ROUT25")
        root_text = st.text_input("Raiz do repositório", value=str(ROOT))
        config_text = st.text_input("Configuração", value="configs/platform_config.json")
        api_url = st.text_input("API local", value="http://127.0.0.1:8000")
        max_records = st.number_input("Máximo de registos por log", min_value=100, max_value=100000, value=5000, step=100)
        refresh = st.button("Recarregar", use_container_width=True)
        if refresh:
            st.rerun()

    snapshot = collect_snapshot(Path(root_text), config_text, max_records=int(max_records))
    config = snapshot["config"]
    aggregates = snapshot["aggregates"]
    records = snapshot["records"]
    reports = snapshot["reports"]
    api_state = api_get(api_url, "/runs/current")

    render_header(config, snapshot, api_state)

    config_error = snapshot.get("config_error")
    if config_error:
        st.error(f"Configuração da plataforma inválida (a usar defaults): {config_error}")

    missing = snapshot.get("missing_critical_artifacts", [])
    if missing:
        st.warning("Artefactos críticos em falta: " + ", ".join(missing))

    tabs = st.tabs([
        "Overview",
        "C-ITS",
        "TSP & Safety",
        "Atuação",
        "Otimização",
        "KPIs",
        "Controlo",
        "Artefactos",
    ])

    with tabs[0]:
        render_overview(config, aggregates, reports)
    with tabs[1]:
        render_cits(aggregates, records)
    with tabs[2]:
        render_tsp(aggregates, records)
    with tabs[3]:
        render_actuation(aggregates, records)
    with tabs[4]:
        render_optimization(aggregates, records, reports)
    with tabs[5]:
        render_kpis(aggregates, reports, snapshot.get("tripinfo", {}))
    with tabs[6]:
        render_control(api_url)
    with tabs[7]:
        render_artifacts(snapshot)


def apply_theme() -> None:
    st.markdown(
        """
        <style>
        :root {
          --pps-bg: #f7f8fa;
          --pps-panel: #ffffff;
          --pps-panel-border: #dfe3e8;
          --pps-text-muted: #5d6673;
          --pps-accent: #0f766e;
          --pps-accent-weak: #e6f4f1;
          --pps-warn: #9a3412;
          --pps-warn-weak: #fff3e8;
          --pps-bad: #b91c1c;
          --pps-bad-weak: #feecec;
        }
        .block-container { padding-top: 1.4rem; padding-bottom: 2.2rem; }
        h1, h2, h3 { letter-spacing: 0; }
        div[data-testid="stMetric"] {
          background: var(--pps-panel);
          border: 1px solid var(--pps-panel-border);
          border-radius: 8px;
          padding: 0.85rem 0.95rem;
          min-height: 94px;
        }
        div[data-testid="stMetricLabel"] p {
          color: var(--pps-text-muted);
          font-size: 0.78rem;
        }
        div[data-testid="stMetricValue"] {
          color: #17202a;
          font-size: 1.75rem;
        }
        .pps-header {
          background: var(--pps-panel);
          border: 1px solid var(--pps-panel-border);
          border-radius: 8px;
          padding: 1rem 1.15rem;
          margin-bottom: 1rem;
        }
        .pps-title {
          margin: 0;
          font-size: 1.65rem;
          font-weight: 700;
          color: #111827;
        }
        .pps-subtitle {
          margin-top: 0.25rem;
          color: var(--pps-text-muted);
          font-size: 0.95rem;
        }
        .pps-strip {
          display: flex;
          flex-wrap: wrap;
          gap: 0.5rem;
          margin-top: 0.85rem;
        }
        .pps-pill {
          display: inline-flex;
          align-items: center;
          min-height: 28px;
          padding: 0.22rem 0.55rem;
          border: 1px solid var(--pps-panel-border);
          border-radius: 999px;
          background: #f9fafb;
          color: #374151;
          font-size: 0.78rem;
          font-weight: 600;
          white-space: nowrap;
        }
        .pps-pill.ok { color: #0f766e; background: var(--pps-accent-weak); border-color: #a7d8cf; }
        .pps-pill.warn { color: var(--pps-warn); background: var(--pps-warn-weak); border-color: #fdba74; }
        .pps-pill.bad { color: var(--pps-bad); background: var(--pps-bad-weak); border-color: #fecaca; }
        .pps-panel-title {
          margin: 1.1rem 0 0.55rem;
          font-size: 1.02rem;
          font-weight: 700;
          color: #1f2937;
        }
        .pps-muted { color: var(--pps-text-muted); }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header(config: Mapping[str, Any], snapshot: Mapping[str, Any], api_state: Mapping[str, Any]) -> None:
    title = config.get("title", "PPS57 ROUT25 Platform")
    description = config.get("description", "Dashboard local de validação PPS57.")
    scenario = config.get("scenario_id", "unknown")
    missing = snapshot.get("missing_critical_artifacts", [])
    api_status = "offline" if "__error__" in api_state else str(api_state.get("status", "online"))
    api_class = "bad" if api_status == "offline" else ("warn" if api_status in {"running", "paused"} else "ok")
    missing_class = "bad" if missing else "ok"
    missing_text = f"críticos em falta: {len(missing)}" if missing else "artefactos críticos OK"
    html = f"""
    <div class="pps-header">
      <div class="pps-title">{escape(str(title))}</div>
      <div class="pps-subtitle">{escape(str(description))}</div>
      <div class="pps-strip">
        <span class="pps-pill">cenário: {escape(str(scenario))}</span>
        <span class="pps-pill {api_class}">API: {escape(api_status)}</span>
        <span class="pps-pill {missing_class}">{escape(missing_text)}</span>
        <span class="pps-pill">root: {escape(str(snapshot.get("root", "")))}</span>
      </div>
    </div>
    """
    st.markdown(html, unsafe_allow_html=True)


def render_overview(config: Mapping[str, Any], aggregates: Mapping[str, Any], reports: Mapping[str, Any]) -> None:
    overview = aggregates["overview"]
    render_panel_title("Operação")
    render_metrics(
        [
            ("Mensagens C-ITS", overview["total_cits_messages"]),
            ("Decisões TSP", overview["total_tsp_decisions"]),
            ("Atuações aplicadas", overview["applied_actuation_events"]),
            ("Bloqueios Safety", overview["blocked_by_safety"]),
        ]
    )

    render_panel_title("Otimização e KPIs")
    render_metrics(
        [
            ("Amostras offline", overview["offline_sample_count"]),
            ("Candidatos política", overview["policy_candidate_count"]),
            ("Unsafe filtrados", overview["unsafe_candidates_filtered"]),
            ("Reward delta", overview["reward_delta"]),
        ]
    )

    render_panel_title("Resumos disponíveis")
    summary_keys = ["cits_summary", "tsp_summary", "optimization_summary", "baseline_kpis"]
    summary_rows = [flatten_summary(name, reports.get(name, {})) for name in summary_keys]
    render_table(summary_rows, height=210)


def render_cits(aggregates: Mapping[str, Any], records: Mapping[str, List[Dict[str, Any]]]) -> None:
    render_panel_title("Mensagens C-ITS/V2X")
    cits = aggregates["cits"]
    chart_columns = st.columns(2)
    render_bar(chart_columns[0], "Mensagens por tipo", cits["by_message_type"])
    render_bar(chart_columns[1], "SSEM por estado", cits["by_status"])
    chart_columns = st.columns(2)
    render_bar(chart_columns[0], "Pedidos por RSU", cits["requests_by_rsu"])
    render_bar(chart_columns[1], "Pedidos por veículo", cits["requests_by_vehicle"])
    render_panel_title("Últimas mensagens")
    render_table(latest_records(records.get("cits_messages", []), 50), height=360)


def render_tsp(aggregates: Mapping[str, Any], records: Mapping[str, List[Dict[str, Any]]]) -> None:
    render_panel_title("Decisões TSP e Safety Layer")
    tsp = aggregates["tsp"]
    chart_columns = st.columns(2)
    render_bar(chart_columns[0], "Decisões por ação", tsp["by_action"])
    render_bar(chart_columns[1], "Decisões por estado", tsp["by_status"])
    chart_columns = st.columns(2)
    render_bar(chart_columns[0], "Decisões por RSU", tsp["by_rsu"])
    render_bar(chart_columns[1], "Decisões por razão", tsp["by_reason"])
    render_panel_title("Últimas decisões")
    render_table(latest_records(records.get("tsp_decisions", []), 50), height=360)


def render_actuation(aggregates: Mapping[str, Any], records: Mapping[str, List[Dict[str, Any]]]) -> None:
    render_panel_title("Atuação semafórica")
    actuation = aggregates["actuation"]
    chart_columns = st.columns(2)
    render_bar(chart_columns[0], "Atuações por ação", actuation["by_action"])
    render_bar(chart_columns[1], "Aplicada?", actuation["by_applied"])
    chart_columns = st.columns(2)
    render_bar(chart_columns[0], "Dry-run?", actuation["by_dry_run"])
    render_bar(chart_columns[1], "Atuações por TLS", actuation["by_tls"])
    render_panel_title("Últimas atuações")
    render_table(latest_records(records.get("tsp_actuation", []), 50), height=360)


def render_optimization(
    aggregates: Mapping[str, Any],
    records: Mapping[str, List[Dict[str, Any]]],
    reports: Mapping[str, Dict[str, Any]],
) -> None:
    render_panel_title("Policy Optimization & Reinforcement Learning")
    opt = aggregates["optimization"]
    render_metrics(
        [
            ("Baseline reward", opt["baseline_reward"]),
            ("Optimized reward", opt["optimized_reward"]),
            ("Reward delta", opt["reward_delta"]),
        ],
        columns=3,
    )

    chart_columns = st.columns(2)
    render_bar(chart_columns[0], "Selecionados por ação", opt["selected_by_action"])
    render_bar(chart_columns[1], "Candidatos por Safety status", opt["candidates_by_safety_status"])

    render_panel_title("Candidatos de política")
    render_table(latest_records(records.get("policy_candidates", []), 100), height=380)

    with st.expander("Relatório de política exportado"):
        st.json(reports.get("policy_report", {}))


def render_kpis(
    aggregates: Mapping[str, Any],
    reports: Mapping[str, Dict[str, Any]],
    tripinfo: Mapping[str, Any],
) -> None:
    render_panel_title("KPIs de mobilidade")
    overview = aggregates["overview"]
    render_metrics(
        [
            ("Veículos TripInfo", overview["tripinfo_vehicle_count"]),
            ("Duração média TripInfo", overview["tripinfo_avg_duration_s"]),
            ("Veículos baseline", reports.get("baseline_kpis", {}).get("vehicle_count", "n/a")),
            ("Espera média baseline", reports.get("baseline_kpis", {}).get("avg_waiting_time_s", "n/a")),
        ]
    )

    st.markdown("### TripInfo SUMO")
    st.json(dict(tripinfo))
    st.markdown("### Baseline KPIs")
    st.json(reports.get("baseline_kpis", {}))


def render_control(api_url: str) -> None:
    render_panel_title("Controlo local via FastAPI")
    state = api_get(api_url, "/runs/current")
    if "__error__" in state:
        st.warning("API local indisponível. Arranca com `make platform-api`.")
        st.code(state["__error__"])
        return

    render_metrics(
        [
            ("Estado", state.get("status", "unknown")),
            ("Job", state.get("kind") or "n/a"),
            ("PID", state.get("pid") or "n/a"),
            ("Return code", state.get("returncode") if state.get("returncode") is not None else "n/a"),
        ]
    )

    left, right = st.columns([1.1, 0.9])
    with left:
        render_panel_title("Executar")
        with st.form("run_command_form"):
            kind = st.selectbox(
                "Job",
                [
                    "tsp-dry-run",
                    "cits-dry-run",
                    "optimize-offline",
                    "train-rl-policy",
                    "platform-demo-data",
                    "platform-check",
                    "tsp-sumo-no-actuation",
                    "tsp-sumo",
                    "cits-sumo",
                ],
            )
            steps = st.number_input("Steps", min_value=1, max_value=100000, value=90, step=10)
            gui = st.checkbox("GUI SUMO", value=False)
            no_actuation = st.checkbox("Sem atuação", value=kind == "tsp-sumo-no-actuation")
            policy_mode = st.selectbox("Policy mode", ["baseline", "optimized"])
            policy_report = st.text_input("Policy report", value="reports/policy_report.json")
            overwrite = st.checkbox("Overwrite demo data", value=True)
            submitted = st.form_submit_button("Executar", use_container_width=True)
        if submitted:
            payload: Dict[str, Any] = {"kind": kind}
            if kind in {"tsp-dry-run", "cits-dry-run", "tsp-sumo", "tsp-sumo-no-actuation", "cits-sumo"}:
                payload["steps"] = int(steps)
            if kind in {"tsp-sumo", "tsp-sumo-no-actuation", "cits-sumo"}:
                payload["gui"] = bool(gui)
            if kind.startswith("tsp-sumo"):
                payload["no_actuation"] = bool(no_actuation)
            if kind in {"tsp-dry-run", "tsp-sumo", "tsp-sumo-no-actuation"}:
                payload["policy_mode"] = policy_mode
                payload["policy_report"] = policy_report
            if kind == "platform-demo-data":
                payload["overwrite"] = bool(overwrite)
            render_api_result(api_post(api_url, "/runs/start", payload))

    with right:
        render_panel_title("Processo")
        col_e, col_f, col_g = st.columns(3)
        if col_e.button("Pausar", use_container_width=True):
            render_api_result(api_post(api_url, "/runs/pause", {}))
        if col_f.button("Continuar", use_container_width=True):
            render_api_result(api_post(api_url, "/runs/resume", {}))
        if col_g.button("Parar", use_container_width=True):
            render_api_result(api_post(api_url, "/runs/stop", {}))
        st.text_input("stdout", value=str(state.get("stdout_log") or ""), disabled=True)
        st.text_input("stderr", value=str(state.get("stderr_log") or ""), disabled=True)

    with st.expander("Estado bruto"):
        st.json(state)


def render_artifacts(snapshot: Mapping[str, Any]) -> None:
    render_panel_title("Artefactos e disponibilidade")
    render_table(snapshot.get("artifacts", []), height=420)
    with st.expander("Snapshot bruto"):
        st.json(snapshot)


def render_bar(container: Any, title: str, counts: Mapping[str, int]) -> None:
    container.markdown(f'<div class="pps-panel-title">{escape(title)}</div>', unsafe_allow_html=True)
    if not counts:
        container.info("Sem dados disponíveis.")
        return
    df = pd.DataFrame([{"categoria": key, "valor": value} for key, value in counts.items()]).set_index("categoria")
    container.bar_chart(df)


def render_metrics(items: Iterable[tuple[str, Any]], columns: int = 4) -> None:
    cols = st.columns(columns)
    for index, (label, value) in enumerate(items):
        cols[index % columns].metric(label, format_metric_value(value))


def render_panel_title(title: str) -> None:
    st.markdown(f'<div class="pps-panel-title">{escape(title)}</div>', unsafe_allow_html=True)


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
    return str(value)


def flatten_summary(name: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    row: Dict[str, Any] = {"summary": name, "available": bool(payload)}
    for key in ["mode", "scenario_id", "total_messages", "total_decisions", "scenario_count", "candidate_count", "reward_delta"]:
        if key in payload:
            row[key] = payload[key]
    return row


def api_get(base_url: str, path: str) -> Dict[str, Any]:
    return api_request(base_url, path, method="GET")


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
