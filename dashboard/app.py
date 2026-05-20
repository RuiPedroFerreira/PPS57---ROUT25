#!/usr/bin/env python3
"""Streamlit dashboard for the PPS57 ROUT25 platform."""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Dict, Iterable, List, Mapping

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
    st.title("PPS57 — ROUT25 Traffic Priority Platform")
    st.caption("Validação e demonstração: C-ITS/V2X, TSP, Safety Layer, atuação e otimização offline.")

    with st.sidebar:
        st.header("Configuração")
        root_text = st.text_input("Raiz do repositório", value=str(ROOT))
        config_text = st.text_input("Configuração", value="configs/platform_config.json")
        max_records = st.number_input("Máximo de registos por log", min_value=100, max_value=100000, value=5000, step=100)
        st.info("Corre primeiro `make tsp-dryrun` ou `make platform-demo-data` para popular os outputs.")
        refresh = st.button("Recarregar")
        if refresh:
            st.rerun()

    snapshot = collect_snapshot(Path(root_text), config_text, max_records=int(max_records))
    config = snapshot["config"]
    aggregates = snapshot["aggregates"]
    records = snapshot["records"]
    reports = snapshot["reports"]

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
        render_artifacts(snapshot)


def render_overview(config: Mapping[str, Any], aggregates: Mapping[str, Any], reports: Mapping[str, Any]) -> None:
    overview = aggregates["overview"]
    st.subheader(config.get("title", "PPS57 Platform"))
    st.write(config.get("description", "Dashboard local de validação PPS57."))

    cols = st.columns(4)
    cols[0].metric("Mensagens C-ITS", overview["total_cits_messages"])
    cols[1].metric("Decisões TSP", overview["total_tsp_decisions"])
    cols[2].metric("Atuações aplicadas", overview["applied_actuation_events"])
    cols[3].metric("Bloqueios Safety", overview["blocked_by_safety"])

    cols = st.columns(4)
    cols[0].metric("Amostras offline", overview["offline_sample_count"])
    cols[1].metric("Candidatos política", overview["policy_candidate_count"])
    cols[2].metric("Unsafe filtrados", overview["unsafe_candidates_filtered"])
    cols[3].metric("Reward delta", overview["reward_delta"])

    st.markdown("### Resumos disponíveis")
    summary_keys = ["cits_summary", "tsp_summary", "optimization_summary", "baseline_kpis"]
    summary_rows = [flatten_summary(name, reports.get(name, {})) for name in summary_keys]
    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True)


def render_cits(aggregates: Mapping[str, Any], records: Mapping[str, List[Dict[str, Any]]]) -> None:
    st.subheader("Mensagens C-ITS/V2X emuladas")
    cits = aggregates["cits"]
    chart_columns = st.columns(2)
    render_bar(chart_columns[0], "Mensagens por tipo", cits["by_message_type"])
    render_bar(chart_columns[1], "SSEM por estado", cits["by_status"])
    chart_columns = st.columns(2)
    render_bar(chart_columns[0], "Pedidos por RSU", cits["requests_by_rsu"])
    render_bar(chart_columns[1], "Pedidos por veículo", cits["requests_by_vehicle"])
    st.markdown("### Últimas mensagens")
    st.dataframe(pd.DataFrame(latest_records(records.get("cits_messages", []), 50)), use_container_width=True)


def render_tsp(aggregates: Mapping[str, Any], records: Mapping[str, List[Dict[str, Any]]]) -> None:
    st.subheader("Decisões TSP e Safety Layer")
    tsp = aggregates["tsp"]
    chart_columns = st.columns(2)
    render_bar(chart_columns[0], "Decisões por ação", tsp["by_action"])
    render_bar(chart_columns[1], "Decisões por estado", tsp["by_status"])
    chart_columns = st.columns(2)
    render_bar(chart_columns[0], "Decisões por RSU", tsp["by_rsu"])
    render_bar(chart_columns[1], "Decisões por razão", tsp["by_reason"])
    st.markdown("### Últimas decisões")
    st.dataframe(pd.DataFrame(latest_records(records.get("tsp_decisions", []), 50)), use_container_width=True)


def render_actuation(aggregates: Mapping[str, Any], records: Mapping[str, List[Dict[str, Any]]]) -> None:
    st.subheader("Atuação semafórica")
    actuation = aggregates["actuation"]
    chart_columns = st.columns(2)
    render_bar(chart_columns[0], "Atuações por ação", actuation["by_action"])
    render_bar(chart_columns[1], "Aplicada?", actuation["by_applied"])
    chart_columns = st.columns(2)
    render_bar(chart_columns[0], "Dry-run?", actuation["by_dry_run"])
    render_bar(chart_columns[1], "Atuações por TLS", actuation["by_tls"])
    st.markdown("### Últimas atuações")
    st.dataframe(pd.DataFrame(latest_records(records.get("tsp_actuation", []), 50)), use_container_width=True)


def render_optimization(
    aggregates: Mapping[str, Any],
    records: Mapping[str, List[Dict[str, Any]]],
    reports: Mapping[str, Dict[str, Any]],
) -> None:
    st.subheader("Pacote 5 — Otimização offline / RL proxy")
    opt = aggregates["optimization"]
    cols = st.columns(3)
    cols[0].metric("Baseline reward", opt["baseline_reward"])
    cols[1].metric("Optimized reward", opt["optimized_reward"])
    cols[2].metric("Reward delta", opt["reward_delta"])

    chart_columns = st.columns(2)
    render_bar(chart_columns[0], "Selecionados por ação", opt["selected_by_action"])
    render_bar(chart_columns[1], "Candidatos por Safety status", opt["candidates_by_safety_status"])

    st.markdown("### Candidatos de política")
    st.dataframe(pd.DataFrame(latest_records(records.get("policy_candidates", []), 100)), use_container_width=True)

    with st.expander("Relatório de política exportado"):
        st.json(reports.get("policy_report", {}))


def render_kpis(
    aggregates: Mapping[str, Any],
    reports: Mapping[str, Dict[str, Any]],
    tripinfo: Mapping[str, Any],
) -> None:
    st.subheader("KPIs de mobilidade")
    overview = aggregates["overview"]
    cols = st.columns(4)
    cols[0].metric("Veículos TripInfo", overview["tripinfo_vehicle_count"])
    cols[1].metric("Duração média TripInfo", overview["tripinfo_avg_duration_s"])
    cols[2].metric("Veículos baseline", reports.get("baseline_kpis", {}).get("vehicle_count", "n/a"))
    cols[3].metric("Espera média baseline", reports.get("baseline_kpis", {}).get("avg_waiting_time_s", "n/a"))

    st.markdown("### TripInfo SUMO")
    st.json(dict(tripinfo))
    st.markdown("### Baseline KPIs")
    st.json(reports.get("baseline_kpis", {}))


def render_artifacts(snapshot: Mapping[str, Any]) -> None:
    st.subheader("Artefactos e disponibilidade")
    artifacts = pd.DataFrame(snapshot.get("artifacts", []))
    st.dataframe(artifacts, use_container_width=True)
    with st.expander("Snapshot bruto"):
        st.json(snapshot)


def render_bar(container: Any, title: str, counts: Mapping[str, int]) -> None:
    container.markdown(f"### {title}")
    if not counts:
        container.info("Sem dados disponíveis.")
        return
    df = pd.DataFrame([{"categoria": key, "valor": value} for key, value in counts.items()]).set_index("categoria")
    container.bar_chart(df)


def flatten_summary(name: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    row: Dict[str, Any] = {"summary": name, "available": bool(payload)}
    for key in ["mode", "scenario_id", "total_messages", "total_decisions", "scenario_count", "candidate_count", "reward_delta"]:
        if key in payload:
            row[key] = payload[key]
    return row


if __name__ == "__main__":
    main()
