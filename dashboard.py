"""PPS57 · TSP Simulation Analysis Dashboard.

Run with: streamlit run dashboard.py
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import subprocess
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
    "mean_time_loss_s":    ("Perda de tempo média",     "s", "Tempo perdido face à velocidade ideal de rede. Indicador principal de eficiência. Menor é melhor."),
    "mean_waiting_time_s": ("Tempo de espera médio",    "s", "Tempo parado em fila ou semáforo vermelho. Menor é melhor."),
    "mean_duration_s":     ("Duração média de viagem",  "s", "Tempo total de trajecto, porta a porta. Menor é melhor."),
    "p95_time_loss_s":     ("Perda de tempo P95",       "s", "Percentil 95 — descreve o pior cenário para 95% dos veículos. Menor é melhor."),
    "mean_speed_mps":      ("Velocidade média",         "m/s","Velocidade média ao longo do trajecto. Maior é melhor."),
    "mean_depart_delay_s": ("Atraso de partida médio",  "s", "Tempo de espera antes de entrar na rede. Menor é melhor."),
    "mean_stop_count":     ("Paragens médias",          "",  "Número médio de paragens por veículo. Menor é melhor."),
}

# metrics where an increase is an improvement (drives delta colouring)
HIGHER_IS_BETTER = {"mean_speed_mps"}

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

# semantic colours (consistent across the whole dashboard)
COLOR_GOOD = "#16a34a"   # improvement
COLOR_BAD  = "#dc2626"   # degradation / cost
COLOR_EMERGENCY = "#dc2626"

# ── CSS ───────────────────────────────────────────────────────────────────────

CSS = """
<style>
html, body, [class*="css"] { font-family: "Inter", "Segoe UI", system-ui, sans-serif; }

/* page header */
.page-header { border-bottom: 3px solid #1d6ef5; padding-bottom: 12px; margin-bottom: 6px; }
.page-header h1 { font-size: 1.55rem; font-weight: 700; color: #0f172a; margin: 0 0 2px; letter-spacing: -0.4px; }
.page-header .subtitle { font-size: 0.82rem; color: #64748b; margin: 0; }
.badge { display:inline-block; background:#eff6ff; border:1px solid #bfdbfe; color:#1d4ed8;
         font-size:0.72rem; font-weight:600; padding:2px 8px; border-radius:4px;
         margin-left:8px; vertical-align:middle; }
.freshness { font-size:0.74rem; color:#94a3b8; margin:6px 0 0; }

/* sidebar */
.sb-label { font-size:0.68rem; font-weight:700; text-transform:uppercase;
            letter-spacing:.08em; color:#94a3b8; margin:16px 0 6px; }
.sb-project { font-weight:700; font-size:0.95rem; color:#0f172a; }
.sb-sub { font-size:0.75rem; color:#64748b; }
.file-row { display:flex; align-items:center; gap:7px; font-size:0.75rem; color:#374151; padding:3px 0; }
.dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
.dot-ok  { background:#22c55e; }
.dot-off { background:#cbd5e1; }
.dot-label { font-family: monospace; color:#475569; }

/* verdict banner */
.verdict-pass    { background:#f0fdf4; border-left:4px solid #22c55e; padding:12px 16px; border-radius:0 8px 8px 0; }
.verdict-review  { background:#fffbeb; border-left:4px solid #f59e0b; padding:12px 16px; border-radius:0 8px 8px 0; }
.verdict-fail    { background:#fef2f2; border-left:4px solid #ef4444; padding:12px 16px; border-radius:0 8px 8px 0; }
.verdict-unknown { background:#f8fafc; border-left:4px solid #94a3b8; padding:12px 16px; border-radius:0 8px 8px 0; }
.verdict-title { font-weight:700; font-size:0.85rem; margin:0 0 2px; }
.verdict-body  { font-size:0.82rem; color:#374151; margin:0; }

/* section label */
.section-label { font-size:0.7rem; font-weight:700; text-transform:uppercase; letter-spacing:.1em;
                 color:#94a3b8; margin:26px 0 8px; border-bottom:1px solid #f1f5f9; padding-bottom:4px; }

/* insight + warning boxes */
.insight { background:#f0f9ff; border:1px solid #bae6fd; border-radius:8px; padding:10px 14px;
           font-size:0.8rem; color:#0c4a6e; margin:8px 0 16px; line-height:1.5; }
.insight strong { color:#0369a1; }
.warn-box { background:#fffbeb; border:1px solid #fde68a; border-radius:8px; padding:10px 14px;
            font-size:0.8rem; color:#78350f; margin:8px 0 16px; line-height:1.5; }

/* empty state */
.empty-wrap { max-width:680px; margin:60px auto; text-align:center; }
.empty-title { font-size:1.2rem; font-weight:700; color:#0f172a; margin:0 0 6px; }
.empty-sub   { font-size:0.88rem; color:#64748b; margin:0 0 36px; line-height:1.6; }
.steps { display:flex; gap:14px; flex-wrap:wrap; justify-content:center; }
.step  { background:#fff; border:1.5px solid #e2e8f0; border-radius:10px; padding:18px 20px; width:175px; text-align:left; }
.step:hover { border-color:#1d6ef5; }
.step-num { display:inline-flex; align-items:center; justify-content:center; width:24px; height:24px;
            border-radius:50%; background:#1d4ed8; color:#fff; font-size:0.75rem; font-weight:700; margin-bottom:8px; }
.step-title { font-weight:600; font-size:0.85rem; color:#0f172a; margin:0 0 4px; }
.step-desc  { font-size:0.76rem; color:#64748b; margin:0 0 8px; line-height:1.4; }
.step-cmd { font-family:monospace; font-size:0.74rem; background:#f1f5f9; border:1px solid #e2e8f0;
            border-radius:5px; padding:2px 7px; color:#1d4ed8; }

/* tighten metric cards */
[data-testid="stMetricValue"] { font-size:1.45rem !important; font-weight:700; }
[data-testid="stMetricLabel"] { font-size:0.78rem; }
</style>
"""

# ── helpers ───────────────────────────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def _read_json(path_str: str, mtime: float) -> dict | None:
    # `mtime` participates in the cache key so the entry invalidates whenever the
    # file changes. It must NOT be named with a leading underscore — Streamlit
    # excludes underscore-prefixed args from the cache key.
    try:
        return json.loads(Path(path_str).read_text())
    except (json.JSONDecodeError, OSError):
        return None


def load_json(path: Path) -> dict | None:
    """Cached JSON loader — invalidates automatically when the file changes."""
    if not path.exists():
        return None
    return _read_json(str(path), path.stat().st_mtime)


def file_mtime(path: Path) -> str | None:
    if path.exists():
        return _dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
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


def get_kpi(data: dict, cls: str) -> dict:
    return data.get(cls, {})


def run_color(label: str) -> str:
    # most-specific (longest) key first so "tsp_controller" keeps its own colour.
    for k in sorted(PALETTE, key=len, reverse=True):
        if k in label.lower():
            return PALETTE[k]
    return "#94a3b8"


def section(title: str) -> None:
    st.markdown(f'<div class="section-label">{title}</div>', unsafe_allow_html=True)


def insight(text: str) -> None:
    st.markdown(f'<div class="insight">{text}</div>', unsafe_allow_html=True)


def warn(text: str) -> None:
    st.markdown(f'<div class="warn-box">{text}</div>', unsafe_allow_html=True)


def render_kpi_metric(col, metric_key: str, value, baseline_val=None) -> None:
    """Native st.metric with accessible delta (arrow + colour) and help tooltip."""
    label, unit, desc = KPI_META.get(metric_key, (metric_key, "", ""))
    delta = None
    if value is not None and baseline_val not in (None, 0):
        dabs = value - baseline_val
        p = pct(baseline_val, value)
        delta = f"{dabs:+.1f} {unit}".strip()
        if p is not None:
            delta += f"  ({p:+.1f}%)"
    col.metric(
        label=label,
        value=fmt(value, unit),
        delta=delta,
        delta_color=("normal" if metric_key in HIGHER_IS_BETTER else "inverse"),
        help=desc,
        border=True,
    )


def download_csv(df: pd.DataFrame, filename: str, key: str, label: str = "Exportar CSV") -> None:
    st.download_button(
        label, df.to_csv(index=False).encode("utf-8"),
        file_name=filename, mime="text/csv", key=key,
    )


def chart_layout(fig: go.Figure, title: str = "", height: int = 380) -> go.Figure:
    fig.update_layout(
        title={"text": title, "font": {"size": 13, "color": "#0f172a", "family": "Inter, system-ui"}, "x": 0, "pad": {"b": 8}},
        paper_bgcolor="white",
        plot_bgcolor="#f8fafc",
        font={"family": "Inter, system-ui, sans-serif", "color": "#374151", "size": 11},
        legend={"bgcolor": "rgba(0,0,0,0)", "borderwidth": 0, "font": {"size": 11}},
        margin={"t": 44, "b": 36, "l": 8, "r": 8},
        height=height,
        hoverlabel={"font_size": 12, "font_family": "Inter, system-ui"},
    )
    fig.update_xaxes(gridcolor="#f1f5f9", linecolor="#e2e8f0", tickfont={"size": 11})
    fig.update_yaxes(gridcolor="#f1f5f9", linecolor="#e2e8f0", tickfont={"size": 11})
    return fig


# ── simulation control helpers ────────────────────────────────────────────────
# NOTE: SUMO file arguments are passed as RELATIVE paths with cwd=ROOT because the
# repo dir name "PPS57---ROUT25" contains "---", which breaks SUMO when it appears
# inside absolute paths handed to the CLI tools.

VENV_BIN = ROOT / ".venv" / "bin"


def _bin(name: str) -> str:
    p = VENV_BIN / name
    return str(p) if p.exists() else name


try:
    import sumo as _sumo_pkg
    _SUMO_HOME = os.path.dirname(_sumo_pkg.__file__)
except Exception:
    _SUMO_HOME = os.environ.get("SUMO_HOME", "")


def _sim_env() -> dict:
    env = dict(os.environ)
    if _SUMO_HOME:
        env["SUMO_HOME"] = _SUMO_HOME
    env["PATH"] = str(VENV_BIN) + os.pathsep + env.get("PATH", "")
    return env


BUILD_CMD = [_bin("python"), "src/pps57_sumo/build_network.py",
             "--config", "configs/sumo_scenario_base.json", "--base-dir", "sumo"]


def _launch_detached(cmd: list[str], success_msg: str) -> None:
    """Start a long-lived / GUI process without blocking Streamlit."""
    try:
        subprocess.Popen(cmd, cwd=str(ROOT), env=_sim_env(),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        st.success(success_msg)
    except FileNotFoundError as exc:
        st.error(f"Binário não encontrado: {exc}")


def _run_streaming(commands: list[tuple[str, list[str]]], label: str) -> bool:
    """Run commands sequentially, streaming combined output into st.status."""
    ok = True
    with st.status(label, expanded=True) as status:
        log = st.empty()
        lines: list[str] = []
        for desc, cmd in commands:
            lines.append(f"$ {' '.join(cmd)}")
            log.code("\n".join(lines[-30:]))
            try:
                proc = subprocess.Popen(cmd, cwd=str(ROOT), env=_sim_env(),
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, bufsize=1)
            except FileNotFoundError as exc:
                status.update(label=f"Binário não encontrado: {exc}", state="error")
                return False
            for line in proc.stdout:
                lines.append(line.rstrip())
                log.code("\n".join(lines[-30:]))
            proc.wait()
            if proc.returncode != 0:
                status.update(label=f"Erro em '{desc}' (código {proc.returncode})", state="error")
                ok = False
                break
        if ok:
            status.update(label="Concluído com sucesso", state="complete")
    return ok


def render_simulation_panel() -> None:
    section("Visualização em tempo real (SUMO-GUI)")
    st.caption("Abre uma janela nativa do SUMO no computador onde esta dashboard corre. "
               "Carrega no botão ▶ dentro do SUMO para iniciar a simulação.")
    gc1, gc2 = st.columns(2)
    with gc1:
        if st.button("Abrir SUMO-GUI · Baseline", use_container_width=True):
            if _run_streaming([("build", BUILD_CMD)], "A construir a rede"):
                _launch_detached([_bin("sumo-gui"), "-c", "sumo/corredor.sumocfg"],
                                 "Janela do SUMO (baseline) a abrir no ambiente de trabalho.")
    with gc2:
        if st.button("Abrir SUMO-GUI · TSP", use_container_width=True):
            if _run_streaming([("build", BUILD_CMD)], "A construir a rede"):
                _launch_detached(
                    [_bin("python"), "scripts/run_tsp_control.py", "--mode", "sumo",
                     "--gui", "--steps", "7200"],
                    "Simulação TSP visual a abrir no SUMO-GUI.")

    section("Gerar dados de análise (headless)")
    steps = st.slider("Passos de simulação (TraCI steps)", min_value=200, max_value=14400,
                      value=1200, step=200,
                      help="Mais passos = simulação mais longa e realista. Autocarros da Linha 25 "
                           "precisam de ≥3600 passos para entrar na rede. 2 passos ≈ 1 segundo simulado.")
    hc1, hc2, hc3 = st.columns(3)
    triggered: list[tuple[str, list[str]]] | None = None
    with hc1:
        if st.button("Correr demonstrador TSP", use_container_width=True, type="primary"):
            triggered = [
                ("build", BUILD_CMD),
                ("demonstrador", [_bin("python"), "scripts/run_tsp_demonstrator.py", "--steps", str(steps)]),
            ]
    with hc2:
        if st.button("Comparação Baseline vs RL", use_container_width=True):
            triggered = [
                ("build", BUILD_CMD),
                ("compare-rl", [_bin("python"), "scripts/compare_tsp_baseline_rl.py",
                                "--steps", str(steps), "--train-rl"]),
            ]
    with hc3:
        if st.button("Cenários multi-seed", use_container_width=True):
            triggered = [
                ("scenario-suite", [_bin("python"), "scripts/run_sumo_scenario.py",
                                    "--all", "--run-type", "baseline"]),
            ]
    st.caption("As simulações headless regeneram os reports e a dashboard recarrega automaticamente no fim. "
               "A janela fica bloqueada durante a execução — acompanha o progresso no log.")

    if triggered:
        if _run_streaming(triggered, "A correr simulação"):
            st.cache_data.clear()
            st.success("Dados actualizados. A recarregar a dashboard...")
            st.rerun()

    with st.expander("Requisitos e diagnóstico"):
        gui_ok = (VENV_BIN / "sumo-gui").exists() or shutil.which("sumo-gui")
        net_ok = (ROOT / "sumo" / "network" / "corredor.net.xml").exists()
        st.markdown(f"- **sumo-gui**: {'encontrado' if gui_ok else 'NÃO encontrado'}")
        st.markdown(f"- **SUMO_HOME**: `{_SUMO_HOME or 'não definido'}`")
        st.markdown(f"- **Rede construída**: {'sim' if net_ok else 'não — corre um build/demonstrador primeiro'}")
        st.markdown("- A visualização SUMO-GUI só funciona com a dashboard a correr **localmente** "
                    "(a janela abre no ecrã desta máquina, não num servidor remoto).")


# ── page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="PPS57 — TSP Analysis",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        "about": "PPS57 · ROUT25 — Dashboard de análise de Traffic Signal Priority (TSP) "
                 "para a Linha 25 do Porto. Compara Baseline SUMO, TSP Rule-based e TSP+RL.",
    },
)
st.markdown(CSS, unsafe_allow_html=True)

# ── load data ─────────────────────────────────────────────────────────────────

demo          = load_json(REPORTS / "tsp_demonstrator_report.json")
baseline_kpis = load_json(REPORTS / "baseline_kpis.json")
rl_comparison = load_json(REPORTS / "tsp_baseline_vs_rl_comparison.json")

# ── collect run KPIs (needed before the sidebar to annotate class counts) ──────

run_kpis: dict[str, dict] = {}
if demo:
    for label, run in demo.get("runs", {}).items():
        if "kpis" in run:
            run_kpis[label] = run["kpis"]
if baseline_kpis and not any("baseline" in k.lower() for k in run_kpis):
    run_kpis["baseline"] = baseline_kpis

baseline_key = next((k for k in run_kpis if "baseline" in k.lower()), None)
tsp_keys     = [k for k in run_kpis if k != baseline_key]
primary_tsp  = tsp_keys[0] if tsp_keys else None


def class_vehicle_count(cls_key: str) -> int:
    """Max vehicles of a class across all runs (0 if the class never appears)."""
    return max((kp.get(cls_key, {}).get("vehicles", 0) or 0 for kp in run_kpis.values()),
               default=0)


# ── sidebar ───────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown('<div class="sb-project">PPS57 · ROUT25</div>', unsafe_allow_html=True)
    st.markdown('<div class="sb-sub">Traffic Signal Priority — Linha 25, Porto</div>', unsafe_allow_html=True)

    st.markdown('<div class="sb-label">Filtro global</div>', unsafe_allow_html=True)
    # annotate each class with its vehicle count so empty/overlapping classes
    # are obvious (e.g. "Veículos de emergência (0)").
    cls_counts = {key: class_vehicle_count(key) for key, _ in VEHICLE_CLASSES}
    cls_label_map = {f"{label} ({cls_counts[key]})": key for key, label in VEHICLE_CLASSES}
    sel_display = st.selectbox(
        "Classe de veículo",
        options=list(cls_label_map.keys()),
        index=0,
        key="veh_cls_select",
        help="Filtra todos os KPIs e gráficos por classe. O número é a contagem de veículos. "
             "Taxonomia: Prioritários = Autocarros + Emergência (a união); "
             "Tráfego geral = todos os não-prioritários. As classes podem sobrepor-se.",
    )
    vehicle_cls = cls_label_map[sel_display]
    vehicle_cls_label = next(l for k, l in VEHICLE_CLASSES if k == vehicle_cls)

    if vehicle_cls == "priority_vehicles" and cls_counts.get("emergency_vehicles", 0) == 0:
        st.caption("Sem veículos de emergência nesta simulação, por isso "
                   "**Prioritários = Autocarros**.")
    elif vehicle_cls == "emergency_vehicles" and cls_counts.get("emergency_vehicles", 0) == 0:
        st.caption("Sem veículos de emergência nesta simulação — métricas vazias.")

    st.markdown('<div class="sb-label">Reports detectados</div>', unsafe_allow_html=True)
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

    st.markdown('<div class="sb-label">Documentação</div>', unsafe_allow_html=True)
    with st.expander("Glossário de métricas"):
        for _, (label, unit, desc) in KPI_META.items():
            unit_s = f" ({unit})" if unit else ""
            st.markdown(f"**{label}**{unit_s}  \n{desc}")
    with st.expander("Como ler esta dashboard"):
        st.markdown(
            "- **Resultado principal**: leitura rápida do ganho do TSP vs baseline.\n"
            "- **Comparação de KPIs**: escolhe dois cenários e compara em detalhe.\n"
            "- **Motor de Decisão**: o que o algoritmo decidiu e porquê.\n"
            "- **Pipeline C-ITS**: comunicação V2X autocarro↔semáforo.\n"
            "- As setas ▲▼ e as cores indicam melhoria (verde) ou degradação (vermelho)."
        )

# ── page header ───────────────────────────────────────────────────────────────

fresh = file_mtime(REPORTS / "tsp_demonstrator_report.json") or file_mtime(REPORTS / "baseline_kpis.json")
scenario_id = ""
if demo:
    for _r in demo.get("runs", {}).values():
        scenario_id = _r.get("summary", {}).get("scenario_id", "")
        if scenario_id:
            break

st.markdown(f"""
<div class="page-header">
  <h1>PPS57 · Análise de Simulação TSP
    <span class="badge">Linha 25 · Porto</span>
    <span class="badge">SUMO 1.26</span>
  </h1>
  <p class="subtitle">
    Comparação entre Baseline SUMO sem prioridade semafórica, TSP Rule-based e TSP com controlador simulado.
    Projecto PPS 57 — Programa de Apoio à Densificação e Extensão da Rede de Transporte Público.
  </p>
</div>
<p class="freshness">
  {"Última actualização dos dados: <strong>" + fresh + "</strong>" if fresh else "Sem dados carregados"}
  {(" · Cenário: <code>" + scenario_id + "</code>") if scenario_id else ""}
</p>
""", unsafe_allow_html=True)

# ── empty state ───────────────────────────────────────────────────────────────

if demo is None and baseline_kpis is None:
    st.markdown("""
<div class="empty-wrap" style="margin-bottom:24px">
  <p class="empty-title">Sem dados de simulação disponíveis</p>
  <p class="empty-sub">
    Nenhum report encontrado em <code>reports/</code>.<br>
    Usa o painel abaixo para gerar os dados — ou corre <code>make tsp-demonstrator</code> no terminal.
  </p>
</div>
""", unsafe_allow_html=True)
    render_simulation_panel()
    st.stop()

# ── per-class data for the selected vehicle filter ────────────────────────────

cls_data = {label: get_kpi(kpis, vehicle_cls) for label, kpis in run_kpis.items()}

# ── vehicle count warning ─────────────────────────────────────────────────────
# Fire only on a MEANINGFUL spread (>10%). Signal-timing changes naturally cause
# small throughput differences between runs of equal duration — that is valid,
# not a methodological problem. A large spread signals mismatched durations.

counts = {k: get_kpi(v, "all_vehicles").get("vehicles", 0) or 0 for k, v in run_kpis.items()}
nonzero = [c for c in counts.values() if c]
if nonzero and (max(nonzero) - min(nonzero)) / max(nonzero) > 0.10:
    count_str = " · ".join(f"{k}: {v}" for k, v in counts.items())
    warn(
        "<strong>Aviso metodológico:</strong> as runs diferem significativamente no número de "
        f"veículos concluídos ({count_str}). Isto sugere durações de simulação distintas — as "
        "comparações de KPI devem ser interpretadas com cautela. Para resultados válidos, todas as "
        "runs devem cobrir a mesma duração simulada (lembrar: o baseline usa <code>--end</code> em "
        "segundos enquanto o TSP/TraCI usa passos com <code>step-length=0.5</code>, pelo que "
        "<code>--steps N</code> = N/2 segundos)."
    )

# ── verdict status map (used by the Resumo tab) ───────────────────────────────

VERDICT_MAP = {
    "value_demonstrated":              ("verdict-pass",   "Evidência positiva"),
    "passes_primary_demonstrator_goal":("verdict-pass",   "Objectivo primário demonstrado"),
    "passes_with_general_traffic_cost":("verdict-review",  "Ganho no transporte público com custo no tráfego geral"),
    "review":                          ("verdict-review",  "Em revisão"),
    "inconclusive_missing_bus_kpi":    ("verdict-unknown", "Inconclusivo — KPI de autocarros em falta"),
    "does_not_demonstrate_actuation":  ("verdict-fail",    "Sem actuação TSP"),
}

# ── tabs ──────────────────────────────────────────────────────────────────────

tab_resumo, tab_kpi, tab_decisions, tab_cits, tab_rl, tab_scenarios, tab_meta, tab_sim = st.tabs([
    "Resumo",
    "KPIs",
    "Motor de Decisão",
    "C-ITS",
    "Baseline vs RL",
    "Cenários",
    "Metodologia",
    "Simulação",
])

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 0 — Resumo (narrativa guiada: a resposta primeiro)
# ═══════════════════════════════════════════════════════════════════════════════

HERO_CLASSES = [
    ("emergency_vehicles", "Emergência"),
    ("buses",              "Autocarros"),
    ("priority_vehicles",  "Prioritários"),
    ("general_traffic",    "Tráfego geral"),
    ("all_vehicles",       "Todos os veículos"),
]

with tab_resumo:
    # ── verdict ───────────────────────────────────────────────────────────────
    if demo and "verdict" in demo:
        v = demo["verdict"]
        vcls, vtitle = VERDICT_MAP.get(v.get("status", ""), ("verdict-unknown", "Estado desconhecido"))
        st.markdown(
            f'<div class="{vcls}"><p class="verdict-title">Veredicto · {vtitle}</p>'
            f'<p class="verdict-body">{v.get("reason", v.get("status", ""))}</p></div>',
            unsafe_allow_html=True,
        )

    if not (baseline_key and primary_tsp):
        st.info("Sem um par baseline + TSP para resumir. Corre o demonstrador no separador Simulação.")
    else:
        bk = run_kpis[baseline_key]
        tk = run_kpis[primary_tsp]

        # per-class time-loss delta for the hero chart
        hero = []
        for key, label in HERO_CLASSES:
            bv = bk.get(key, {}).get("mean_time_loss_s")
            tv = tk.get(key, {}).get("mean_time_loss_s")
            n = tk.get(key, {}).get("vehicles") or 0
            if bv and tv and n:
                hero.append({"Classe": label, "key": key, "pct": (tv - bv) / bv * 100,
                             "n": n, "baseline": bv, "tsp": tv})

        bus = next((r for r in hero if r["key"] in ("buses", "priority_vehicles")), None)
        gen = next((r for r in hero if r["key"] == "general_traffic"), None)

        # ── plain-language headline ───────────────────────────────────────────
        if bus:
            verb = "reduz" if bus["pct"] < 0 else "aumenta"
            gen_txt = ""
            if gen:
                if abs(gen["pct"]) < 2:
                    gen_txt = " com impacto praticamente nulo no tráfego geral"
                elif gen["pct"] < 0:
                    gen_txt = f" e ainda melhora o tráfego geral em {abs(gen['pct']):.0f}%"
                else:
                    gen_txt = f" a um custo de {gen['pct']:.0f}% no tráfego geral"
            st.markdown(
                f"#### O TSP {verb} a perda de tempo do transporte público em "
                f"**{abs(bus['pct']):.0f}%**{gen_txt}."
            )

        # ── headline metrics (the win, in the priority class) ─────────────────
        if bus:
            bcls = bus["key"]
            m1, m2, m3 = st.columns(3)
            render_kpi_metric(m1, "mean_time_loss_s",
                              tk.get(bcls, {}).get("mean_time_loss_s"),
                              bk.get(bcls, {}).get("mean_time_loss_s"))
            render_kpi_metric(m2, "mean_waiting_time_s",
                              tk.get(bcls, {}).get("mean_waiting_time_s"),
                              bk.get(bcls, {}).get("mean_waiting_time_s"))
            render_kpi_metric(m3, "mean_speed_mps",
                              tk.get(bcls, {}).get("mean_speed_mps"),
                              bk.get(bcls, {}).get("mean_speed_mps"))
            st.caption(f"Classe {bus['Classe'].lower()} ({bus['n']} veículos) · "
                       f"{primary_tsp} vs {baseline_key}. Verde = melhoria, vermelho = custo.")

        # ── hero chart: who benefits ──────────────────────────────────────────
        section("Quem ganha com o TSP — variação da perda de tempo por classe")
        if hero:
            dfh = pd.DataFrame(hero)
            fig_hero = go.Figure(go.Bar(
                x=dfh["pct"], y=dfh["Classe"], orientation="h",
                marker_color=[COLOR_GOOD if p < 0 else COLOR_BAD for p in dfh["pct"]],
                text=[f"{p:+.1f}%" for p in dfh["pct"]], textposition="outside",
                customdata=dfh[["baseline", "tsp", "n"]].values,
                hovertemplate="%{y}: %{x:+.1f}%<br>%{customdata[0]:.0f}s → %{customdata[1]:.0f}s "
                              "(n=%{customdata[2]})<extra></extra>",
            ))
            fig_hero.add_vline(x=0, line_width=2, line_color="#334155")
            chart_layout(fig_hero, "Δ perda de tempo · TSP vs baseline (barras à esquerda = melhoria)",
                         height=max(260, len(hero) * 56 + 90))
            st.plotly_chart(fig_hero, use_container_width=True)
            insight("Barras <strong>verdes à esquerda</strong> = o TSP melhora essa classe; "
                    "<strong>vermelhas à direita</strong> = custo. O padrão esperado de um sistema de "
                    "prioridade: classes prioritárias (autocarros, emergência) ganham, o tráfego geral "
                    "fica perto de zero. Passa o rato numa barra para ver os valores absolutos.")
            if not any(r["key"] == "emergency_vehicles" for r in hero):
                st.caption("Emergência não aparece aqui (o cenário base não tem veículos de emergência). "
                           "Vê o separador **Cenários → emergency_vehicle_conflict** para o caso de emergência.")

        # ── navigation hint ───────────────────────────────────────────────────
        section("Explorar em detalhe")
        n1, n2, n3 = st.columns(3)
        n1.markdown("**KPIs** — comparação detalhada entre cenários, por classe e métrica.")
        n2.markdown("**Motor de Decisão** — o que o algoritmo decidiu e porquê.")
        n3.markdown("**Cenários** — impacto do TSP nas 8 situações operacionais.")

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — KPI comparison
# ═══════════════════════════════════════════════════════════════════════════════

with tab_kpi:
    if not cls_data:
        st.info("Sem dados de KPI disponíveis.")
    else:
        # ── contextual hint: TSP gains live in the bus class ──────────────────
        bus_n = cls_counts.get("buses", 0)
        if vehicle_cls in ("all_vehicles", "general_traffic", "non_priority_vehicles") and bus_n:
            hint_col, btn_col = st.columns([4, 1])
            with hint_col:
                insight("O TSP é <strong>prioridade ao transporte público</strong>: melhora os "
                        f"<strong>{bus_n} autocarros</strong>, não o tráfego geral. Nesta vista "
                        f"(<strong>{vehicle_cls_label.lower()}</strong>) o ganho dos autocarros dilui-se "
                        "na média e sobra um pequeno custo no tráfego geral — por desenho. "
                        "Para ver os ganhos da prioridade, filtra por <strong>Autocarros</strong>.")
            with btn_col:
                bus_display = f"Autocarros ({bus_n})"
                if bus_display in cls_label_map:
                    def _focus_buses(target=bus_display):
                        st.session_state["veh_cls_select"] = target
                    st.button("Ver autocarros", use_container_width=True,
                              on_click=_focus_buses, help="Muda o filtro para a classe Autocarros.")

        # ── interactive A/B selector ──────────────────────────────────────────
        section("Comparação interactiva entre dois cenários")
        opts = list(run_kpis.keys())
        sc1, sc2 = st.columns(2)
        ref_idx = opts.index(baseline_key) if baseline_key in opts else 0
        cmp_idx = opts.index(primary_tsp) if primary_tsp in opts else min(1, len(opts) - 1)
        ref_run = sc1.selectbox("Cenário de referência", opts, index=ref_idx,
                                help="O ponto de comparação (tipicamente o baseline sem TSP).")
        cmp_run = sc2.selectbox("Cenário a comparar", opts, index=cmp_idx,
                                help="O cenário cujo desempenho se quer avaliar.")

        ref_data = cls_data.get(ref_run, {})
        cmp_data = cls_data.get(cmp_run, {})

        if ref_run == cmp_run:
            st.info("Selecciona dois cenários diferentes para ver a comparação.")
        else:
            card_metrics = ["mean_time_loss_s", "mean_waiting_time_s", "mean_duration_s", "p95_time_loss_s"]
            ccols = st.columns(len(card_metrics))
            for col, m in zip(ccols, card_metrics):
                render_kpi_metric(col, m, cmp_data.get(m), ref_data.get(m))
            insight(f"Cartões: valor de <strong>{cmp_run}</strong>, delta vs <strong>{ref_run}</strong>. "
                    "Passa o rato sobre o ícone (?) de cada métrica para ver a definição.")

        # ── grouped bar chart — all runs ──────────────────────────────────────
        section("Comparação de métricas entre todos os cenários")
        plot_metrics = ["mean_time_loss_s", "mean_waiting_time_s", "mean_duration_s",
                        "mean_depart_delay_s", "p95_time_loss_s"]
        rows = []
        for metric in plot_metrics:
            mname, unit, _ = KPI_META.get(metric, (metric, "", ""))
            for run_label, data in cls_data.items():
                val = data.get(metric)
                if val is not None:
                    rows.append({"Métrica": mname, "Cenário": run_label, "Valor": val})

        if rows:
            df = pd.DataFrame(rows)
            sel_metrics = st.multiselect(
                "Métricas a mostrar", options=df["Métrica"].unique().tolist(),
                default=df["Métrica"].unique().tolist()[:3],
                help="Adiciona ou remove métricas do gráfico.",
            )
            df_plot = df[df["Métrica"].isin(sel_metrics)] if sel_metrics else df
            colors = [run_color(r) for r in df_plot["Cenário"].unique()]
            fig = px.bar(df_plot, x="Valor", y="Métrica", color="Cenário",
                         barmode="group", orientation="h",
                         color_discrete_sequence=colors,
                         height=max(300, len(sel_metrics or plot_metrics) * 78 + 80))
            fig.update_traces(texttemplate="%{x:.1f}", textposition="outside",
                              hovertemplate="%{y}<br>%{fullData.name}: %{x:.1f}<extra></extra>")
            chart_layout(fig, "KPIs por cenário (segundos)")
            st.plotly_chart(fig, use_container_width=True)
            insight("Barras mais curtas = melhor desempenho nas métricas de tempo. "
                    "Compare o <strong>baseline</strong> (cinzento) com os cenários TSP para quantificar o ganho.")

        # ── delta chart — cmp vs ref ──────────────────────────────────────────
        if ref_run != cmp_run:
            section(f"Variação por métrica — {cmp_run} vs {ref_run}")
            wf_rows = []
            for m in ["mean_time_loss_s", "mean_waiting_time_s", "mean_duration_s",
                      "p95_time_loss_s", "mean_depart_delay_s"]:
                bv, tv = ref_data.get(m), cmp_data.get(m)
                if bv and tv:
                    label, _, _ = KPI_META[m]
                    wf_rows.append({"Métrica": label, "Delta": round(tv - bv, 2),
                                    "Pct": round((tv - bv) / bv * 100, 1)})
            if wf_rows:
                df_wf = pd.DataFrame(wf_rows)
                fig_wf = go.Figure(go.Bar(
                    x=df_wf["Delta"], y=df_wf["Métrica"], orientation="h",
                    text=[f"{p:+.1f}%" for p in df_wf["Pct"]], textposition="outside",
                    marker_color=["#22c55e" if v < 0 else "#ef4444" for v in df_wf["Delta"]],
                    hovertemplate="%{y}: %{x:+.1f}s<extra></extra>",
                ))
                fig_wf.add_vline(x=0, line_width=2, line_color="#334155")
                chart_layout(fig_wf, "Ganho absoluto (s) — verde reduz, vermelho aumenta", height=320)
                st.plotly_chart(fig_wf, use_container_width=True)
                insight("Verde = melhoria (redução do tempo). Vermelho = degradação. "
                        "A linha vertical é o cenário de referência. Percentagens = variação relativa.")

        # ── detailed comparison tables ────────────────────────────────────────
        if demo:
            section("Tabelas de comparação detalhada")
            comp_map = [
                ("tsp_vs_sumo_baseline_kpis",            "TSP vs Baseline"),
                ("tsp_controller_vs_sumo_baseline_kpis", "TSP+Controller vs Baseline"),
                ("tsp_controller_vs_tsp_runtime",        "TSP+Controller vs TSP"),
            ]
            for ckey, title in comp_map:
                comp = demo.get("comparisons", {}).get(ckey, {})
                if not comp.get("available") or not comp.get("rows"):
                    continue
                rows_out = []
                for r in comp["rows"]:
                    mk = r.get("metric", "")
                    lab, unit, _ = KPI_META.get(mk, (mk, "", ""))
                    bv = r.get("baseline")
                    cv = r.get("candidate") or r.get("tsp_controller") or r.get("tsp")
                    p = pct(bv, cv)
                    rows_out.append({
                        "Métrica": lab or mk, "Unidade": unit,
                        "Baseline": fmt(bv), "TSP / Controller": fmt(cv),
                        "Δ absoluto": fmt(r.get("delta")),
                        "Δ relativo": f"{p:+.1f}%" if p is not None else "—",
                    })
                if rows_out:
                    with st.expander(title, expanded=(ckey == "tsp_vs_sumo_baseline_kpis")):
                        df_comp = pd.DataFrame(rows_out)

                        def _color_delta(col):
                            out = []
                            for v in col:
                                try:
                                    f = float(str(v).replace("%", "").replace("+", ""))
                                    out.append("color:#15803d;font-weight:600" if f < 0
                                               else ("color:#dc2626;font-weight:600" if f > 0 else ""))
                                except (ValueError, TypeError):
                                    out.append("")
                            return out

                        st.dataframe(df_comp.style.apply(_color_delta, subset=["Δ absoluto", "Δ relativo"]),
                                     use_container_width=True, hide_index=True)
                        download_csv(df_comp, f"{ckey}.csv", key=f"dl_{ckey}")

        # ── P95 vs mean ───────────────────────────────────────────────────────
        section("Distribuição — média vs P95 (perda de tempo)")
        dist_rows = []
        for run_label, data in cls_data.items():
            if data.get("mean_time_loss_s") is not None:
                dist_rows.append({"Cenário": run_label, "Tipo": "Média", "Valor (s)": data["mean_time_loss_s"]})
            if data.get("p95_time_loss_s") is not None:
                dist_rows.append({"Cenário": run_label, "Tipo": "P95", "Valor (s)": data["p95_time_loss_s"]})
        if dist_rows:
            df_dist = pd.DataFrame(dist_rows)
            colors_dist = [run_color(r) for r in df_dist["Cenário"].unique()]
            fig_dist = px.bar(df_dist, x="Cenário", y="Valor (s)", color="Cenário",
                              facet_col="Tipo", barmode="group",
                              color_discrete_sequence=colors_dist, height=320)
            chart_layout(fig_dist, "Perda de tempo: média e cauda da distribuição (P95)")
            fig_dist.update_layout(showlegend=False)
            st.plotly_chart(fig_dist, use_container_width=True)
            insight("O P95 representa os 5% de viagens com pior desempenho — a cauda é relevante para "
                    "avaliar equidade e o pior caso. Um bom TSP reduz tanto a média como o P95.")

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — TSP decision engine
# ═══════════════════════════════════════════════════════════════════════════════

with tab_decisions:
    if not demo:
        st.info("Report do demonstrador não disponível.")
    else:
        all_labels = list(demo.get("runs", {}).keys())
        sel_run = st.selectbox(
            "Run TSP", options=all_labels,
            index=next((i for i, k in enumerate(all_labels) if k != "sumo_baseline"), 0),
            help="Escolhe a run cujo motor de decisão queres analisar.",
        )
        runtime = demo["runs"][sel_run].get("runtime", {})

        section("Pipeline de decisão — do seguimento à actuação")
        total    = runtime.get("total_decisions", 0)
        applied  = runtime.get("applied_events", 0)
        blocked  = runtime.get("blocked_by_safety", 0)
        rejected = runtime.get("controller_rejections", 0)
        by_action = runtime.get("by_action", {})

        # Só estas duas acções propõem uma mudança real ao semáforo. As restantes
        # (reavaliar / rejeitar / sem acção) são não-actuações deliberadas, não
        # "aplicações falhadas" — por isso o denominador honesto da taxa de
        # aplicação é o nº de decisões ACCIONÁVEIS, não o total de avaliações.
        actionable = by_action.get("green_extension", 0) + by_action.get("early_green", 0)
        non_actionable = {
            "Reavaliar no ciclo seguinte": by_action.get("reevaluate_next_cycle", 0),
            "Rejeitadas (score abaixo do limiar)": by_action.get("reject", 0),
            "Sem acção necessária (verde já chega)": by_action.get("no_action", 0),
        }

        if total == 0:
            warn("Esta run não gerou decisões TSP. Selecciona uma run TSP "
                 "(ex. <code>tsp</code> ou <code>tsp_controller</code>) para ver a análise.")
        else:
            col_f, col_m = st.columns([1, 1])
            with col_f:
                fig_funnel = go.Figure(go.Funnel(
                    y=["Decisões avaliadas", "Accionáveis (propõem mudança)", "Aplicadas em rede"],
                    x=[total, actionable, applied],
                    textinfo="value+percent initial",
                    marker_color=["#94a3b8", "#1d6ef5", "#22c55e"],
                    hovertemplate="%{y}: %{x}<extra></extra>",
                ))
                chart_layout(fig_funnel, "Funil de decisão TSP", height=300)
                st.plotly_chart(fig_funnel, use_container_width=True)
            with col_m:
                st.markdown("&nbsp;")
                mm1, mm2 = st.columns(2)
                mm1.metric("Decisões avaliadas", total, border=True,
                           help="Total de avaliações do motor. Cada autocarro é reavaliado "
                                "várias vezes ao longo da aproximação, por isso este número é "
                                "muito maior que o nº de autocarros.")
                mm2.metric("Accionáveis", actionable, border=True,
                           help="Decisões que propuseram uma mudança real ao semáforo "
                                "(extensão de verde + verde antecipado).")
                mm3, mm4 = st.columns(2)
                mm3.metric("Aplicadas em rede", applied, border=True,
                           help="Accionáveis que passaram a Safety Layer e foram aplicadas via TraCI.")
                mm4.metric("Bloqueadas (safety)", blocked, border=True,
                           help="Accionáveis barradas pela Safety Layer por risco de segurança.")
                ar = f"{applied/actionable*100:.0f}%" if actionable else "—"
                st.caption(f"Taxa de aplicação: **{ar}** ({applied}/{actionable} accionáveis aplicadas)"
                           + (f" · {rejected} rejeições do controller" if rejected else ""))

            insight("As <strong>decisões avaliadas</strong> incluem cada vez que um autocarro em "
                    "aproximação é reavaliado. Só uma fracção propõe mudar o semáforo "
                    "(<strong>accionáveis</strong>); destas, a Safety Layer só barra as inseguras. "
                    "A taxa correcta é aplicadas/accionáveis — não aplicadas/avaliadas.")

            # explain the non-actionable bulk so the total→actionable drop is clear
            na_total = sum(non_actionable.values())
            if na_total:
                with st.expander(f"Porque é que {na_total} decisões não actuaram? (não-actuações deliberadas)"):
                    df_na = pd.DataFrame(
                        [{"Categoria": k, "Decisões": v} for k, v in non_actionable.items() if v]
                    ).sort_values("Decisões", ascending=False)
                    st.dataframe(df_na, use_container_width=True, hide_index=True)
                    st.caption(
                        "**Reavaliar** = o autocarro ainda está a ser seguido mas não é o momento "
                        "de actuar (fase ainda não pronta, verde mínimo por servir, benefício pequeno "
                        "ou pressão de rede). **Rejeitar** = o autocarro não precisa de prioridade "
                        "(pontual / desvio baixo). **Sem acção** = o verde actual já é suficiente."
                    )

        section("Distribuição de acções decididas")
        col_pie, col_legend = st.columns([1, 1])
        with col_pie:
            if by_action:
                labels_a = list(by_action.keys())
                fig_pie = go.Figure(go.Pie(
                    labels=[ACTION_META.get(k, (k, "", ""))[0] for k in labels_a],
                    values=list(by_action.values()),
                    marker_colors=[ACTION_META.get(k, ("", "#94a3b8", ""))[1] for k in labels_a],
                    hole=0.45, textinfo="label+percent", textfont={"size": 11},
                    hovertemplate="%{label}: %{value} (%{percent})<extra></extra>",
                ))
                chart_layout(fig_pie, "Acções do motor TSP", height=320)
                fig_pie.update_layout(showlegend=False)
                st.plotly_chart(fig_pie, use_container_width=True)
            else:
                st.caption("Sem acções registadas.")
        with col_legend:
            st.markdown("&nbsp;")
            for key, (label, color, desc) in ACTION_META.items():
                count = by_action.get(key)
                cstr = f" · {count}" if count else ""
                st.markdown(
                    f'<div style="display:flex;gap:8px;margin-bottom:10px;align-items:flex-start">'
                    f'<span style="width:10px;height:10px;border-radius:50%;background:{color};'
                    f'flex-shrink:0;margin-top:4px"></span><div>'
                    f'<b style="font-size:0.82rem">{label}{cstr}</b><br>'
                    f'<span style="font-size:0.76rem;color:#64748b">{desc}</span></div></div>',
                    unsafe_allow_html=True,
                )

        safety_reasons = runtime.get("safety_block_by_reason", {})
        section("Bloqueios da Safety Layer por motivo")
        if safety_reasons:
            df_sf = pd.DataFrame({"Motivo": list(safety_reasons.keys()),
                                  "Bloqueios": list(safety_reasons.values())}).sort_values("Bloqueios")
            fig_sf = px.bar(df_sf, x="Bloqueios", y="Motivo", orientation="h",
                            color_discrete_sequence=["#ef4444"], height=max(260, len(df_sf) * 50 + 80))
            chart_layout(fig_sf, "Safety Layer — motivos de bloqueio")
            st.plotly_chart(fig_sf, use_container_width=True)
            insight("A Safety Layer bloqueia actuações que criem conflitos: amarelo insuficiente, "
                    "violação de verde mínimo/máximo, cooldown entre actuações ou conflito de fases.")
        else:
            st.caption("Sem bloqueios de segurança registados nesta run.")

        per_tls = runtime.get("per_tls", {})
        if per_tls:
            section("Actividade por semáforo (TLS)")
            tls_rows = [{
                "Semáforo": tid,
                "Decisões": d.get("decisions", 0),
                "Aplicadas": d.get("applied_events", 0),
                "Bloqueadas": d.get("safety_blocks", 0) or d.get("blocked_by_safety", 0),
                "Taxa aplicação": (f"{d.get('applied_events',0)/d.get('decisions',1)*100:.0f}%"
                                   if d.get("decisions", 0) else "—"),
            } for tid, d in per_tls.items()]
            df_tls = pd.DataFrame(tls_rows).sort_values("Decisões", ascending=False)
            st.dataframe(df_tls, use_container_width=True, hide_index=True)
            download_csv(df_tls, f"per_tls_{sel_run}.csv", key=f"dl_tls_{sel_run}")

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
            sel_cits_run = st.selectbox("Run", tsp_run_keys, key="cits_run",
                                        help="Run cujo tráfego C-ITS (V2X) queres inspeccionar.")
            summ = demo["runs"][sel_cits_run].get("summary", {})

            section("Volume de mensagens C-ITS por tipo")
            by_type = summ.get("cits_by_type", {})
            if by_type:
                cits_descs = {
                    "MAPEM":  "Informação topológica da rede semafórica",
                    "SPATEM": "Estado em tempo real de cada fase semafórica",
                    "SREM":   "Pedido de prioridade enviado pelo autocarro",
                    "SSEM":   "Resposta do RSU ao pedido de prioridade",
                }
                col_chart, col_desc = st.columns([1, 1])
                with col_chart:
                    df_ct = pd.DataFrame({"Tipo": list(by_type.keys()), "Mensagens": list(by_type.values())})
                    fig_ct = px.bar(df_ct, x="Tipo", y="Mensagens", color="Tipo",
                                    color_discrete_sequence=["#1d4ed8", "#0891b2", "#7c3aed", "#059669"],
                                    height=320, log_y=True)
                    fig_ct.update_layout(showlegend=False)
                    fig_ct.update_traces(hovertemplate="%{x}: %{y}<extra></extra>")
                    chart_layout(fig_ct, "Mensagens por protocolo C-ITS (escala log)")
                    st.plotly_chart(fig_ct, use_container_width=True)
                with col_desc:
                    st.markdown("&nbsp;")
                    for mtype, mdesc in cits_descs.items():
                        cnt = by_type.get(mtype, 0)
                        st.markdown(f"**{mtype}** — {cnt:,} mensagens  \n"
                                    f'<span style="font-size:0.78rem;color:#64748b">{mdesc}</span>',
                                    unsafe_allow_html=True)
                        st.markdown("")
                insight("Escala logarítmica no eixo Y porque o SPATEM (estado de fase, emitido a cada "
                        "passo) domina em volume face aos pedidos pontuais (SREM/SSEM).")

            section("Saúde do transporte de mensagens")
            mt = summ.get("message_transport", {})
            if mt:
                mc1, mc2, mc3, mc4 = st.columns(4)
                published = mt.get("published", 0) or 0
                delivered = mt.get("delivered", 0) or 0
                rate = f"{delivered/published*100:.0f}%" if published else "—"
                mc1.metric("Publicadas", f"{published:,}", border=True)
                mc2.metric("Entregues", f"{delivered:,}", border=True)
                mc3.metric("Perdidas", mt.get("dropped", "—"), border=True,
                           help="Mensagens que não chegaram ao destino.")
                mc4.metric("Taxa de entrega", rate, border=True)
                if mt.get("dropped", 0) == 0:
                    insight("Taxa de entrega: <strong>100%</strong> — nenhuma mensagem perdida no canal C-ITS simulado.")

            section("Ciclo de vida dos pedidos de prioridade (SREM/SSEM)")
            prl = summ.get("priority_request_lifecycle", {})
            if prl:
                lifecycle = {
                    "Tracked": prl.get("tracked_requests", 0),
                    "Granted": prl.get("granted_requests", 0),
                    "Cleared": prl.get("cleared_requests", 0),
                    "Expired": prl.get("expired_requests", 0),
                }
                df_prl = pd.DataFrame({"Estado": list(lifecycle.keys()), "Pedidos": list(lifecycle.values())})
                fig_prl = px.bar(df_prl, x="Estado", y="Pedidos", color="Estado",
                                 color_discrete_sequence=["#1d6ef5", "#22c55e", "#94a3b8", "#ef4444"], height=300)
                fig_prl.update_layout(showlegend=False)
                chart_layout(fig_prl, "Pedidos de prioridade — estados no ciclo de vida")
                st.plotly_chart(fig_prl, use_container_width=True)
                insight("<strong>Granted</strong> = prioridade concedida. <strong>Cleared</strong> = "
                        "pedido concluído (autocarro passou). <strong>Expired</strong> = timeout sem "
                        "concessão. Granted/Tracked = taxa de sucesso do TSP.")

            gc = summ.get("green_compensation", {})
            if gc.get("enabled"):
                section("Compensação de verde (equidade)")
                g1, g2, g3 = st.columns(3)
                g1.metric("Eventos de compensação", gc.get("events", 0), border=True)
                g2.metric("Verde concedido (s)", fmt(gc.get("granted_s_total")), border=True)
                g3.metric("Verde recuperado (s)", fmt(gc.get("reclaimed_s_total")), border=True)
                insight("A compensação devolve nos ciclos seguintes o verde \"emprestado\" às outras "
                        "fases para dar prioridade ao autocarro, mantendo a equidade semafórica.")

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Baseline vs RL
# ═══════════════════════════════════════════════════════════════════════════════

with tab_rl:
    if not rl_comparison:
        warn("Report de comparação Baseline vs RL não disponível. "
             "Corre <code>make compare-tsp-rl</code> para gerar este relatório.")
    else:
        rc1, rc2, rc3 = st.columns(3)
        rc1.metric("Decisões comparadas", rl_comparison.get("matched_decision_count", "—"), border=True)
        rc2.metric("Veredicto de rede", rl_comparison.get("network_impact_verdict", "—"), border=True)
        rc3.metric("Tipo de avaliação", rl_comparison.get("evaluation", "—").replace("_", " "), border=True)

        section("Distribuição de veredictos por decisão")
        vc = rl_comparison.get("verdict_counts", {})
        if vc:
            df_vc = pd.DataFrame({"Veredicto": list(vc.keys()), "Contagem": list(vc.values())})
            fig_vc = px.bar(df_vc, x="Veredicto", y="Contagem", color="Veredicto",
                            color_discrete_sequence=["#22c55e", "#ef4444", "#94a3b8", "#f59e0b"], height=320)
            fig_vc.update_layout(showlegend=False)
            chart_layout(fig_vc, "Veredictos da política RL vs baseline rule-based")
            st.plotly_chart(fig_vc, use_container_width=True)
            insight("Cada decisão compara a acção da política RL com a rule-based. Veredicto positivo = "
                    "RL escolheu acção com melhor valor estimado de recompensa.")

        kpi_eval = rl_comparison.get("kpi_evaluation", {})
        if kpi_eval.get("available") and kpi_eval.get("rows"):
            section("KPIs — Baseline vs RL")
            rl_rows = []
            for r in kpi_eval["rows"]:
                mk = r.get("metric", "")
                lab, _, _ = KPI_META.get(mk, (mk, "", ""))
                bv, rv = r.get("baseline"), r.get("rl")
                p = pct(bv, rv)
                rl_rows.append({"Métrica": lab or mk, "Baseline": fmt(bv), "RL": fmt(rv),
                                "Δ (s)": fmt(r.get("delta")),
                                "Δ (%)": f"{p:+.1f}%" if p is not None else "—"})
            df_rl = pd.DataFrame(rl_rows)
            st.dataframe(df_rl, use_container_width=True, hide_index=True)
            download_csv(df_rl, "baseline_vs_rl_kpis.csv", key="dl_rl")

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 — Scenarios
# ═══════════════════════════════════════════════════════════════════════════════

with tab_scenarios:
    scenario_dir = REPORTS / "scenarios"
    scen_names = sorted(p.name for p in scenario_dir.iterdir() if p.is_dir()) if scenario_dir.exists() else []
    if not scen_names:
        warn("Sem resultados de cenários. Corre <code>make scenario-suite</code> (ou o separador "
             "Simulação) para gerar runs por cenário com baseline vs TSP emparelhados.")
    else:
        # ── load every scenario/run-type/seed into one long dataframe ─────────
        rows = []
        for scen in scen_names:
            for rt_dir in (scenario_dir / scen).iterdir():
                if not rt_dir.is_dir():
                    continue
                for seed_dir in rt_dir.iterdir():
                    if not seed_dir.is_dir():
                        continue
                    kpis = load_json(seed_dir / "kpis.json")
                    if not kpis:
                        continue
                    data = get_kpi(kpis, vehicle_cls)
                    for m, (lab, _, _) in KPI_META.items():
                        v = data.get(m)
                        if v is not None:
                            rows.append({"Cenário": scen, "Run type": rt_dir.name,
                                         "Seed": seed_dir.name, "metric_key": m,
                                         "Métrica": lab, "Valor": v})

        if not rows:
            st.info(f"Cenários presentes mas sem KPIs para a classe '{vehicle_cls_label}'. "
                    "Experimenta 'Todos os veículos' ou 'Autocarros'.")
        else:
            df_all = pd.DataFrame(rows)
            run_types_all = sorted(df_all["Run type"].unique())
            color_map = {rt: run_color(rt) for rt in run_types_all}
            baseline_rt = next((r for r in run_types_all if "baseline" in r), None)
            tsp_rt = next((r for r in run_types_all if "tsp" in r), None)
            n_scen = df_all["Cenário"].nunique()

            # ── cross-scenario overview ───────────────────────────────────────
            section(f"Visão geral — {n_scen} cenários · {vehicle_cls_label}")
            ov_keys = ["mean_time_loss_s", "mean_waiting_time_s", "mean_duration_s", "mean_speed_mps"]
            ov_keys = [k for k in ov_keys if k in df_all["metric_key"].values]
            ov_label = st.selectbox("Métrica", [KPI_META[k][0] for k in ov_keys], key="ov_metric")
            ov_key = next(k for k in ov_keys if KPI_META[k][0] == ov_label)

            dfm = df_all[df_all["metric_key"] == ov_key]
            piv = dfm.groupby(["Cenário", "Run type"])["Valor"].mean().reset_index()
            fig_ov = px.bar(piv, x="Cenário", y="Valor", color="Run type", barmode="group",
                            color_discrete_map=color_map, height=420)
            fig_ov.update_layout(legend_title_text="", xaxis_tickangle=-30)
            chart_layout(fig_ov, f"{ov_label} por cenário — baseline vs TSP")
            st.plotly_chart(fig_ov, use_container_width=True)

            # per-scenario delta (TSP vs baseline), correctly coloured by improvement
            if baseline_rt and tsp_rt:
                wide = piv.pivot(index="Cenário", columns="Run type", values="Valor")
                higher_better = ov_key in HIGHER_IS_BETTER
                drows = []
                for scen, r in wide.iterrows():
                    b, t = r.get(baseline_rt), r.get(tsp_rt)
                    if b and t is not None and b != 0:
                        d = (t - b) / abs(b) * 100
                        improved = (d > 0) if higher_better else (d < 0)
                        drows.append({"Cenário": scen, "Delta %": round(d, 1), "improved": improved})
                if drows:
                    ddf = pd.DataFrame(drows).sort_values("Delta %")
                    fig_d = go.Figure(go.Bar(
                        x=ddf["Delta %"], y=ddf["Cenário"], orientation="h",
                        marker_color=["#22c55e" if i else "#ef4444" for i in ddf["improved"]],
                        text=[f"{d:+.1f}%" for d in ddf["Delta %"]], textposition="outside",
                        hovertemplate="%{y}: %{x:+.1f}%<extra></extra>",
                    ))
                    fig_d.add_vline(x=0, line_width=2, line_color="#334155")
                    chart_layout(fig_d, f"Impacto do TSP por cenário (Δ% {ov_label})",
                                 height=max(280, n_scen * 42 + 80))
                    st.plotly_chart(fig_d, use_container_width=True)
                    insight("Verde = o TSP melhora o cenário; vermelho = piora. "
                            "Permite ver em que cenários a prioridade semafórica traz mais valor "
                            "(ex. autocarros atrasados ou bunching) e onde tem custo.")

            # ── per-scenario detail ───────────────────────────────────────────
            section("Detalhe por cenário")
            cc1, cc2 = st.columns([1, 2])
            with cc1:
                sel_scen = st.selectbox("Cenário", scen_names, key="detail_scen")
            df_scen = df_all[df_all["Cenário"] == sel_scen]
            with cc2:
                sel_metric = st.selectbox("Métrica", df_scen["Métrica"].unique().tolist(), key="detail_metric")
            df_plot = df_scen[df_scen["Métrica"] == sel_metric]

            n_seeds = df_plot.groupby("Run type")["Seed"].nunique().max() if not df_plot.empty else 0
            fig_box = px.box(df_plot, x="Run type", y="Valor", color="Run type",
                             points="all", height=400, color_discrete_map=color_map)
            fig_box.update_layout(showlegend=False)
            chart_layout(fig_box, f"{sel_metric} — {sel_scen} ({vehicle_cls_label})")
            st.plotly_chart(fig_box, use_container_width=True)
            if n_seeds and n_seeds > 1:
                insight("Cada ponto = uma seed. A caixa mostra Q1–Q3; a linha central é a mediana. "
                        "Pouca sobreposição entre arms sugere diferença significativa.")
            else:
                insight("Apenas <strong>1 seed</strong> por arm — sem dispersão estatística. "
                        "Corre mais seeds (ex. <code>--seeds 57 58 59</code>) para boxplots com "
                        "variabilidade e intervalos de confiança.")

            section("Estatísticas descritivas")
            summary = (df_plot.groupby("Run type")["Valor"]
                       .agg(["mean", "std", "min", "max", "count"])
                       .rename(columns={"mean": "Média", "std": "Desvio-padrão",
                                        "min": "Mín", "max": "Máx", "count": "Seeds"}).round(2))
            st.dataframe(summary, use_container_width=True)
            download_csv(summary.reset_index(), f"scenario_{sel_scen}.csv", key="dl_scen")

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 6 — Metodologia
# ═══════════════════════════════════════════════════════════════════════════════

with tab_meta:
    section("Configuração da simulação")
    if demo:
        sel_run_meta = st.selectbox("Run", list(demo.get("runs", {}).keys()), key="meta_run")
        summ_m = demo["runs"][sel_run_meta].get("summary", {})
        col_a, col_b = st.columns(2)
        with col_a:
            sim_params = {
                "Modo": summ_m.get("mode", "—"),
                "Passos (steps)": summ_m.get("steps", "—"),
                "Cenário": summ_m.get("scenario_id", "—"),
                "Política runtime": summ_m.get("policy_mode", "—"),
                "Actuação activa": str(summ_m.get("actuation_enabled", "—")),
                "Runtime policy carregada": str(summ_m.get("runtime_policy_loaded", "—")),
            }
            st.dataframe(pd.DataFrame({"Parâmetro": list(sim_params.keys()),
                                       "Valor": list(sim_params.values())}),
                         use_container_width=True, hide_index=True)
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

    section("Limitações conhecidas")
    limitations = demo.get("limitations", []) if demo else []
    standard_limits = [
        "Os KPIs são calculados apenas sobre veículos que completaram a viagem durante a janela simulada. "
        "Runs com durações distintas produzem amostras de populações diferentes e não são directamente comparáveis.",
        "A simulação usa um modelo de tráfego microscópico (SUMO) calibrado com dados de rede, mas não com "
        "contagens de tráfego reais do CMP/IMT — os valores absolutos são indicativos, não previsões operacionais.",
        "A Safety Layer pode não ser exercida em runs curtas (0 bloqueios). Os caminhos de segurança são "
        "cobertos por testes unitários mas requerem cenários de stress para aparecer em evidência de runtime.",
        "Autocarros (Linha 25) requerem duração de simulação suficiente para entrar na rede. "
        "Runs com menos de 3600 steps podem não incluir nenhuma viagem de autocarro completa.",
    ]
    for lim in (limitations + standard_limits):
        st.markdown(f"- {lim}")

    section("Fontes de dados")
    data_policy = demo.get("data_policy", {}) if demo else {}
    dp_rows = [
        {"Campo": "Fonte operacional", "Valor": data_policy.get("operational_data_source", "—")},
        {"Campo": "Dados sintéticos", "Valor": str(data_policy.get("synthetic_operational_data", "—"))},
        {"Campo": "Rede viária", "Valor": "sumo/plain/corredor.{nod,edg}.xml — geometria manual da Boavista"},
        {"Campo": "Paragens", "Valor": "sumo/additional/bus_stops.add.xml"},
        {"Campo": "Rotas", "Valor": "sumo/routes/routes.rou.xml — randomTrips com semente controlada"},
    ]
    st.dataframe(pd.DataFrame(dp_rows), use_container_width=True, hide_index=True)

    if demo:
        section("Caminhos de evidência")
        with st.expander("Ver caminhos dos artefactos gerados"):
            ev_rows = []
            for run_name, paths in demo.get("evidence_paths", {}).items():
                for atype, path in paths.items():
                    if atype != "root":
                        ev_rows.append({"Run": run_name, "Artefacto": atype, "Path": path})
            if ev_rows:
                st.dataframe(pd.DataFrame(ev_rows), use_container_width=True, hide_index=True)

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 7 — Simulação
# ═══════════════════════════════════════════════════════════════════════════════

with tab_sim:
    st.markdown("Lança simulações SUMO directamente a partir da dashboard — visualmente no "
                "SUMO-GUI ou em modo headless para regenerar os reports de análise.")
    render_simulation_panel()
