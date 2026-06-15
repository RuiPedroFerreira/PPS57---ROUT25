"""PPS57 · TSP Simulation Analysis Dashboard.

Run with: streamlit run dashboard.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).parent
REPORTS = ROOT / "reports"

# ── constants ─────────────────────────────────────────────────────────────────

VEHICLE_CLASSES = [
    ("all_vehicles",        "Todos os veículos"),
    ("buses",               "Autocarros"),
    ("emergency_vehicles",  "Veículos de emergência"),
    ("priority_vehicles",   "Veículos prioritários"),
    ("general_traffic",     "Tráfego geral"),
]

KPI_META = {
    "mean_time_loss_s":    ("Perda de tempo média",     "s", "Tempo perdido face à velocidade ideal de rede. Indicador principal de eficiência."),
    "mean_waiting_time_s": ("Tempo de espera médio",    "s", "Tempo parado em fila ou semáforo vermelho."),
    "mean_duration_s":     ("Duração média de viagem",  "s", "Tempo total de trajecto, porta a porta."),
    "p95_time_loss_s":     ("Perda de tempo P95",       "s", "Percentil 95 — descreve o pior cenário para 95% dos veículos."),
    "mean_speed_mps":      ("Velocidade média",         "m/s","Velocidade média ao longo do trajecto."),
    "mean_depart_delay_s": ("Atraso de partida médio",  "s", "Tempo de espera antes de entrar na rede."),
    "mean_stop_count":     ("Paragens médias",          "",  "Número médio de paragens por veículo."),
}

ACTION_META = {
    "green_extension":       ("Extensão de verde",      "#22c55e", "Alonga a fase verde actual para deixar passar o autocarro."),
    "early_green":           ("Verde antecipado",       "#1d6ef5", "Avança o início da fase verde para a aproximação do autocarro."),
    "no_action":             ("Sem acção",              "#94a3b8", "Nenhuma intervenção necessária neste ciclo."),
    "reject":                ("Rejeitado",              "#ef4444", "Pedido recusado por critério de elegibilidade."),
    "reevaluate_next_cycle": ("Reavaliar no ciclo",     "#f59e0b", "Decisão adiada — reavalia na próxima janela de decisão."),
}

PALETTE = {
    "sumo_baseline": "#64748b",
    "baseline":      "#64748b",
    "tsp":           "#1d6ef5",
    "tsp_controller":"#7c3aed",
}

COLOR_GOOD    = "#15803d"
COLOR_BAD     = "#dc2626"
COLOR_NEUTRAL = "#374151"

# ── CSS ───────────────────────────────────────────────────────────────────────

CSS = """
<style>
/* ── global typography ─────────────────────────────────────────────────── */
html, body, [class*="css"] {
    font-family: "Inter", "Segoe UI", system-ui, sans-serif;
}

/* ── page header ────────────────────────────────────────────────────────── */
.page-header {
    border-bottom: 3px solid #1d6ef5;
    padding-bottom: 14px;
    margin-bottom: 20px;
}
.page-header h1 {
    font-size: 1.55rem;
    font-weight: 700;
    color: #0f172a;
    margin: 0 0 2px;
    letter-spacing: -0.4px;
}
.page-header .subtitle {
    font-size: 0.82rem;
    color: #64748b;
    margin: 0;
}
.badge {
    display: inline-block;
    background: #eff6ff;
    border: 1px solid #bfdbfe;
    color: #1d4ed8;
    font-size: 0.72rem;
    font-weight: 600;
    padding: 2px 8px;
    border-radius: 4px;
    margin-left: 8px;
    vertical-align: middle;
}

/* ── sidebar ────────────────────────────────────────────────────────────── */
.sb-label { font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
            letter-spacing: .08em; color: #94a3b8; margin: 16px 0 6px; }
.sb-project { font-weight: 700; font-size: 0.95rem; color: #0f172a; }
.sb-sub { font-size: 0.75rem; color: #64748b; }
.file-row { display:flex; align-items:center; gap:7px;
            font-size:0.75rem; color:#374151; padding: 3px 0; }
.dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
.dot-ok  { background:#22c55e; }
.dot-off { background:#cbd5e1; }
.dot-label { font-family: monospace; color:#475569; }

/* ── verdict banner ─────────────────────────────────────────────────────── */
.verdict-pass    { background:#f0fdf4; border-left:4px solid #22c55e;
                   padding:12px 16px; border-radius:0 8px 8px 0; }
.verdict-review  { background:#fffbeb; border-left:4px solid #f59e0b;
                   padding:12px 16px; border-radius:0 8px 8px 0; }
.verdict-fail    { background:#fef2f2; border-left:4px solid #ef4444;
                   padding:12px 16px; border-radius:0 8px 8px 0; }
.verdict-unknown { background:#f8fafc; border-left:4px solid #94a3b8;
                   padding:12px 16px; border-radius:0 8px 8px 0; }
.verdict-title { font-weight:700; font-size:0.85rem; margin:0 0 2px; }
.verdict-body  { font-size:0.82rem; color:#374151; margin:0; }

/* ── KPI delta cards ────────────────────────────────────────────────────── */
.kpi-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
            gap:12px; margin:16px 0; }
.kpi-card { background:#fff; border:1px solid #e2e8f0; border-radius:10px;
            padding:14px 16px; }
.kpi-card .label { font-size:0.72rem; color:#64748b; margin:0 0 4px;
                   text-transform:uppercase; letter-spacing:.05em; font-weight:600; }
.kpi-card .value { font-size:1.5rem; font-weight:700; color:#0f172a; margin:0; }
.kpi-card .delta-good { font-size:0.8rem; color:#15803d; font-weight:600; }
.kpi-card .delta-bad  { font-size:0.8rem; color:#dc2626; font-weight:600; }
.kpi-card .delta-neu  { font-size:0.8rem; color:#64748b; }

/* ── section label ──────────────────────────────────────────────────────── */
.section-label {
    font-size:0.7rem; font-weight:700; text-transform:uppercase;
    letter-spacing:.1em; color:#94a3b8; margin:28px 0 8px;
    border-bottom:1px solid #f1f5f9; padding-bottom:4px;
}

/* ── insight box ────────────────────────────────────────────────────────── */
.insight {
    background:#f0f9ff; border:1px solid #bae6fd;
    border-radius:8px; padding:10px 14px;
    font-size:0.8rem; color:#0c4a6e; margin:8px 0 16px;
    line-height: 1.5;
}
.insight strong { color:#0369a1; }

/* ── warning box ────────────────────────────────────────────────────────── */
.warn-box {
    background:#fffbeb; border:1px solid #fde68a;
    border-radius:8px; padding:10px 14px;
    font-size:0.8rem; color:#78350f; margin:8px 0 16px;
}

/* ── table colour helpers ───────────────────────────────────────────────── */
.tbl-good { color:#15803d !important; font-weight:600; }
.tbl-bad  { color:#dc2626 !important; font-weight:600; }

/* ── empty state ────────────────────────────────────────────────────────── */
.empty-wrap { max-width:680px; margin:60px auto; text-align:center; }
.empty-title { font-size:1.2rem; font-weight:700; color:#0f172a; margin:0 0 6px; }
.empty-sub   { font-size:0.88rem; color:#64748b; margin:0 0 36px; line-height:1.6; }
.steps { display:flex; gap:14px; flex-wrap:wrap; justify-content:center; }
.step  { background:#fff; border:1.5px solid #e2e8f0; border-radius:10px;
         padding:18px 20px; width:175px; text-align:left; }
.step:hover { border-color:#1d6ef5; }
.step-num   { display:inline-flex; align-items:center; justify-content:center;
              width:24px; height:24px; border-radius:50%; background:#1d4ed8;
              color:#fff; font-size:0.75rem; font-weight:700; margin-bottom:8px; }
.step-title { font-weight:600; font-size:0.85rem; color:#0f172a; margin:0 0 4px; }
.step-desc  { font-size:0.76rem; color:#64748b; margin:0 0 8px; line-height:1.4; }
.step-cmd   { font-family:monospace; font-size:0.74rem; background:#f1f5f9;
              border:1px solid #e2e8f0; border-radius:5px;
              padding:2px 7px; color:#1d4ed8; }

/* ── metric tooltip tweak ───────────────────────────────────────────────── */
[data-testid="stMetricValue"] { font-size:1.5rem !important; font-weight:700; }
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


def fmt(val, unit: str = "") -> str:
    if val is None:
        return "—"
    s = f"{val:.1f}"
    return f"{s} {unit}".strip() if unit else s


def pct(baseline, candidate) -> float | None:
    if baseline and candidate is not None and baseline != 0:
        return (candidate - baseline) / abs(baseline) * 100
    return None


def delta_str(baseline, candidate) -> tuple[str, str]:
    """Return (formatted string, css class)."""
    p = pct(baseline, candidate)
    if p is None:
        return "—", "delta-neu"
    sign = "+" if p > 0 else ""
    css = "delta-good" if p < 0 else ("delta-bad" if p > 0 else "delta-neu")
    return f"{sign}{p:.1f}%", css


def get_kpi(data: dict, cls: str) -> dict:
    return data.get(cls, {})


def run_color(label: str) -> str:
    # check most-specific (longest) keys first so "tsp_controller" matches
    # its own colour rather than the "tsp" substring.
    for k in sorted(PALETTE, key=len, reverse=True):
        if k in label.lower():
            return PALETTE[k]
    return "#94a3b8"


def chart_layout(fig: go.Figure, title: str = "", height: int = 380) -> go.Figure:
    fig.update_layout(
        title={"text": title, "font": {"size": 13, "color": "#0f172a", "family": "Inter, system-ui"}, "x": 0, "pad": {"b": 8}},
        paper_bgcolor="white",
        plot_bgcolor="#f8fafc",
        font={"family": "Inter, system-ui, sans-serif", "color": "#374151", "size": 11},
        legend={"bgcolor": "rgba(0,0,0,0)", "borderwidth": 0, "font": {"size": 11}},
        margin={"t": 44, "b": 36, "l": 8, "r": 8},
        height=height,
    )
    fig.update_xaxes(gridcolor="#f1f5f9", linecolor="#e2e8f0", tickfont={"size": 11})
    fig.update_yaxes(gridcolor="#f1f5f9", linecolor="#e2e8f0", tickfont={"size": 11})
    return fig


# ── page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="PPS57 — TSP Analysis",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(CSS, unsafe_allow_html=True)

# ── sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown('<div class="sb-project">PPS57 · ROUT25</div>', unsafe_allow_html=True)
    st.markdown('<div class="sb-sub">Traffic Signal Priority — Linha 25, Porto</div>', unsafe_allow_html=True)

    st.markdown('<div class="sb-label">Filtro</div>', unsafe_allow_html=True)
    vehicle_cls_label = st.selectbox(
        "Classe de veículo",
        options=[l for _, l in VEHICLE_CLASSES],
        index=0,
        label_visibility="collapsed",
    )
    vehicle_cls = next(k for k, l in VEHICLE_CLASSES if l == vehicle_cls_label)

    st.markdown('<div class="sb-label">Reports</div>', unsafe_allow_html=True)
    report_files = {
        "baseline_kpis.json":               REPORTS / "baseline_kpis.json",
        "tsp_demonstrator_report.json":     REPORTS / "tsp_demonstrator_report.json",
        "tsp_baseline_vs_rl_comparison.json": REPORTS / "tsp_baseline_vs_rl_comparison.json",
    }
    for name, path in report_files.items():
        dot = "dot-ok" if path.exists() else "dot-off"
        st.markdown(
            f'<div class="file-row"><span class="dot {dot}"></span>'
            f'<span class="dot-label">{name}</span></div>',
            unsafe_allow_html=True,
        )

    st.markdown('<div class="sb-label">Documentacao</div>', unsafe_allow_html=True)
    with st.expander("Glossário de métricas"):
        for _, (label, unit, desc) in KPI_META.items():
            st.markdown(f"**{label}** ({unit})\n{desc}\n")

# ── load data ─────────────────────────────────────────────────────────────────

demo          = load_json(REPORTS / "tsp_demonstrator_report.json")
baseline_kpis = load_json(REPORTS / "baseline_kpis.json")
rl_comparison = load_json(REPORTS / "tsp_baseline_vs_rl_comparison.json")

# ── page header ───────────────────────────────────────────────────────────────

st.markdown("""
<div class="page-header">
  <h1>PPS57 · Análise de Simulação TSP
    <span class="badge">Linha 25 · Porto</span>
    <span class="badge">SUMO 1.26</span>
  </h1>
  <p class="subtitle">
    Comparação entre Baseline SUMO sem prioridade semafórica, TSP Rule-based e TSP com controlador simulado.
    Desenvolvido no âmbito do Projecto PPS 57 — Programa de Apoio à Densificação e Extensão da Rede de Transporte Público.
  </p>
</div>
""", unsafe_allow_html=True)

# ── empty state ───────────────────────────────────────────────────────────────

if demo is None and baseline_kpis is None:
    st.markdown("""
<div class="empty-wrap">
  <p class="empty-title">Sem dados de simulação disponíveis</p>
  <p class="empty-sub">
    Nenhum report encontrado em <code>reports/</code>.<br>
    Corre os comandos abaixo por ordem para gerar os dados de análise.
  </p>
  <div class="steps">
    <div class="step">
      <div class="step-num">1</div>
      <div class="step-title">Baseline SUMO</div>
      <div class="step-desc">Simulação sem algoritmo TSP activo — ponto de referência</div>
      <span class="step-cmd">make baseline</span>
    </div>
    <div class="step">
      <div class="step-num">2</div>
      <div class="step-title">Run TSP</div>
      <div class="step-desc">Simulação com prioridade semafórica para autocarros</div>
      <span class="step-cmd">make tsp-run</span>
    </div>
    <div class="step">
      <div class="step-num">3</div>
      <div class="step-title">Demonstrador completo</div>
      <div class="step-desc">Compara os três modos lado a lado com report detalhado</div>
      <span class="step-cmd">make tsp-demonstrator</span>
    </div>
    <div class="step">
      <div class="step-num">4</div>
      <div class="step-title">Cenários multi-seed</div>
      <div class="step-desc">Análise estatística com múltiplas seeds de aleatoriedade</div>
      <span class="step-cmd">make scenario-suite</span>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)
    st.stop()

# ── collect run KPIs ──────────────────────────────────────────────────────────

run_kpis: dict[str, dict] = {}
if demo:
    for label, run in demo.get("runs", {}).items():
        if "kpis" in run:
            run_kpis[label] = run["kpis"]
if baseline_kpis and not any("baseline" in k.lower() for k in run_kpis):
    run_kpis["baseline"] = baseline_kpis

# identify baseline run
baseline_key = next((k for k in run_kpis if "baseline" in k.lower()), None)
tsp_keys     = [k for k in run_kpis if k != baseline_key]

# ── vehicle count warning ─────────────────────────────────────────────────────

counts = {k: get_kpi(v, vehicle_cls).get("vehicles", 0) or 0 for k, v in run_kpis.items()}
if len(set(counts.values())) > 1:
    count_str = " vs ".join(f"{k}: {v}" for k, v in counts.items())
    st.markdown(f"""
<div class="warn-box">
  <strong>Aviso metodologico:</strong> as runs nao apresentam o mesmo numero de veiculos concluidos
  ({count_str}). Isto indica durações de simulação distintas — as comparações de KPI devem ser
  interpretadas com cautela. Para resultados válidos, todas as runs devem correr pela mesma duração
  simulada (ex. <code>make tsp-demonstrator</code> sem reduzir <code>--steps</code>).
</div>
""", unsafe_allow_html=True)

# ── verdict banner ────────────────────────────────────────────────────────────

if demo and "verdict" in demo:
    v      = demo["verdict"]
    status = v.get("status", "")
    cls_map = {
        "value_demonstrated":          ("verdict-pass",    "Evidência positiva"),
        "review":                       ("verdict-review",  "Em revisão"),
        "does_not_demonstrate_actuation":("verdict-fail",  "Sem actuação TSP"),
    }
    vcls, vtitle = cls_map.get(status, ("verdict-unknown", "Estado desconhecido"))
    st.markdown(f"""
<div class="{vcls}">
  <p class="verdict-title">{vtitle}</p>
  <p class="verdict-body">{v.get("reason", status)}</p>
</div>
""", unsafe_allow_html=True)

st.markdown("")  # spacing

# ── tabs ──────────────────────────────────────────────────────────────────────

tab_kpi, tab_decisions, tab_cits, tab_rl, tab_scenarios, tab_meta = st.tabs([
    "Comparação de KPIs",
    "Motor de Decisão TSP",
    "Pipeline C-ITS",
    "Baseline vs RL",
    "Cenários",
    "Metodologia",
])

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — KPI comparison
# ═══════════════════════════════════════════════════════════════════════════════

with tab_kpi:

    cls_data = {label: get_kpi(kpis, vehicle_cls) for label, kpis in run_kpis.items()}

    if not cls_data:
        st.info("Sem dados de KPI disponíveis.")
    else:
        # ── impact cards ──────────────────────────────────────────────────────
        st.markdown('<div class="section-label">Indicadores de impacto</div>', unsafe_allow_html=True)

        bdata = cls_data.get(baseline_key, {}) if baseline_key else {}
        cards_html = '<div class="kpi-grid">'
        card_metrics = ["mean_time_loss_s", "mean_waiting_time_s", "mean_duration_s", "p95_time_loss_s"]
        for tsp_k in (tsp_keys or list(cls_data.keys())):
            tdata = cls_data.get(tsp_k, {})
            for m in card_metrics:
                label, unit, _ = KPI_META.get(m, (m, "", ""))
                val = tdata.get(m)
                ds, dcls = delta_str(bdata.get(m), val)
                cards_html += f"""
                <div class="kpi-card">
                  <p class="label">{label}<br><span style="color:#94a3b8;font-weight:400">{tsp_k}</span></p>
                  <p class="value">{fmt(val, unit)}</p>
                  <span class="{dcls}">vs baseline: {ds}</span>
                </div>"""
            break  # one TSP run for cards
        cards_html += "</div>"
        st.markdown(cards_html, unsafe_allow_html=True)

        # ── horizontal bar chart — all runs ───────────────────────────────────
        st.markdown('<div class="section-label">Comparação de métricas entre cenários</div>', unsafe_allow_html=True)

        plot_metrics = ["mean_time_loss_s", "mean_waiting_time_s", "mean_duration_s",
                        "mean_depart_delay_s", "p95_time_loss_s"]
        rows = []
        for metric in plot_metrics:
            mname, unit, _ = KPI_META.get(metric, (metric, "", ""))
            for run_label, data in cls_data.items():
                val = data.get(metric)
                if val is not None:
                    rows.append({"Métrica": mname, "Cenário": run_label, "Valor": val, "Unit": unit})

        if rows:
            df = pd.DataFrame(rows)
            sel_metrics = st.multiselect(
                "Métricas a mostrar",
                options=df["Métrica"].unique().tolist(),
                default=df["Métrica"].unique().tolist()[:3],
            )
            df_plot = df[df["Métrica"].isin(sel_metrics)] if sel_metrics else df

            colors = [run_color(r) for r in df_plot["Cenário"].unique()]
            fig = px.bar(
                df_plot, x="Valor", y="Métrica", color="Cenário",
                barmode="group", orientation="h",
                color_discrete_sequence=colors,
                height=max(300, len(sel_metrics or plot_metrics) * 70 + 80),
            )
            fig.update_traces(texttemplate="%{x:.1f}", textposition="outside")
            chart_layout(fig, "KPIs por cenário (segundos)")
            st.plotly_chart(fig, use_container_width=True)
            st.markdown('<div class="insight">Barras mais curtas representam melhores resultados para todas as métricas de tempo. '
                        'Compare o comprimento da barra <b>baseline</b> (cinzento) com os cenários TSP para quantificar o ganho. '
                        'O selector de métricas acima permite focar nas dimensões mais relevantes.</div>',
                        unsafe_allow_html=True)

        # ── delta waterfall (baseline vs best TSP) ────────────────────────────
        if baseline_key and tsp_keys:
            st.markdown('<div class="section-label">Variação relativa ao baseline (melhor run TSP)</div>',
                        unsafe_allow_html=True)

            best_tsp = tsp_keys[0]
            bkpi     = cls_data.get(baseline_key, {})
            tkpi     = cls_data.get(best_tsp, {})

            wf_rows = []
            for m in ["mean_time_loss_s", "mean_waiting_time_s", "mean_duration_s",
                      "p95_time_loss_s", "mean_depart_delay_s"]:
                bv = bkpi.get(m)
                tv = tkpi.get(m)
                if bv and tv:
                    delta = tv - bv
                    label, unit, _ = KPI_META[m]
                    wf_rows.append({"Métrica": label, "Delta (s)": round(delta, 2),
                                    "Pct": round((delta / bv) * 100, 1)})

            if wf_rows:
                df_wf = pd.DataFrame(wf_rows)
                fig_wf = go.Figure(go.Bar(
                    x=df_wf["Delta (s)"],
                    y=df_wf["Métrica"],
                    orientation="h",
                    text=[f"{r['Pct']:+.1f}%" for _, r in df_wf.iterrows()],
                    textposition="outside",
                    marker_color=["#22c55e" if v < 0 else "#ef4444" for v in df_wf["Delta (s)"]],
                ))
                fig_wf.add_vline(x=0, line_width=2, line_color="#334155")
                chart_layout(fig_wf, f"Ganho absoluto (s) por métrica — {best_tsp} vs {baseline_key}", height=320)
                st.plotly_chart(fig_wf, use_container_width=True)
                st.markdown('<div class="insight">Verde = melhoria (redução do tempo). '
                            'Vermelho = degradação. A linha vertical representa o baseline. '
                            'As percentagens indicam a variação relativa sobre cada métrica.</div>',
                            unsafe_allow_html=True)

        # ── full comparison table ──────────────────────────────────────────────
        if demo:
            st.markdown('<div class="section-label">Tabelas de comparação detalhada</div>',
                        unsafe_allow_html=True)
            comp_map = [
                ("tsp_vs_sumo_baseline_kpis",           f"TSP vs Baseline"),
                ("tsp_controller_vs_sumo_baseline_kpis", f"TSP+Controller vs Baseline"),
                ("tsp_controller_vs_tsp_runtime",        f"TSP+Controller vs TSP"),
            ]
            for key, title in comp_map:
                comp = demo.get("comparisons", {}).get(key, {})
                if not comp.get("available") or not comp.get("rows"):
                    continue
                rows_out = []
                for r in comp["rows"]:
                    mk = r.get("metric", "")
                    lab, unit, desc = KPI_META.get(mk, (mk, "", ""))
                    bv, cv, dv = r.get("baseline"), r.get("candidate") or r.get("tsp_controller") or r.get("tsp"), r.get("delta")
                    p = pct(bv, cv)
                    rows_out.append({
                        "Métrica": lab or mk,
                        "Unidade": unit,
                        "Baseline": fmt(bv),
                        "TSP / Controller": fmt(cv),
                        "Δ absoluto": fmt(dv),
                        "Δ relativo": f"{p:+.1f}%" if p is not None else "—",
                    })
                if rows_out:
                    with st.expander(title, expanded=(key == "tsp_vs_sumo_baseline_kpis")):
                        df_comp = pd.DataFrame(rows_out)

                        def _color_delta(col):
                            styles = []
                            for v in col:
                                try:
                                    f = float(str(v).replace("%","").replace("+",""))
                                    styles.append("color:#15803d;font-weight:600" if f < 0
                                                  else ("color:#dc2626;font-weight:600" if f > 0 else ""))
                                except (ValueError, TypeError):
                                    styles.append("")
                            return styles

                        styled = df_comp.style.apply(_color_delta, subset=["Δ absoluto", "Δ relativo"])
                        st.dataframe(styled, use_container_width=True, hide_index=True)

        # ── P95 vs mean distribution ───────────────────────────────────────────
        st.markdown('<div class="section-label">Distribuição — média vs P95 (perda de tempo)</div>',
                    unsafe_allow_html=True)
        dist_rows = []
        for run_label, data in cls_data.items():
            m = data.get("mean_time_loss_s")
            p95 = data.get("p95_time_loss_s")
            if m is not None:
                dist_rows.append({"Cenário": run_label, "Tipo": "Média",  "Valor (s)": m})
            if p95 is not None:
                dist_rows.append({"Cenário": run_label, "Tipo": "P95", "Valor (s)": p95})
        if dist_rows:
            df_dist = pd.DataFrame(dist_rows)
            colors_dist = [run_color(r) for r in df_dist["Cenário"].unique()]
            fig_dist = px.bar(df_dist, x="Cenário", y="Valor (s)", color="Cenário",
                              facet_col="Tipo", barmode="group",
                              color_discrete_sequence=colors_dist, height=320)
            chart_layout(fig_dist, "Perda de tempo: média e cauda da distribuição (P95)")
            fig_dist.update_layout(showlegend=False)
            st.plotly_chart(fig_dist, use_container_width=True)
            st.markdown('<div class="insight">O P95 representa os 5% de viagens com pior desempenho — '
                        'a cauda da distribuição é relevante para avaliar equidade e experiência no pior caso. '
                        'Um bom algoritmo TSP deve reduzir tanto a média como o P95.</div>',
                        unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — TSP decision engine
# ═══════════════════════════════════════════════════════════════════════════════

with tab_decisions:
    if not demo:
        st.info("Report do demonstrador não disponível.")
    else:
        run_labels = [k for k in demo.get("runs", {}) if k != "sumo_baseline"]
        all_labels = list(demo.get("runs", {}).keys())

        sel_run = st.selectbox(
            "Run TSP",
            options=all_labels,
            index=next((i for i, k in enumerate(all_labels) if k != "sumo_baseline"), 0),
        )
        runtime = demo["runs"][sel_run].get("runtime", {})
        summary = demo["runs"][sel_run].get("summary", {})

        # ── decision pipeline funnel ──────────────────────────────────────────
        st.markdown('<div class="section-label">Pipeline de decisão — funil</div>', unsafe_allow_html=True)

        total    = runtime.get("total_decisions", 0)
        applied  = runtime.get("applied_events", 0)
        blocked  = runtime.get("blocked_by_safety", 0)
        rejected = runtime.get("controller_rejections", 0)
        actuable = total - blocked - rejected

        if total == 0:
            st.markdown('<div class="warn-box">Esta run não gerou decisões TSP. '
                        'Selecciona uma run TSP (ex. <code>tsp</code> ou <code>tsp_controller</code>) '
                        'para ver a análise do motor de decisão.</div>', unsafe_allow_html=True)
        else:
            col_f, col_m = st.columns([1, 1])
            with col_f:
                fig_funnel = go.Figure(go.Funnel(
                    y=["Decisões totais", "Elegíveis (safety OK)", "Aplicadas em rede"],
                    x=[total, actuable, applied],
                    textinfo="value+percent initial",
                    marker_color=["#1d6ef5", "#7c3aed", "#22c55e"],
                ))
                chart_layout(fig_funnel, "Funil de decisão TSP", height=300)
                st.plotly_chart(fig_funnel, use_container_width=True)

            with col_m:
                st.markdown('<div class="section-label">Resumo do motor</div>', unsafe_allow_html=True)
                m1, m2 = st.columns(2)
                m1.metric("Decisões totais",        total)
                m2.metric("Bloqueadas (safety)",    blocked)
                m3, m4 = st.columns(2)
                m3.metric("Rejeitadas (controller)", rejected)
                m4.metric("Aplicadas em rede",       applied)

                apply_rate = f"{applied/total*100:.0f}%" if total else "—"
                block_rate = f"{blocked/total*100:.0f}%" if total else "—"
                st.caption(f"Taxa de aplicação: **{apply_rate}** · Taxa de bloqueio: **{block_rate}**")

            st.markdown('<div class="insight">O funil mostra quantas das decisões geradas pelo TSP '
                        'passaram pela Safety Layer e foram efectivamente aplicadas aos semáforos via TraCI. '
                        'Uma taxa de aplicação baixa indica que os pedidos chegam em janelas não elegíveis '
                        'ou são bloqueados por restrições de segurança.</div>', unsafe_allow_html=True)

        # ── action breakdown ──────────────────────────────────────────────────
        st.markdown('<div class="section-label">Distribuição de acções decididas</div>', unsafe_allow_html=True)
        by_action = runtime.get("by_action", {})

        col_pie, col_legend = st.columns([1, 1])
        with col_pie:
            if by_action:
                labels_a = list(by_action.keys())
                values_a = list(by_action.values())
                colors_a = [ACTION_META.get(k, ("", "#94a3b8", ""))[1] for k in labels_a]
                fig_pie = go.Figure(go.Pie(
                    labels=[ACTION_META.get(k, (k, "", ""))[0] for k in labels_a],
                    values=values_a,
                    marker_colors=colors_a,
                    hole=0.45,
                    textinfo="label+percent",
                    textfont={"size": 11},
                ))
                chart_layout(fig_pie, "Acções do motor TSP", height=320)
                fig_pie.update_layout(showlegend=False)
                st.plotly_chart(fig_pie, use_container_width=True)
            else:
                st.caption("Sem acções registadas.")

        with col_legend:
            st.markdown('<div class="section-label">O que significa cada acção</div>', unsafe_allow_html=True)
            for key, (label, color, desc) in ACTION_META.items():
                count = by_action.get(key)
                count_str = f" ({count})" if count else ""
                st.markdown(
                    f'<div style="display:flex;gap:8px;margin-bottom:10px;align-items:flex-start">'
                    f'<span style="width:10px;height:10px;border-radius:50%;background:{color};'
                    f'flex-shrink:0;margin-top:4px"></span>'
                    f'<div><b style="font-size:0.82rem">{label}{count_str}</b>'
                    f'<br><span style="font-size:0.76rem;color:#64748b">{desc}</span></div></div>',
                    unsafe_allow_html=True,
                )

        # ── safety block reasons ──────────────────────────────────────────────
        safety_reasons = runtime.get("safety_block_by_reason", {})
        st.markdown('<div class="section-label">Bloqueios da Safety Layer por motivo</div>',
                    unsafe_allow_html=True)
        if safety_reasons:
            df_sf = pd.DataFrame({
                "Motivo": list(safety_reasons.keys()),
                "Bloqueios": list(safety_reasons.values()),
            }).sort_values("Bloqueios")
            fig_sf = px.bar(df_sf, x="Bloqueios", y="Motivo", orientation="h",
                            color_discrete_sequence=["#ef4444"], height=max(260, len(df_sf)*50 + 80))
            chart_layout(fig_sf, "Safety Layer — motivos de bloqueio")
            st.plotly_chart(fig_sf, use_container_width=True)
            st.markdown('<div class="insight">A Safety Layer é um componente obrigatório que bloqueia '
                        'qualquer actuação que possa criar conflitos de segurança rodoviária: '
                        'transições de amarelo insuficientes, violação de verde mínimo, '
                        'limite de verde máximo, cooldown entre actuações consecutivas ou '
                        'conflito na sequência de fases.</div>', unsafe_allow_html=True)
        else:
            st.caption("Sem bloqueios de segurança registados nesta run.")

        # ── per-TLS table ─────────────────────────────────────────────────────
        per_tls = runtime.get("per_tls", {})
        if per_tls:
            st.markdown('<div class="section-label">Actividade por semáforo (TLS)</div>',
                        unsafe_allow_html=True)
            tls_rows = [
                {
                    "Semáforo": tid,
                    "Decisões": d.get("decisions", 0),
                    "Aplicadas": d.get("applied_events", 0),
                    "Bloqueadas": d.get("safety_blocks", 0) or d.get("blocked_by_safety", 0),
                    "Taxa aplicação": (
                        f"{d.get('applied_events',0)/d.get('decisions',1)*100:.0f}%"
                        if d.get("decisions", 0) else "—"
                    ),
                }
                for tid, d in per_tls.items()
            ]
            st.dataframe(
                pd.DataFrame(tls_rows).sort_values("Decisões", ascending=False),
                use_container_width=True,
                hide_index=True,
            )

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — C-ITS Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

with tab_cits:
    if not demo:
        st.info("Report do demonstrador não disponível.")
    else:
        tsp_run_keys = [k for k in demo.get("runs", {}) if k != "sumo_baseline"]
        if not tsp_run_keys:
            st.info("Sem runs TSP disponíveis.")
        else:
            sel_cits_run = st.selectbox("Run", tsp_run_keys, key="cits_run")
            summ = demo["runs"][sel_cits_run].get("summary", {})

            # ── message flow ─────────────────────────────────────────────────
            st.markdown('<div class="section-label">Volume de mensagens C-ITS por tipo</div>',
                        unsafe_allow_html=True)
            by_type = summ.get("cits_by_type", {})
            if by_type:
                cits_descs = {
                    "MAPEM": "Informação topológica da rede semafórica",
                    "SPATEM": "Estado em tempo real de cada fase semafórica",
                    "SREM":   "Pedido de prioridade enviado pelo autocarro",
                    "SSEM":   "Resposta do RSU ao pedido de prioridade",
                }
                col_chart, col_desc = st.columns([1, 1])
                with col_chart:
                    df_ct = pd.DataFrame({
                        "Tipo": list(by_type.keys()),
                        "Mensagens": list(by_type.values()),
                    })
                    fig_ct = px.bar(df_ct, x="Tipo", y="Mensagens",
                                    color="Tipo",
                                    color_discrete_sequence=["#1d4ed8", "#0891b2", "#7c3aed", "#059669"],
                                    height=320)
                    fig_ct.update_layout(showlegend=False)
                    chart_layout(fig_ct, "Mensagens por tipo de protocolo C-ITS")
                    st.plotly_chart(fig_ct, use_container_width=True)
                with col_desc:
                    st.markdown('<div class="section-label">Protocolo C-ITS — tipos de mensagem</div>',
                                unsafe_allow_html=True)
                    for mtype, mdesc in cits_descs.items():
                        cnt = by_type.get(mtype, 0)
                        st.markdown(
                            f"**{mtype}** — {cnt:,} mensagens  \n"
                            f'<span style="font-size:0.78rem;color:#64748b">{mdesc}</span>',
                            unsafe_allow_html=True,
                        )
                        st.markdown("")

            # ── message transport health ──────────────────────────────────────
            st.markdown('<div class="section-label">Saúde do transporte de mensagens</div>',
                        unsafe_allow_html=True)
            mt = summ.get("message_transport", {})
            if mt:
                mc1, mc2, mc3, mc4 = st.columns(4)
                mc1.metric("Publicadas",  mt.get("published", "—"))
                mc2.metric("Entregues",   mt.get("delivered", "—"))
                mc3.metric("Perdidas",    mt.get("dropped", "—"))
                mc4.metric("Pendentes",   mt.get("pending", "—"))
                if mt.get("dropped", 0) == 0:
                    st.markdown('<div class="insight">Taxa de entrega: <strong>100%</strong> — '
                                'nenhuma mensagem foi perdida no canal C-ITS simulado.</div>',
                                unsafe_allow_html=True)

            # ── priority request lifecycle ────────────────────────────────────
            st.markdown('<div class="section-label">Ciclo de vida dos pedidos de prioridade (SREM/SSEM)</div>',
                        unsafe_allow_html=True)
            prl = summ.get("priority_request_lifecycle", {})
            if prl:
                lifecycle_data = {
                    "Tracked":  prl.get("tracked_requests", 0),
                    "Granted":  prl.get("granted_requests", 0),
                    "Cleared":  prl.get("cleared_requests", 0),
                    "Expired":  prl.get("expired_requests", 0),
                }
                df_prl = pd.DataFrame({
                    "Estado": list(lifecycle_data.keys()),
                    "Pedidos": list(lifecycle_data.values()),
                })
                colors_prl = ["#1d6ef5", "#22c55e", "#94a3b8", "#ef4444"]
                fig_prl = px.bar(df_prl, x="Estado", y="Pedidos",
                                 color="Estado", color_discrete_sequence=colors_prl,
                                 height=300)
                fig_prl.update_layout(showlegend=False)
                chart_layout(fig_prl, "Pedidos de prioridade — estados no ciclo de vida")
                st.plotly_chart(fig_prl, use_container_width=True)
                st.markdown('<div class="insight">'
                            '<strong>Granted</strong> = pedido aceite e prioridade concedida. '
                            '<strong>Cleared</strong> = pedido concluído (autocarro passou). '
                            '<strong>Expired</strong> = timeout sem concessão. '
                            'O rácio Granted/Tracked indica a taxa de sucesso do algoritmo TSP.</div>',
                            unsafe_allow_html=True)

            # ── green compensation ────────────────────────────────────────────
            gc = summ.get("green_compensation", {})
            if gc.get("enabled"):
                st.markdown('<div class="section-label">Compensação de verde (equidade)</div>',
                            unsafe_allow_html=True)
                gc1, gc2, gc3 = st.columns(3)
                gc1.metric("Eventos de compensação",     gc.get("events", 0))
                gc2.metric("Verde concedido total (s)",  fmt(gc.get("granted_s_total")))
                gc3.metric("Verde recuperado total (s)", fmt(gc.get("reclaimed_s_total")))
                st.markdown('<div class="insight">A compensação de verde garante que o tempo '
                            'de verde "roubado" a outras fases para dar prioridade ao autocarro '
                            'é devolvido nos ciclos seguintes, mantendo a equidade semafórica.</div>',
                            unsafe_allow_html=True)

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Baseline vs RL
# ═══════════════════════════════════════════════════════════════════════════════

with tab_rl:
    if not rl_comparison:
        st.markdown('<div class="warn-box">Report de comparação Baseline vs RL não disponível. '
                    'Corre <code>make compare-tsp-rl</code> para gerar este relatório.</div>',
                    unsafe_allow_html=True)
    else:
        rc1, rc2, rc3 = st.columns(3)
        rc1.metric("Decisões comparadas",  rl_comparison.get("matched_decision_count", "—"))
        rc2.metric("Veredicto de rede",    rl_comparison.get("network_impact_verdict", "—"))
        rc3.metric("Tipo de avaliação",    rl_comparison.get("evaluation", "—").replace("_", " "))

        st.markdown('<div class="section-label">Distribuição de veredictos por decisão</div>',
                    unsafe_allow_html=True)
        vc = rl_comparison.get("verdict_counts", {})
        if vc:
            df_vc = pd.DataFrame({"Veredicto": list(vc.keys()), "Contagem": list(vc.values())})
            fig_vc = px.bar(df_vc, x="Veredicto", y="Contagem", color="Veredicto",
                            color_discrete_sequence=["#22c55e", "#ef4444", "#94a3b8", "#f59e0b"],
                            height=320)
            fig_vc.update_layout(showlegend=False)
            chart_layout(fig_vc, "Veredictos da política RL vs baseline rule-based")
            st.plotly_chart(fig_vc, use_container_width=True)
            st.markdown('<div class="insight">Cada decisão foi avaliada comparando a acção escolhida '
                        'pela política RL com a acção da política rule-based. Um veredicto positivo '
                        'indica que a RL escolheu uma acção com melhor valor estimado de recompensa.</div>',
                        unsafe_allow_html=True)

        kpi_eval = rl_comparison.get("kpi_evaluation", {})
        if kpi_eval.get("available") and kpi_eval.get("rows"):
            st.markdown('<div class="section-label">KPIs — Baseline vs RL</div>', unsafe_allow_html=True)
            rl_rows = []
            for r in kpi_eval["rows"]:
                mk = r.get("metric", "")
                lab, unit, _ = KPI_META.get(mk, (mk, "", ""))
                bv, rv = r.get("baseline"), r.get("rl")
                p = pct(bv, rv)
                rl_rows.append({
                    "Métrica": lab or mk,
                    "Baseline": fmt(bv),
                    "RL": fmt(rv),
                    "Delta (s)": fmt(r.get("delta")),
                    "Delta (%)": f"{p:+.1f}%" if p is not None else "—",
                })
            st.dataframe(pd.DataFrame(rl_rows), use_container_width=True, hide_index=True)

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 — Scenarios
# ═══════════════════════════════════════════════════════════════════════════════

with tab_scenarios:
    scenario_dir = REPORTS / "scenarios"
    has_scenarios = scenario_dir.exists() and any(scenario_dir.iterdir())
    if not has_scenarios:
        st.markdown('<div class="warn-box">Sem resultados de cenários multi-seed. '
                    'Corre <code>make scenario-suite</code> para gerar runs com múltiplas seeds '
                    'e análise estatística com intervalos de confiança.</div>', unsafe_allow_html=True)
    else:
        scenarios = sorted(p.name for p in scenario_dir.iterdir() if p.is_dir())
        col_s1, col_s2 = st.columns([1, 2])
        with col_s1:
            sel_scen = st.selectbox("Cenário", scenarios)
        scen_path = scenario_dir / sel_scen
        run_types = sorted(p.name for p in scen_path.iterdir() if p.is_dir())

        if not run_types:
            st.info("Sem run types neste cenário.")
        else:
            all_rows = []
            for rt in run_types:
                for seed_dir in (scen_path / rt).iterdir():
                    kpis = load_json(seed_dir / "kpis.json")
                    if kpis:
                        data = get_kpi(kpis, vehicle_cls)
                        for m, (lab, unit, _) in KPI_META.items():
                            val = data.get(m)
                            if val is not None:
                                all_rows.append({"Run type": rt, "Seed": seed_dir.name,
                                                 "Métrica": lab, "Valor": val})

            if all_rows:
                df_sc = pd.DataFrame(all_rows)
                with col_s2:
                    sel_metric = st.selectbox("Métrica", df_sc["Métrica"].unique().tolist())
                df_plot = df_sc[df_sc["Métrica"] == sel_metric]

                st.markdown('<div class="section-label">Distribuição por seed — boxplot</div>',
                            unsafe_allow_html=True)
                fig_box = px.box(df_plot, x="Run type", y="Valor", color="Run type",
                                 points="all", height=400,
                                 color_discrete_sequence=list(PALETTE.values()))
                fig_box.update_layout(showlegend=False)
                chart_layout(fig_box, f"{sel_metric} — {sel_scen} ({vehicle_cls_label})")
                st.plotly_chart(fig_box, use_container_width=True)
                st.markdown('<div class="insight">Cada ponto representa uma seed de aleatoriedade diferente. '
                            'A caixa mostra Q1–Q3; a linha central é a mediana. '
                            'Boxplots com pouca sobreposição entre cenários indicam diferença estatisticamente '
                            'significativa.</div>', unsafe_allow_html=True)

                st.markdown('<div class="section-label">Estatísticas descritivas</div>',
                            unsafe_allow_html=True)
                summary = (df_plot.groupby("Run type")["Valor"]
                           .agg(["mean", "std", "min", "max", "count"])
                           .rename(columns={"mean": "Média", "std": "Desvio-padrão",
                                            "min": "Mín", "max": "Máx", "count": "Seeds"})
                           .round(2))
                st.dataframe(summary, use_container_width=True)

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 6 — Metodologia
# ═══════════════════════════════════════════════════════════════════════════════

with tab_meta:
    st.markdown('<div class="section-label">Configuração da simulação</div>', unsafe_allow_html=True)

    if demo:
        sel_run_meta = st.selectbox("Run", list(demo.get("runs", {}).keys()), key="meta_run")
        summ_m = demo["runs"][sel_run_meta].get("summary", {})

        col_a, col_b = st.columns(2)
        with col_a:
            sim_params = {
                "Modo":             summ_m.get("mode", "—"),
                "Passos (steps)":   summ_m.get("steps", "—"),
                "Cenário":          summ_m.get("scenario_id", "—"),
                "Política runtime": summ_m.get("policy_mode", "—"),
                "Actuação activa":  str(summ_m.get("actuation_enabled", "—")),
                "Runtime policy carregada": str(summ_m.get("runtime_policy_loaded", "—")),
            }
            df_params = pd.DataFrame({"Parâmetro": list(sim_params.keys()),
                                      "Valor": list(sim_params.values())})
            st.dataframe(df_params, use_container_width=True, hide_index=True)

        with col_b:
            sp_ver = summ_m.get("signal_program_verification", {})
            st.markdown("**Verificação do programa semafórico**")
            problems = sp_ver.get("problems", [])
            if not problems:
                st.success("Sem problemas no programa semafórico.")
            else:
                for p in problems:
                    st.error(p)

            if summ_m.get("actuation_downgraded") or sp_ver.get("actuation_downgraded"):
                st.warning("Actuação foi downgraded para modo seguro.")

    st.markdown('<div class="section-label">Limitações conhecidas</div>', unsafe_allow_html=True)
    limitations = demo.get("limitations", []) if demo else []
    standard_limits = [
        "Os KPIs são calculados apenas sobre veículos que completaram a viagem durante a janela simulada. "
        "Runs com durações distintas produzem amostras de populações diferentes e não são directamente comparáveis.",
        "A simulação usa um modelo de tráfego microscópico (SUMO) calibrado com dados de rede, mas não com "
        "contagens de tráfego reais do CMP/IMT — os valores absolutos devem ser interpretados como "
        "indicativos, não como previsões operacionais.",
        "A Safety Layer não foi exercida neste teste (0 bloqueios). Os caminhos de segurança são cobertos "
        "por testes unitários mas requerem cenários de stress para aparecer em evidência de runtime.",
        "Autocarros (Linha 25) requerem duração de simulação suficiente para entrar na rede. "
        "Runs com menos de 3600 steps podem não incluir nenhuma viagem de autocarro completa.",
    ]
    for lim in (limitations + standard_limits):
        st.markdown(f"- {lim}")

    st.markdown('<div class="section-label">Fontes de dados</div>', unsafe_allow_html=True)
    data_policy = demo.get("data_policy", {}) if demo else {}
    dp_rows = [
        {"Campo": "Fonte operacional", "Valor": data_policy.get("operational_data_source", "—")},
        {"Campo": "Dados sintéticos", "Valor": str(data_policy.get("synthetic_operational_data", "—"))},
        {"Campo": "Rede viária", "Valor": "sumo/plain/corredor.{nod,edg}.xml — geometria manual da Boavista"},
        {"Campo": "Paragens", "Valor": "sumo/additional/bus_stops.add.xml"},
        {"Campo": "Rotas", "Valor": "sumo/routes/routes.rou.xml — geradas por randomTrips com semente controlada"},
    ]
    st.dataframe(pd.DataFrame(dp_rows), use_container_width=True, hide_index=True)

    if demo:
        st.markdown('<div class="section-label">Caminhos de evidência</div>', unsafe_allow_html=True)
        with st.expander("Ver caminhos dos artefactos gerados"):
            ev = demo.get("evidence_paths", {})
            ev_rows = []
            for run_name, paths in ev.items():
                for atype, path in paths.items():
                    if atype != "root":
                        ev_rows.append({"Run": run_name, "Artefacto": atype, "Path": path})
            if ev_rows:
                st.dataframe(pd.DataFrame(ev_rows), use_container_width=True, hide_index=True)
