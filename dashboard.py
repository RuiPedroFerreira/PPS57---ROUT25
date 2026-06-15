"""Streamlit dashboard — PPS57 simulation results viewer.

Run with: streamlit run dashboard.py
"""

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).parent
REPORTS = ROOT / "reports"

VEHICLE_CLASSES = [
    ("all_vehicles", "Todos os veículos"),
    ("buses", "Autocarros"),
    ("emergency_vehicles", "Emergência"),
    ("priority_vehicles", "Prioritários"),
    ("general_traffic", "Tráfego geral"),
]

KPI_LABELS = {
    "mean_duration_s": "Duração média (s)",
    "mean_waiting_time_s": "Espera média (s)",
    "mean_time_loss_s": "Perda de tempo média (s)",
    "p95_time_loss_s": "Perda de tempo P95 (s)",
    "mean_speed_mps": "Velocidade média (m/s)",
    "mean_depart_delay_s": "Atraso de partida médio (s)",
    "mean_stop_count": "Paragens médias",
}

ACTION_COLORS = {
    "green_extension": "#22c55e",
    "early_green": "#1d6ef5",
    "no_action": "#94a3b8",
    "reject": "#ef4444",
    "reevaluate_next_cycle": "#f59e0b",
}

SCENARIO_PALETTE = ["#64748b", "#1d6ef5", "#7c3aed", "#0891b2", "#059669"]

CSS = """
<style>
/* ── brand header ──────────────────────────────────────────── */
.pps57-header {
    background: linear-gradient(135deg, #1d4ed8 0%, #1d6ef5 60%, #38bdf8 100%);
    border-radius: 14px;
    padding: 28px 36px;
    margin-bottom: 8px;
    color: white;
}
.pps57-header h1 {
    margin: 0 0 4px;
    font-size: 1.75rem;
    font-weight: 700;
    color: white;
    letter-spacing: -0.5px;
}
.pps57-header p {
    margin: 0;
    opacity: 0.85;
    font-size: 0.88rem;
}
.pps57-badge {
    display: inline-block;
    background: rgba(255,255,255,0.2);
    border: 1px solid rgba(255,255,255,0.35);
    border-radius: 20px;
    padding: 2px 10px;
    font-size: 0.75rem;
    margin-left: 10px;
    vertical-align: middle;
    font-weight: 500;
}

/* ── sidebar status dots ───────────────────────────────────── */
.file-status {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 5px 0;
    font-size: 0.82rem;
    color: #374151;
}
.dot {
    width: 9px; height: 9px;
    border-radius: 50%;
    flex-shrink: 0;
}
.dot-ok  { background: #22c55e; box-shadow: 0 0 0 2px #bbf7d0; }
.dot-off { background: #d1d5db; }
.file-name { font-family: monospace; color: #4b5563; font-size: 0.78rem; }

/* ── empty-state ───────────────────────────────────────────── */
.empty-wrap {
    display: flex;
    flex-direction: column;
    align-items: center;
    margin-top: 48px;
}
.empty-icon { font-size: 3rem; margin-bottom: 12px; }
.empty-title { font-size: 1.25rem; font-weight: 700; color: #111827; margin: 0 0 6px; }
.empty-sub { color: #6b7280; font-size: 0.9rem; margin: 0 0 36px; text-align: center; }
.steps-row { display: flex; gap: 16px; flex-wrap: wrap; justify-content: center; }
.step-card {
    background: #ffffff;
    border: 1.5px solid #e5e7eb;
    border-radius: 12px;
    padding: 20px 22px;
    width: 200px;
    position: relative;
}
.step-card:hover { border-color: #1d6ef5; box-shadow: 0 0 0 3px #dbeafe; }
.step-num {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 26px; height: 26px;
    border-radius: 50%;
    background: #1d6ef5;
    color: white;
    font-size: 0.78rem;
    font-weight: 700;
    margin-bottom: 10px;
}
.step-title { font-weight: 600; font-size: 0.9rem; color: #111827; margin: 0 0 4px; }
.step-desc  { font-size: 0.8rem; color: #6b7280; margin: 0 0 10px; }
.step-cmd {
    display: inline-block;
    background: #f3f4f6;
    border: 1px solid #e5e7eb;
    border-radius: 6px;
    padding: 3px 8px;
    font-family: monospace;
    font-size: 0.78rem;
    color: #1d4ed8;
    font-weight: 500;
}

/* ── verdict pill ──────────────────────────────────────────── */
.verdict-pill {
    display: inline-block;
    padding: 5px 14px;
    border-radius: 20px;
    font-size: 0.82rem;
    font-weight: 600;
    margin-top: 12px;
}
.verdict-pass    { background: #dcfce7; color: #15803d; border: 1px solid #86efac; }
.verdict-review  { background: #fef3c7; color: #92400e; border: 1px solid #fde68a; }
.verdict-unknown { background: #f3f4f6; color: #374151; border: 1px solid #e5e7eb; }

/* ── sidebar project label ─────────────────────────────────── */
.sidebar-project {
    font-weight: 700;
    font-size: 1rem;
    color: #1d6ef5;
    margin-bottom: 2px;
}
.sidebar-sub { font-size: 0.78rem; color: #6b7280; }

/* ── misc tweaks ───────────────────────────────────────────── */
[data-testid="stMetricValue"] { font-size: 1.6rem !important; font-weight: 700; }
[data-testid="stMetricLabel"] { font-size: 0.78rem; color: #6b7280; }
</style>
"""


# ── helpers ───────────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict | None:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return None
    return None


def fmt(val) -> str:
    if val is None:
        return "—"
    return f"{val:.1f}"


def comp_rows_df(rows: list[dict]) -> pd.DataFrame:
    out = []
    for row in rows:
        metric_key = row.get("metric", "")
        out.append({
            "Métrica": KPI_LABELS.get(metric_key, metric_key),
            "Baseline": fmt(row.get("baseline")),
            "TSP / RL": fmt(row.get("tsp") or row.get("rl")),
            "Δ (s)": row.get("delta"),
            "Δ (%)": row.get("delta_pct"),
            "p-valor": row.get("p_value"),
        })
    return pd.DataFrame(out)


def style_delta(df: pd.DataFrame):
    if "Δ (s)" not in df.columns:
        return df

    def color(val):
        try:
            v = float(val)
            return "color: #15803d; font-weight:600" if v < 0 else (
                "color: #dc2626; font-weight:600" if v > 0 else ""
            )
        except (TypeError, ValueError):
            return ""

    return df.style.map(color, subset=["Δ (s)"])


def kpi_row(data: dict, cls: str) -> dict:
    return data.get(cls, {})


def apply_chart_style(fig: go.Figure, title: str = "") -> go.Figure:
    fig.update_layout(
        title={"text": title, "font": {"size": 14, "color": "#111827"}, "x": 0},
        paper_bgcolor="white",
        plot_bgcolor="#f8fafc",
        font={"family": "Inter, system-ui, sans-serif", "color": "#374151"},
        legend={"bgcolor": "rgba(0,0,0,0)", "borderwidth": 0},
        margin={"t": 48, "b": 32, "l": 8, "r": 8},
    )
    fig.update_xaxes(gridcolor="#f1f5f9", linecolor="#e2e8f0", tickfont={"size": 11})
    fig.update_yaxes(gridcolor="#f1f5f9", linecolor="#e2e8f0", tickfont={"size": 11})
    return fig


# ── page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="PPS57 — Simulação",
    page_icon="🚌",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(CSS, unsafe_allow_html=True)

# ── branded header ────────────────────────────────────────────────────────────

st.markdown("""
<div class="pps57-header">
  <h1>PPS57 · Simulação TSP <span class="pps57-badge">Linha 25 · Porto</span></h1>
  <p>Comparação Baseline SUMO &nbsp;·&nbsp; TSP Rule-based &nbsp;·&nbsp; TSP + RL</p>
</div>
""", unsafe_allow_html=True)

# ── sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown('<div class="sidebar-project">PPS57 · ROUT25</div>', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-sub">Traffic Signal Priority · SUMO</div>', unsafe_allow_html=True)
    st.divider()

    vehicle_cls_label = st.selectbox(
        "Classe de veículo",
        options=[label for _, label in VEHICLE_CLASSES],
        index=0,
    )
    vehicle_cls = next(k for k, l in VEHICLE_CLASSES if l == vehicle_cls_label)

    st.divider()
    st.markdown("**Reports disponíveis**")

    report_files = {
        "baseline_kpis.json": REPORTS / "baseline_kpis.json",
        "tsp_demonstrator_report.json": REPORTS / "tsp_demonstrator_report.json",
        "tsp_baseline_vs_rl_comparison.json": REPORTS / "tsp_baseline_vs_rl_comparison.json",
    }
    for name, path in report_files.items():
        dot = "dot-ok" if path.exists() else "dot-off"
        st.markdown(
            f'<div class="file-status"><span class="dot {dot}"></span>'
            f'<span class="file-name">{name}</span></div>',
            unsafe_allow_html=True,
        )

# ── load data ─────────────────────────────────────────────────────────────────

demo = load_json(REPORTS / "tsp_demonstrator_report.json")
baseline_kpis = load_json(REPORTS / "baseline_kpis.json")
rl_comparison = load_json(REPORTS / "tsp_baseline_vs_rl_comparison.json")

# ── no data — empty state ─────────────────────────────────────────────────────

if demo is None and baseline_kpis is None:
    st.markdown("""
<div class="empty-wrap">
  <div class="empty-icon">🚌</div>
  <p class="empty-title">Sem dados de simulação</p>
  <p class="empty-sub">Nenhum report encontrado em <code>reports/</code>.<br>
     Segue os passos abaixo para gerar resultados.</p>
  <div class="steps-row">
    <div class="step-card">
      <div class="step-num">1</div>
      <div class="step-title">Baseline SUMO</div>
      <div class="step-desc">Simulação sem TSP — ponto de referência</div>
      <span class="step-cmd">make baseline</span>
    </div>
    <div class="step-card">
      <div class="step-num">2</div>
      <div class="step-title">Run TSP</div>
      <div class="step-desc">Simulação com algoritmo de prioridade activo</div>
      <span class="step-cmd">make tsp-run</span>
    </div>
    <div class="step-card">
      <div class="step-num">3</div>
      <div class="step-title">Cenários multi-seed</div>
      <div class="step-desc">Suite completa com análise estatística</div>
      <span class="step-cmd">make scenario-suite</span>
    </div>
    <div class="step-card">
      <div class="step-num">4</div>
      <div class="step-title">Ver resultados</div>
      <div class="step-desc">A dashboard actualiza automaticamente</div>
      <span class="step-cmd">↑ recarrega</span>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)
    st.stop()

# ── tabs ──────────────────────────────────────────────────────────────────────

tab_kpi, tab_decisions, tab_rl, tab_scenarios, tab_headways = st.tabs([
    "📊  KPIs",
    "🚦  Decisões TSP",
    "🤖  Baseline vs RL",
    "🗺️  Cenários",
    "🚌  Headways",
])

# ═════════════════════════════════════════════════════════════════════════════
# TAB 1 — KPI comparison
# ═════════════════════════════════════════════════════════════════════════════

with tab_kpi:
    run_kpis: dict[str, dict] = {}
    if demo:
        for label, run in demo.get("runs", {}).items():
            if "kpis" in run:
                run_kpis[label] = run["kpis"]
    # only add standalone baseline_kpis.json if the demo report has no sumo_baseline run
    if baseline_kpis and not any("baseline" in k.lower() for k in run_kpis):
        run_kpis["Baseline"] = baseline_kpis

    if not run_kpis:
        st.info("Sem dados de KPI disponíveis.")
    else:
        cls_data = {label: kpi_row(kpis, vehicle_cls) for label, kpis in run_kpis.items()}

        # metric cards
        cols = st.columns(len(run_kpis))
        for col, (label, data) in zip(cols, cls_data.items()):
            with col:
                st.metric(
                    f"{label} — perda de tempo",
                    fmt(data.get("mean_time_loss_s")) + " s",
                )

        st.divider()

        # bar chart
        metrics_to_plot = [
            "mean_waiting_time_s",
            "mean_time_loss_s",
            "mean_duration_s",
            "mean_depart_delay_s",
        ]
        chart_rows = []
        for metric in metrics_to_plot:
            for label, data in cls_data.items():
                val = data.get(metric)
                if val is not None:
                    chart_rows.append({
                        "Métrica": KPI_LABELS.get(metric, metric),
                        "Cenário": label,
                        "Valor (s)": val,
                    })

        if chart_rows:
            df_chart = pd.DataFrame(chart_rows)
            fig = px.bar(
                df_chart,
                x="Métrica",
                y="Valor (s)",
                color="Cenário",
                barmode="group",
                color_discrete_sequence=SCENARIO_PALETTE,
                height=400,
            )
            fig.update_layout(legend_title_text="")
            apply_chart_style(fig, f"KPIs por cenário — {vehicle_cls_label}")
            st.plotly_chart(fig, use_container_width=True)

        # delta tables
        if demo:
            st.subheader("Tabela de comparação")
            comp_map = [
                ("tsp_vs_sumo_baseline_kpis", "TSP vs Baseline SUMO"),
                ("tsp_controller_vs_sumo_baseline_kpis", "TSP+Controller vs Baseline SUMO"),
                ("tsp_controller_vs_tsp_runtime", "TSP+Controller vs TSP Runtime"),
            ]
            for comp_key, comp_title in comp_map:
                comp = demo.get("comparisons", {}).get(comp_key, {})
                if comp.get("available") and comp.get("rows"):
                    with st.expander(comp_title, expanded=(comp_key == "tsp_vs_sumo_baseline_kpis")):
                        df = comp_rows_df(comp["rows"])
                        st.dataframe(style_delta(df), use_container_width=True)

        # verdict
        if demo and "verdict" in demo:
            v = demo["verdict"]
            status = v.get("status", "")
            reason = v.get("reason", status)
            pill_cls = {
                "value_demonstrated": "verdict-pass",
                "review": "verdict-review",
            }.get(status, "verdict-unknown")
            st.markdown(
                f'<span class="verdict-pill {pill_cls}">Veredicto: {reason}</span>',
                unsafe_allow_html=True,
            )

# ═════════════════════════════════════════════════════════════════════════════
# TAB 2 — TSP decisions
# ═════════════════════════════════════════════════════════════════════════════

with tab_decisions:
    if not demo:
        st.info("Sem report do demonstrador disponível.")
    else:
        run_labels = list(demo.get("runs", {}).keys())
        if not run_labels:
            st.info("Sem dados de runs TSP.")
        else:
            selected_run = st.selectbox("Run", run_labels)
            runtime = demo["runs"][selected_run].get("runtime", {})

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Decisões totais", runtime.get("total_decisions", "—"))
            c2.metric("Aplicadas", runtime.get("applied_events", "—"))
            c3.metric("Bloqueadas (safety)", runtime.get("blocked_by_safety", "—"))
            c4.metric("Rejeitadas (controller)", runtime.get("controller_rejections", "—"))

            if runtime.get("total_decisions", 0) == 0:
                st.info("Esta run não tem decisões TSP — selecciona uma run TSP na caixa acima.")
            st.divider()
            col_l, col_r = st.columns(2)

            by_action = runtime.get("by_action", {})
            if by_action:
                with col_l:
                    fig_pie = go.Figure(go.Pie(
                        labels=list(by_action.keys()),
                        values=list(by_action.values()),
                        marker_colors=[ACTION_COLORS.get(k, "#94a3b8") for k in by_action],
                        hole=0.42,
                        textinfo="label+percent",
                        textfont={"size": 11},
                    ))
                    apply_chart_style(fig_pie, "Distribuição de ações")
                    fig_pie.update_layout(height=380, showlegend=False)
                    st.plotly_chart(fig_pie, use_container_width=True)

            safety_reasons = runtime.get("safety_block_by_reason", {})
            if safety_reasons:
                with col_r:
                    df_safety = pd.DataFrame({
                        "Motivo": list(safety_reasons.keys()),
                        "Bloqueios": list(safety_reasons.values()),
                    }).sort_values("Bloqueios", ascending=True)
                    fig_safety = px.bar(
                        df_safety,
                        x="Bloqueios",
                        y="Motivo",
                        orientation="h",
                        color_discrete_sequence=["#ef4444"],
                        height=380,
                    )
                    apply_chart_style(fig_safety, "Bloqueios por motivo de segurança")
                    st.plotly_chart(fig_safety, use_container_width=True)

            per_tls = runtime.get("per_tls", {})
            if per_tls:
                st.subheader("Por semáforo (TLS)")
                tls_rows = [
                    {
                        "TLS": tls_id,
                        "Decisões": d.get("decisions", 0),
                        "Aplicadas": d.get("applied_events", 0),
                        "Bloqueadas": d.get("blocked_by_safety", 0),
                    }
                    for tls_id, d in per_tls.items()
                ]
                st.dataframe(
                    pd.DataFrame(tls_rows).sort_values("Decisões", ascending=False),
                    use_container_width=True,
                )

# ═════════════════════════════════════════════════════════════════════════════
# TAB 3 — RL comparison
# ═════════════════════════════════════════════════════════════════════════════

with tab_rl:
    if not rl_comparison:
        st.info("Sem report de comparação Baseline vs RL disponível.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Decisões comparadas", rl_comparison.get("matched_decision_count", "—"))
        c2.metric("Veredicto de rede", rl_comparison.get("network_impact_verdict", "—"))
        c3.metric("Tipo de avaliação", rl_comparison.get("evaluation", "—").replace("_", " "))

        verdict_counts = rl_comparison.get("verdict_counts", {})
        if verdict_counts:
            df_v = pd.DataFrame({
                "Veredicto": list(verdict_counts.keys()),
                "Contagem": list(verdict_counts.values()),
            })
            fig_v = px.bar(
                df_v,
                x="Veredicto",
                y="Contagem",
                color="Veredicto",
                color_discrete_sequence=SCENARIO_PALETTE,
                height=380,
            )
            fig_v.update_layout(showlegend=False)
            apply_chart_style(fig_v, "Distribuição de veredictos por decisão")
            st.plotly_chart(fig_v, use_container_width=True)

        kpi_eval = rl_comparison.get("kpi_evaluation", {})
        if kpi_eval.get("available") and kpi_eval.get("rows"):
            st.subheader("KPIs — Baseline vs RL")
            df = comp_rows_df(kpi_eval["rows"])
            st.dataframe(style_delta(df), use_container_width=True)

# ═════════════════════════════════════════════════════════════════════════════
# TAB 4 — Scenarios
# ═════════════════════════════════════════════════════════════════════════════

with tab_scenarios:
    scenario_dir = REPORTS / "scenarios"
    if not scenario_dir.exists() or not any(scenario_dir.iterdir()):
        st.info("Sem resultados de cenários. Corre `make scenario-suite` para gerar runs multi-seed.")
    else:
        scenarios = sorted(p.name for p in scenario_dir.iterdir() if p.is_dir())
        sel_scenario = st.selectbox("Cenário", scenarios)
        scen_path = scenario_dir / sel_scenario

        run_types = sorted(p.name for p in scen_path.iterdir() if p.is_dir())
        if not run_types:
            st.info("Sem run types neste cenário.")
        else:
            all_rows = []
            for run_type in run_types:
                for seed_dir in (scen_path / run_type).iterdir():
                    kpi_file = seed_dir / "kpis.json"
                    kpis = load_json(kpi_file)
                    if kpis:
                        data = kpi_row(kpis, vehicle_cls)
                        for metric, label in KPI_LABELS.items():
                            val = data.get(metric)
                            if val is not None:
                                all_rows.append({
                                    "Run type": run_type,
                                    "Seed": seed_dir.name,
                                    "Métrica": label,
                                    "Valor": val,
                                })

            if all_rows:
                df_scen = pd.DataFrame(all_rows)
                sel_metric = st.selectbox("Métrica", df_scen["Métrica"].unique().tolist())
                df_plot = df_scen[df_scen["Métrica"] == sel_metric]

                fig_box = px.box(
                    df_plot,
                    x="Run type",
                    y="Valor",
                    color="Run type",
                    color_discrete_sequence=SCENARIO_PALETTE,
                    points="all",
                    height=420,
                )
                apply_chart_style(fig_box, f"{sel_metric} — {sel_scenario} ({vehicle_cls_label})")
                fig_box.update_layout(showlegend=False)
                st.plotly_chart(fig_box, use_container_width=True)

                summary = (
                    df_plot.groupby("Run type")["Valor"]
                    .agg(["mean", "std", "min", "max", "count"])
                    .rename(columns={
                        "mean": "Média", "std": "Desvio-padrão",
                        "min": "Mín", "max": "Máx", "count": "Seeds",
                    })
                    .round(2)
                )
                st.dataframe(summary, use_container_width=True)
            else:
                st.info("Nenhum ficheiro kpis.json encontrado nos seeds.")

# ═════════════════════════════════════════════════════════════════════════════
# TAB 5 — Bus headways
# ═════════════════════════════════════════════════════════════════════════════

with tab_headways:
    headway_sources: dict[str, dict] = {}
    if baseline_kpis and "bus_headways" in baseline_kpis:
        headway_sources["Baseline"] = baseline_kpis["bus_headways"]
    if demo:
        for label, run in demo.get("runs", {}).items():
            hw = run.get("kpis", {}).get("bus_headways", {})
            if hw:
                headway_sources[label] = hw

    if not headway_sources:
        st.info("Sem dados de headways de autocarros.")
    else:
        hw_rows = []
        for run_label, headways in headway_sources.items():
            for line_key, stats in headways.items():
                hw_rows.append({
                    "Cenário": run_label,
                    "Linha": line_key,
                    "Partidas": stats.get("departures"),
                    "Headway médio (s)": stats.get("mean_headway_s"),
                    "Headway mín (s)": stats.get("min_headway_s"),
                    "Headway máx (s)": stats.get("max_headway_s"),
                })
        df_hw = pd.DataFrame(hw_rows)

        if df_hw.empty or "Linha" not in df_hw.columns:
            st.info("Sem dados de headways nas runs disponíveis.")
            st.stop()
        sel_line = st.selectbox("Linha", df_hw["Linha"].unique().tolist())
        df_line = df_hw[df_hw["Linha"] == sel_line]

        fig_hw = px.bar(
            df_line,
            x="Cenário",
            y="Headway médio (s)",
            color="Cenário",
            color_discrete_sequence=SCENARIO_PALETTE,
            height=380,
        )
        fig_hw.update_layout(showlegend=False)
        apply_chart_style(fig_hw, f"Headway médio — {sel_line}")
        st.plotly_chart(fig_hw, use_container_width=True)
        st.dataframe(df_line.set_index("Cenário"), use_container_width=True)
