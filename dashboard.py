"""PPS57 · TSP Simulation Analysis Dashboard.

Run with: streamlit run dashboard.py
"""

from __future__ import annotations

import base64
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
import yaml

ROOT = Path(__file__).parent
REPORTS = ROOT / "reports"
PUBLIC = ROOT / "public"

# ── constants ─────────────────────────────────────────────────────────────────

VEHICLE_CLASSES = [
    ("all_vehicles", "Todos os veículos"),
    ("buses", "Autocarros"),
    ("emergency_vehicles", "Veículos de emergência"),
    ("priority_vehicles", "Veículos prioritários"),
    ("general_traffic", "Tráfego geral"),
]

KPI_META = {
    "mean_time_loss_s": (
        "Perda de tempo média",
        "s",
        "Tempo perdido face à velocidade ideal de rede. Indicador principal de eficiência. Menor é melhor.",
    ),
    "mean_waiting_time_s": (
        "Tempo de espera médio",
        "s",
        "Tempo parado em fila ou semáforo vermelho. Menor é melhor.",
    ),
    "mean_duration_s": (
        "Duração média de viagem",
        "s",
        "Tempo total de trajecto, porta a porta. Menor é melhor.",
    ),
    "p95_time_loss_s": (
        "Perda de tempo P95",
        "s",
        "Percentil 95 — descreve o pior cenário para 95% dos veículos. Menor é melhor.",
    ),
    "mean_speed_mps": (
        "Velocidade média",
        "m/s",
        "Velocidade média ao longo do trajecto. Maior é melhor.",
    ),
    "mean_depart_delay_s": (
        "Atraso de partida médio",
        "s",
        "Tempo de espera antes de entrar na rede. Menor é melhor.",
    ),
    "mean_stop_count": (
        "Paragens médias",
        "",
        "Número médio de paragens por veículo. Menor é melhor.",
    ),
}

# metrics where an increase is an improvement (drives delta colouring)
HIGHER_IS_BETTER = {"mean_speed_mps"}

ACTION_META = {
    "green_extension": (
        "Extensão de verde",
        "#22c55e",
        "Alonga a fase verde actual para deixar passar o autocarro.",
    ),
    "early_green": (
        "Verde antecipado",
        "#1d6ef5",
        "Avança o início da fase verde para a aproximação do autocarro.",
    ),
    "no_action": ("Sem acção", "#94a3b8", "Nenhuma intervenção necessária neste ciclo."),
    "reject": ("Rejeitado", "#ef4444", "Pedido recusado por critério de elegibilidade."),
    "reevaluate_next_cycle": (
        "Reavaliar no ciclo",
        "#f59e0b",
        "Decisão adiada — reavalia na próxima janela de decisão.",
    ),
}

# friendly labels for the priority-score objectives (score_components keys)
OBJECTIVE_LABELS = {
    "schedule_delay": "Atraso ao horário",
    "headway_deviation": "Desvio de intervalo (headway)",
    "priority_level": "Nível de prioridade",
    "proximity": "Proximidade (ETA)",
}

PALETTE = {
    "sumo_baseline": "#64748b",
    "baseline": "#64748b",
    "tsp": "#1d6ef5",
    "tsp_controller": "#7c3aed",
}

# semantic colours (consistent across the whole dashboard)
COLOR_GOOD = "#16a34a"  # improvement
COLOR_BAD = "#dc2626"  # degradation / cost
COLOR_EMERGENCY = "#dc2626"

# ── CSS ───────────────────────────────────────────────────────────────────────

CSS = """
<style>
html, body, [class*="css"] { font-family: "Inter", "Segoe UI", system-ui, sans-serif; }

/* page header */
.page-header { border-bottom: 3px solid #1d6ef5; padding-bottom: 12px; margin-bottom: 6px;
               margin-top: 2.5rem; }  /* clear Streamlit's 60px fixed top header */
.page-header h1 { font-size: 1.55rem; font-weight: 700; color: #0f172a; margin: 0 0 2px; letter-spacing: -0.4px; }
.page-header .subtitle { font-size: 0.82rem; color: #64748b; margin: 0; }
.badge { display:inline-block; background:#eff6ff; border:1px solid #bfdbfe; color:#1d4ed8;
         font-size:0.72rem; font-weight:600; padding:2px 8px; border-radius:4px;
         margin-left:8px; vertical-align:middle; }
.freshness { font-size:0.74rem; color:#94a3b8; margin:6px 0 0; }

/* brand bar pinned to the bottom of every page — product logo left, partner
   logo right; centred at the same max width as the content so the logos line up
   with the content edges. */
.page-footer { position: fixed; left: 0; right: 0; bottom: 0; z-index: 90;
               background: #ffffff; border-top: 0.5px solid #e5e7eb; height: 56px; }
.page-footer-inner { max-width: 1500px; height: 100%; margin: 0 auto; padding: 0 2.5rem;
                     display: flex; align-items: center; justify-content: space-between; }
.page-footer img { display: block; width: auto; }
.page-footer .pf-logo { height: 30px; }
.page-footer .pf-partner { height: 24px; opacity: 0.9; }

/* sidebar */
.sb-label { font-size:0.68rem; font-weight:700; text-transform:uppercase;
            letter-spacing:.1em; color:#94a3b8; margin:16px 0 6px; }
.sb-project { font-weight:700; font-size:0.95rem; color:#0f172a; }
.sb-sub { font-size:0.75rem; color:#64748b; }
.file-row { display:flex; align-items:center; gap:7px; font-size:0.75rem; color:#374151; padding:3px 0; }
.dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
.dot-ok  { background:#22c55e; }
.dot-off { background:#cbd5e1; }

/* verdict banner — single styled card; the left accent reflects the status */
.verdict-card { background:#ffffff; border:1px solid #f1f5f9; border-left:4px solid #f59e0b;
                border-radius:8px; padding:1rem 1.25rem; margin-bottom:1.5rem; }
.verdict-card .verdict-headline { font-size:15px; font-weight:700; color:#92400e; margin:0 0 3px; }
.verdict-card .verdict-support  { font-size:13px; color:#78716c; margin:0; line-height:1.5; }
.verdict-card.is-pass    { border-left-color:#22c55e; }
.verdict-card.is-pass .verdict-headline    { color:#15803d; }
.verdict-card.is-fail    { border-left-color:#ef4444; }
.verdict-card.is-fail .verdict-headline    { color:#b91c1c; }
.verdict-card.is-unknown { border-left-color:#94a3b8; }
.verdict-card.is-unknown .verdict-headline { color:#475569; }

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

/* C-ITS conversation flow */
.flow { display:flex; align-items:stretch; gap:6px; flex-wrap:wrap; margin:6px 0 4px; }
.flow-step { flex:1 1 0; min-width:150px; background:#f8fafc; border:1px solid #e2e8f0;
             border-left:3px solid #1d6ef5; border-radius:8px; padding:10px 12px; }
.flow-step .ft { font-weight:700; font-size:0.8rem; color:#0f172a; }
.flow-step .fd { font-size:0.74rem; color:#64748b; line-height:1.35; margin-top:2px; }
.flow-arrow { align-self:center; color:#94a3b8; font-size:1.1rem; font-weight:700; }

/* remove the default Streamlit top padding */
section.main > div { padding-top: 1rem; }
.block-container { padding-top: 1rem; }

/* KPI metric cards — left accent stripe signals improvement vs cost */
.kpi-card { border:1px solid #e5e7eb; border-radius:12px; padding:1.25rem; position:relative;
            overflow:hidden; background:#ffffff; }
.kpi-card::before { content:""; position:absolute; left:0; top:0; bottom:0; width:4px; background:transparent; }
.kpi-card.kpi-good::before { background:#22c55e; }
.kpi-card.kpi-bad::before  { background:#dc2626; }
.kpi-card .kpi-label { font-size:12px; color:#6b7280; font-weight:600; margin:0; }
.kpi-card .kpi-value { font-size:28px; font-weight:700; color:#0f172a; line-height:1.15; margin:4px 0 0; }
.kpi-card .kpi-delta { font-size:14px; font-weight:600; margin:6px 0 0; }
.kpi-delta.kpi-good { color:#16a34a; }
.kpi-delta.kpi-bad  { color:#dc2626; }
.kpi-delta.kpi-flat { color:#6b7280; }

/* "Explorar em detalhe" chips are real st.buttons styled near the drawer rules below. */

/* expanders — clean white card with a subtle hover (app-wide: sidebar + tabs) */
[data-testid="stExpander"] details {
  border:1px solid #e2e8f0; border-radius:8px; background:#ffffff; overflow:hidden;
}
[data-testid="stExpander"] summary { background:#ffffff; }
[data-testid="stExpander"] summary:hover { background:#f8fafc; }
[data-testid="stExpanderDetails"] { background:#ffffff; }

/* tabs — rounded top corners with a subtle hover, matching the card polish */
[data-testid="stTabs"] button[data-testid="stTab"] {
  border-radius:6px 6px 0 0;
  transition:background-color 0.15s ease, color 0.15s ease;
}
[data-testid="stTabs"] button[data-testid="stTab"]:hover { background-color:#f1f5f9; }

/* ══════════════════════════════════════════════════════════════════════════
   DRAWER NAVIGATION — replaces the native sidebar. One block, light theme.
   The native sidebar/header are hidden. The topbar + slide-over drawer are real
   Streamlit widgets pinned via Streamlit's stable .st-key-<key> classes (1.39+),
   since widgets can't live inside an st.markdown HTML string. The content column
   is centred at a max width so the page reads structured, not edge-to-edge.
   ══════════════════════════════════════════════════════════════════════════ */
section[data-testid="stSidebar"] { display: none !important; }
header[data-testid="stHeader"] { display: none !important; }
[data-testid="stMainBlockContainer"], .block-container {
  max-width: 1500px !important; padding: 66px 2.5rem 88px !important; margin: 0 auto !important; }
.page-header { margin-top: 0.25rem; }

/* topbar — fixed to the viewport top; the hamburger (also fixed) aligns to it */
.topbar { position: fixed; top: 0; left: 0; right: 0; z-index: 100; background: #ffffff;
          border-bottom: 0.5px solid #e5e7eb; height: 48px; display: flex; align-items: center;
          padding: 0 1.5rem 0 3.6rem; gap: 12px; }
.topbar-logo { font-size: 15px; font-weight: 600; color: #111827; letter-spacing: -0.01em; }
.topbar-logo span { color: #10b981; }
.status-dot { width: 6px; height: 6px; border-radius: 50%; background: #10b981; flex-shrink: 0; }

/* hamburger — real st.button(key="open_drawer") pinned over the topbar's left edge */
.st-key-open_drawer { position: fixed; top: 7px; left: 14px; z-index: 130; width: 34px; }
.st-key-open_drawer button { width: 34px !important; height: 34px; min-height: 34px; padding: 0;
  border: 0.5px solid #e5e7eb; border-radius: 8px; background: #f9fafb; color: #111827; font-size: 16px; }
.st-key-open_drawer button:hover { background: #f3f4f6; border-color: #d1d5db; }

/* overlay — real full-screen st.button(key="overlay_close"); clicking it closes the
   drawer. The scrim lives on the container (reliable) and the button fills it
   transparently to capture the click. */
.st-key-overlay_close { position: fixed; inset: 0; z-index: 900; width: 100vw !important;
  height: 100vh !important; background: rgba(15,23,42,0.32); }
.st-key-overlay_close .stButton { width: 100%; height: 100%; }
.st-key-overlay_close button { width: 100%; height: 100vh; min-height: 100vh; margin: 0;
  background: transparent !important; border: none !important; border-radius: 0;
  color: transparent !important; box-shadow: none !important; }

/* drawer panel — st.container(key="drawer_panel") pinned to the left.
   Flex column so content stacks top-down; the footer is fixed to the bottom
   (see .drawer-footer), and the panel reserves bottom padding so the last
   status block never hides behind it. */
.st-key-drawer_panel { position: fixed; top: 0; left: 0; bottom: 0; width: 264px; z-index: 901;
  background: #ffffff; border-right: 0.5px solid #e5e7eb; box-shadow: 4px 0 24px rgba(0,0,0,0.06);
  overflow-y: auto; overflow-x: hidden; padding: 0 0 64px; gap: 0;
  display: flex; flex-direction: column; }
.st-key-drawer_panel button {
  width: calc(100% - 12px) !important; margin: 1px 6px !important;
  display: flex !important; justify-content: flex-start !important; align-items: center !important;
  text-align: left !important; background: transparent !important; border: none !important;
  box-shadow: none !important; color: #374151 !important;
  font-size: 13px !important; font-weight: 400 !important;
  padding: 8px 14px !important; border-radius: 8px !important; min-height: 0 !important; }
.st-key-drawer_panel button > div {
  justify-content: flex-start !important; width: 100% !important; }
.st-key-drawer_panel button p { text-align: left !important; margin: 0 !important; }
.st-key-drawer_panel button:hover { background: #f3f4f6 !important; color: #111827 !important; }
.st-key-drawer_panel button[kind="primary"] { background: #eff6ff !important; color: #1d4ed8 !important; font-weight: 500 !important; }
.st-key-drawer_panel [data-testid="stSelectbox"],
.st-key-drawer_panel [data-testid="stCaptionContainer"] { padding-left: 12px; padding-right: 12px; }
/* filter helper line + a clear, card-like select so its purpose reads at a glance.
   Streamlit gives markdown containers a -16px bottom margin (compact spacing) which
   pulls each block up onto the previous one; neutralise it across the bottom group
   so the label, hint, select and status block stack with their own spacing. */
.drawer-filter-hint { font-size: 10.5px; color: #94a3b8; line-height: 1.45; padding: 0 14px 8px; }
.st-key-drawer_bottom [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
.drawer-filter-hint strong { color: #6b7280; font-weight: 600; }
.st-key-drawer_panel [data-baseweb="select"] > div {
  background: #ffffff !important; border-radius: 9px !important; min-height: 40px; }
.st-key-drawer_panel [data-testid="stSelectbox"] label { display: none; }
/* navigation spread across the whole white area between the header and the
   bottom group: its wrapper grows to fill the slack (flex:1) and the nav block
   distributes its items evenly over that full height (space-evenly). */
.st-key-drawer_panel > div:has(> .st-key-drawer_nav) {
  flex: 1 1 auto !important; display: flex !important; flex-direction: column !important; }
.st-key-drawer_nav { flex: 1 1 auto; justify-content: space-evenly !important; gap: 0; }
.st-key-drawer_bottom { gap: 0; }

/* drawer decorative bits */
.drawer-head { height: 48px; border-bottom: 0.5px solid #e5e7eb; display: flex; align-items: center;
               padding: 0 14px; justify-content: space-between; }
.drawer-head .dh-logo { font-size: 15px; font-weight: 600; color: #111827; }
.drawer-head .dh-logo span { color: #10b981; }
.drawer-head .dh-sub { font-size: 11px; color: #6b7280; }
.drawer-section-label { font-size: 9.5px; font-weight: 700; letter-spacing: 0.11em; text-transform: uppercase;
                        color: #9ca3af; padding: 16px 14px 5px; }
.nav-divider { height: 0.5px; background: #eef1f5; margin: 10px 14px; }
.status-block { margin: 12px 12px 4px; padding: 11px 12px; border: 0.5px solid #e5e7eb; border-radius: 10px;
                background: #f9fafb; }
.status-block-row { display: flex; align-items: center; gap: 7px; margin-bottom: 4px; }
.status-block-title { font-size: 12px; font-weight: 600; color: #111827; }
.status-block-sub { font-size: 11px; color: #6b7280; line-height: 1.55; }
/* footer pinned to the bottom of the 264px drawer; white bg + top/right borders
   so scrolling content disappears cleanly behind it. */
.drawer-footer { position: fixed; left: 0; bottom: 0; width: 264px; z-index: 902;
                 box-sizing: border-box; display: flex; align-items: center; gap: 9px;
                 padding: 12px 14px; background: #ffffff;
                 border-top: 0.5px solid #e5e7eb; border-right: 0.5px solid #e5e7eb; }
.drawer-footer-label { font-size: 10px; color: #9ca3af; font-weight: 600; white-space: nowrap;
                       text-transform: uppercase; letter-spacing: 0.06em; }
.drawer-footer-logo { height: 18px; width: auto; opacity: 0.75; }

/* "Explorar em detalhe" chips — real buttons (key=chip_*) styled as cards */
.st-key-explore_chips button { height: auto; min-height: 0; text-align: left; justify-content: flex-start;
  align-items: flex-start; border: 1px solid #e5e7eb; border-radius: 10px; background: #ffffff;
  color: #0f172a; font-weight: 700; font-size: 14px; padding: 0.85rem 1.1rem; box-shadow: none; }
.st-key-explore_chips button:hover { background: #f9fafb; border-color: #cbd5e1; }

/* glossary + reading-guide cards (relocated from the sidebar to the Método tab) */
.gloss-card { background:#ffffff; border:1px solid #e2e8f0; border-radius:8px;
              padding:0.6rem 0.75rem; margin-bottom:0.5rem; }
.gloss-term { font-size:13px; font-weight:600; color:#0f172a; margin-bottom:0.2rem;
              display:flex; justify-content:space-between; align-items:baseline; gap:0.5rem; }
.gloss-unit { font-size:11px; font-weight:400; color:#94a3b8; white-space:nowrap; }
.gloss-def { font-size:12px; color:#64748b; line-height:1.55; }
.step-card { display:flex; gap:0.75rem; align-items:flex-start; margin-bottom:0.75rem; }
.step-card .step-num { font-size:11px; font-weight:700; color:#ffffff; background:#1d4ed8;
                       border-radius:50%; width:22px; height:22px; display:flex; align-items:center;
                       justify-content:center; flex-shrink:0; margin-top:1px; }
.step-card .step-title { font-size:13px; font-weight:600; color:#0f172a; margin-bottom:0.2rem; }
.step-card .step-body { font-size:12px; color:#64748b; line-height:1.55; }
</style>
"""

# ── sidebar CSS ─────────────────────────────────────────────────────────────────
# A single style block injected at the top of the sidebar. Light-theme palette:
# the sidebar background is secondaryBackgroundColor (#f0f4f8), so the cards use
# dark text on white, consistent with the .sb-* classes above.
#
# Scoped under section[data-testid="stSidebar"] on purpose — `.step-num` and
# `.step-title` also exist (with different sizing) for the empty-state cards on
# the MAIN page; scoping stops the sidebar rules from leaking onto those.
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


@st.cache_data(show_spinner=False)
def _read_yaml(path_str: str, mtime: float) -> dict | None:
    # `mtime` participates in the cache key so the entry refreshes on edit (see _read_json).
    try:
        return yaml.safe_load(Path(path_str).read_text())
    except (yaml.YAMLError, OSError):
        return None


def load_yaml(path: Path) -> dict | None:
    """Cached YAML loader — invalidates automatically when the file changes."""
    if not path.exists():
        return None
    return _read_yaml(str(path), path.stat().st_mtime)


@st.cache_data(show_spinner=False)
def _img_data_uri(path_str: str, mtime: float, strip_white: bool) -> str | None:
    # `mtime` keeps the cache key fresh when the asset changes (see _read_json).
    try:
        raw = Path(path_str).read_bytes()
    except OSError:
        return None
    if not strip_white:
        return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
    # Remove a baked-in white background -> transparency. Alpha scales with each
    # pixel's distance from white, so real colours stay fully opaque while the
    # white field drops out and anti-aliased edges keep a smooth ramp. The source
    # PNG on disk is never modified.
    import io

    import numpy as np
    from PIL import Image

    im = Image.open(io.BytesIO(raw)).convert("RGBA")
    arr = np.asarray(im).astype(np.int16)
    mn = arr[..., :3].min(axis=2)  # closest channel to white
    alpha = np.clip((255 - mn) * 3, 0, 255)  # white -> 0, colours -> opaque
    arr[..., 3] = np.minimum(arr[..., 3], alpha)
    buf = io.BytesIO()
    Image.fromarray(arr.astype("uint8"), "RGBA").save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def logo_uri(path: Path, strip_white: bool = False) -> str | None:
    """Inline a PNG logo as a base64 data URI so it can be embedded directly in
    the sidebar's custom HTML (consistent with the rest of the styled markup).
    Pass strip_white=True to drop a solid white background to transparency."""
    if not path.exists():
        return None
    return _img_data_uri(str(path), path.stat().st_mtime, strip_white)


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


def render_kpi_card(col, metric_key: str, value, baseline_val=None) -> None:
    """Custom .kpi-card: label, value and delta. A left accent stripe signals
    improvement (green) or cost (red); neutral metrics (speed) get no stripe.
    The metric definition is exposed as a native hover tooltip via `title`."""
    label, unit, desc = KPI_META.get(metric_key, (metric_key, "", ""))
    neutral = metric_key in HIGHER_IS_BETTER  # speed → neutral framing, no stripe
    stripe_cls = ""
    delta_html = ""
    if value is not None and baseline_val not in (None, 0):
        dabs = value - baseline_val
        p = pct(baseline_val, value)
        improved = (dabs > 0) if metric_key in HIGHER_IS_BETTER else (dabs < 0)
        arrow = "↑" if dabs > 0 else ("↓" if dabs < 0 else "→")
        tone = "kpi-flat" if dabs == 0 else ("kpi-good" if improved else "kpi-bad")
        dtxt = f"{arrow} {dabs:+.1f} {unit}".strip()
        if p is not None:
            dtxt += f" ({p:+.1f}%)"
        delta_html = f'<div class="kpi-delta {tone}">{dtxt}</div>'
        if not neutral and dabs != 0:
            stripe_cls = " kpi-good" if improved else " kpi-bad"
    col.markdown(
        f'<div class="kpi-card{stripe_cls}" title="{desc}">'
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{fmt(value, unit)}</div>'
        f"{delta_html}</div>",
        unsafe_allow_html=True,
    )


def download_csv(df: pd.DataFrame, filename: str, key: str, label: str = "Exportar CSV") -> None:
    st.download_button(
        label,
        df.to_csv(index=False).encode("utf-8"),
        file_name=filename,
        mime="text/csv",
        key=key,
    )


def chart_layout(fig: go.Figure, title: str = "", height: int = 380) -> go.Figure:
    fig.update_layout(
        title={
            "text": title,
            "font": {"size": 13, "color": "#0f172a", "family": "Inter, system-ui"},
            "x": 0,
            "pad": {"b": 8},
        },
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


BUILD_CMD = [
    _bin("python"),
    "src/pps57_sumo/build_network.py",
    "--config",
    "configs/sumo_scenario_base.json",
    "--base-dir",
    "sumo",
]


def _launch_detached(cmd: list[str], success_msg: str) -> None:
    """Start a long-lived / GUI process without blocking Streamlit."""
    try:
        subprocess.Popen(
            cmd, cwd=str(ROOT), env=_sim_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
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
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(ROOT),
                    env=_sim_env(),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
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
    st.caption(
        "Abre uma janela nativa do SUMO no computador onde esta dashboard corre. "
        "Carrega no botão ▶ dentro do SUMO para iniciar a simulação."
    )
    gc1, gc2 = st.columns(2)
    with gc1:
        if st.button("Abrir SUMO-GUI · Baseline", use_container_width=True) and _run_streaming(
            [("build", BUILD_CMD)], "A construir a rede"
        ):
            _launch_detached(
                [_bin("sumo-gui"), "-c", "sumo/corredor.sumocfg"],
                "Janela do SUMO (baseline) a abrir no ambiente de trabalho.",
            )
    with gc2:
        if st.button("Abrir SUMO-GUI · TSP", use_container_width=True) and _run_streaming(
            [("build", BUILD_CMD)], "A construir a rede"
        ):
            _launch_detached(
                [
                    _bin("python"),
                    "scripts/run_tsp_control.py",
                    "--mode",
                    "sumo",
                    "--gui",
                    "--steps",
                    "7200",
                ],
                "Simulação TSP visual a abrir no SUMO-GUI.",
            )

    section("Gerar dados de análise (headless)")
    steps = st.slider(
        "Passos de simulação (TraCI steps)",
        min_value=200,
        max_value=14400,
        value=1200,
        step=200,
        help="Mais passos = simulação mais longa e realista. Autocarros da Linha 25 "
        "precisam de ≥3600 passos para entrar na rede. 2 passos ≈ 1 segundo simulado.",
    )
    hc1, hc2, hc3 = st.columns(3)
    triggered: list[tuple[str, list[str]]] | None = None
    with hc1:
        if st.button("Correr demonstrador TSP", use_container_width=True, type="primary"):
            triggered = [
                ("build", BUILD_CMD),
                (
                    "demonstrador",
                    [_bin("python"), "scripts/run_tsp_demonstrator.py", "--steps", str(steps)],
                ),
            ]
    with hc2:
        if st.button("Comparação Baseline vs RL", use_container_width=True):
            triggered = [
                ("build", BUILD_CMD),
                (
                    "compare-rl",
                    [
                        _bin("python"),
                        "scripts/compare_tsp_baseline_rl.py",
                        "--steps",
                        str(steps),
                        "--train-rl",
                    ],
                ),
            ]
    with hc3:
        if st.button("Cenários multi-seed", use_container_width=True):
            triggered = [
                (
                    "scenario-suite",
                    [
                        _bin("python"),
                        "scripts/run_sumo_scenario.py",
                        "--all",
                        "--run-type",
                        "baseline",
                    ],
                ),
            ]
    st.caption(
        "As simulações headless regeneram os reports e a dashboard recarrega automaticamente no fim. "
        "A janela fica bloqueada durante a execução — acompanha o progresso no log."
    )

    if triggered and _run_streaming(triggered, "A correr simulação"):
        st.cache_data.clear()
        st.success("Dados actualizados. A recarregar a dashboard...")
        st.rerun()

    with st.expander("Requisitos e diagnóstico"):
        gui_ok = (VENV_BIN / "sumo-gui").exists() or shutil.which("sumo-gui")
        net_ok = (ROOT / "sumo" / "network" / "corredor.net.xml").exists()
        st.markdown(f"- **sumo-gui**: {'encontrado' if gui_ok else 'NÃO encontrado'}")
        st.markdown(f"- **SUMO_HOME**: `{_SUMO_HOME or 'não definido'}`")
        st.markdown(
            f"- **Rede construída**: {'sim' if net_ok else 'não — corre um build/demonstrador primeiro'}"
        )
        st.markdown(
            "- A visualização SUMO-GUI só funciona com a dashboard a correr **localmente** "
            "(a janela abre no ecrã desta máquina, não num servidor remoto)."
        )


# ── page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="PPS57 — TSP Analysis",
    page_icon=None,
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items={
        "about": "PPS57 · ROUT25 — Dashboard de análise de Traffic Signal Priority (TSP) "
        "para a Linha 25 do Porto. Compara Baseline SUMO, TSP Rule-based e TSP+RL.",
    },
)
st.markdown(CSS, unsafe_allow_html=True)

# ── load data ─────────────────────────────────────────────────────────────────

demo = load_json(REPORTS / "tsp_demonstrator_report.json")
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
tsp_keys = [k for k in run_kpis if k != baseline_key]
primary_tsp = tsp_keys[0] if tsp_keys else None


def class_vehicle_count(cls_key: str) -> int:
    """Max vehicles of a class across all runs (0 if the class never appears)."""
    return max((kp.get(cls_key, {}).get("vehicles", 0) or 0 for kp in run_kpis.values()), default=0)


# ── drawer content data (glossary + reading guide, rendered in the Método tab) ──

# Glossary entries (the Método-tab search filters reactively on every keystroke)
GLOSSARY = [
    {
        "term": "Perda de tempo média",
        "unit": "segundos (s)",
        "def": "Tempo adicional gasto por veículo face ao percurso sem paragens.",
    },
    {
        "term": "Tempo de espera médio",
        "unit": "segundos (s)",
        "def": "Tempo total parado em semáforos por veículo durante a simulação.",
    },
    {
        "term": "Velocidade média",
        "unit": "m/s",
        "def": "Velocidade média de todos os veículos durante a simulação.",
    },
    {
        "term": "Delta TSP vs Baseline",
        "unit": "%",
        "def": "Variação percentual de cada métrica entre o cenário TSP e o cenário base sem prioridade.",
    },
    {
        "term": "Prioridade semafórica (TSP)",
        "unit": "—",
        "def": "Extensão ou antecipação do verde concedida ao autocarro quando detectado na zona de aproximação.",
    },
    {
        "term": "Classe de veículo",
        "unit": "—",
        "def": "Segmentação dos veículos simulados: autocarros, tráfego geral, veículos prioritários, emergência.",
    },
    {
        "term": "Throughput",
        "unit": "veículos/hora",
        "def": "Número de veículos que completam o percurso por hora de simulação.",
    },
    {
        "term": "Headway",
        "unit": "segundos (s)",
        "def": "Intervalo de tempo entre dois autocarros consecutivos na mesma paragem.",
    },
    {
        "term": "C-ITS",
        "unit": "—",
        "def": "Cooperative Intelligent Transport Systems — comunicação V2I entre o autocarro e o semáforo.",
    },
    {
        "term": "Cenário base (Baseline)",
        "unit": "—",
        "def": "Simulação SUMO sem qualquer prioridade semafórica — referência de comparação.",
    },
    {
        "term": "RL (Reinforcement Learning)",
        "unit": "—",
        "def": "Controlador de semáforo treinado por aprendizagem por reforço, comparado com a regra TSP.",
    },
    {
        "term": "Delay por fase",
        "unit": "segundos (s)",
        "def": "Atraso acumulado durante cada fase semafórica, por classe de veículo.",
    },
]

# Zone C · step-by-step reading guide
STEPS = [
    {
        "n": "1",
        "title": "Lê o veredicto",
        "body": "O banner no topo resume o impacto global do TSP. Verde = melhoria, amarelo = tradeoff a avaliar.",
    },
    {
        "n": "2",
        "title": "Verifica os KPIs",
        "body": "Os três cartões mostram as métricas-chave para a classe seleccionada. O valor a bold é o resultado TSP; a seta é o delta face ao baseline.",
    },
    {
        "n": "3",
        "title": "Analisa o gráfico de barras",
        "body": "Barras à esquerda (verde) = o TSP melhora essa classe. Barras à direita (vermelho) = custo para essa classe. O eixo está centrado em zero.",
    },
    {
        "n": "4",
        "title": "Explora os separadores",
        "body": "Cada separador aprofunda um aspecto: KPIs detalha por métrica, Cenários mostra os 8 casos operacionais, Decisão explica o algoritmo.",
    },
    {
        "n": "5",
        "title": "Muda a classe de veículo",
        "body": "Abre o menu (☰, canto superior esquerdo) e usa o filtro 'Classe de veículo' para ver o impacto do TSP especificamente em autocarros, tráfego geral ou veículos prioritários.",
    },
]

# ── session state ─────────────────────────────────────────────────────────────

if "drawer_open" not in st.session_state:
    st.session_state.drawer_open = False
if "active_tab" not in st.session_state:
    st.session_state.active_tab = "Resumo"

# ── global vehicle-class filter ───────────────────────────────────────────────
# The filter lives in the drawer, but it is computed here at module level so the
# topbar pill and every tab can read it whether or not the drawer is open. The
# selectbox owns the "drawer_class_select" key; we seed it once (so the value
# survives while the drawer is closed) and reset it if a data refresh changed the
# option labels — vehicle counts are baked into each label.
cls_counts = {key: class_vehicle_count(key) for key, _ in VEHICLE_CLASSES}
cls_label_map = {f"{label} ({cls_counts[key]})": key for key, label in VEHICLE_CLASSES}
CLASS_OPTIONS = list(cls_label_map.keys())
# Default to Autocarros — that's where the TSP value lives. Buses are a tiny share
# of all vehicles, so opening on "Todos os veículos" dilutes the gain to ~0 and
# hides what the TSP does. Fall back to "Todos" only if there are no buses.
_default_key = "buses" if cls_counts.get("buses", 0) else "all_vehicles"
_default_display = next(d for d, k in cls_label_map.items() if k == _default_key)
if st.session_state.get("drawer_class_select") not in CLASS_OPTIONS:
    st.session_state.drawer_class_select = _default_display
selected_class = st.session_state.drawer_class_select
vehicle_cls = cls_label_map[selected_class]
vehicle_cls_label = next(lbl for k, lbl in VEHICLE_CLASSES if k == vehicle_cls)

# ── drawer navigation model + system status ───────────────────────────────────

TABS = ["Resumo", "KPIs", "Decisão", "C-ITS", "vs RL", "Cenários", "Método", "Simulação"]

NAV_GROUPS = [
    (None, ["Resumo"]),
    ("Análise", ["KPIs", "Decisão", "C-ITS", "vs RL"]),
    ("Exploração", ["Cenários"]),
    ("Referência", ["Método", "Simulação"]),
]

report_files = {
    "Baseline KPIs": REPORTS / "baseline_kpis.json",
    "Demonstrador TSP": REPORTS / "tsp_demonstrator_report.json",
    "Comparação TSP vs RL": REPORTS / "tsp_baseline_vs_rl_comparison.json",
}
_reports_ok = sum(1 for p in report_files.values() if p.exists())
_reports_total = len(report_files)
_fresh = file_mtime(REPORTS / "tsp_demonstrator_report.json") or file_mtime(
    REPORTS / "baseline_kpis.json"
)
# ── topbar (fixed) + hamburger trigger ────────────────────────────────────────
# The ☰ is a real st.button pinned over the topbar's left edge via its stable
# .st-key-open_drawer class (a plain HTML icon can't trigger a rerun, and
# components/JS are out of scope).
if st.button("☰", key="open_drawer", help="Abrir menu"):
    st.session_state.drawer_open = True
    st.rerun()

st.markdown(
    """
<div class="topbar">
  <div class="topbar-logo">Route<span>_25</span></div>
</div>
""",
    unsafe_allow_html=True,
)

# ── slide-over drawer ─────────────────────────────────────────────────────────

if st.session_state.drawer_open:
    # Overlay: a real full-screen button — clicking anywhere outside closes the
    # drawer (a plain <div> can't trigger a rerun without JS/components).
    if st.button("Fechar menu", key="overlay_close", help="Fechar o menu"):
        st.session_state.drawer_open = False
        st.rerun()

    # Panel: real Streamlit widgets, pinned into the fixed drawer via the
    # container's stable .st-key-drawer_panel class.
    with st.container(key="drawer_panel"):
        st.markdown(
            '<div class="drawer-head"><div class="dh-logo">Route<span>_25</span></div>'
            '<div class="dh-sub">Linha 25 · Porto</div></div>',
            unsafe_allow_html=True,
        )
        # Navigation is vertically centred in the free space between the header
        # and the bottom group via .st-key-drawer_nav { margin: auto 0 }.
        with st.container(key="drawer_nav"):
            if st.button("✕  Fechar", key="close_drawer", use_container_width=True):
                st.session_state.drawer_open = False
                st.rerun()

            for group_label, group_tabs in NAV_GROUPS:
                if group_label:
                    st.markdown(
                        f'<div class="drawer-section-label">{group_label}</div>',
                        unsafe_allow_html=True,
                    )
                for _label in group_tabs:
                    if st.button(
                        _label,
                        key=f"nav_{_label}",
                        use_container_width=True,
                        type="primary" if st.session_state.active_tab == _label else "secondary",
                    ):
                        st.session_state.active_tab = _label
                        st.session_state.drawer_open = False
                        st.rerun()

        # Filter + status are pushed to the bottom of the flex column (just above
        # the fixed footer) via .st-key-drawer_bottom { margin-top: auto }.
        with st.container(key="drawer_bottom"):
            st.markdown('<div class="nav-divider"></div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="drawer-section-label">Filtro global</div>', unsafe_allow_html=True
            )
            st.markdown(
                '<div class="drawer-filter-hint">Aplica-se a <strong>todos</strong> os '
                'KPIs e gráficos</div>',
                unsafe_allow_html=True,
            )
            st.selectbox(
                "Classe de veículo",
                options=CLASS_OPTIONS,
                key="drawer_class_select",
                label_visibility="collapsed",
                help="Classe de veículo aplicada a todos os KPIs e gráficos; o número é a "
                "contagem de veículos. Abre em Autocarros (onde o TSP actua); muda para "
                "'Todos os veículos' para o efeito líquido na rede. Prioritários = Autocarros "
                "+ Emergência; Tráfego geral = não-prioritários.",
            )
            if vehicle_cls == "priority_vehicles" and cls_counts.get("emergency_vehicles", 0) == 0:
                st.caption("Sem veículos de emergência — **Prioritários = Autocarros**.")
            elif vehicle_cls == "emergency_vehicles" and cls_counts.get("emergency_vehicles", 0) == 0:
                st.caption("Sem veículos de emergência — métricas vazias.")

            st.markdown(
                f'<div class="status-block"><div class="status-block-row">'
                f'<div class="status-dot"></div>'
                f'<span class="status-block-title">Sistema activo</span></div>'
                f'<div class="status-block-sub">{_reports_ok}/{_reports_total} relatórios detectados'
                f'{("<br>Atualizado " + _fresh) if _fresh else ""}</div></div>',
                unsafe_allow_html=True,
            )

        _cap_uri = logo_uri(PUBLIC / "CAP_LOGO.png")
        _cap_img = (
            f'<img class="drawer-footer-logo" src="{_cap_uri}" alt="Capgemini">'
            if _cap_uri
            else '<span style="font-size:11px;font-weight:600;color:#6b7280;">Capgemini</span>'
        )
        st.markdown(
            f'<div class="drawer-footer">'
            f'<span class="drawer-footer-label">Developed by</span>{_cap_img}</div>',
            unsafe_allow_html=True,
        )

# ── page header ───────────────────────────────────────────────────────────────

fresh = file_mtime(REPORTS / "tsp_demonstrator_report.json") or file_mtime(
    REPORTS / "baseline_kpis.json"
)
scenario_id = ""
if demo:
    for _r in demo.get("runs", {}).values():
        scenario_id = _r.get("summary", {}).get("scenario_id", "")
        if scenario_id:
            break

_hdr_r25 = logo_uri(PUBLIC / "route25_logo.png", strip_white=True)
_hdr_cap = logo_uri(PUBLIC / "CAP_LOGO.png")

st.markdown(
    f"""
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
""",
    unsafe_allow_html=True,
)

# Brand bar pinned to the bottom of every page (product logo left, partner right).
if _hdr_r25 or _hdr_cap:
    _fl = f'<img class="pf-logo" src="{_hdr_r25}" alt="Route 25">' if _hdr_r25 else "<span></span>"
    _fr = f'<img class="pf-partner" src="{_hdr_cap}" alt="Capgemini">' if _hdr_cap else "<span></span>"
    st.markdown(
        f'<div class="page-footer"><div class="page-footer-inner">{_fl}{_fr}</div></div>',
        unsafe_allow_html=True,
    )

# ── empty state ───────────────────────────────────────────────────────────────

if demo is None and baseline_kpis is None:
    st.markdown(
        """
<div class="empty-wrap" style="margin-bottom:24px">
  <p class="empty-title">Sem dados de simulação disponíveis</p>
  <p class="empty-sub">
    Nenhum report encontrado em <code>reports/</code>.<br>
    Usa o painel abaixo para gerar os dados — ou corre <code>make tsp-demonstrator</code> no terminal.
  </p>
</div>
""",
        unsafe_allow_html=True,
    )
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
    "value_demonstrated": ("is-pass", "Evidência positiva"),
    "passes_primary_demonstrator_goal": ("is-pass", "Objectivo primário demonstrado"),
    "passes_with_general_traffic_cost": (
        "",
        "Ganho no transporte público com custo no tráfego geral",
    ),
    "review": ("", "Em revisão"),
    "inconclusive_missing_bus_kpi": ("is-unknown", "Inconclusivo — KPI de autocarros em falta"),
    "does_not_demonstrate_actuation": ("is-fail", "Sem actuação TSP"),
}

# ── navigation routing ────────────────────────────────────────────────────────
# st.tabs is gone — the drawer sets st.session_state.active_tab, and each former
# tab body now renders inside an if/elif on that value (bodies unchanged).
_active = st.session_state.active_tab

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 0 — Resumo (narrativa guiada: a resposta primeiro)
# ═══════════════════════════════════════════════════════════════════════════════

HERO_CLASSES = [
    ("emergency_vehicles", "Emergência"),
    ("buses", "Autocarros"),
    ("priority_vehicles", "Prioritários"),
    ("general_traffic", "Tráfego geral"),
    ("all_vehicles", "Todos os veículos"),
]

if _active == "Resumo":
    # ── verdict ───────────────────────────────────────────────────────────────
    if demo and "verdict" in demo:
        v = demo["verdict"]
        vmod, vtitle = VERDICT_MAP.get(v.get("status", ""), ("is-unknown", "Estado desconhecido"))
        st.markdown(
            f'<div class="verdict-card {vmod}">'
            f'<p class="verdict-headline">Veredicto · {vtitle}</p>'
            f'<p class="verdict-support">{v.get("reason", v.get("status", ""))}</p></div>',
            unsafe_allow_html=True,
        )

    if not (baseline_key and primary_tsp):
        st.info(
            "Sem um par baseline + TSP para resumir. Corre o demonstrador no separador Simulação."
        )
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
                hero.append(
                    {
                        "Classe": label,
                        "key": key,
                        "pct": (tv - bv) / bv * 100,
                        "n": n,
                        "baseline": bv,
                        "tsp": tv,
                    }
                )

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
            render_kpi_card(
                m1,
                "mean_time_loss_s",
                tk.get(bcls, {}).get("mean_time_loss_s"),
                bk.get(bcls, {}).get("mean_time_loss_s"),
            )
            render_kpi_card(
                m2,
                "mean_waiting_time_s",
                tk.get(bcls, {}).get("mean_waiting_time_s"),
                bk.get(bcls, {}).get("mean_waiting_time_s"),
            )
            render_kpi_card(
                m3,
                "mean_speed_mps",
                tk.get(bcls, {}).get("mean_speed_mps"),
                bk.get(bcls, {}).get("mean_speed_mps"),
            )
            st.caption(
                f"Classe {bus['Classe'].lower()} ({bus['n']} veículos) · "
                f"{primary_tsp} vs {baseline_key}. Verde = melhoria, vermelho = custo."
            )

        # ── hero chart: who benefits ──────────────────────────────────────────
        # Title lives above the chart (st.markdown/section), not inside the figure.
        section("Quem ganha com o TSP — variação da perda de tempo por classe")
        if hero:
            dfh = pd.DataFrame(hero)
            # Absolute delta in seconds per class — shown alongside the % so the
            # magnitude is visible (e.g. -22% reads as ~-72 s/bus, while +0.3% on
            # general traffic is well under a second). The % alone understates how
            # asymmetric — and favourable — the tradeoff is.
            dfh["delta_s"] = dfh["tsp"] - dfh["baseline"]

            def _fmt_delta_s(ds: float) -> str:
                # sub-10 s deltas keep one decimal so tiny costs don't round to "+1 s"
                return f"{ds:+.1f} s" if abs(ds) < 10 else f"{ds:+.0f} s"

            bar_labels = [
                f"{p:+.1f}% · {_fmt_delta_s(ds)}"
                for p, ds in zip(dfh["pct"], dfh["delta_s"], strict=False)
            ]

            # Diverging x-axis centred on zero. Range comes from the data (plus zero)
            # and is padded enough that the now-wider outside labels stay inside the
            # figure — never hardcoded, never clipped.
            vals = dfh["pct"].tolist()
            lo_v, hi_v = min(vals + [0.0]), max(vals + [0.0])
            pad = max(3.0, (hi_v - lo_v) * 0.32)
            fig_hero = go.Figure(
                go.Bar(
                    x=dfh["pct"],
                    y=dfh["Classe"],
                    orientation="h",
                    marker_color=[COLOR_GOOD if p < 0 else COLOR_BAD for p in dfh["pct"]],
                    text=bar_labels,
                    textposition="outside",
                    cliponaxis=False,
                    customdata=dfh[["baseline", "tsp", "n"]].values,
                    hovertemplate="%{y}: %{x:+.1f}%<br>%{customdata[0]:.0f}s → %{customdata[1]:.0f}s "
                    "(n=%{customdata[2]})<extra></extra>",
                )
            )
            fig_hero.add_vline(x=0, line_color="#6b7280", line_width=1)
            chart_layout(fig_hero, "", height=280)
            fig_hero.update_layout(bargap=0.35)
            fig_hero.update_xaxes(range=[lo_v - pad, hi_v + pad])
            st.plotly_chart(fig_hero, use_container_width=True, config={"displayModeBar": False})

            # ── legend: colour squares instead of a wall of italic text ─────────
            lg1, lg2 = st.columns(2)
            lg1.markdown(":green[■] **Melhoria** (barras à esquerda)")
            lg2.markdown(":red[■] **Custo** (barras à direita)")
            if not any(r["key"] == "emergency_vehicles" for r in hero):
                st.caption(
                    "Emergência não aparece aqui (o cenário base não tem veículos de emergência). "
                    "Vê o separador **Cenários → emergency_vehicle_conflict** para o caso de emergência."
                )

        # ── navigation chips: click jumps to the matching view ────────────────
        # Real st.buttons (styled as cards via .st-key-explore_chips) set
        # active_tab and rerun — the drawer-driven equivalent of the old tabs.
        section("Explorar em detalhe")
        nav_items = [
            ("KPIs", "Comparação detalhada entre cenários, por classe e métrica."),
            ("Decisão", "O que o algoritmo decidiu e porquê."),
            ("Cenários", "Impacto do TSP nas 8 situações operacionais."),
        ]
        with st.container(key="explore_chips"):
            chip_cols = st.columns(3)
            for chip_col, (chip_label, chip_desc) in zip(chip_cols, nav_items, strict=False):
                with chip_col:
                    if st.button(
                        f"{chip_label} →", key=f"chip_{chip_label}", use_container_width=True
                    ):
                        st.session_state.active_tab = chip_label
                        st.rerun()
                    st.caption(chip_desc)

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — KPI comparison
# ═══════════════════════════════════════════════════════════════════════════════

elif _active == "KPIs":
    if not cls_data:
        st.info("Sem dados de KPI disponíveis.")
    else:
        # ── contextual hint: TSP gains live in the bus class ──────────────────
        bus_n = cls_counts.get("buses", 0)
        if vehicle_cls in ("all_vehicles", "general_traffic", "non_priority_vehicles") and bus_n:
            hint_col, btn_col = st.columns([4, 1])
            with hint_col:
                insight(
                    "O TSP é <strong>prioridade ao transporte público</strong>: melhora os "
                    f"<strong>{bus_n} autocarros</strong>, não o tráfego geral. Nesta vista "
                    f"(<strong>{vehicle_cls_label.lower()}</strong>) o ganho dos autocarros dilui-se "
                    "na média e sobra um pequeno custo no tráfego geral — por desenho. "
                    "Para ver os ganhos da prioridade, filtra por <strong>Autocarros</strong>."
                )
            with btn_col:
                bus_display = f"Autocarros ({bus_n})"
                if bus_display in cls_label_map:

                    def _focus_buses(target=bus_display):
                        # drives the drawer's class selectbox (single source of truth)
                        st.session_state["drawer_class_select"] = target

                    st.button(
                        "Ver autocarros",
                        use_container_width=True,
                        on_click=_focus_buses,
                        help="Muda o filtro para a classe Autocarros.",
                    )

        # ── interactive A/B selector ──────────────────────────────────────────
        section("Comparação interactiva entre dois cenários")
        opts = list(run_kpis.keys())
        sc1, sc2 = st.columns(2)
        ref_idx = opts.index(baseline_key) if baseline_key in opts else 0
        cmp_idx = opts.index(primary_tsp) if primary_tsp in opts else min(1, len(opts) - 1)
        ref_run = sc1.selectbox(
            "Cenário de referência",
            opts,
            index=ref_idx,
            help="O ponto de comparação (tipicamente o baseline sem TSP).",
        )
        cmp_run = sc2.selectbox(
            "Cenário a comparar",
            opts,
            index=cmp_idx,
            help="O cenário cujo desempenho se quer avaliar.",
        )

        ref_data = cls_data.get(ref_run, {})
        cmp_data = cls_data.get(cmp_run, {})

        if ref_run == cmp_run:
            st.info("Selecciona dois cenários diferentes para ver a comparação.")
        else:
            card_metrics = [
                "mean_time_loss_s",
                "mean_waiting_time_s",
                "mean_duration_s",
                "p95_time_loss_s",
            ]
            ccols = st.columns(len(card_metrics))
            for col, m in zip(ccols, card_metrics, strict=False):
                render_kpi_card(col, m, cmp_data.get(m), ref_data.get(m))
            insight(
                f"Cartões: valor de <strong>{cmp_run}</strong>, delta vs <strong>{ref_run}</strong>. "
                "Passa o rato sobre cada cartão para ver a definição da métrica."
            )

        # ── grouped bar chart — all runs ──────────────────────────────────────
        section("Comparação de métricas entre todos os cenários")
        plot_metrics = [
            "mean_time_loss_s",
            "mean_waiting_time_s",
            "mean_duration_s",
            "mean_depart_delay_s",
            "p95_time_loss_s",
        ]
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
                "Métricas a mostrar",
                options=df["Métrica"].unique().tolist(),
                default=df["Métrica"].unique().tolist()[:3],
                help="Adiciona ou remove métricas do gráfico.",
            )
            df_plot = df[df["Métrica"].isin(sel_metrics)] if sel_metrics else df
            colors = [run_color(r) for r in df_plot["Cenário"].unique()]
            fig = px.bar(
                df_plot,
                x="Valor",
                y="Métrica",
                color="Cenário",
                barmode="group",
                orientation="h",
                color_discrete_sequence=colors,
                height=max(300, len(sel_metrics or plot_metrics) * 78 + 80),
            )
            fig.update_traces(
                texttemplate="%{x:.1f}",
                textposition="outside",
                hovertemplate="%{y}<br>%{fullData.name}: %{x:.1f}<extra></extra>",
            )
            chart_layout(fig, "KPIs por cenário (segundos)")
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
            insight(
                "Barras mais curtas = melhor desempenho nas métricas de tempo. "
                "Compare o <strong>baseline</strong> (cinzento) com os cenários TSP para quantificar o ganho."
            )

        # ── delta chart — cmp vs ref ──────────────────────────────────────────
        if ref_run != cmp_run:
            section(f"Variação por métrica — {cmp_run} vs {ref_run}")
            wf_rows = []
            for m in [
                "mean_time_loss_s",
                "mean_waiting_time_s",
                "mean_duration_s",
                "p95_time_loss_s",
                "mean_depart_delay_s",
            ]:
                bv, tv = ref_data.get(m), cmp_data.get(m)
                if bv and tv:
                    label, _, _ = KPI_META[m]
                    wf_rows.append(
                        {
                            "Métrica": label,
                            "Delta": round(tv - bv, 2),
                            "Pct": round((tv - bv) / bv * 100, 1),
                        }
                    )
            if wf_rows:
                df_wf = pd.DataFrame(wf_rows)
                fig_wf = go.Figure(
                    go.Bar(
                        x=df_wf["Delta"],
                        y=df_wf["Métrica"],
                        orientation="h",
                        text=[f"{p:+.1f}%" for p in df_wf["Pct"]],
                        textposition="outside",
                        marker_color=["#22c55e" if v < 0 else "#ef4444" for v in df_wf["Delta"]],
                        hovertemplate="%{y}: %{x:+.1f}s<extra></extra>",
                    )
                )
                fig_wf.add_vline(x=0, line_width=2, line_color="#334155")
                chart_layout(
                    fig_wf, "Ganho absoluto (s) — verde reduz, vermelho aumenta", height=320
                )
                st.plotly_chart(fig_wf, use_container_width=True, config={"displayModeBar": False})
                insight(
                    "Verde = melhoria (redução do tempo). Vermelho = degradação. "
                    "A linha vertical é o cenário de referência. Percentagens = variação relativa."
                )

        # ── detailed comparison tables ────────────────────────────────────────
        if demo:
            section("Tabelas de comparação detalhada")
            comp_map = [
                ("tsp_vs_sumo_baseline_kpis", "TSP vs Baseline"),
                ("tsp_controller_vs_sumo_baseline_kpis", "TSP+Controller vs Baseline"),
                ("tsp_controller_vs_tsp_runtime", "TSP+Controller vs TSP"),
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
                    rows_out.append(
                        {
                            "Métrica": lab or mk,
                            "Unidade": unit,
                            "Baseline": fmt(bv),
                            "TSP / Controller": fmt(cv),
                            "Δ absoluto": fmt(r.get("delta")),
                            "Δ relativo": f"{p:+.1f}%" if p is not None else "—",
                        }
                    )
                if rows_out:
                    with st.expander(title, expanded=(ckey == "tsp_vs_sumo_baseline_kpis")):
                        df_comp = pd.DataFrame(rows_out)

                        def _color_delta(col):
                            out = []
                            for v in col:
                                try:
                                    f = float(str(v).replace("%", "").replace("+", ""))
                                    out.append(
                                        "color:#15803d;font-weight:600"
                                        if f < 0
                                        else ("color:#dc2626;font-weight:600" if f > 0 else "")
                                    )
                                except (ValueError, TypeError):
                                    out.append("")
                            return out

                        st.dataframe(
                            df_comp.style.apply(_color_delta, subset=["Δ absoluto", "Δ relativo"]),
                            use_container_width=True,
                            hide_index=True,
                        )
                        download_csv(df_comp, f"{ckey}.csv", key=f"dl_{ckey}")

        # ── P95 vs mean ───────────────────────────────────────────────────────
        section("Distribuição — média vs P95 (perda de tempo)")
        dist_rows = []
        for run_label, data in cls_data.items():
            if data.get("mean_time_loss_s") is not None:
                dist_rows.append(
                    {"Cenário": run_label, "Tipo": "Média", "Valor (s)": data["mean_time_loss_s"]}
                )
            if data.get("p95_time_loss_s") is not None:
                dist_rows.append(
                    {"Cenário": run_label, "Tipo": "P95", "Valor (s)": data["p95_time_loss_s"]}
                )
        if dist_rows:
            df_dist = pd.DataFrame(dist_rows)
            colors_dist = [run_color(r) for r in df_dist["Cenário"].unique()]
            fig_dist = px.bar(
                df_dist,
                x="Cenário",
                y="Valor (s)",
                color="Cenário",
                facet_col="Tipo",
                barmode="group",
                color_discrete_sequence=colors_dist,
                height=320,
            )
            chart_layout(fig_dist, "Perda de tempo: média e cauda da distribuição (P95)")
            fig_dist.update_layout(showlegend=False)
            st.plotly_chart(fig_dist, use_container_width=True, config={"displayModeBar": False})
            insight(
                "O P95 representa os 5% de viagens com pior desempenho — a cauda é relevante para "
                "avaliar equidade e o pior caso. Um bom TSP reduz tanto a média como o P95."
            )

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2 — TSP decision engine
# ═══════════════════════════════════════════════════════════════════════════════

elif _active == "Decisão":
    if not demo:
        st.info("Report do demonstrador não disponível.")
    else:
        all_labels = list(demo.get("runs", {}).keys())
        sel_run = st.selectbox(
            "Run TSP",
            options=all_labels,
            index=next((i for i, k in enumerate(all_labels) if k != "sumo_baseline"), 0),
            help="Escolhe a run cujo motor de decisão queres analisar.",
        )
        runtime = demo["runs"][sel_run].get("runtime", {})

        total = runtime.get("total_decisions", 0)
        applied = runtime.get("applied_events", 0)
        blocked = runtime.get("blocked_by_safety", 0)
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

        # ── plain-language headline (lead with the story) ─────────────────────
        if total > 0:
            ar_txt = f"{applied / actionable * 100:.0f}%" if actionable else "—"
            block_txt = (
                f" A Safety Layer bloqueou **{blocked}** por segurança."
                if blocked
                else " Nenhuma foi bloqueada pela Safety Layer."
            )
            st.markdown(
                f"#### De **{total}** decisões avaliadas, **{actionable}** propuseram mudar o "
                f"semáforo e **{applied}** foram aplicadas ({ar_txt}).{block_txt}"
            )
            st.caption(
                "O motor reavalia cada autocarro várias vezes na aproximação; a maioria das "
                "avaliações conclui, correctamente, que não há nada a fazer naquele instante."
            )

        section("Pipeline de decisão — do seguimento à actuação")
        if total == 0:
            warn(
                "Esta run não gerou decisões TSP. Selecciona uma run TSP "
                "(ex. <code>tsp</code> ou <code>tsp_controller</code>) para ver a análise."
            )
        else:
            col_f, col_m = st.columns([1, 1])
            with col_f:
                fig_funnel = go.Figure(
                    go.Funnel(
                        y=[
                            "Decisões avaliadas",
                            "Accionáveis (propõem mudança)",
                            "Aplicadas em rede",
                        ],
                        x=[total, actionable, applied],
                        textinfo="value+percent initial",
                        marker_color=["#94a3b8", "#1d6ef5", "#22c55e"],
                        hovertemplate="%{y}: %{x}<extra></extra>",
                    )
                )
                chart_layout(fig_funnel, "Funil de decisão TSP", height=300)
                st.plotly_chart(fig_funnel, use_container_width=True, config={"displayModeBar": False})
            with col_m:
                st.markdown("&nbsp;")
                mm1, mm2 = st.columns(2)
                mm1.metric(
                    "Decisões avaliadas",
                    total,
                    border=True,
                    help="Total de avaliações do motor. Cada autocarro é reavaliado "
                    "várias vezes ao longo da aproximação, por isso este número é "
                    "muito maior que o nº de autocarros.",
                )
                mm2.metric(
                    "Accionáveis",
                    actionable,
                    border=True,
                    help="Decisões que propuseram uma mudança real ao semáforo "
                    "(extensão de verde + verde antecipado).",
                )
                mm3, mm4 = st.columns(2)
                mm3.metric(
                    "Aplicadas em rede",
                    applied,
                    border=True,
                    help="Accionáveis que passaram a Safety Layer e foram aplicadas via TraCI.",
                )
                mm4.metric(
                    "Bloqueadas (safety)",
                    blocked,
                    border=True,
                    help="Accionáveis barradas pela Safety Layer por risco de segurança.",
                )
                ar = f"{applied / actionable * 100:.0f}%" if actionable else "—"
                st.caption(
                    f"Taxa de aplicação: **{ar}** ({applied}/{actionable} accionáveis aplicadas)"
                    + (f" · {rejected} rejeições do controller" if rejected else "")
                )

            insight(
                "As <strong>decisões avaliadas</strong> incluem cada vez que um autocarro em "
                "aproximação é reavaliado. Só uma fracção propõe mudar o semáforo "
                "(<strong>accionáveis</strong>); destas, a Safety Layer só barra as inseguras. "
                "A taxa correcta é aplicadas/accionáveis — não aplicadas/avaliadas."
            )

            # explain the non-actionable bulk so the total→actionable drop is clear
            na_total = sum(non_actionable.values())
            if na_total:
                with st.expander(
                    f"Porque é que {na_total} decisões não actuaram? (não-actuações deliberadas)"
                ):
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
                fig_pie = go.Figure(
                    go.Pie(
                        labels=[ACTION_META.get(k, (k, "", ""))[0] for k in labels_a],
                        values=list(by_action.values()),
                        marker_colors=[
                            ACTION_META.get(k, ("", "#94a3b8", ""))[1] for k in labels_a
                        ],
                        hole=0.45,
                        textinfo="label+percent",
                        textfont={"size": 11},
                        hovertemplate="%{label}: %{value} (%{percent})<extra></extra>",
                    )
                )
                chart_layout(fig_pie, "Acções do motor TSP", height=320)
                fig_pie.update_layout(showlegend=False)
                st.plotly_chart(fig_pie, use_container_width=True, config={"displayModeBar": False})
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

        # ── decision-quality KPIs: dose, attribution ──────────────────────────
        green = runtime.get("green_time", {})
        score_attr = runtime.get("score_attribution", {})

        section("Verde concedido ao transporte público")
        gt_total = green.get("applied_extension_s_total", 0) or 0
        if gt_total:
            st.markdown(
                f"#### O motor concedeu **{gt_total:.1f} s** de verde extra ao transporte público, "
                f"em **{green.get('n_extensions', 0)}** extensões aplicadas "
                f"(média {green.get('mean_extension_s', 0):.1f} s, máx {green.get('max_extension_s', 0):.1f} s)."
            )
            gq1, gq2, gq3, gq4 = st.columns(4)
            gq1.metric(
                "Verde total concedido",
                f"{gt_total:.1f} s",
                border=True,
                help="Soma do verde de extensão em decisões efectivamente aplicadas na rede via "
                "TraCI (excl. runs em modo --no-actuation e rejeições do controlador). "
                "É a dose entregue à rede, não a aprovada.",
            )
            gq2.metric(
                "Extensão média",
                f"{green.get('mean_extension_s', 0):.1f} s",
                border=True,
                help="Média de segundos por extensão de verde concedida.",
            )
            gq3.metric(
                "Extensão máxima",
                f"{green.get('max_extension_s', 0):.1f} s",
                border=True,
                help="Maior extensão de verde aplicada numa única decisão.",
            )
            gq4.metric(
                "Nº de extensões",
                green.get("n_extensions", 0),
                border=True,
                help="Decisões aprovadas que adicionaram segundos de verde.",
            )
            insight(
                "Esta é a <strong>intensidade</strong> da prioridade: quanto verde o TSP "
                "efectivamente injectou na rede. Liga-se à equidade — esse verde é depois "
                "compensado às outras fases (ver <strong>C-ITS → Compensação de verde</strong>)."
            )
        else:
            st.caption("Sem extensões de verde aplicadas nesta run.")

        section("O que motivou as actuações — decomposição do priority score")
        if score_attr:
            items = sorted(score_attr.items(), key=lambda kv: kv[1], reverse=True)
            labels_o = [OBJECTIVE_LABELS.get(k, k) for k, _ in items]
            vals_o = [v for _, v in items]
            fig_o = go.Figure(
                go.Bar(
                    x=vals_o,
                    y=labels_o,
                    orientation="h",
                    marker_color="#1d6ef5",
                    texttemplate="%{x:.3f}",
                    textposition="outside",
                    cliponaxis=False,
                    hovertemplate="%{y}: contribuição média %{x:.3f}<extra></extra>",
                )
            )
            chart_layout(fig_o, "", height=max(220, len(items) * 46 + 80))
            fig_o.update_xaxes(range=[0, max(vals_o) * 1.25 + 0.01])
            st.plotly_chart(fig_o, use_container_width=True, config={"displayModeBar": False})
            top_obj = labels_o[0] if labels_o else "—"
            insight(
                "Contribuição média de cada objectivo para o priority score das decisões que "
                f"<strong>actuaram</strong>. O motor agiu sobretudo por <strong>{top_obj.lower()}</strong>. "
                "A soma das barras ≈ score médio das actuações; objectivos a zero não tiveram peso "
                "neste cenário (ex. headway, quando não há bunching)."
            )
        else:
            st.caption("Sem decomposição de score disponível (sem actuações aprovadas nesta run).")

        safety_reasons = runtime.get("safety_block_by_reason", {})
        section("Bloqueios da Safety Layer por motivo")
        if safety_reasons:
            df_sf = pd.DataFrame(
                {"Motivo": list(safety_reasons.keys()), "Bloqueios": list(safety_reasons.values())}
            ).sort_values("Bloqueios")
            fig_sf = px.bar(
                df_sf,
                x="Bloqueios",
                y="Motivo",
                orientation="h",
                color_discrete_sequence=["#ef4444"],
                height=max(260, len(df_sf) * 50 + 80),
            )
            chart_layout(fig_sf, "Safety Layer — motivos de bloqueio")
            st.plotly_chart(fig_sf, use_container_width=True, config={"displayModeBar": False})
            insight(
                "A Safety Layer bloqueia actuações que criem conflitos: amarelo insuficiente, "
                "violação de verde mínimo/máximo, cooldown entre actuações ou conflito de fases."
            )
        else:
            st.caption("Sem bloqueios de segurança registados nesta run.")

        per_tls = runtime.get("per_tls", {})
        if per_tls:
            section("Actividade por semáforo (TLS)")
            tls_rows = [
                {
                    "Semáforo": tid,
                    "Decisões": d.get("decisions", 0),
                    "Aplicadas": d.get("applied_events", 0),
                    "Bloqueadas": d.get("safety_blocks", 0) or d.get("blocked_by_safety", 0),
                    "Taxa aplicação": (
                        f"{d.get('applied_events', 0) / d.get('decisions', 1) * 100:.0f}%"
                        if d.get("decisions", 0)
                        else "—"
                    ),
                }
                for tid, d in per_tls.items()
            ]
            df_tls = pd.DataFrame(tls_rows).sort_values("Decisões", ascending=False)
            st.dataframe(df_tls, use_container_width=True, hide_index=True)
            download_csv(df_tls, f"per_tls_{sel_run}.csv", key=f"dl_tls_{sel_run}")

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3 — C-ITS Pipeline
# ═══════════════════════════════════════════════════════════════════════════════

elif _active == "C-ITS":
    if not demo:
        st.info("Report do demonstrador não disponível.")
    else:
        tsp_run_keys = [k for k in demo.get("runs", {}) if k != "sumo_baseline"]
        if not tsp_run_keys:
            st.info("Sem runs TSP disponíveis.")
        else:
            sel_cits_run = st.selectbox(
                "Run",
                tsp_run_keys,
                key="cits_run",
                help="Run cujo tráfego C-ITS (V2X) queres inspeccionar.",
            )
            summ = demo["runs"][sel_cits_run].get("summary", {})
            by_type = summ.get("cits_by_type", {})
            prl = summ.get("priority_request_lifecycle", {})

            # ── plain-language intro: the V2X conversation ────────────────────
            srem_n = by_type.get("SREM", 0)
            ssem_n = by_type.get("SSEM", 0)
            granted_n = prl.get("granted_requests", 0)
            tracked_n = prl.get("tracked_requests", 0)
            if srem_n or ssem_n:
                grant_txt = (
                    f", e **{granted_n}** dos **{tracked_n}** pedidos foram concedidos"
                    if tracked_n
                    else ""
                )
                st.markdown(
                    f"#### Os veículos prioritários e os semáforos trocaram **{srem_n + ssem_n:,}** "
                    f"mensagens de prioridade — **{srem_n:,}** pedidos (SREM) e **{ssem_n:,}** "
                    f"respostas (SSEM){grant_txt}."
                )
            st.markdown(
                """
<div class="flow">
  <div class="flow-step"><div class="ft">1 · MAPEM</div><div class="fd">O semáforo anuncia o mapa das aproximações</div></div>
  <span class="flow-arrow">›</span>
  <div class="flow-step"><div class="ft">2 · SPATEM</div><div class="fd">Difunde o estado das fases em tempo real</div></div>
  <span class="flow-arrow">›</span>
  <div class="flow-step"><div class="ft">3 · SREM</div><div class="fd">O veículo pede prioridade ao aproximar-se</div></div>
  <span class="flow-arrow">›</span>
  <div class="flow-step"><div class="ft">4 · SSEM</div><div class="fd">O RSU responde: concede ou recusa</div></div>
</div>
""",
                unsafe_allow_html=True,
            )
            st.caption(
                "É esta conversa V2X (vehicle-to-everything) que alimenta o motor de decisão TSP."
            )

            section("Volume de mensagens C-ITS por tipo")
            if by_type:
                cits_descs = {
                    "MAPEM": "Informação topológica da rede semafórica",
                    "SPATEM": "Estado em tempo real de cada fase semafórica",
                    "SREM": "Pedido de prioridade enviado pelo autocarro",
                    "SSEM": "Resposta do RSU ao pedido de prioridade",
                }
                col_chart, col_desc = st.columns([1, 1])
                with col_chart:
                    df_ct = pd.DataFrame(
                        {"Tipo": list(by_type.keys()), "Mensagens": list(by_type.values())}
                    )
                    fig_ct = px.bar(
                        df_ct,
                        x="Tipo",
                        y="Mensagens",
                        color="Tipo",
                        color_discrete_sequence=["#1d4ed8", "#0891b2", "#7c3aed", "#059669"],
                        height=320,
                        log_y=True,
                    )
                    fig_ct.update_layout(showlegend=False)
                    fig_ct.update_traces(hovertemplate="%{x}: %{y}<extra></extra>")
                    chart_layout(fig_ct, "Mensagens por protocolo C-ITS (escala log)")
                    st.plotly_chart(fig_ct, use_container_width=True, config={"displayModeBar": False})
                with col_desc:
                    st.markdown("&nbsp;")
                    for mtype, mdesc in cits_descs.items():
                        cnt = by_type.get(mtype, 0)
                        st.markdown(
                            f"**{mtype}** — {cnt:,} mensagens  \n"
                            f'<span style="font-size:0.78rem;color:#64748b">{mdesc}</span>',
                            unsafe_allow_html=True,
                        )
                        st.markdown("")
                insight(
                    "Escala logarítmica no eixo Y porque o SPATEM (estado de fase, emitido a cada "
                    "passo) domina em volume face aos pedidos pontuais (SREM/SSEM)."
                )

            section("Saúde do transporte de mensagens")
            mt = summ.get("message_transport", {})
            if mt:
                mc1, mc2, mc3, mc4 = st.columns(4)
                published = mt.get("published", 0) or 0
                delivered = mt.get("delivered", 0) or 0
                rate = f"{delivered / published * 100:.0f}%" if published else "—"
                mc1.metric("Publicadas", f"{published:,}", border=True)
                mc2.metric("Entregues", f"{delivered:,}", border=True)
                mc3.metric(
                    "Perdidas",
                    mt.get("dropped", "—"),
                    border=True,
                    help="Mensagens que não chegaram ao destino.",
                )
                mc4.metric("Taxa de entrega", rate, border=True)
                if mt.get("dropped", 0) == 0:
                    insight(
                        "Taxa de entrega: <strong>100%</strong> — nenhuma mensagem perdida no canal C-ITS simulado."
                    )

            section("Ciclo de vida dos pedidos de prioridade (SREM/SSEM)")
            prl = summ.get("priority_request_lifecycle", {})
            if prl:
                lifecycle = {
                    "Tracked": prl.get("tracked_requests", 0),
                    "Granted": prl.get("granted_requests", 0),
                    "Cleared": prl.get("cleared_requests", 0),
                    "Expired": prl.get("expired_requests", 0),
                }
                df_prl = pd.DataFrame(
                    {"Estado": list(lifecycle.keys()), "Pedidos": list(lifecycle.values())}
                )
                fig_prl = px.bar(
                    df_prl,
                    x="Estado",
                    y="Pedidos",
                    color="Estado",
                    color_discrete_sequence=["#1d6ef5", "#22c55e", "#94a3b8", "#ef4444"],
                    height=300,
                )
                fig_prl.update_layout(showlegend=False)
                chart_layout(fig_prl, "Pedidos de prioridade — estados no ciclo de vida")
                st.plotly_chart(fig_prl, use_container_width=True, config={"displayModeBar": False})
                insight(
                    "<strong>Granted</strong> = prioridade concedida. <strong>Cleared</strong> = "
                    "pedido concluído (autocarro passou). <strong>Expired</strong> = timeout sem "
                    "concessão. Granted/Tracked = taxa de sucesso do TSP."
                )

            gc = summ.get("green_compensation", {})
            if gc.get("enabled"):
                section("Compensação de verde (equidade)")
                g1, g2, g3 = st.columns(3)
                g1.metric("Eventos de compensação", gc.get("events", 0), border=True)
                g2.metric("Verde concedido (s)", fmt(gc.get("granted_s_total")), border=True)
                g3.metric("Verde recuperado (s)", fmt(gc.get("reclaimed_s_total")), border=True)
                insight(
                    'A compensação devolve nos ciclos seguintes o verde "emprestado" às outras '
                    "fases para dar prioridade ao autocarro, mantendo a equidade semafórica."
                )

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Baseline vs RL
# ═══════════════════════════════════════════════════════════════════════════════

elif _active == "vs RL":
    if not rl_comparison:
        warn(
            "Report de comparação Baseline vs RL não disponível. "
            "Corre <code>make compare-tsp-rl</code> para gerar este relatório."
        )
    else:
        matched = rl_comparison.get("matched_decision_count", 0) or 0
        net_verdict = rl_comparison.get("network_impact_verdict", "—")
        st.markdown(
            f"#### Comparámos **{matched}** decisões da política RL com a baseline rule-based. "
            f"Veredicto de impacto na rede: **{net_verdict}**."
        )
        st.caption(
            "A política RL é treinada offline e avaliada contra a regra heurística; aqui vê-se "
            "onde diverge e se as divergências melhoram o resultado."
        )

        rc1, rc2, rc3 = st.columns(3)
        rc1.metric("Decisões comparadas", matched, border=True)
        rc2.metric("Veredicto de rede", net_verdict, border=True)
        rc3.metric(
            "Tipo de avaliação", rl_comparison.get("evaluation", "—").replace("_", " "), border=True
        )

        section("Distribuição de veredictos por decisão")
        vc = rl_comparison.get("verdict_counts", {})
        if vc:
            df_vc = pd.DataFrame({"Veredicto": list(vc.keys()), "Contagem": list(vc.values())})
            fig_vc = px.bar(
                df_vc,
                x="Veredicto",
                y="Contagem",
                color="Veredicto",
                color_discrete_sequence=["#22c55e", "#ef4444", "#94a3b8", "#f59e0b"],
                height=320,
            )
            fig_vc.update_layout(showlegend=False)
            chart_layout(fig_vc, "Veredictos da política RL vs baseline rule-based")
            st.plotly_chart(fig_vc, use_container_width=True, config={"displayModeBar": False})
            insight(
                "Cada decisão compara a acção da política RL com a rule-based. Veredicto positivo = "
                "RL escolheu acção com melhor valor estimado de recompensa."
            )

        kpi_eval = rl_comparison.get("kpi_evaluation", {})
        if kpi_eval.get("available") and kpi_eval.get("rows"):
            section("KPIs — Baseline vs RL")
            rl_rows = []
            for r in kpi_eval["rows"]:
                mk = r.get("metric", "")
                lab, _, _ = KPI_META.get(mk, (mk, "", ""))
                bv, rv = r.get("baseline"), r.get("rl")
                p = pct(bv, rv)
                rl_rows.append(
                    {
                        "Métrica": lab or mk,
                        "Baseline": fmt(bv),
                        "RL": fmt(rv),
                        "Δ (s)": fmt(r.get("delta")),
                        "Δ (%)": f"{p:+.1f}%" if p is not None else "—",
                    }
                )
            df_rl = pd.DataFrame(rl_rows)
            st.dataframe(df_rl, use_container_width=True, hide_index=True)
            download_csv(df_rl, "baseline_vs_rl_kpis.csv", key="dl_rl")

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 — Scenarios
# ═══════════════════════════════════════════════════════════════════════════════

elif _active == "Cenários":
    scenario_dir = REPORTS / "scenarios"
    scen_names = (
        sorted(p.name for p in scenario_dir.iterdir() if p.is_dir())
        if scenario_dir.exists()
        else []
    )
    if not scen_names:
        warn(
            "Sem resultados de cenários. Corre <code>make scenario-suite</code> (ou o separador "
            "Simulação) para gerar runs por cenário com baseline vs TSP emparelhados."
        )
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
                            rows.append(
                                {
                                    "Cenário": scen,
                                    "Run type": rt_dir.name,
                                    "Seed": seed_dir.name,
                                    "metric_key": m,
                                    "Métrica": lab,
                                    "Valor": v,
                                }
                            )

        if not rows:
            st.info(
                f"Cenários presentes mas sem KPIs para a classe '{vehicle_cls_label}'. "
                "Experimenta 'Todos os veículos' ou 'Autocarros'."
            )
        else:
            # ── single injected <style> block for this tab ────────────────────
            st.markdown(
                """
<style>
.scenario-header { margin-bottom: 1.5rem; }
.scenario-title { font-size: 22px; font-weight: 700; color: var(--text-color, #111827); margin-bottom: 0.25rem; }
.scenario-sub { font-size: 14px; color: rgba(128,128,128,0.9); margin-bottom: 0.75rem; }
.scenario-badges { display: flex; gap: 0.5rem; flex-wrap: wrap; }
.badge-green { background:#dcfce7; color:#15803d; border:1px solid #bbf7d0; border-radius:99px; padding:0.2rem 0.75rem; font-size:13px; font-weight:500; }
.badge-red { background:#fee2e2; color:#b91c1c; border:1px solid #fecaca; border-radius:99px; padding:0.2rem 0.75rem; font-size:13px; font-weight:500; }
.stat-card { border:1px solid #e5e7eb; border-radius:12px; padding:1.25rem 1rem; text-align:center; }
.stat-label { font-size:12px; color:#6b7280; margin-bottom:0.35rem; text-transform:uppercase; letter-spacing:0.06em; }
.stat-value { font-size:28px; font-weight:700; line-height:1.1; }
.stat-unit { font-size:13px; color:#9ca3af; margin-top:0.2rem; }
.stat-positive { color:#16a34a; }
.stat-negative { color:#dc2626; }
.stat-neutral { color:#111827; }
.scen-obj { border:1px solid #e5e7eb; border-left:4px solid #2563eb; border-radius:12px;
            padding:1.1rem 1.3rem; background:#fbfcfe; margin:0.25rem 0 1.1rem; }
.scen-obj-head { display:flex; align-items:center; gap:0.65rem; margin-bottom:0.35rem; }
.scen-obj-icon { font-size:26px; line-height:1; }
.scen-obj-desc { font-size:15px; font-weight:600; color:#0f172a; line-height:1.4; }
.scen-obj-row { margin-top:0.75rem; }
.scen-obj-lbl { font-size:11px; font-weight:700; letter-spacing:0.07em; text-transform:uppercase;
                color:#2563eb; margin-bottom:0.2rem; }
.scen-obj-lbl.muted { color:#94a3b8; }
.scen-obj-txt { font-size:13.5px; color:#334155; line-height:1.55; margin:0; }
.scen-obj-txt.muted { color:#64748b; }
.scen-obj-chips { display:flex; flex-wrap:wrap; gap:0.4rem; margin-top:0.85rem; }
.scen-chip { background:#eef2ff; color:#4338ca; border:1px solid #e0e7ff; border-radius:99px;
             padding:0.18rem 0.65rem; font-size:11.5px; font-weight:500; }
</style>
""",
                unsafe_allow_html=True,
            )

            df_all = pd.DataFrame(rows)
            run_types_all = sorted(df_all["Run type"].unique())
            baseline_rt = next((r for r in run_types_all if "baseline" in r), None)
            tsp_rt = next((r for r in run_types_all if "tsp" in r), None)
            n_scen = df_all["Cenário"].nunique()

            label_map = {
                "bunched_buses": "Bunching de autocarros",
                "emergency_vehicle_conflict": "Conflito c/ emergência",
                "congested_am_peak": "Congestionamento AM",
                "baseline_am_peak": "Pico AM (base)",
                "baseline_off_peak": "Fora de pico (base)",
                "congested_delayed_bus": "Autocarro atrasado c/ congestionamento",
                "cross_traffic_pressure": "Pressão tráfego cruzado",
                "delayed_bus_westbound": "Autocarro atrasado sentido Oeste",
            }
            # Per-scenario glyph — purely presentational, keyed by scenario id.
            scen_icon = {
                "baseline_am_peak": "🌅",
                "baseline_off_peak": "🌙",
                "congested_am_peak": "🚦",
                "cross_traffic_pressure": "🔀",
                "delayed_bus_westbound": "🚌",
                "bunched_buses": "🚏",
                "emergency_vehicle_conflict": "🚑",
                "congested_delayed_bus": "🛑",
            }
            # PT labels for the catalog's kpi_focus tokens (localisation of the
            # source-of-truth keys, not new data). Unknown tokens fall back to a
            # de-snake-cased form below.
            kpi_focus_pt = {
                "bus_time_loss": "Perda de tempo do autocarro",
                "general_traffic_delay": "Atraso do tráfego geral",
                "queue_baseline": "Filas (referência)",
                "travel_time_variability": "Variabilidade do tempo de viagem",
                "unnecessary_interventions": "Intervenções desnecessárias",
                "low_delay_regime": "Regime de baixo atraso",
                "bus_headway": "Headway dos autocarros",
                "tsp_under_saturation": "TSP em saturação",
                "general_traffic_penalty": "Penalização do tráfego geral",
                "queue_spillback": "Spillback de filas",
                "cross_street_queues": "Filas nas transversais",
                "blocked_by_safety": "Bloqueado pela safety layer",
                "controller_rejections": "Rejeições do controlador",
                "bus_vs_general_tradeoff": "Trade-off autocarro vs geral",
                "westbound_bus_delay": "Atraso do autocarro (sentido Oeste)",
                "priority_request_rate": "Taxa de pedidos de prioridade",
                "green_extension_effect": "Efeito da extensão de verde",
                "headway_variability": "Variabilidade de headway",
                "bus_bunching": "Bunching de autocarros",
                "second_bus_priority_suppression": "Supressão de prioridade ao 2.º autocarro",
                "emergency_travel_time": "Tempo de viagem da emergência",
                "priority_hierarchy": "Hierarquia de prioridades",
                "bus_priority_preemption": "Preempção da prioridade ao autocarro",
                "bus_time_loss_under_saturation": "Perda de tempo do autocarro em saturação",
            }
            # Scenario objectives come straight from the catalog (configs/) — the
            # single source of truth — so the dashboard never invents descriptions.
            scen_catalog = load_yaml(ROOT / "configs" / "scenario_catalog.yaml") or {}
            scen_meta = scen_catalog.get("scenarios") or {}

            # Lower-is-better metrics only: keeps the "negativo = melhoria" framing and
            # the green=Δ<0 colouring valid. Speed is excluded here on purpose (it would
            # invert that framing).
            metric_keys = [
                k
                for k in [
                    "mean_time_loss_s",
                    "mean_waiting_time_s",
                    "mean_duration_s",
                    "p95_time_loss_s",
                    "mean_depart_delay_s",
                    "mean_stop_count",
                ]
                if k in df_all["metric_key"].values
            ]
            metric_options = [KPI_META[k][0] for k in metric_keys]
            label_to_key = {KPI_META[k][0]: k for k in metric_keys}

            # ── header placeholder (filled after the controls choose the metric) ─
            header_box = st.container()

            # ── unified controls — drive BOTH the overview chart and the detail ─
            col_metric, col_scenario, col_sort = st.columns([2, 2, 1], vertical_alignment="bottom")
            with col_metric:
                selected_metric = st.selectbox("Métrica", metric_options, key="scen_metric")
            with col_scenario:
                selected_scenario = st.selectbox(
                    "Cenário (detalhe)",
                    scen_names,
                    format_func=lambda s: label_map.get(s, s),
                    key="scen_detail",
                )
            with col_sort:
                sort_asc = st.toggle("Ordenar ↑", value=False, key="scen_sort")
            sel_metric_key = label_to_key[selected_metric]

            # ── per-scenario Δ% (TSP vs baseline) for the selected metric ───────
            dfm = df_all[df_all["metric_key"] == sel_metric_key]
            piv = dfm.groupby(["Cenário", "Run type"])["Valor"].mean().reset_index()
            ddf = pd.DataFrame()
            if baseline_rt and tsp_rt:
                wide = piv.pivot(index="Cenário", columns="Run type", values="Valor")
                drows = []
                for scen, r in wide.iterrows():
                    b, t = r.get(baseline_rt), r.get(tsp_rt)
                    if b and t is not None and b != 0:
                        drows.append(
                            {
                                "scen": scen,
                                "label": label_map.get(scen, scen),
                                "delta": round((t - b) / abs(b) * 100, 1),
                            }
                        )
                ddf = pd.DataFrame(drows)

            # ── header (title · class · best/worst badges) ──────────────────────
            with header_box:
                badges = ""
                if not ddf.empty:
                    b_row = ddf.loc[ddf["delta"].idxmin()]  # most negative = biggest gain
                    w_row = ddf.loc[ddf["delta"].idxmax()]  # most positive = worst case
                    badges = (
                        f'<span class="badge-green">Maior ganho: {b_row["label"]} '
                        f"({b_row['delta']:+.1f}%)</span>"
                        f'<span class="badge-red">Pior caso: {w_row["label"]} '
                        f"({w_row['delta']:+.1f}%)</span>"
                    )
                st.markdown(
                    '<div class="scenario-header">'
                    f'<div class="scenario-title">Impacto do TSP nos {n_scen} cenários operacionais</div>'
                    '<div class="scenario-sub">Comparação baseline vs TSP por situação de tráfego'
                    f" · classe: {vehicle_cls_label}</div>"
                    f'<div class="scenario-badges">{badges}</div>'
                    "</div>",
                    unsafe_allow_html=True,
                )

            # ── scenario detail — placed directly under the controls that drive
            #    it. Order within: objective card + headline cards → all-scenarios
            #    overview → this scenario's raw stats table + export. ─────────────
            st.markdown("---")
            st.markdown(
                "<p style='font-size:11px;font-weight:600;letter-spacing:0.08em;"
                "color:#9ca3af;text-transform:uppercase;margin-bottom:0.5rem'>"
                "DETALHE POR CENÁRIO</p>",
                unsafe_allow_html=True,
            )

            # ── scenario detail panel ───────────────────────────────────────────
            st.markdown(
                f"#### {label_map.get(selected_scenario, selected_scenario)} · {selected_metric}"
            )

            # ── dynamic objective card (what this scenario is for) ──────────────
            # Reacts to the "Cenário (detalhe)" selectbox; content is read verbatim
            # from the scenario catalog so it stays in sync with the source of truth.
            meta = scen_meta.get(selected_scenario) or {}
            if meta:
                rows_html = ""
                if meta.get("expected_use"):
                    rows_html += (
                        '<div class="scen-obj-row"><div class="scen-obj-lbl">Objetivo</div>'
                        f'<p class="scen-obj-txt">{meta["expected_use"].strip()}</p></div>'
                    )
                if meta.get("realism_basis"):
                    rows_html += (
                        '<div class="scen-obj-row"><div class="scen-obj-lbl muted">'
                        'Porquê é realista</div>'
                        f'<p class="scen-obj-txt muted">{meta["realism_basis"].strip()}</p></div>'
                    )
                chips_html = ""
                focus = meta.get("kpi_focus") or []
                if focus:
                    chips = "".join(
                        f'<span class="scen-chip">'
                        f'{kpi_focus_pt.get(k, k.replace("_", " ").capitalize())}</span>'
                        for k in focus
                    )
                    chips_html = (
                        '<div class="scen-obj-row"><div class="scen-obj-lbl muted">Foco de KPIs'
                        f'</div><div class="scen-obj-chips">{chips}</div></div>'
                    )
                icon = scen_icon.get(selected_scenario, "📊")
                desc = (meta.get("description") or "").strip()
                st.markdown(
                    '<div class="scen-obj">'
                    f'<div class="scen-obj-head"><span class="scen-obj-icon">{icon}</span>'
                    f'<span class="scen-obj-desc">{desc}</span></div>'
                    f"{rows_html}{chips_html}</div>",
                    unsafe_allow_html=True,
                )

            sdf = df_all[
                (df_all["Cenário"] == selected_scenario) & (df_all["metric_key"] == sel_metric_key)
            ]
            arm_vals = sdf.groupby("Run type")["Valor"].mean()
            bval = arm_vals.get(baseline_rt) if baseline_rt else None
            tval = arm_vals.get(tsp_rt) if tsp_rt else None
            unit = KPI_META[sel_metric_key][1]
            unit_disp = unit if unit else "—"

            detail_ok = not (bval is None or tval is None or bval == 0)
            if not detail_ok:
                st.info("Sem valores baseline + TSP para este cenário e métrica nesta classe.")
            else:
                delta = (tval - bval) / abs(bval) * 100
                improved = delta < 0  # lower-is-better metrics only on this tab
                tone = "stat-positive" if improved else "stat-negative"
                c1, c2, c3 = st.columns(3)
                c1.markdown(
                    '<div class="stat-card"><div class="stat-label">Baseline</div>'
                    f'<div class="stat-value stat-neutral">{bval:.1f}</div>'
                    f'<div class="stat-unit">{unit_disp}</div></div>',
                    unsafe_allow_html=True,
                )
                c2.markdown(
                    '<div class="stat-card"><div class="stat-label">TSP</div>'
                    f'<div class="stat-value {tone}">{tval:.1f}</div>'
                    f'<div class="stat-unit">{unit_disp}</div></div>',
                    unsafe_allow_html=True,
                )
                c3.markdown(
                    '<div class="stat-card"><div class="stat-label">Δ variação</div>'
                    f'<div class="stat-value {tone}">{delta:+.1f}%</div>'
                    '<div class="stat-unit">face ao baseline</div></div>',
                    unsafe_allow_html=True,
                )

                n_seeds = int(sdf.groupby("Run type")["Seed"].nunique().max() or 0)
                if n_seeds <= 1:
                    st.info(
                        "Com 1 seed por arm, estes valores são determinísticos — sem intervalo "
                        "de confiança. Para análise estatística robusta, corre com múltiplos "
                        "seeds (--seeds 57 98 99)."
                    )
                else:
                    st.info(
                        f"{n_seeds} seeds por arm — a tabela de estatísticas descritivas resume "
                        "média e dispersão entre seeds."
                    )

            # ── all-scenarios overview (zoom out) — sits right after the selected
            #    scenario's headline cards. Depends only on ddf, so it renders even
            #    when the selected scenario has no paired data; the raw stats table
            #    + export follow below. ───────────────────────────────────────────
            st.markdown("---")
            st.markdown(
                "<p style='font-size:11px;font-weight:600;letter-spacing:0.08em;"
                "color:#9ca3af;text-transform:uppercase;margin-bottom:0.5rem'>"
                "VISÃO GERAL · COMPARAÇÃO ENTRE CENÁRIOS</p>",
                unsafe_allow_html=True,
            )
            if ddf.empty:
                st.info("Sem par baseline + TSP para calcular o impacto por cenário.")
            else:
                cdf = ddf.sort_values("delta", ascending=sort_asc)
                vals = cdf["delta"].tolist()
                lo, hi = min(vals + [0.0]), max(vals + [0.0])
                pad = max(3.0, (hi - lo) * 0.18)
                fig_d = go.Figure(
                    go.Bar(
                        x=cdf["delta"],
                        y=cdf["label"],
                        orientation="h",
                        marker_color=["#16a34a" if d < 0 else "#dc2626" for d in cdf["delta"]],
                        texttemplate="%{x:.1f}%",
                        textposition="outside",
                        cliponaxis=False,
                        hovertemplate="%{y}: %{x:+.1f}%<extra></extra>",
                    )
                )
                fig_d.add_vline(x=0, line_color="#9ca3af", line_width=1.5, line_dash="dot")
                fig_d.update_layout(
                    paper_bgcolor="white",
                    plot_bgcolor="#f8fafc",
                    font={"family": "Inter, system-ui, sans-serif", "color": "#374151", "size": 11},
                    margin={"l": 220, "r": 80, "t": 20, "b": 40},
                    bargap=0.4,
                    height=320,
                    showlegend=False,
                    xaxis_title="Δ% face ao baseline (negativo = melhoria)",
                )
                fig_d.update_xaxes(
                    range=[lo - pad, hi + pad],
                    gridcolor="#f1f5f9",
                    linecolor="#e2e8f0",
                    zeroline=False,
                    tickfont={"size": 11},
                )
                fig_d.update_yaxes(
                    title="", tickfont={"size": 13}, gridcolor="#f1f5f9", linecolor="#e2e8f0"
                )
                st.plotly_chart(fig_d, use_container_width=True, config={"displayModeBar": False})
                st.caption(
                    "*Verde = o TSP melhora o cenário; vermelho = piora. Mostra onde a "
                    "prioridade semafórica traz mais valor (autocarros atrasados, bunching) "
                    "e onde tem custo.*"
                )

            # ── per-scenario raw stats + export (the selected scenario's numbers,
            #    kept below the overview so the headline cards lead) ──────────────
            if detail_ok:
                st.markdown("---")
                st.markdown(
                    f"**Estatísticas descritivas · {label_map.get(selected_scenario, selected_scenario)}**"
                )
                u = f" ({unit})" if unit else ""
                stats = (
                    sdf.groupby("Run type")["Valor"]
                    .agg(["mean", "std", "min", "max", "count"])
                    .rename(
                        columns={
                            "mean": f"Média{u}",
                            "std": f"Desvio-padrão{u}",
                            "min": f"Mín{u}",
                            "max": f"Máx{u}",
                            "count": "Seeds",
                        }
                    )
                )
                stats = stats.dropna(axis=1, how="all").round(2)
                stats = stats.reset_index().rename(columns={"Run type": "Arm"})
                stats["Arm"] = (
                    stats["Arm"].map({baseline_rt: "Baseline", tsp_rt: "TSP"}).fillna(stats["Arm"])
                )
                if "Seeds" in stats.columns:
                    stats["Seeds"] = stats["Seeds"].astype(int)
                st.dataframe(stats, hide_index=True, use_container_width=True)

                csv_bytes = stats.to_csv(index=False).encode("utf-8")
                _, col_btn = st.columns([4, 1])
                with col_btn:
                    st.download_button(
                        "⬇ Exportar CSV",
                        data=csv_bytes,
                        file_name=f"{selected_scenario}_{sel_metric_key}.csv",
                        mime="text/csv",
                        key="dl_scenario",
                        use_container_width=True,
                    )

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 6 — Metodologia
# ═══════════════════════════════════════════════════════════════════════════════

elif _active == "Método":
    st.markdown("#### Como ler estes resultados — fontes, parâmetros e limites da simulação.")
    st.caption(
        "Tudo nesta dashboard vem de runs SUMO/TraCI reais (não há números inventados). "
        "Esta página documenta a configuração, o que está calibrado e o que não está, e as "
        "limitações a ter em conta na interpretação."
    )
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
            st.dataframe(
                pd.DataFrame(
                    {"Parâmetro": list(sim_params.keys()), "Valor": list(sim_params.values())}
                ),
                use_container_width=True,
                hide_index=True,
            )
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
    for lim in limitations + standard_limits:
        st.markdown(f"- {lim}")

    section("Fontes de dados")
    data_policy = demo.get("data_policy", {}) if demo else {}
    dp_rows = [
        {"Campo": "Fonte operacional", "Valor": data_policy.get("operational_data_source", "—")},
        {
            "Campo": "Dados sintéticos",
            "Valor": str(data_policy.get("synthetic_operational_data", "—")),
        },
        {
            "Campo": "Rede viária",
            "Valor": "sumo/plain/corredor.{nod,edg}.xml — geometria manual da Boavista",
        },
        {"Campo": "Paragens", "Valor": "sumo/additional/bus_stops.add.xml"},
        {
            "Campo": "Rotas",
            "Valor": "sumo/routes/routes.rou.xml — randomTrips com semente controlada",
        },
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

    # ── Glossário de métricas (relocated from the sidebar; searchable cards) ───
    section("Glossário de métricas")
    gloss_query = st.text_input(
        "Pesquisar métrica",
        placeholder="Pesquisar métrica...",
        label_visibility="collapsed",
        key="gloss_search",
    )
    gloss_filtered = (
        [
            g
            for g in GLOSSARY
            if gloss_query.lower() in g["term"].lower()
            or gloss_query.lower() in g["def"].lower()
        ]
        if gloss_query
        else GLOSSARY
    )
    if not gloss_filtered:
        st.caption("Nenhuma métrica encontrada.")
    else:
        gloss_cols = st.columns(2)
        for gi, entry in enumerate(gloss_filtered):
            with gloss_cols[gi % 2]:
                st.markdown(
                    f'<div class="gloss-card">'
                    f'<div class="gloss-term">{entry["term"]}'
                    f'<span class="gloss-unit">{entry["unit"]}</span></div>'
                    f'<div class="gloss-def">{entry["def"]}</div>'
                    f"</div>",
                    unsafe_allow_html=True,
                )

    # ── Guia de leitura (relocated from the sidebar; numbered step cards) ──────
    section("Guia de leitura")
    for step in STEPS:
        st.markdown(
            f'<div class="step-card">'
            f'<div class="step-num">{step["n"]}</div>'
            f"<div>"
            f'<div class="step-title">{step["title"]}</div>'
            f'<div class="step-body">{step["body"]}</div>'
            f"</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 7 — Simulação
# ═══════════════════════════════════════════════════════════════════════════════

elif _active == "Simulação":
    st.markdown(
        "Lança simulações SUMO directamente a partir da dashboard — visualmente no "
        "SUMO-GUI ou em modo headless para regenerar os reports de análise."
    )
    render_simulation_panel()
