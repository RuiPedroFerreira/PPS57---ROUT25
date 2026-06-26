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
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import yaml
from streamlit_echarts import JsCode, st_echarts

ROOT = Path(__file__).parent
REPORTS = ROOT / "reports"
PUBLIC = ROOT / "public"
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pps57_dashboard.results import (  # noqa: E402
    catalog_label_map,
    default_scenario_dataset,
    discover_scenario_report_roots,
    load_scenario_focus_significance,
    load_scenario_run_table,
    scenario_catalog_path,
)

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
        "Velocidade média de viagem",
        "m/s",
        "Velocidade média de viagem (routeLength/duração, inclui tempo parado); "
        "diverge das velocidades instantâneas dos detectores. Maior é melhor.",
    ),
    "mean_depart_delay_s": (
        "Atraso de partida médio",
        "s",
        "Tempo de espera antes de entrar na rede. Menor é melhor.",
    ),
    "mean_stop_count": (
        "Episódios de paragem médios",
        "",
        "Média de episódios de paragem/abrandamento por veículo (waitingCount do "
        "SUMO — inclui paragens por congestão), não visitas a paragens. Menor é melhor.",
    ),
    "total_co2_mg": (
        "CO2 total",
        "mg",
        "Emissão total de CO2. Menor é melhor.",
    ),
    "total_co2_mg_per_vehicle": (
        "CO2 por veículo",
        "mg/veículo",
        "Emissão de CO2 normalizada por veículo concluído. Menor é melhor.",
    ),
    "total_co2_mg_per_vehicle_km": (
        "CO2 por veículo-km",
        "mg/km",
        "Emissão de CO2 normalizada por quilómetro percorrido. Menor é melhor.",
    ),
    "total_fuel_mg": (
        "Combustível total",
        "mg",
        "Consumo total de combustível (proxy SUMO). Menor é melhor.",
    ),
    "total_fuel_mg_per_vehicle": (
        "Combustível por veículo",
        "mg/veículo",
        "Combustível normalizado por veículo concluído. Menor é melhor.",
    ),
    "total_fuel_mg_per_vehicle_km": (
        "Combustível por veículo-km",
        "mg/km",
        "Combustível normalizado por quilómetro percorrido. Menor é melhor.",
    ),
    # ── reliability / tail ──────────────────────────────────────────────────
    "p95_duration_s": (
        "Duração P95",
        "s",
        "Percentil 95 da duração de viagem — o pior caso para 95% dos veículos. Menor é melhor.",
    ),
    # ── network / queues (detectores E2) ────────────────────────────────────
    "max_queue_vehicles": (
        "Fila máxima",
        "veíc.",
        "Maior fila observada no pior troço da rede (detectores E2). Menor é melhor.",
    ),
    "mean_queue_vehicles": (
        "Fila média",
        "veíc.",
        "Fila média por intervalo na rede (detectores E2). Menor é melhor.",
    ),
    "edge_intervals_above_8_veh": (
        "Arco-intervalos congestionados",
        "",
        "Ocorrências de fila ≥8 veículos somadas sobre arcos e intervalos (arco×intervalo, "
        "detectores E2) — escala com o tamanho da rede. Menor é melhor.",
    ),
    "mean_occupancy_pct": (
        "Ocupação média",
        "%",
        "Ocupação média dos detectores ao longo da simulação. Menor é melhor.",
    ),
    # ── safety / viability (insertion + statistics) ─────────────────────────
    "collisions": (
        "Colisões",
        "",
        "Colisões registadas na simulação. Limiar de qualidade: 0. Menor é melhor.",
    ),
    "teleports_total": (
        "Teleports",
        "",
        "Veículos teletransportados (gridlock ou yield prolongado). Limiar: ≤ 3. Menor é melhor.",
    ),
    "teleports_jam": (
        "Teleports por gridlock",
        "",
        "Teletransportes causados por bloqueio total. Limiar: 0. Menor é melhor.",
    ),
    "emergency_braking": (
        "Travagens de emergência",
        "",
        "Travagens bruscas (proxy de conforto/segurança). Limiar: ≤ 150 (e ≤ 30/1000 veíc.). Menor é melhor.",
    ),
    "max_waiting_to_insert": (
        "Espera máx. p/ inserção",
        "s",
        "Maior espera de um veículo para entrar na rede. Limiar: ≤ 150 s. Menor é melhor.",
    ),
    "final_waiting": (
        "Backlog no fim",
        "veíc.",
        "Veículos ainda à espera de entrar no fim da simulação. Limiar: ≤ 150. Menor é melhor.",
    ),
    "backlog_step_count": (
        "Passos com backlog",
        "",
        "Número de passos de simulação com fila de inserção pendente. Menor é melhor.",
    ),
    # ── air quality (poluentes para além do CO2) ────────────────────────────
    "total_nox_mg": (
        "NOx total",
        "mg",
        "Óxidos de azoto — poluente de saúde urbana. Menor é melhor.",
    ),
    "total_nox_mg_per_vehicle_km": (
        "NOx por veículo-km",
        "mg/km",
        "NOx normalizado por quilómetro percorrido. Menor é melhor.",
    ),
    "total_pmx_mg": (
        "Partículas (PMx) total",
        "mg",
        "Matéria particulada — poluente de saúde urbana. Menor é melhor.",
    ),
    "total_pmx_mg_per_vehicle_km": (
        "PMx por veículo-km",
        "mg/km",
        "PMx normalizado por quilómetro percorrido. Menor é melhor.",
    ),
    "total_co_mg": (
        "CO total",
        "mg",
        "Monóxido de carbono. Menor é melhor.",
    ),
    "total_co_mg_per_vehicle_km": (
        "CO por veículo-km",
        "mg/km",
        "CO normalizado por quilómetro percorrido. Menor é melhor.",
    ),
    "total_hc_mg": (
        "HC total",
        "mg",
        "Hidrocarbonetos. Menor é melhor.",
    ),
    "total_hc_mg_per_vehicle_km": (
        "HC por veículo-km",
        "mg/km",
        "HC normalizado por quilómetro percorrido. Menor é melhor.",
    ),
    # ── bus regularity / headways ───────────────────────────────────────────
    "mean_headway_s": (
        "Headway médio",
        "s",
        "Intervalo médio entre autocarros da mesma linha/sentido. Contexto operacional (orientado pela procura).",
    ),
    "headway_amplitude_s": (
        "Amplitude de headway",
        "s",
        "Diferença entre o maior e o menor headway — proxy coarse de bunching (poucas partidas). Menor é melhor.",
    ),
    "departures": (
        "Partidas",
        "",
        "Número de partidas observadas da linha/sentido na simulação.",
    ),
}

# metrics where an increase is an improvement (drives delta colouring)
HIGHER_IS_BETTER = {"mean_speed_mps"}

# ── semantic colours ──────────────────────────────────────────────────────────
# One canonical green/red pair, used everywhere a chart (or card) encodes
# improvement vs cost — so every figure across the dashboard speaks the same two
# colours instead of a mix of near-identical greens/reds.
COLOR_GOOD = "#16a34a"  # improvement / win
COLOR_BAD = "#dc2626"  # degradation / cost

ACTION_META = {
    "green_extension": (
        "Extensão de verde",
        COLOR_GOOD,
        "Alonga a fase verde actual para deixar passar o autocarro.",
    ),
    "early_green": (
        "Verde antecipado",
        "#1d4ed8",
        "Avança o início da fase verde para a aproximação do autocarro.",
    ),
    "no_action": ("Sem acção", "#94a3b8", "Nenhuma intervenção necessária neste ciclo."),
    "reject": ("Rejeitado", COLOR_BAD, "Pedido recusado por critério de elegibilidade."),
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

# Friendly PT labels per operational scenario id — shared by the KPIs tab and the
# Resumo overview so both read the same names. Catalog descriptions override these.
SCENARIO_LABELS = {
    "bunched_buses": "Bunching de autocarros",
    "emergency_vehicle_conflict": "Conflito c/ emergência",
    "congested_am_peak": "Congestionamento AM",
    "baseline_am_peak": "Pico AM (base)",
    "baseline_off_peak": "Fora de pico (base)",
    "congested_delayed_bus": "Autocarro atrasado c/ congestionamento",
    "cross_traffic_pressure": "Pressão tráfego cruzado",
    "delayed_bus_westbound": "Autocarro atrasado sentido Oeste",
}

PALETTE = {
    "sumo_baseline": "#64748b",
    "baseline": "#64748b",
    "tsp": "#1d4ed8",
    "tsp_controller": "#7c3aed",
}

# ── CSS ───────────────────────────────────────────────────────────────────────

CSS = """
<style>
/* Inter is referenced throughout this stylesheet — load it so the typography is
   actually rendered in Inter (not the system fallback) and looks crisp. */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

html, body, [class*="css"], [data-testid="stAppViewContainer"], .stApp {
  font-family: "Inter", "Segoe UI", system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  text-rendering: optimizeLegibility; }

/* Soft page canvas — white cards/charts (and tinted boxes) lift off this subtle
   cool off-white instead of floating on flat white. Content column is transparent
   (no bg on .block-container) so it shows through; the translucent topbar/footer
   blur over it. This is the "soft canvas" the card shadows were always tuned for. */
[data-testid="stAppViewContainer"], .stApp { background: #f6f8fb; }

/* page header */
.page-header { border-bottom: 3px solid #1d4ed8; padding-bottom: 12px; margin-bottom: 6px;
               margin-top: 2.5rem; }  /* clear Streamlit's 60px fixed top header */
.page-header .kicker { font-size: 0.68rem; font-weight: 700; text-transform: uppercase;
                       letter-spacing: 0.12em; color: #1d4ed8; margin: 0 0 5px; }
.page-header h1 { font-size: 1.55rem; font-weight: 700; color: #0f172a; margin: 0 0 2px; letter-spacing: -0.4px; }
.page-header .subtitle { font-size: 0.82rem; color: #64748b; margin: 0; }
.badge { display:inline-block; background:#eff6ff; border:1px solid #bfdbfe; color:#1d4ed8;
         font-size:0.72rem; font-weight:600; padding:2px 8px; border-radius:4px;
         margin-left:8px; vertical-align:middle; }
.freshness { font-size:0.74rem; color:#94a3b8; margin:6px 0 0; }
.ctx-block { background:#f8fafc; border:1px solid #e2e8f0; border-left:3px solid #1d4ed8;
             border-radius:6px; padding:14px 18px; margin-bottom:16px; }
.ctx-block p { font-size:0.83rem; color:#334155; line-height:1.65; margin:0 0 6px; }
.ctx-block p:last-child { margin:0; }
.ctx-block strong { color:#0f172a; font-weight:600; }

/* brand bar pinned to the bottom of every page — product logo left, partner
   logo right; centred at the same max width as the content so the logos line up
   with the content edges. */
.page-footer { position: fixed; left: 0; right: 0; bottom: 0; z-index: 90;
               background: #ffffff; border-top: 0.5px solid #e2e8f0; height: 56px; }
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
.dot-ok  { background:#16a34a; }
.dot-off { background:#cbd5e1; }

/* verdict banner — single styled card; the left accent reflects the status */
.verdict-card { background:#ffffff; border:1px solid #f1f5f9; border-left:4px solid #f59e0b;
                border-radius:8px; padding:1rem 1.25rem; margin-bottom:1.5rem; }
.verdict-card .verdict-headline { font-size:15px; font-weight:700; color:#92400e; margin:0 0 3px; }
.verdict-card .verdict-support  { font-size:13px; color:#78716c; margin:0; line-height:1.5; }
.verdict-card.is-pass    { border-left-color:#16a34a; }
.verdict-card.is-pass .verdict-headline    { color:#15803d; }
.verdict-card.is-fail    { border-left-color:#dc2626; }
.verdict-card.is-fail .verdict-headline    { color:#b91c1c; }
.verdict-card.is-unknown { border-left-color:#94a3b8; }
.verdict-card.is-unknown .verdict-headline { color:#475569; }

/* chart / section block title — ONE consistent heading style for every section
   header (emitted by section()) and chart title across all tabs. */
.chart-title { font-size:1.05rem; font-weight:700; color:#0f172a; margin:1.6rem 0 2px; letter-spacing:-0.2px; }
.chart-desc  { font-size:0.8rem; color:#64748b; margin:0 0 10px; line-height:1.5; }
/* "Foco" pill on a section header — marks the sections the chosen scenario's
   kpi_focus (catalog) flags as the ones to read first. Additive: a scenario whose
   focus maps to no KPIs section simply shows no pill. */
.chart-title .focus-badge { display:inline-block; margin-left:10px; padding:1px 8px;
  background:#eff6ff; border:1px solid #bfdbfe; color:#1d4ed8; border-radius:10px;
  font-size:10px; font-weight:700; letter-spacing:.04em; vertical-align:middle; }

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
.step:hover { border-color:#1d4ed8; }
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
             border-left:3px solid #1d4ed8; border-radius:8px; padding:10px 12px; }
.flow-step .ft { font-weight:700; font-size:0.8rem; color:#0f172a; }
.flow-step .fd { font-size:0.74rem; color:#64748b; line-height:1.35; margin-top:2px; }
.flow-arrow { align-self:center; color:#94a3b8; font-size:1.1rem; font-weight:700; }

/* remove the default Streamlit top padding */
section.main > div { padding-top: 1rem; }
.block-container { padding-top: 1rem; }

/* KPI metric cards — left accent stripe signals improvement vs cost */
.kpi-card { border:1px solid #e2e8f0; border-radius:12px; padding:1.25rem; position:relative;
            overflow:hidden; background:#ffffff; }
.kpi-card::before { content:""; position:absolute; left:0; top:0; bottom:0; width:4px; background:transparent; }
.kpi-card.kpi-good::before { background:#16a34a; }
.kpi-card.kpi-bad::before  { background:#dc2626; }
.kpi-card .kpi-label { font-size:12px; color:#64748b; font-weight:600; margin:0; }
.kpi-card .kpi-value { font-size:28px; font-weight:700; color:#0f172a; line-height:1.15; margin:4px 0 0; }
.kpi-card .kpi-delta { font-size:14px; font-weight:600; margin:6px 0 0; }
.kpi-delta.kpi-good { color:#16a34a; }
.kpi-delta.kpi-bad  { color:#dc2626; }
.kpi-delta.kpi-flat { color:#64748b; }
.kpi-card .kpi-explain { font-size:11.5px; color:#64748b; line-height:1.6; margin:12px 0 0;
                          padding-top:10px; border-top:1px solid #f1f5f9; }

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
          border-bottom: 0.5px solid #e2e8f0; height: 48px; display: flex; align-items: center;
          padding: 0 1.5rem 0 3.6rem; gap: 12px; }
.topbar-logo { font-size: 15px; font-weight: 600; color: #0f172a; letter-spacing: -0.01em; }
.topbar-logo span { color: #10b981; }
.status-dot { width: 6px; height: 6px; border-radius: 50%; background: #10b981; flex-shrink: 0; }

/* hamburger — real st.button(key="open_drawer") pinned over the topbar's left edge */
.st-key-open_drawer { position: fixed; top: 7px; left: 14px; z-index: 130; width: 34px; }
.st-key-open_drawer button { width: 34px !important; height: 34px; min-height: 34px; padding: 0;
  border: 0.5px solid #e2e8f0; border-radius: 8px; background: #f8fafc; color: #0f172a; font-size: 16px; }
.st-key-open_drawer button:hover { background: #f1f5f9; border-color: #cbd5e1; }

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
  background: #ffffff; border-right: 0.5px solid #e2e8f0; box-shadow: 4px 0 24px rgba(0,0,0,0.06);
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
.st-key-drawer_panel button:hover { background: #f1f5f9 !important; color: #0f172a !important; }
.st-key-drawer_panel button[kind="primary"] {
  background: #eff6ff !important; color: #1d4ed8 !important; font-weight: 600 !important;
  box-shadow: inset 3px 0 0 #1d4ed8 !important; }
/* nav icons (Material Symbols) — muted grey by default, brand-blue when active */
.st-key-drawer_panel button [data-testid="stIconMaterial"] { font-size: 18px !important; color: #94a3b8 !important; }
.st-key-drawer_panel button:hover [data-testid="stIconMaterial"] { color: #64748b !important; }
.st-key-drawer_panel button[kind="primary"] [data-testid="stIconMaterial"] { color: #1d4ed8 !important; }
.st-key-drawer_panel [data-testid="stSelectbox"],
.st-key-drawer_panel [data-testid="stCaptionContainer"] { padding-left: 12px; padding-right: 12px; }
/* filter helper line + a clear, card-like select so its purpose reads at a glance.
   Streamlit gives markdown containers a -16px bottom margin (compact spacing) which
   pulls each block up onto the previous one; neutralise it across the bottom group
   so the label, hint, select and status block stack with their own spacing. */
.drawer-filter-hint { font-size: 10.5px; color: #94a3b8; line-height: 1.45; padding: 0 14px 8px; }
.st-key-drawer_bottom [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
.drawer-filter-hint strong { color: #64748b; font-weight: 600; }
.st-key-drawer_panel [data-baseweb="select"] > div {
  background: #ffffff !important; border-radius: 9px !important; min-height: 40px; }
.st-key-drawer_panel [data-testid="stSelectbox"] label { display: none; }
/* navigation: the wrapper grows to fill the slack (flex:1); the nav itself is
   top-aligned and grouped (flex-start, tight gap) so the items read as clusters
   under their section labels, with the bottom group pinned below the slack. */
.st-key-drawer_panel > div:has(> .st-key-drawer_nav) {
  flex: 1 1 auto !important; display: flex !important; flex-direction: column !important; }
.st-key-drawer_nav { flex: 1 1 auto; justify-content: flex-start !important; gap: 2px;
  padding-top: 16px; /* matches the 16px section-label top padding so the header→Fechar
                        gap shares the drawer's vertical rhythm */ }
/* Same compact-spacing fix as drawer_bottom: Streamlit gives markdown containers a
   negative bottom margin that pulls the following nav button UP onto the section
   label, so the button's hover highlight paints over "ANÁLISE"/"REFERÊNCIA".
   Neutralise it here — each .drawer-section-label carries its own padding. */
.st-key-drawer_nav [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
.st-key-drawer_bottom { gap: 0; }

/* drawer decorative bits */
.drawer-head { height: 48px; border-bottom: 0.5px solid #e2e8f0; display: flex; align-items: center;
               padding: 0 14px; justify-content: space-between; }
.drawer-head .dh-logo { font-size: 15px; font-weight: 600; color: #0f172a; }
.drawer-head .dh-logo span { color: #10b981; }
.drawer-head .dh-sub { font-size: 11px; color: #64748b; }
.drawer-section-label { font-size: 9.5px; font-weight: 700; letter-spacing: 0.11em; text-transform: uppercase;
                        color: #94a3b8; padding: 16px 14px 5px;
                        /* keep the label above any adjacent button hover highlight */
                        position: relative; z-index: 1; }
.nav-divider { height: 0.5px; background: #eef2f7; margin: 10px 14px; }
.status-block { margin: 12px 12px 4px; padding: 11px 12px; border: 0.5px solid #e2e8f0; border-radius: 10px;
                background: #f8fafc; }
.status-block-row { display: flex; align-items: center; gap: 7px; margin-bottom: 4px; }
.status-block-title { font-size: 12px; font-weight: 600; color: #0f172a; }
.status-block-sub { font-size: 11px; color: #64748b; line-height: 1.55; }
/* footer pinned to the bottom of the 264px drawer; white bg + top/right borders
   so scrolling content disappears cleanly behind it. */
.drawer-footer { position: fixed; left: 0; bottom: 0; width: 264px; z-index: 902;
                 box-sizing: border-box; display: flex; align-items: center; gap: 9px;
                 padding: 12px 14px; background: #ffffff;
                 border-top: 0.5px solid #e2e8f0; border-right: 0.5px solid #e2e8f0; }
.drawer-footer-label { font-size: 10px; color: #94a3b8; font-weight: 600; white-space: nowrap;
                       text-transform: uppercase; letter-spacing: 0.06em; }
.drawer-footer-logo { height: 18px; width: auto; opacity: 0.75; }

/* "Explorar em detalhe" chips — real buttons (key=chip_*) styled as cards */
.st-key-explore_chips button { height: auto; min-height: 0; text-align: left; justify-content: flex-start;
  align-items: flex-start; border: 1px solid #e2e8f0; border-radius: 10px; background: #ffffff;
  color: #0f172a; font-weight: 700; font-size: 14px; padding: 0.85rem 1.1rem; box-shadow: none; }
.st-key-explore_chips button:hover { background: #f8fafc; border-color: #cbd5e1; }

/* glossary + reading-guide cards (relocated from the sidebar to the Documentação tab) */
.gloss-card { background:#ffffff; border:1px solid #e2e8f0; border-radius:8px;
              padding:0.6rem 0.75rem; margin-bottom:0.5rem; }
.gloss-term { font-size:13px; font-weight:600; color:#0f172a; margin-bottom:0.2rem;
              display:flex; justify-content:space-between; align-items:baseline; gap:0.5rem; }
.gloss-unit { font-size:11px; font-weight:400; color:#94a3b8; white-space:nowrap; }
.gloss-def { font-size:12px; color:#64748b; line-height:1.55; }
.step-card { display:flex; gap:0.75rem; align-items:flex-start; margin-bottom:0.75rem;
             background:#ffffff; border:1px solid #e2e8f0; border-radius:10px; padding:0.85rem 1rem; }
.step-card .step-num { font-size:11px; font-weight:700; color:#ffffff; background:#1d4ed8;
                       border-radius:50%; width:22px; height:22px; display:flex; align-items:center;
                       justify-content:center; flex-shrink:0; margin-top:1px; }
.step-card .step-title { font-size:13px; font-weight:600; color:#0f172a; margin-bottom:0.2rem; }
.step-card .step-body { font-size:12px; color:#64748b; line-height:1.55; }

/* ══════════════════════════════════════════════════════════════════════════
   POLISH LAYER — typography depth and micro-interactions layered on top of the
   structural rules above. Kept in one block so the visual refresh is easy to
   read and revert without touching the drawer/topbar machinery.
   ══════════════════════════════════════════════════════════════════════════ */

/* App chrome (topbar + bottom brand bar) becomes a translucent, blurred surface
   that floats over the soft canvas — content scrolls subtly beneath it. */
.topbar, .page-footer {
  background: rgba(255,255,255,0.82) !important;
  backdrop-filter: saturate(180%) blur(10px);
  -webkit-backdrop-filter: saturate(180%) blur(10px);
  box-shadow: 0 1px 2px rgba(16,24,40,0.04); }

/* Lighter horizontal rules (Streamlit "---") so section breaks read as hairlines. */
hr { border: none !important; border-top: 1px solid #eef2f7 !important; margin: 1.4rem 0 !important; }

/* Card depth — a subtle two-layer shadow, consistent across every card surface.
   stat-card has no background of its own, so give it white to sit on the canvas. */
.stat-card { background: #ffffff; }
.verdict-card, .kpi-card, .stat-card, .gloss-card, .scen-obj, .flow-step,
.status-block, .step, .step-card {
  box-shadow: 0 1px 2px rgba(16,24,40,0.04), 0 1px 3px rgba(16,24,40,0.05); }

/* Lift the interactive-feeling cards on hover. */
.kpi-card, .stat-card, .gloss-card, .step {
  transition: box-shadow .18s ease, transform .18s ease, border-color .18s ease; }
.kpi-card:hover, .stat-card:hover, .gloss-card:hover, .step:hover {
  box-shadow: 0 6px 16px rgba(16,24,40,0.09), 0 2px 5px rgba(16,24,40,0.05);
  transform: translateY(-2px); }

/* Explore chips (real buttons) get the same gentle motion as the cards. */
.st-key-explore_chips button {
  transition: background .15s ease, border-color .15s ease, box-shadow .15s ease, transform .15s ease; }
.st-key-explore_chips button:hover {
  box-shadow: 0 6px 16px rgba(16,24,40,0.09); transform: translateY(-2px); }

/* Native bordered metrics (st.metric(border=True)) — match the card radius/shadow
   and sit on a clean white surface so they lift off the soft canvas. */
[data-testid="stMetric"] {
  background: #ffffff;
  border-radius: 12px;
  box-shadow: 0 1px 2px rgba(16,24,40,0.04), 0 1px 3px rgba(16,24,40,0.05); }

/* Refined, unobtrusive scrollbars. */
::-webkit-scrollbar { width: 10px; height: 10px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 8px;
  border: 2px solid transparent; background-clip: content-box; }
::-webkit-scrollbar-thumb:hover { background: #94a3b8; background-clip: content-box; }
* { scrollbar-color: #cbd5e1 transparent; scrollbar-width: thin; }

/* Plotly charts read as clean white cards floating on the soft canvas, matching
   the KPI/stat card surfaces. The figures already paint a white paper, so the
   card just frames them with a hairline border, radius and soft shadow. */
[data-testid="stPlotlyChart"] {
  background: #ffffff;
  border: 1px solid #eef2f7;
  border-radius: 14px;
  padding: 14px 16px 8px;
  box-shadow: 0 1px 2px rgba(16,24,40,0.04), 0 1px 3px rgba(16,24,40,0.05); }
[data-testid="stPlotlyChart"] .svg-container { border-radius: 12px; }

/* ECharts charts (st_echarts renders an iframe) get the same card frame as Plotly
   so every chart across the dashboard reads as one consistent surface on the soft
   canvas. Modern browsers clip the iframe content to the border-radius; the chart
   paints its own white background so the rounded corners stay clean. */
iframe[title*="echarts" i] {
  background: #ffffff;
  border: 1px solid #eef2f7;
  border-radius: 14px;
  box-shadow: 0 1px 2px rgba(16,24,40,0.04), 0 1px 3px rgba(16,24,40,0.05); }

/* Resumo hero — the headline statement with its key figure pulled large, so the
   single most important number leads the whole dashboard. */
.hero-lead { display:flex; align-items:center; gap:18px; flex-wrap:wrap; margin:8px 0 22px; }
.hero-figure { font-size:3.4rem; font-weight:800; line-height:1; letter-spacing:-0.03em;
               color:#0f172a; flex-shrink:0;
               font-variant-numeric:tabular-nums; font-feature-settings:"tnum"; }
.hero-figure.is-good  { color:#15803d; }
.hero-figure.is-bad   { color:#b91c1c; }
.hero-figure.is-brand { color:#1d4ed8; }
/* No max-width: the statement uses the free horizontal space (one line on wide
   screens, bounded by the 1500px content column); short statements stay short. */
.hero-statement { font-size:1.06rem; font-weight:500; color:#334155; line-height:1.55; }
.hero-statement strong { color:#0f172a; font-weight:700; }

/* Documentação tab — caveat cards (amber accent) + a light key→value spec list
   (cleaner and more compact than a Streamlit dataframe for short metadata). */
.doc-limit { background:#ffffff; border:1px solid #f1f5f9; border-left:3px solid #f59e0b;
  border-radius:8px; padding:10px 14px; margin-bottom:8px; font-size:0.82rem;
  color:#475569; line-height:1.55; box-shadow:0 1px 2px rgba(16,24,40,0.04); }
.spec-table { border:1px solid #e2e8f0; border-radius:10px; overflow:hidden; background:#ffffff;
  box-shadow:0 1px 2px rgba(16,24,40,0.04), 0 1px 3px rgba(16,24,40,0.05); }
.spec-row { display:flex; gap:1rem; padding:9px 14px; border-top:1px solid #f1f5f9; font-size:0.83rem; }
.spec-row:first-child { border-top:none; }
.spec-k { flex:0 0 190px; color:#64748b; font-weight:600; }
.spec-v { color:#0f172a; word-break:break-word; }
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
def _cits_latency_from_jsonl(path_str: str, mtime: float) -> dict:
    """SREM→SSEM latency summary computed on demand from a cits_messages.jsonl.

    Fallback for reports generated before `cits_latency_ms` was folded into the run
    summary — lets the C-ITS tab surface real latency without re-running SUMO. The
    import is local so the C-ITS package only loads when this tab needs it; `mtime`
    keeps the cache fresh when the log changes."""
    from pps57_cits.audit import audit_protocol_lifecycle

    return audit_protocol_lifecycle(path_str).get("latency_ms", {})


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


def _reports_fingerprint(report_root: Path) -> float:
    """Cheap change-signal for a scenario report root: the suite summary's mtime
    (rewritten at the end of every suite run), falling back to the dir mtime. Lets the
    per-run table be cached across reruns without re-reading every seed_*/kpis.json."""
    summary = report_root / "scenario_suite_summary.json"
    try:
        if summary.exists():
            return summary.stat().st_mtime
        return report_root.stat().st_mtime
    except OSError:
        return 0.0


@st.cache_data(show_spinner=False)
def _scenario_run_table_cached(report_root_str: str, fingerprint: float) -> list[dict]:
    # `fingerprint` is part of the cache key so the entry invalidates when the suite is
    # regenerated (same mtime-keyed pattern as _read_json).
    return load_scenario_run_table(Path(report_root_str))


def scenario_run_table(report_root: Path) -> list[dict]:
    """Cached `load_scenario_run_table`. The Resumo and KPIs tabs both load this table
    and Streamlit reruns top-to-bottom on every interaction, so without caching each
    rerun re-reads and re-parses every seed_*/kpis.json from disk multiple times."""
    return _scenario_run_table_cached(str(report_root), _reports_fingerprint(report_root))


@st.cache_data(show_spinner=False)
def _scenario_focus_significance_cached(report_root_str: str, fingerprint: float) -> dict:
    return load_scenario_focus_significance(Path(report_root_str))


def scenario_focus_significance(report_root: Path) -> dict:
    """Cached suite-summary significance map (same mtime-keyed pattern as the run table)."""
    return _scenario_focus_significance_cached(str(report_root), _reports_fingerprint(report_root))


def fmt(val, unit: str = "") -> str:
    if val is None:
        return "—"
    s = f"{val:.1f}"
    return f"{s} {unit}".strip() if unit else s


def pct(baseline, candidate) -> float | None:
    # Guard the divide-by-zero / None paths explicitly (None baseline, 0 baseline, or
    # missing candidate). abs() keeps the sign reflecting (candidate - baseline) direction.
    if baseline is not None and baseline != 0 and candidate is not None:
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


def section(title: str, focus: bool = False) -> None:
    """Section header — uses the .chart-title style so every section heading and chart
    title across all tabs reads in one consistent style. `focus=True` appends a "Foco"
    pill (KPIs tab) marking sections the selected scenario's catalog kpi_focus points at."""
    badge = '<span class="focus-badge">Foco</span>' if focus else ""
    st.markdown(f'<p class="chart-title">{title}{badge}</p>', unsafe_allow_html=True)


def insight(text: str) -> None:
    st.markdown(f'<div class="insight">{text}</div>', unsafe_allow_html=True)


def warn(text: str) -> None:
    st.markdown(f'<div class="warn-box">{text}</div>', unsafe_allow_html=True)


def hero_lead(figure: str, statement_html: str, tone: str = "is-brand") -> None:
    """Tab lead line: the single key figure pulled large beside a one-sentence
    statement. `tone` ∈ {"is-good", "is-bad", "is-brand", ""} colours the figure
    (green = a win, red = a cost, brand-blue = a neutral volume/activity figure)."""
    cls = f"hero-figure {tone}".strip()
    st.markdown(
        f'<div class="hero-lead"><span class="{cls}">{figure}</span>'
        f'<span class="hero-statement">{statement_html}</span></div>',
        unsafe_allow_html=True,
    )


def spec_table(rows: list[tuple[str, str]]) -> None:
    """Lightweight key→value spec list for the Documentação page — cleaner and
    more compact than a Streamlit dataframe for short metadata."""
    body = "".join(
        f'<div class="spec-row"><span class="spec-k">{k}</span>'
        f'<span class="spec-v">{v}</span></div>'
        for k, v in rows
    )
    st.markdown(f'<div class="spec-table">{body}</div>', unsafe_allow_html=True)


def render_kpi_card(col, metric_key: str, value, baseline_val=None, explanation: str = "") -> None:
    """Custom .kpi-card: label, value and delta. A left accent stripe signals
    improvement (green) or cost (red); neutral metrics (speed) get no stripe.
    The metric definition is exposed as a native hover tooltip via `title`.
    Pass `explanation` to render a context paragraph inside the card below the delta."""
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
    explain_html = f'<p class="kpi-explain">{explanation}</p>' if explanation else ""
    col.markdown(
        f'<div class="kpi-card{stripe_cls}" title="{desc}">'
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{fmt(value, unit)}</div>'
        f"{delta_html}{explain_html}</div>",
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
        plot_bgcolor="white",
        font={"family": "Inter, system-ui, sans-serif", "color": "#475569", "size": 11},
        legend={"bgcolor": "rgba(0,0,0,0)", "borderwidth": 0, "font": {"size": 11}},
        margin={"t": 44, "b": 36, "l": 8, "r": 8},
        height=height,
        hoverlabel={
            "bgcolor": "white",
            "bordercolor": "#e2e8f0",
            "font_size": 12,
            "font_family": "Inter, system-ui",
            "font_color": "#0f172a",
        },
    )
    fig.update_xaxes(gridcolor="#eef2f7", linecolor="#e2e8f0", tickfont={"size": 11})
    fig.update_yaxes(gridcolor="#eef2f7", linecolor="#e2e8f0", tickfont={"size": 11})
    # Soft rounded bar corners — applies only to bar traces (funnels/pies ignored).
    fig.update_traces(marker_cornerradius=4, selector={"type": "bar"})
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
        if st.button("Abrir SUMO-GUI · Baseline", width="stretch") and _run_streaming(
            [("build", BUILD_CMD)], "A construir a rede"
        ):
            _launch_detached(
                [_bin("sumo-gui"), "-c", "sumo/corredor.sumocfg"],
                "Janela do SUMO (baseline) a abrir no ambiente de trabalho.",
            )
    with gc2:
        if st.button("Abrir SUMO-GUI · TSP", width="stretch") and _run_streaming(
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
        if st.button("Correr demonstrador TSP", width="stretch", type="primary"):
            triggered = [
                ("build", BUILD_CMD),
                (
                    "demonstrador",
                    [_bin("python"), "scripts/run_tsp_demonstrator.py", "--steps", str(steps)],
                ),
            ]
    with hc2:
        if st.button("Comparação Baseline vs RL", width="stretch"):
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
        if st.button("Cenários multi-seed", width="stretch"):
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


def render_scenario_overview(metric_key: str = "mean_time_loss_s") -> None:
    """Per-scenario Δ% (TSP vs baseline) for bus vs general traffic — the core TSP
    trade-off across every operational scenario, rendered in the Resumo tab. Reads
    the rich per-run table so it always shows BOTH classes (independent of the global
    vehicle-class filter); renders nothing without paired baseline+TSP data."""
    roots = discover_scenario_report_roots(REPORTS)
    dataset = default_scenario_dataset(REPORTS)
    scenario_dir = roots.get(dataset)
    if scenario_dir is None or not scenario_dir.exists():
        return
    rows = scenario_run_table(scenario_dir)
    if not rows:
        return
    tdf = pd.DataFrame(rows)
    run_types_all = sorted(tdf["Run type"].unique())
    baseline_rt = next((r for r in run_types_all if "baseline" in r), None)
    tsp_rt = next((r for r in run_types_all if "tsp" in r), None)
    if not (baseline_rt and tsp_rt):
        return
    label_map = dict(SCENARIO_LABELS)
    label_map.update(catalog_label_map(load_yaml(scenario_catalog_path(ROOT, dataset)) or {}))

    def _delta_pct(scen: str, scope: str) -> float | None:
        def _mean(rt: str) -> float | None:
            vals = tdf.loc[
                (tdf["Cenário"] == scen)
                & (tdf["Run type"] == rt)
                & (tdf["scope"] == scope)
                & (tdf["metric_key"] == metric_key),
                "Valor",
            ]
            return float(vals.mean()) if len(vals) else None

        b, t = _mean(baseline_rt), _mean(tsp_rt)
        if b and t is not None and b != 0:
            return round((t - b) / abs(b) * 100, 1)
        return None

    drows = []
    for scen in sorted(tdf["Cenário"].unique()):
        bus = _delta_pct(scen, "buses")
        gen = _delta_pct(scen, "general_traffic")
        if bus is None and gen is None:
            continue
        drows.append({"label": label_map.get(scen, scen), "bus": bus, "gen": gen})
    if not drows:
        return
    # Sorted so the biggest bus gains sit at the top of the chart.
    cdf = pd.DataFrame(drows).sort_values("bus", ascending=False, na_position="first")

    metric_label = KPI_META.get(metric_key, (metric_key, "", ""))[0]
    st.markdown(
        '<p class="chart-title">Autocarro vs tráfego geral, por cenário</p>'
        '<p class="chart-desc">Para cada cenário, a variação da '
        f"<em>{metric_label.lower()}</em> (TSP face ao baseline), com autocarros e tráfego "
        "geral lado a lado. Barras à esquerda do zero indicam melhoria (menos tempo "
        "perdido); à direita, custo.</p>",
        unsafe_allow_html=True,
    )

    nums = [v for v in cdf["bus"].tolist() + cdf["gen"].tolist() if v is not None]
    lo, hi = min(nums + [0.0]), max(nums + [0.0])
    pad = max(3.0, (hi - lo) * 0.22)
    chart_h = max(340, len(cdf) * 72 + 80)
    BUS_COLOR, GEN_COLOR = "#2563eb", "#f59e0b"  # colourblind-safe blue/orange pair

    def _series(name: str, col: str, color: str) -> dict:
        return {
            "name": name,
            "type": "bar",
            "barMaxWidth": 15,
            "barGap": "30%",
            "itemStyle": {"color": color, "borderRadius": 3},
            "data": [
                None
                if v is None
                else {
                    "value": round(v, 1),
                    "label": {
                        "show": True,
                        "position": "left" if v < 0 else "right",
                        "formatter": f"{v:+.1f}%",
                        "color": "#475569",
                        "fontSize": 10,
                        "fontFamily": "Inter, system-ui, sans-serif",
                    },
                }
                for v in cdf[col]
            ],
        }

    scen_option = {
        "backgroundColor": "white",
        "grid": {"left": "48%", "right": "14%", "top": "13%", "bottom": "10%"},
        "legend": {
            "data": ["Autocarro", "Tráfego geral"],
            "top": "1%",
            "icon": "roundRect",
            "itemWidth": 12,
            "itemHeight": 12,
            "textStyle": {
                "color": "#374151",
                "fontSize": 12,
                "fontFamily": "Inter, system-ui, sans-serif",
            },
        },
        "tooltip": {
            "trigger": "axis",
            "axisPointer": {"type": "shadow"},
            "backgroundColor": "white",
            "borderColor": "#e2e8f0",
            "borderWidth": 1,
            "textStyle": {
                "color": "#0f172a",
                "fontSize": 12,
                "fontFamily": "Inter, system-ui, sans-serif",
            },
            "formatter": JsCode(
                "function(ps){var s='<b>'+ps[0].name+'</b>';"
                "ps.forEach(function(p){var v=p.value;"
                "if(v&&typeof v==='object'){v=v.value;}"
                "if(v==null)return;"
                "s+='<br/>'+p.marker+p.seriesName+': '+(v>0?'+':'')+v+'%';});"
                "return s;}"
            ).js_code,
        },
        "xAxis": {
            "type": "value",
            "min": round(lo - pad, 1),
            "max": round(hi + pad, 1),
            "axisLabel": {
                "color": "#94a3b8",
                "fontSize": 11,
                "formatter": JsCode("function(v){return v+'%';}").js_code,
            },
            "name": "Variação face ao baseline (%)",
            "nameLocation": "middle",
            "nameGap": 30,
            "nameTextStyle": {
                "color": "#94a3b8",
                "fontSize": 11,
                "fontFamily": "Inter, system-ui, sans-serif",
            },
            "splitLine": {"lineStyle": {"color": "#f1f5f9"}},
            "axisLine": {"lineStyle": {"color": "#e2e8f0"}},
            "axisTick": {"show": False},
        },
        "yAxis": {
            "type": "category",
            "data": cdf["label"].tolist(),
            "axisLabel": {
                "color": "#374151",
                "fontSize": 12,
                "fontFamily": "Inter, system-ui, sans-serif",
                "width": 390,
                "overflow": "break",
                "align": "right",
            },
            "axisLine": {"lineStyle": {"color": "#e2e8f0"}},
            "axisTick": {"show": False},
        },
        "series": [
            {
                **_series("Autocarro", "bus", BUS_COLOR),
                "markLine": {
                    "silent": True,
                    "symbol": "none",
                    "data": [{"xAxis": 0}],
                    "lineStyle": {"color": "#94a3b8", "width": 1.5, "type": "dotted"},
                    "label": {"show": False},
                },
            },
            _series("Tráfego geral", "gen", GEN_COLOR),
        ],
        "animation": True,
        "animationDuration": 700,
        "animationEasing": "cubicOut",
    }
    st_echarts(scen_option, height=f"{chart_h}px", key="resumo_scenario_overview")


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
baseline_kpis = load_json(REPORTS / "sumo_baseline_kpis.json")
# B1 — the "vs RL" tab renders decision-outcome fields (matched_decision_count,
# verdict_counts, kpi_evaluation, network_impact_verdict) that live ONLY in
# decision_outcome_evaluation.json (produced by `make evaluate-decision-outcomes`).
# The runtime-delta tsp_baseline_vs_rl_comparison.json carries a different schema
# ({comparison, baseline_mode, rl_mode, rows}), so reading it left the tab empty.
decision_outcome = load_json(REPORTS / "decision_outcome_evaluation.json")

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


# ── drawer content data (glossary + reading guide, rendered in the Documentação tab) ──

# Glossary entries (the Documentação-tab search filters reactively on every keystroke)
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
        "body": "Cada separador aprofunda um aspecto: KPIs mostra os KPIs por cenário operacional (baseline vs TSP), Decisão explica o algoritmo e C-ITS as mensagens V2X.",
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
# topbar pill and every tab can read it whether or not the drawer is open.
#
# The selectbox is only mounted while the drawer is open, so its widget key
# ("drawer_class_select") can't be the source of truth: Streamlit garbage-collects
# the state of widgets that aren't rendered on a run, so the first interaction
# after closing the drawer would drop the key and silently reset the filter to the
# default. The canonical value therefore lives in a plain (non-widget) session key
# that survives every run; the widget is seeded from it via `index` and writes
# back through `on_change`.
cls_counts = {key: class_vehicle_count(key) for key, _ in VEHICLE_CLASSES}
cls_label_map = {f"{label} ({cls_counts[key]})": key for key, label in VEHICLE_CLASSES}
CLASS_OPTIONS = list(cls_label_map.keys())
# Default to Autocarros — that's where the TSP value lives. Buses are a tiny share
# of all vehicles, so opening on "Todos os veículos" dilutes the gain to ~0 and
# hides what the TSP does. Fall back to "Todos" only if there are no buses.
_default_key = "buses" if cls_counts.get("buses", 0) else "all_vehicles"
_default_display = next(d for d, k in cls_label_map.items() if k == _default_key)
# Re-seed when unset or when a data refresh changed the option labels (vehicle
# counts are baked into each label).
if st.session_state.get("vehicle_class_display") not in CLASS_OPTIONS:
    st.session_state.vehicle_class_display = _default_display
selected_class = st.session_state.vehicle_class_display
vehicle_cls = cls_label_map[selected_class]
vehicle_cls_label = next(lbl for k, lbl in VEHICLE_CLASSES if k == vehicle_cls)


def _sync_vehicle_class() -> None:
    """Persist the drawer selectbox choice into the non-widget session key."""
    st.session_state.vehicle_class_display = st.session_state.drawer_class_select


def _sync_vehicle_class_kpis() -> None:
    """Mirror of _sync_vehicle_class for the in-tab (KPIs) class selector — both
    widgets write the same canonical key, so changing the class in either place
    stays consistent across reruns."""
    st.session_state.vehicle_class_display = st.session_state.kpis_class_select


# ── drawer navigation model + system status ───────────────────────────────────

# "Demonstrador" is a drill-down view reachable from the "KPIs" tab — it is not a
# top-level nav entry (kept out of NAV_GROUPS on purpose).
NAV_GROUPS = [
    (None, ["Resumo"]),
    ("Análise", ["KPIs", "Decisão", "C-ITS", "vs RL"]),
    ("Referência", ["Documentação", "Simulação"]),
]

# Material Symbols icon per view — gives the drawer nav scannability and hierarchy.
NAV_ICONS = {
    "Resumo": ":material/dashboard:",
    "KPIs": ":material/query_stats:",
    "Decisão": ":material/account_tree:",
    "C-ITS": ":material/cell_tower:",
    "vs RL": ":material/smart_toy:",
    "Documentação": ":material/menu_book:",
    "Simulação": ":material/play_circle:",
}

# Per-tab page header — the project kicker + badges + freshness line stay constant
# while the title and description adapt to the active view (title, subtitle).
TAB_HEADERS = {
    "Resumo": (
        "Resumo executivo",
        "",
    ),
    "KPIs": (
        "KPIs por cenário",
        "Escolhe um cenário operacional e vê os KPIs baseline vs TSP — cartões, deltas, "
        "emissões e a comparação entre todos os cenários, por classe de veículo.",
    ),
    "Decisão": (
        "Motor de decisão TSP",
        "Da avaliação à actuação: funil de decisões, acções concedidas, verde injectado e bloqueios da Safety Layer.",
    ),
    "C-ITS": (
        "Comunicação C-ITS (V2X)",
        "Mensagens de prioridade trocadas entre veículos e semáforos (MAPEM · SPATEM · SREM · SSEM) e ciclo de vida dos pedidos.",
    ),
    "vs RL": (
        "Baseline vs Reinforcement Learning",
        "Comparação da política RL treinada com a regra heurística — decisão a decisão e em KPIs de rede.",
    ),
    "Demonstrador": (
        "Corredor demonstrador",
        "Comparação baseline vs TSP no corredor demonstrador — vista detalhada acedida "
        "a partir dos KPIs. A análise RL está na tab «vs RL».",
    ),
    "Documentação": (
        "Documentação",
        "Como ler os resultados: guia de leitura, glossário, configuração das runs, fontes de dados e limitações.",
    ),
    "Simulação": (
        "Simulação",
        "Lança runs SUMO a partir da dashboard — visualização no SUMO-GUI ou modo headless para regenerar os reports.",
    ),
}

report_files = {
    "Baseline KPIs": REPORTS / "sumo_baseline_kpis.json",
    "Demonstrador TSP": REPORTS / "tsp_demonstrator_report.json",
}
_reports_ok = sum(1 for p in report_files.values() if p.exists())
_reports_total = len(report_files)
_fresh = file_mtime(REPORTS / "tsp_demonstrator_report.json") or file_mtime(
    REPORTS / "sumo_baseline_kpis.json"
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
            if st.button("Fechar", icon=":material/close:", key="close_drawer", width="stretch"):
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
                        icon=NAV_ICONS.get(_label),
                        key=f"nav_{_label}",
                        width="stretch",
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
                "KPIs e gráficos</div>",
                unsafe_allow_html=True,
            )
            st.selectbox(
                "Classe de veículo",
                options=CLASS_OPTIONS,
                index=CLASS_OPTIONS.index(selected_class),
                key="drawer_class_select",
                on_change=_sync_vehicle_class,
                label_visibility="collapsed",
                help="Classe de veículo aplicada a todos os KPIs e gráficos; o número é a "
                "contagem de veículos. Abre em Autocarros (onde o TSP actua); muda para "
                "'Todos os veículos' para o efeito líquido na rede. Prioritários = Autocarros "
                "+ Emergência; Tráfego geral = não-prioritários.",
            )
            if vehicle_cls == "priority_vehicles" and cls_counts.get("emergency_vehicles", 0) == 0:
                st.caption("Sem veículos de emergência — **Prioritários = Autocarros**.")
            elif (
                vehicle_cls == "emergency_vehicles" and cls_counts.get("emergency_vehicles", 0) == 0
            ):
                st.caption("Sem veículos de emergência — métricas vazias.")

            st.markdown(
                f'<div class="status-block"><div class="status-block-row">'
                f'<div class="status-dot"></div>'
                f'<span class="status-block-title">Sistema activo</span></div>'
                f'<div class="status-block-sub">{_reports_ok}/{_reports_total} relatórios detectados'
                f"{('<br>Atualizado ' + _fresh) if _fresh else ''}</div></div>",
                unsafe_allow_html=True,
            )

        _cap_uri = logo_uri(PUBLIC / "CAP_LOGO.png")
        _cap_img = (
            f'<img class="drawer-footer-logo" src="{_cap_uri}" alt="Capgemini">'
            if _cap_uri
            else '<span style="font-size:11px;font-weight:600;color:#64748b;">Capgemini</span>'
        )
        st.markdown(
            f'<div class="drawer-footer">'
            f'<span class="drawer-footer-label">Developed by</span>{_cap_img}</div>',
            unsafe_allow_html=True,
        )

# ── page header ───────────────────────────────────────────────────────────────

fresh = file_mtime(REPORTS / "tsp_demonstrator_report.json") or file_mtime(
    REPORTS / "sumo_baseline_kpis.json"
)
scenario_id = ""
if demo:
    for _r in demo.get("runs", {}).values():
        scenario_id = _r.get("summary", {}).get("scenario_id", "")
        if scenario_id:
            break

_hdr_r25 = logo_uri(PUBLIC / "route25_logo.png", strip_white=True)
_hdr_cap = logo_uri(PUBLIC / "CAP_LOGO.png")

_active_tab = st.session_state.active_tab
_htitle, _hsub = TAB_HEADERS.get(_active_tab, (_active_tab, ""))

st.markdown(
    f"""
<div class="page-header">
  <p class="kicker">PPS57 · Análise TSP</p>
  <h1>{_htitle}
    <span class="badge">Linha 25 · Porto</span>
    <span class="badge">SUMO 1.26</span>
  </h1>
  {f'<p class="subtitle">{_hsub}</p>' if _hsub else ""}
</div>

""",
    unsafe_allow_html=True,
)

# Brand bar pinned to the bottom of every page (product logo left, partner right).
if _hdr_r25 or _hdr_cap:
    _fl = f'<img class="pf-logo" src="{_hdr_r25}" alt="Route 25">' if _hdr_r25 else "<span></span>"
    _fr = (
        f'<img class="pf-partner" src="{_hdr_cap}" alt="Capgemini">'
        if _hdr_cap
        else "<span></span>"
    )
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
    # ── context block ─────────────────────────────────────────────────────────
    if demo and baseline_key and primary_tsp:
        _bk = run_kpis.get(baseline_key, {})
        _tk = run_kpis.get(primary_tsp, {})
        _tsp_run = demo.get("runs", {}).get(primary_tsp, {})
        _tsp_summ = _tsp_run.get("summary", {})

        # scenario identity
        _scn_id = _tsp_summ.get("scenario_id", scenario_id) or scenario_id
        _n_tls = len(_tsp_summ.get("per_tls", {})) or _tsp_summ.get(
            "signal_program_verification", {}
        ).get("tls_actuable")
        _sim_steps = _tsp_summ.get("steps", 0)
        _sim_h = f"{_sim_steps // 3600:.0f}" if _sim_steps else None

        # TSP actuation
        _total_dec = _tsp_summ.get("total_decisions") or _tsp_summ.get("cits_processing_messages")
        _applied = _tsp_summ.get("applied_events") or _tsp_summ.get("approved_decisions")
        _early_g = (_tsp_summ.get("by_action") or {}).get("early_green")
        _ext_g = (_tsp_summ.get("by_action") or {}).get("green_extension")
        _safety_blocks = _tsp_summ.get("blocked_by_safety", 0)

        # KPI deltas
        _b_bus = _bk.get("buses", {}).get("mean_time_loss_s") or _bk.get("priority_vehicles", {}).get("mean_time_loss_s")
        _t_bus = _tk.get("buses", {}).get("mean_time_loss_s") or _tk.get("priority_vehicles", {}).get("mean_time_loss_s")
        _b_gen = _bk.get("general_traffic", {}).get("mean_time_loss_s")
        _t_gen = _tk.get("general_traffic", {}).get("mean_time_loss_s")
        _n_all = _tk.get("all_vehicles", {}).get("vehicles") or _bk.get("all_vehicles", {}).get("vehicles") or 0

        # paragraph 1 — what the experiment is
        _p1 = (
            "Este demonstrador avalia a aplicação de <strong>Traffic Signal Priority (TSP)</strong> "
            "na <strong>Linha 25 do Porto</strong>, no corredor Boavista, através de microsimulação "
            "com SUMO e comunicação C-ITS entre os autocarros e os semáforos instrumentados. "
            "O objectivo é verificar se a prioridade semafórica melhora a pontualidade dos "
            "autocarros sem penalizar o tráfego geral — uma condição essencial para a adopção "
            "do sistema em contexto urbano real."
        )

        _SCENARIO_LABELS = {
            "porto_boavista_base_v04_tsp_safety_layer": "corredor da Avenida da Boavista em direcção a Matosinhos",
            "porto_boavista_base_v04": "corredor da Avenida da Boavista em direcção a Matosinhos",
        }
        _scn_label = _SCENARIO_LABELS.get(_scn_id, _scn_id) if _scn_id else None

        # paragraph 2 — scenario and test conditions
        _p2_parts = ["O teste foi realizado no"]
        if _scn_label:
            _p2_parts.append(f"<strong>{_scn_label}</strong>,")
        _p2_parts.append("que inclui uma <strong>Safety Layer</strong> activa — responsável por filtrar")
        _p2_parts.append("actuações que possam comprometer a segurança da intersecção.")
        if _sim_h and _n_tls:
            _p2_parts.append(
                f"Foram simuladas <strong>{_sim_h} horas</strong> de tráfego em "
                f"<strong>{_n_tls} intersecções instrumentadas</strong>"
                + (f", com <strong>{_n_all:,} veículos</strong> em circulação" if _n_all else "")
                + "."
            )
        if _total_dec and _applied is not None:
            _act_parts = []
            if _early_g:
                _act_parts.append(f"{_early_g} green antecipado")
            if _ext_g:
                _act_parts.append(f"{_ext_g} extensão de verde")
            _act_str = " e ".join(_act_parts) if _act_parts else f"{_applied} actuações"
            _p2_parts.append(
                f"O controlador TSP processou <strong>{_total_dec} decisões</strong> "
                f"e aplicou <strong>{_applied}</strong> ({_act_str})"
                + ("; a Safety Layer não bloqueou nenhuma actuação." if _safety_blocks == 0 else f"; {_safety_blocks} foram bloqueadas pela Safety Layer.")
            )
        _p2 = " ".join(_p2_parts)

        # paragraph 3 — qualitative comment on results. The exact figures live in the
        # KPI cards right below, so this prose stays number-free to avoid restating them.
        _p3_parts = []
        if _b_bus and _t_bus:
            if _t_bus < _b_bus:
                _p3_parts.append(
                    "Os resultados confirmam o objectivo primário: com o TSP, os "
                    "<strong>autocarros passam a perder menos tempo</strong> no corredor."
                )
            else:
                _p3_parts.append(
                    "Neste cenário, o TSP <strong>não reduziu a perda de tempo dos "
                    "autocarros</strong>."
                )
        if _b_gen and _t_gen:
            if _t_gen <= _b_gen:
                _p3_parts.append(
                    "O <strong>tráfego geral não foi penalizado</strong> — chegou mesmo a "
                    "beneficiar —, sinal de que a actuação não introduziu disrupção na rede."
                )
            else:
                _p3_parts.append(
                    "O <strong>tráfego geral</strong> registou um custo contido, dentro dos "
                    "limiares aceitáveis do demonstrador."
                )
        if _p3_parts:
            _p3_parts.append("Os valores por classe estão nos cartões abaixo.")
        else:
            _p3_parts.append("Os resultados detalhados estão disponíveis nas secções abaixo.")
        _p3 = " ".join(_p3_parts)

        _ctx_html = (
            '<p class="chart-title">Análise Geral</p>'
            '<div class="ctx-block">'
            f"<p>{_p1}</p>"
            f"<p>{_p2}</p>"
            f"<p>{_p3}</p>"
            "</div>"
        )
        st.markdown(_ctx_html, unsafe_allow_html=True)

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

        # ── headline metrics (the win, in the priority class) ─────────────────
        if bus:
            bcls = bus["key"]
            section(f"Impacto do TSP · {bus['Classe'].lower()} ({bus['n']} veículos)")
            _tl_b = bk.get(bcls, {}).get("mean_time_loss_s")
            _tl_t = tk.get(bcls, {}).get("mean_time_loss_s")
            _wt_b = bk.get(bcls, {}).get("mean_waiting_time_s")
            _wt_t = tk.get(bcls, {}).get("mean_waiting_time_s")
            _sp_b = bk.get(bcls, {}).get("mean_speed_mps")
            _sp_t = tk.get(bcls, {}).get("mean_speed_mps")

            _tl_explain = _wt_explain = _sp_explain = ""
            if _tl_b and _tl_t:
                _tl_d = _tl_t - _tl_b
                _tl_p = bus["pct"]
                _tl_explain = (
                    f"Tempo adicional por viagem face ao percurso sem paragens "
                    f"(velocidade ideal de rede). "
                    f"Baseline: <strong>{_tl_b:.0f} s</strong> · "
                    f"TSP: <strong>{_tl_t:.0f} s</strong> — "
                    f"redução de <strong>{abs(_tl_d):.1f} s ({_tl_p:+.1f}%)</strong>. "
                    f"A prioridade semafórica encurta a espera antes dos cruzamentos, "
                    f"reflectindo-se directamente neste indicador."
                )
            if _wt_b and _wt_t:
                _wt_d = _wt_t - _wt_b
                _wt_p = (_wt_d / abs(_wt_b)) * 100
                _wt_dir = "desce" if _wt_d < 0 else "sobe"
                _wt_explain = (
                    f"Tempo total parado em semáforo vermelho ou em fila por viagem. "
                    f"Baseline: <strong>{_wt_b:.0f} s</strong> · "
                    f"TSP: <strong>{_wt_t:.0f} s</strong> "
                    f"({_wt_d:+.1f} s, {_wt_p:+.1f}%). "
                    f"O verde antecipado ou estendido elimina parte das paragens "
                    f"completas — daí o valor {_wt_dir}."
                )
            if _sp_b and _sp_t:
                _sp_d = _sp_t - _sp_b
                _sp_p = (_sp_d / abs(_sp_b)) * 100
                _sp_dir = "sobe" if _sp_d > 0 else "desce"
                _sp_explain = (
                    f"Distância de rota a dividir pela duração total (inclui tempo "
                    f"parado). "
                    f"Baseline: <strong>{_sp_b:.2f} m/s</strong> · "
                    f"TSP: <strong>{_sp_t:.2f} m/s</strong> "
                    f"({_sp_d:+.2f} m/s, {_sp_p:+.1f}%). "
                    f"A subida confirma maior fluidez — menos interrupções por viagem."
                )

            m1, m2, m3 = st.columns(3)
            render_kpi_card(m1, "mean_time_loss_s", _tl_t, _tl_b, _tl_explain)
            render_kpi_card(m2, "mean_waiting_time_s", _wt_t, _wt_b, _wt_explain)
            render_kpi_card(m3, "mean_speed_mps", _sp_t, _sp_b, _sp_explain)
            # breathing room before the per-scenario chart so it reads as a distinct
            # block from the cards, matching the page's section rhythm
            st.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)

        # ── per-scenario overview (zoom-out) — impacto do TSP em todos os cenários
        render_scenario_overview()

        # ── navigation chips: click jumps to the matching view ────────────────
        # Real st.buttons (styled as cards via .st-key-explore_chips) set
        # active_tab and rerun — the drawer-driven equivalent of the old tabs.
        section("Explorar em detalhe")
        nav_items = [
            ("KPIs", "KPIs por cenário operacional — baseline vs TSP, por classe e métrica."),
            ("Decisão", "O que o algoritmo decidiu e porquê."),
            ("vs RL", "Política RL treinada vs regra heurística TSP."),
        ]
        with st.container(key="explore_chips"):
            chip_cols = st.columns(3)
            for chip_col, (chip_label, chip_desc) in zip(chip_cols, nav_items, strict=False):
                with chip_col:
                    if st.button(f"{chip_label} →", key=f"chip_{chip_label}", width="stretch"):
                        st.session_state.active_tab = chip_label
                        st.rerun()
                    st.caption(chip_desc)

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1 — KPI comparison
# ═══════════════════════════════════════════════════════════════════════════════

elif _active == "Demonstrador":
    # Drill-down acedido a partir da tab "KPIs" (não está na navegação). Mantém a
    # comparação baseline vs TSP do corredor demonstrador, sem a perder. O braço
    # controller/RL é deliberadamente excluído daqui — a análise RL vive só na
    # tab "vs RL".
    if st.button("← Voltar aos KPIs", key="demo_back"):
        st.session_state.active_tab = "KPIs"
        st.rerun()
    # Filtra os braços controller/RL para que o RL não apareça fora da tab "vs RL".
    run_kpis = {
        k: v
        for k, v in run_kpis.items()
        if not any(tok in k.lower() for tok in ("controller", "rl"))
    }
    cls_data = {label: get_kpi(kpis, vehicle_cls) for label, kpis in run_kpis.items()}
    baseline_key = next((k for k in run_kpis if "baseline" in k.lower()), None)
    tsp_keys = [k for k in run_kpis if k != baseline_key]
    primary_tsp = tsp_keys[0] if tsp_keys else None
    if not cls_data:
        st.info("Sem dados de KPI do corredor demonstrador disponíveis.")
    else:
        # ── contextual hint: TSP gains live in the bus class ──────────────────
        bus_n = cls_counts.get("buses", 0)
        if vehicle_cls in ("all_vehicles", "general_traffic") and bus_n:
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
                        # write the canonical (non-widget) key — the drawer's
                        # selectbox isn't mounted here, so its widget key would be
                        # garbage-collected before the next run reads it.
                        st.session_state["vehicle_class_display"] = target

                    st.button(
                        "Ver autocarros",
                        width="stretch",
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
                "total_co2_mg_per_vehicle",
                "total_fuel_mg_per_vehicle",
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
            "total_co2_mg_per_vehicle_km",
            "total_fuel_mg_per_vehicle_km",
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
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
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
                "total_co2_mg",
                "total_fuel_mg",
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
                        marker_color=[COLOR_GOOD if v < 0 else COLOR_BAD for v in df_wf["Delta"]],
                        hovertemplate="%{y}: %{x:+.3f}<extra></extra>",
                    )
                )
                fig_wf.add_vline(x=0, line_width=2, line_color="#334155")
                chart_layout(
                    fig_wf, "Ganho absoluto (s) — verde reduz, vermelho aumenta", height=320
                )
                st.plotly_chart(fig_wf, width="stretch", config={"displayModeBar": False})
                insight(
                    "Verde = melhoria (redução do tempo). Vermelho = degradação. "
                    "A linha vertical é o cenário de referência. Percentagens = variação relativa."
                )

        # ── detailed comparison tables ────────────────────────────────────────
        if demo:
            section("Tabelas de comparação detalhada")
            # Só TSP vs Baseline — as tabelas do controller/RL ficam reservadas à
            # tab "vs RL".
            comp_map = [
                ("tsp_vs_sumo_baseline_kpis", "TSP vs Baseline"),
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
                            width="stretch",
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
            st.plotly_chart(fig_dist, width="stretch", config={"displayModeBar": False})
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
        runs_all = list(demo.get("runs", {}).keys())
        tsp_run_keys = [k for k in runs_all if k != "sumo_baseline"]
        # Sem selector: a análise do motor de decisão é sempre de uma run de actuação
        # TSP (o baseline não toma decisões). Escolhe-se automaticamente o controlador
        # completo, depois a regra TSP, depois a primeira run disponível.
        sel_run = next(
            (k for k in ("tsp_controller", "tsp") if k in tsp_run_keys),
            tsp_run_keys[0] if tsp_run_keys else (runs_all[0] if runs_all else ""),
        )
        runtime = demo["runs"].get(sel_run, {}).get("runtime", {})

        total = runtime.get("total_decisions", 0)
        applied = runtime.get("applied_events", 0)
        blocked = runtime.get("blocked_by_safety", 0)
        rejected = runtime.get("controller_rejections", 0)
        by_action = runtime.get("by_action", {})

        # Só estas duas acções propõem uma mudança real ao semáforo. As restantes
        # (reavaliar / rejeitar / sem acção) são não-actuações deliberadas, não
        # "aplicações falhadas" — por isso o denominador honesto da taxa de
        # aplicação é o nº de decisões ACCIONÁVEIS, não o total de avaliações.
        actionable = (by_action.get("green_extension") or 0) + (by_action.get("early_green") or 0)
        non_actionable = {
            "Reavaliar no ciclo seguinte": by_action.get("reevaluate_next_cycle", 0),
            "Rejeitadas (score abaixo do limiar)": by_action.get("reject", 0),
            "Sem acção necessária (verde já chega)": by_action.get("no_action", 0),
        }

        # ── explanatory context (mirrors the Resumo "Análise Geral" block) ────
        # Conceptual, number-free prose: how the engine works, independent of the
        # run. The figures live in the hero_lead immediately below, so this stays
        # free of numbers to avoid restating them.
        _dp1 = (
            "O <strong>motor de decisão TSP</strong> decide, ciclo a ciclo, se um autocarro "
            "recebe prioridade semafórica. Quando um autocarro se aproxima de uma intersecção "
            "instrumentada, envia um pedido por <strong>C-ITS</strong>; o motor pontua esse "
            "pedido com base no <strong>atraso ao horário</strong>, na regularidade do "
            "<strong>intervalo (headway)</strong> e na <strong>proximidade</strong> da intersecção."
        )
        _dp2 = (
            "De cada avaliação resulta uma acção: <strong>verde antecipado</strong> ou "
            "<strong>extensão de verde</strong> — as duas únicas que alteram o semáforo "
            "(accionáveis) — ou então <strong>rejeitar</strong>, <strong>reavaliar no ciclo "
            "seguinte</strong> ou <strong>sem acção</strong> quando o verde já é suficiente. "
            "Por isso o número de decisões avaliadas é muito superior ao de actuações aplicadas: "
            "a maioria das decisões é, deliberadamente, uma não-actuação."
        )
        _dp3 = (
            "Antes de chegar à rede, cada actuação accionável passa por uma "
            "<strong>Safety Layer</strong>, que barra qualquer mudança que comprometa a "
            "segurança da intersecção (amarelo insuficiente, conflito com peões ou com outra "
            "fase). As secções abaixo percorrem este caminho completo — do funil de decisões "
            "às acções concedidas, ao verde injectado e aos bloqueios de segurança."
        )
        st.markdown(
            '<p class="chart-title">Como funciona o motor de decisão</p>'
            '<div class="ctx-block">'
            f"<p>{_dp1}</p><p>{_dp2}</p><p>{_dp3}</p>"
            "</div>",
            unsafe_allow_html=True,
        )

        # ── plain-language headline (lead with the story) ─────────────────────
        if total > 0:
            ar_txt = f"{applied / actionable * 100:.0f}%" if actionable else "—"
            block_html = (
                f" A Safety Layer bloqueou <strong>{blocked}</strong> por segurança."
                if blocked
                else " Nenhuma foi bloqueada pela Safety Layer."
            )
            hero_lead(
                f"{applied}",
                "actuações TSP aplicadas na rede — de "
                f"<strong>{actionable}</strong> accionáveis em "
                f"<strong>{total}</strong> decisões avaliadas ({ar_txt}).{block_html}",
            )

        section("Pipeline de decisão — do seguimento à actuação")
        if total == 0:
            warn(
                "A run TSP analisada não gerou decisões — provavelmente uma simulação demasiado "
                "curta. Corre o demonstrador com mais passos (≥3600) no separador "
                "<strong>Simulação</strong> para os autocarros entrarem na rede e o motor actuar."
            )
        else:
            col_f, col_m = st.columns([1, 1], vertical_alignment="center")
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
                        marker_color=["#94a3b8", "#1d4ed8", COLOR_GOOD],
                        hovertemplate="%{y}: %{x}<extra></extra>",
                    )
                )
                chart_layout(fig_funnel, "Funil de decisão TSP", height=300)
                st.plotly_chart(fig_funnel, width="stretch", config={"displayModeBar": False})
            with col_m:
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
                    st.dataframe(df_na, width="stretch", hide_index=True)
                    st.caption(
                        "**Reavaliar** = o autocarro ainda está a ser seguido mas não é o momento "
                        "de actuar (fase ainda não pronta, verde mínimo por servir, benefício pequeno "
                        "ou pressão de rede). **Rejeitar** = o autocarro não precisa de prioridade "
                        "(pontual / desvio baixo). **Sem acção** = o verde actual já é suficiente."
                    )

        section("Distribuição de acções decididas")
        col_pie, col_legend = st.columns([1, 1], vertical_alignment="center")
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
                st.plotly_chart(fig_pie, width="stretch", config={"displayModeBar": False})
            else:
                st.caption("Sem acções registadas.")
        with col_legend:
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
            insight(
                f"O motor concedeu <strong>{gt_total:.1f} s</strong> de verde extra ao transporte público, "
                f"em <strong>{green.get('n_extensions', 0)}</strong> extensões aplicadas "
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
                "efectivamente injectou na rede. Esse verde é emprestado às outras fases; o "
                "TSP inclui um mecanismo de compensação que o pode devolver em ciclos "
                "seguintes para preservar a equidade semafórica."
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
                    marker_color="#1d4ed8",
                    texttemplate="%{x:.3f}",
                    textposition="outside",
                    cliponaxis=False,
                    hovertemplate="%{y}: contribuição média %{x:.3f}<extra></extra>",
                )
            )
            chart_layout(fig_o, "", height=max(220, len(items) * 46 + 80))
            fig_o.update_xaxes(range=[0, max(vals_o) * 1.25 + 0.01])
            st.plotly_chart(fig_o, width="stretch", config={"displayModeBar": False})
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
                text="Bloqueios",
                color_discrete_sequence=[COLOR_BAD],
                height=max(260, len(df_sf) * 50 + 80),
            )
            fig_sf.update_traces(
                texttemplate="%{x:.0f}",
                textposition="outside",
                cliponaxis=False,
                hovertemplate="%{y}: %{x} bloqueios<extra></extra>",
            )
            chart_layout(fig_sf, "Safety Layer — motivos de bloqueio")
            fig_sf.update_xaxes(range=[0, (df_sf["Bloqueios"].max() or 1) * 1.18])
            st.plotly_chart(fig_sf, width="stretch", config={"displayModeBar": False})
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
            st.dataframe(df_tls, width="stretch", hide_index=True)
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
            # O tráfego C-ITS (V2X) só existe nas runs de actuação TSP — o baseline
            # não troca mensagens de prioridade — por isso não há selector de run:
            # mostra-se directamente a run de actuação (preferindo o controlador
            # completo, depois a regra TSP, depois a primeira disponível).
            sel_cits_run = next(
                (k for k in ("tsp_controller", "tsp") if k in tsp_run_keys),
                tsp_run_keys[0],
            )
            summ = demo["runs"][sel_cits_run].get("summary", {})
            by_type = summ.get("cits_by_type", {})
            prl = summ.get("priority_request_lifecycle", {})

            # ── explanatory context (mirrors the Resumo/Decisão intro blocks) ──
            # Conceptual, number-free prose: what C-ITS is and how the protocol
            # works. The figures live in the hero_lead immediately below.
            _cp1 = (
                "O <strong>C-ITS</strong> (Cooperative Intelligent Transport Systems) é a "
                "camada de comunicação <strong>V2X</strong> que liga os autocarros aos "
                "semáforos. É por aqui que o autocarro 'fala' com a infraestrutura: anuncia "
                "que se aproxima e pede prioridade, e o semáforo responde. Sem esta troca de "
                "mensagens, o motor de decisão TSP não teria como saber que vem um autocarro "
                "a caminho."
            )
            _cp2 = (
                "A conversa segue um protocolo normalizado de quatro mensagens: o semáforo "
                "difunde o <strong>mapa das aproximações (MAPEM)</strong> e o <strong>estado "
                "das fases em tempo real (SPATEM)</strong>; o autocarro, ao aproximar-se, "
                "envia um <strong>pedido de prioridade (SREM)</strong>; e a unidade de berma "
                "(RSU) devolve a decisão — concede ou recusa — por <strong>SSEM</strong>. O "
                "diagrama abaixo mostra esta sequência."
            )
            _cp3 = (
                "Cada pedido SREM origina exactamente uma resposta SSEM — é nesse par que se "
                "medem a fiabilidade e a latência do canal. Esta tab quantifica o volume de "
                "mensagens por tipo, a saúde do transporte e o ciclo de vida dos pedidos — os "
                "dados que alimentam, em tempo real, as decisões analisadas na tab "
                "<strong>Motor de decisão</strong>."
            )
            st.markdown(
                '<p class="chart-title">Como funciona a comunicação C-ITS</p>'
                '<div class="ctx-block">'
                f"<p>{_cp1}</p><p>{_cp2}</p><p>{_cp3}</p>"
                "</div>",
                unsafe_allow_html=True,
            )

            # ── plain-language intro: the V2X conversation ────────────────────
            srem_n = by_type.get("SREM") or 0
            ssem_n = by_type.get("SSEM") or 0
            granted_n = prl.get("granted_requests") or 0
            tracked_n = prl.get("tracked_requests") or 0
            if srem_n or ssem_n:
                grant_html = (
                    f", e <strong>{granted_n}</strong> dos <strong>{tracked_n}</strong> "
                    "pedidos foram concedidos"
                    if tracked_n
                    else ""
                )
                hero_lead(
                    f"{srem_n + ssem_n:,}",
                    "mensagens de prioridade trocadas entre veículos prioritários e "
                    f"semáforos — <strong>{srem_n:,}</strong> pedidos (SREM) e "
                    f"<strong>{ssem_n:,}</strong> respostas (SSEM){grant_html}.",
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
                "Camada de comunicação V2X que fornece, em tempo real, os dados de entrada "
                "do motor de decisão TSP."
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
                        color_discrete_sequence=["#1d4ed8", "#0891b2", "#7c3aed", "#16a34a"],
                        height=320,
                        log_y=True,
                    )
                    fig_ct.update_layout(showlegend=False)
                    fig_ct.update_traces(hovertemplate="%{x}: %{y}<extra></extra>")
                    chart_layout(fig_ct, "Mensagens por protocolo C-ITS (escala log)")
                    st.plotly_chart(fig_ct, width="stretch", config={"displayModeBar": False})
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
                        "Entrega de <strong>100%</strong>: nesta corrida o canal V2X está "
                        "configurado como ideal (sem perdas, latência ou jitter), por isso o valor "
                        "é esperado <em>por construção</em> — um sanity-check ao transporte, não uma "
                        "robustez emergente. Modelar perda/latência exige activar o transporte na "
                        "configuração C-ITS."
                    )

            section("Latência do protocolo (SREM → SSEM)")
            latency = summ.get("cits_latency_ms")
            if not latency:
                # Reports gerados antes deste KPI não trazem a latência no sumário;
                # calcula-se na hora a partir do JSONL da run (sem re-correr SUMO).
                _ep = (demo.get("evidence_paths") or {}).get(sel_cits_run) or {}
                _root = _ep.get("root")
                if _root:
                    _jsonl = Path(_root) / "outputs" / "cits_messages.jsonl"
                    if _jsonl.exists():
                        latency = _cits_latency_from_jsonl(str(_jsonl), _jsonl.stat().st_mtime)
            proc = (latency or {}).get("srem_to_processing") or {}
            final = (latency or {}).get("srem_to_final") or {}

            def _ms_to_s(ms) -> str:
                return f"{ms / 1000:.1f} s" if isinstance(ms, (int, float)) else "—"

            if proc.get("count") or final.get("count"):
                la1, la2 = st.columns(2)
                la1.metric(
                    "SREM → processamento (médio)",
                    _ms_to_s(proc.get("avg")),
                    help="Tempo simulado médio até o RSU acusar o pedido (processing). "
                    f"Mín {_ms_to_s(proc.get('min'))} · máx {_ms_to_s(proc.get('max'))} · "
                    f"n={proc.get('count', 0)} pares.",
                    border=True,
                )
                la2.metric(
                    "SREM → resposta final (médio)",
                    _ms_to_s(final.get("avg")),
                    help="Tempo simulado médio até à decisão final (concedido/recusado). "
                    f"Mín {_ms_to_s(final.get('min'))} · máx {_ms_to_s(final.get('max'))} · "
                    f"n={final.get('count', 0)} pares.",
                    border=True,
                )
                insight(
                    "Medido em <strong>tempo de simulação</strong> a partir do "
                    "<code>generation_time_ms</code> das mensagens (do primeiro SREM de um pedido "
                    "até ao SSEM). Como o canal de transporte é ideal (sem latência de rede), "
                    "isto reflecte o tempo de <strong>resolução do pedido</strong> ao longo da "
                    "aproximação do autocarro — não latência de comunicação."
                )
            else:
                st.caption("Sem pares SREM→SSEM suficientes para medir latência neste cenário.")

            section("Ciclo de vida dos pedidos de prioridade (SREM/SSEM)")
            prl = summ.get("priority_request_lifecycle", {})
            if prl:
                tracked = prl.get("tracked_requests") or 0
                granted = prl.get("granted_requests") or 0
                # `by_status` parte os pedidos seguidos pelo ESTADO FINAL
                # (resolvido + expirado = seguidos). Os campos cleared_requests /
                # expired_requests do sumário são contadores de eventos cumulativos
                # (incrementam a cada passo em que um pedido limpa/expira) e por isso
                # excedem `tracked` — não servem para o gráfico de estados.
                by_status = prl.get("by_status") or {}
                cleared = by_status.get("cleared", 0)
                expired = by_status.get("expired", 0)
                active = prl.get("active_requests") or 0
                if tracked:
                    grant_rate = granted / tracked * 100
                    lc1, lc2, lc3, lc4 = st.columns(4)
                    lc1.metric(
                        "Pedidos seguidos",
                        f"{tracked}",
                        help="Pedidos de prioridade únicos (SREM) seguidos até ao desfecho final.",
                        border=True,
                    )
                    lc2.metric(
                        "Concedidos",
                        f"{granted}",
                        help=f"Pedidos que receberam prioridade (verde). Taxa de concessão {grant_rate:.0f}%.",
                        border=True,
                    )
                    lc3.metric(
                        "Resolvidos",
                        f"{cleared}",
                        help="Estado final: o autocarro passou o cruzamento (pedido concluído).",
                        border=True,
                    )
                    lc4.metric(
                        "Expirados",
                        f"{expired}",
                        help="Estado final: timeout sem concessão (TTL excedido).",
                        border=True,
                    )
                    # Honest partition: a single stacked bar where Resolvidos + Expirados
                    # (+ Ativos, se os houver) somam exactamente os pedidos seguidos.
                    bar_rows = [
                        ("Resolvidos", cleared, COLOR_GOOD),
                        ("Expirados", expired, COLOR_BAD),
                    ]
                    if active:
                        bar_rows.append(("Ativos", active, "#94a3b8"))
                    bar_rows = [(name, n, col) for name, n, col in bar_rows if n]
                    if bar_rows:
                        df_lc = pd.DataFrame(
                            {
                                "Desfecho": [r[0] for r in bar_rows],
                                "Pedidos": [r[1] for r in bar_rows],
                                "_": ["Desfecho final"] * len(bar_rows),
                            }
                        )
                        fig_lc = px.bar(
                            df_lc,
                            x="Pedidos",
                            y="_",
                            color="Desfecho",
                            orientation="h",
                            text="Pedidos",
                            color_discrete_map={r[0]: r[2] for r in bar_rows},
                            height=200,
                        )
                        fig_lc.update_traces(
                            textposition="inside",
                            insidetextanchor="middle",
                            textfont={"size": 13, "color": "white"},
                            hovertemplate="%{fullData.name}: %{x} pedidos<extra></extra>",
                        )
                        fig_lc.update_layout(
                            barmode="stack",
                            legend_title="",
                            margin={"t": 44, "b": 16, "l": 8, "r": 8},
                        )
                        fig_lc.update_yaxes(title_text="", showticklabels=False)
                        fig_lc.update_xaxes(title_text="")
                        chart_layout(fig_lc, f"Desfecho dos {tracked} pedidos seguidos", height=200)
                        st.plotly_chart(fig_lc, width="stretch", config={"displayModeBar": False})
                    _parts = cleared + expired + active
                    _eq = (
                        "= todos os pedidos seguidos"
                        if _parts == tracked
                        else f"de {tracked} seguidos"
                    )
                    insight(
                        f"<strong>Resolvidos + Expirados = {cleared} + {expired} = {_parts}</strong> {_eq}. "
                        f"<strong>Concedidos</strong> ({granted}) é o subconjunto que recebeu prioridade "
                        f"— taxa de concessão {grant_rate:.0f}%. Um pedido pode ser concedido e ainda "
                        "assim contar como «resolvido» quando o autocarro acaba por passar."
                    )

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4 — Baseline vs RL
# ═══════════════════════════════════════════════════════════════════════════════

elif _active == "vs RL":
    # ── explanatory context (mirrors the Resumo/Decisão/C-ITS intro blocks) ──
    # Shown regardless of data availability so the tab's purpose reads even when
    # the comparison report hasn't been generated yet.
    _rp1 = (
        "Esta tab compara duas formas de decidir a prioridade semafórica: a "
        "<strong>regra heurística (rule-based)</strong> da tab Motor de decisão — critérios "
        "fixos e explicáveis — e uma <strong>política de Reinforcement Learning (RL)</strong>, "
        "treinada para maximizar o desempenho da rede. A pergunta é simples: uma política "
        "aprendida com dados decide melhor do que regras escritas à mão?"
    )
    _rp2 = (
        "A política RL é treinada <strong>offline</strong>, por tentativa e erro em simulação, "
        "ajustando-se para reduzir o tempo perdido sem degradar o resto da rede. É depois "
        "avaliada <strong>decisão a decisão</strong> contra a baseline rule-based: para cada "
        "situação compara-se a acção que cada abordagem escolheria e o valor estimado do "
        "respectivo resultado."
    )
    _rp3 = (
        "Um <strong>veredicto positivo</strong> indica que a RL escolheu uma acção com melhor "
        "valor esperado do que a regra. As secções abaixo mostram a distribuição desses "
        "veredictos e o impacto agregado nos KPIs de rede. A RL é aqui uma <strong>linha de "
        "investigação</strong> — serve para testar se há margem para superar a heurística, que "
        "continua a ser a base de referência."
    )
    st.markdown(
        '<p class="chart-title">Como funciona a comparação vs RL</p>'
        '<div class="ctx-block">'
        f"<p>{_rp1}</p><p>{_rp2}</p><p>{_rp3}</p>"
        "</div>",
        unsafe_allow_html=True,
    )
    if not decision_outcome:
        warn(
            "Report de comparação Baseline vs RL não disponível. "
            "Corre <code>make evaluate-decision-outcomes</code> para gerar este relatório."
        )
    else:
        matched = decision_outcome.get("matched_decision_count", 0) or 0
        net_verdict = decision_outcome.get("network_impact_verdict") or "—"
        hero_lead(
            f"{matched}",
            "decisões da política RL comparadas com a baseline rule-based — veredicto "
            f"de impacto na rede: <strong>{net_verdict}</strong>.",
        )
        st.caption(
            "A política RL é treinada offline e avaliada contra a regra heurística; aqui vê-se "
            "onde diverge e se as divergências melhoram o resultado."
        )

        rc1, rc2, rc3 = st.columns(3)
        rc1.metric("Decisões comparadas", matched, border=True)
        rc2.metric("Veredicto de rede", net_verdict, border=True)
        rc3.metric(
            "Tipo de avaliação",
            (decision_outcome.get("evaluation") or "—").replace("_", " "),
            border=True,
        )

        section("Distribuição de veredictos por decisão")
        vc = decision_outcome.get("verdict_counts", {})
        if vc:
            df_vc = pd.DataFrame({"Veredicto": list(vc.keys()), "Contagem": list(vc.values())})
            fig_vc = px.bar(
                df_vc,
                x="Veredicto",
                y="Contagem",
                color="Veredicto",
                color_discrete_sequence=[COLOR_GOOD, COLOR_BAD, "#94a3b8", "#f59e0b"],
                height=320,
            )
            fig_vc.update_layout(showlegend=False)
            chart_layout(fig_vc, "Veredictos da política RL vs baseline rule-based")
            st.plotly_chart(fig_vc, width="stretch", config={"displayModeBar": False})
            insight(
                "Cada decisão compara a acção da política RL com a rule-based. Veredicto positivo = "
                "RL escolheu acção com melhor valor estimado de recompensa."
            )

        kpi_eval = decision_outcome.get("kpi_evaluation", {})
        if kpi_eval.get("available") and kpi_eval.get("rows"):
            section("KPIs — Baseline vs RL")
            rl_rows = []
            for r in kpi_eval["rows"]:
                mk = r.get("metric", "")
                # B25: the outcome evaluator emits composite "group:metric" keys
                # (e.g. "buses:mean_time_loss_s"); split so KPI_META (keyed by the
                # bare metric) resolves to a friendly label instead of the raw key.
                grp, sep, metric_only = mk.partition(":")
                base_key = metric_only if sep else mk
                lab, _, _ = KPI_META.get(base_key, (base_key, "", ""))
                if sep:
                    grp_label = {
                        "all_vehicles": "Todos",
                        "buses": "Autocarros",
                        "general_traffic": "Tráfego geral",
                    }.get(grp, grp)
                    lab = f"{grp_label}: {lab}"
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
            st.dataframe(df_rl, width="stretch", hide_index=True)
            download_csv(df_rl, "baseline_vs_rl_kpis.csv", key="dl_rl")

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5 — Scenarios
# ═══════════════════════════════════════════════════════════════════════════════

elif _active == "KPIs":
    scenario_roots = discover_scenario_report_roots(REPORTS)
    dataset_labels = {
        "synthetic": "Corredor sintético — Boavista",
    }
    dataset_ids = list(scenario_roots) or ["synthetic"]
    default_dataset = default_scenario_dataset(REPORTS)
    default_idx = dataset_ids.index(default_dataset) if default_dataset in dataset_ids else 0
    selected_dataset = st.selectbox(
        "Dataset de cenários",
        dataset_ids,
        index=default_idx,
        format_func=lambda item: dataset_labels.get(item, item),
        key="scenario_dataset",
    )
    scenario_dir = scenario_roots.get(selected_dataset, REPORTS / "scenarios")
    scen_names = (
        sorted(p.name for p in scenario_dir.iterdir() if p.is_dir())
        if scenario_dir.exists()
        else []
    )
    if not scen_names:
        warn(
            "Sem resultados de cenários. Corre <code>make scenario-suite</code> para gerar "
            "os cenários emparelhados baseline SUMO vs TSP em <code>reports/scenarios</code>."
        )
    else:
        # ── rich per-run KPI table: every scope from each kpis.json ───────────
        # Unlike the legacy single-class loader, this reads the whole kpis.json so
        # we can juxtapose bus vs general traffic, queues, safety and air quality
        # from the same run. Source of truth: reports/<dataset>/*/*/seed_*/kpis.json.
        table_rows = scenario_run_table(scenario_dir)

        if not table_rows:
            st.info(
                "Cenários presentes mas sem KPIs legíveis nos kpis.json. "
                "Corre a suite para (re)gerar os resultados emparelhados baseline vs TSP."
            )
        else:
            # ── single injected <style> block for this tab ────────────────────
            st.markdown(
                """
<style>
.scenario-header { margin-bottom: 1.5rem; }
.scenario-title { font-size: 22px; font-weight: 700; color: var(--text-color, #0f172a); margin-bottom: 0.25rem; }
.scenario-sub { font-size: 14px; color: rgba(128,128,128,0.9); margin-bottom: 0.75rem; }
.scenario-badges { display: flex; gap: 0.5rem; flex-wrap: wrap; }
.badge-green { background:#dcfce7; color:#15803d; border:1px solid #bbf7d0; border-radius:99px; padding:0.2rem 0.75rem; font-size:13px; font-weight:500; }
.badge-red { background:#fee2e2; color:#b91c1c; border:1px solid #fecaca; border-radius:99px; padding:0.2rem 0.75rem; font-size:13px; font-weight:500; }
.stat-card { border:1px solid #e2e8f0; border-radius:12px; padding:1.25rem 1rem; text-align:center; }
.stat-label { font-size:12px; color:#64748b; margin-bottom:0.35rem; text-transform:uppercase; letter-spacing:0.06em; }
.stat-value { font-size:28px; font-weight:700; line-height:1.1; }
.stat-unit { font-size:13px; color:#94a3b8; margin-top:0.2rem; }
.stat-positive { color:#16a34a; }
.stat-negative { color:#dc2626; }
.stat-neutral { color:#0f172a; }
.scen-obj { border:1px solid #e2e8f0; border-left:4px solid #2563eb; border-radius:12px;
            padding:1.1rem 1.3rem; background:#f8fafc; margin:0.25rem 0 1.1rem; }
.scen-obj-head { display:flex; align-items:center; gap:0.65rem; margin-bottom:0.35rem; }
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
.class-tag { font-size:13px; font-weight:700; color:#0f172a; margin:0.4rem 0 0.2rem; }
</style>
""",
                unsafe_allow_html=True,
            )

            GOOD, BAD, GREY = "#16a34a", "#dc2626", "#94a3b8"
            BASE_COL, TSP_COL = "#64748b", "#2563eb"

            tdf = pd.DataFrame(table_rows)
            run_types_all = sorted(tdf["Run type"].unique())
            baseline_rt = next((r for r in run_types_all if "baseline" in r), None)
            tsp_rt = next((r for r in run_types_all if "tsp" in r), None)
            scen_list = sorted(tdf["Cenário"].unique())
            n_scen = len(scen_list)

            # ── label / catalog machinery (objective card = source of truth) ──
            label_map = dict(SCENARIO_LABELS)
            # Scenario objectives come straight from the catalog (configs/) — the
            # source of truth — so the dashboard never invents scenario descriptions.
            scen_catalog = load_yaml(scenario_catalog_path(ROOT, selected_dataset)) or {}
            scen_meta = scen_catalog.get("scenarios") or {}
            label_map.update(catalog_label_map(scen_catalog))
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

            # Which per-scenario KPIs section each kpi_focus token makes relevant.
            # Tokens describing decision-engine behaviour (rejections, preemption,
            # request rate, green extensions, hierarchy, …) live in the Decisão tab
            # and intentionally map to nothing here, so they light up no section.
            kpi_focus_section = {
                "bus_time_loss": "tradeoff",
                "bus_time_loss_under_saturation": "tradeoff",
                "general_traffic_delay": "tradeoff",
                "general_traffic_penalty": "tradeoff",
                "bus_vs_general_tradeoff": "tradeoff",
                "tsp_under_saturation": "tradeoff",
                "low_delay_regime": "tradeoff",
                "westbound_bus_delay": "tradeoff",
                "emergency_travel_time": "tradeoff",
                "travel_time_variability": "reliability",
                "queue_baseline": "network",
                "queue_spillback": "network",
                "cross_street_queues": "network",
                "bus_headway": "headways",
                "headway_variability": "headways",
                "bus_bunching": "headways",
            }

            # ── value accessor: mean over seeds for one cell ──────────────────
            def _agg(scen, rt, scope, metric_key):
                if rt is None:
                    return None
                mask = (
                    (tdf["Cenário"] == scen)
                    & (tdf["Run type"] == rt)
                    & (tdf["scope"] == scope)
                    & (tdf["metric_key"] == metric_key)
                )
                vals = tdf.loc[mask, "Valor"]
                return float(vals.mean()) if len(vals) else None

            def _absdelta(b, t):
                return (t - b) if (b is not None and t is not None) else None

            # ═════════════════════════════════════════════════════════════════
            # ① VISÃO GERAL — todos os cenários de relance
            # ═════════════════════════════════════════════════════════════════
            section(f"Visão geral — impacto do TSP nos {n_scen} cenários")
            ov_rows = []
            for scen in scen_list:
                bus_b = _agg(scen, baseline_rt, "buses", "mean_time_loss_s")
                bus_t = _agg(scen, tsp_rt, "buses", "mean_time_loss_s")
                gen_b = _agg(scen, baseline_rt, "general_traffic", "mean_time_loss_s")
                gen_t = _agg(scen, tsp_rt, "general_traffic", "mean_time_loss_s")
                q_b = _agg(scen, baseline_rt, "network", "max_queue_vehicles")
                q_t = _agg(scen, tsp_rt, "network", "max_queue_vehicles")
                eb_b = _agg(scen, baseline_rt, "safety", "emergency_braking")
                eb_t = _agg(scen, tsp_rt, "safety", "emergency_braking")
                coll_t = _agg(scen, tsp_rt, "safety", "collisions")
                jam_t = _agg(scen, tsp_rt, "safety", "teleports_jam")
                bus_d = pct(bus_b, bus_t)
                gen_d = pct(gen_b, gen_t)
                gen_penalty = _absdelta(gen_b, gen_t)
                # Safety/net verdict MIRRORS the repo's own gates
                # (run_sumo_scenario.compare_scenario_runs): collisions=0 ∧
                # jam-teleports=0, and the general-traffic penalty must stay ≤ ~90 s.
                safe = coll_t is not None and jam_t is not None and coll_t == 0 and jam_t == 0
                if bus_d is None:
                    verdict = "—"
                elif bus_d < 0 and (gen_penalty is None or gen_penalty <= 90) and safe:
                    verdict = "favorável"
                elif bus_d > 0:
                    verdict = "bus pior"
                elif not safe:
                    verdict = "segurança"
                else:
                    verdict = "neutro"
                ov_rows.append(
                    {
                        "Cenário": label_map.get(scen, scen),
                        "Bus Δ% perda tempo": bus_d,
                        "Geral Δ% perda tempo": gen_d,
                        "Fila máx Δ": _absdelta(q_b, q_t),
                        "Trav. emerg. Δ": _absdelta(eb_b, eb_t),
                        "Segurança": "sim" if safe else "não",
                        "Veredicto": verdict,
                    }
                )
            ov = pd.DataFrame(ov_rows)
            delta_cols = [
                "Bus Δ% perda tempo",
                "Geral Δ% perda tempo",
                "Fila máx Δ",
                "Trav. emerg. Δ",
            ]

            def _delta_color(v):
                # green = improvement (TSP below baseline); red = cost.
                if v is None or (isinstance(v, float) and pd.isna(v)):
                    return ""
                if v < 0:
                    return "background-color:#dcfce7;color:#15803d"
                if v > 0:
                    return "background-color:#fee2e2;color:#b91c1c"
                return ""

            def _style_overview(frame):
                # Styler for COLOURS only; number formatting is column_config's job
                # (per the data-display guidance).
                styles = pd.DataFrame("", index=frame.index, columns=frame.columns)
                for col in delta_cols:
                    if col in frame.columns:
                        styles[col] = frame[col].map(_delta_color)
                return styles

            st.dataframe(
                ov.style.apply(_style_overview, axis=None),
                width="stretch",
                hide_index=True,
                column_config={
                    "Bus Δ% perda tempo": st.column_config.NumberColumn(
                        format="%+.1f%%",
                        help="Variação da perda de tempo dos autocarros (TSP vs baseline).",
                    ),
                    "Geral Δ% perda tempo": st.column_config.NumberColumn(
                        format="%+.1f%%",
                        help="Variação da perda de tempo do tráfego geral — o custo do TSP.",
                    ),
                    "Fila máx Δ": st.column_config.NumberColumn(
                        format="%+.0f", help="Variação da fila máxima na rede (veículos)."
                    ),
                    "Trav. emerg. Δ": st.column_config.NumberColumn(
                        format="%+.0f", help="Variação do nº de travagens de emergência."
                    ),
                },
            )
            insight(
                "Verde = melhoria (TSP abaixo do baseline) · vermelho = custo. "
                "O <strong>veredicto</strong> espelha os gates do próprio pipeline: "
                "autocarro melhora, tráfego geral não penalizado &gt; ~90&nbsp;s, "
                "sem colisões nem teleports por gridlock."
            )
            download_csv(ov, "kpis_visao_geral.csv", key="dl_overview")
            _n_seeds = int(tdf["Seed"].nunique()) if "Seed" in tdf.columns else 1
            if _n_seeds > 1:
                st.caption(
                    f"Médias entre {_n_seeds} seeds. Esta vista geral mostra o ponto médio; "
                    "o IC95 emparelhado por cenário está na secção «Foco por cenário» abaixo "
                    "e no RESULTS.md."
                )
            else:
                st.caption(
                    "Estimativa pontual, single-seed. Sem barras de erro — corre a suite "
                    "multi-seed para intervalos de confiança."
                )

            # ═════════════════════════════════════════════════════════════════
            # ② FOCO POR CENÁRIO — a métrica que o headline (bus/geral) não isola,
            #    com IC95 emparelhado vindo do suite summary. Cálculo uniforme (os
            #    mesmos grupos para todos); aqui é só destaque por cenário.
            # ═════════════════════════════════════════════════════════════════
            # (scope em kpis.json, rótulo, chave de significância no comparison)
            SCENARIO_FOCUS = {
                "emergency_vehicle_conflict": (
                    "emergency_vehicles",
                    "Perda de tempo · veículo de emergência",
                    "emergency_time_loss_replication_significance",
                ),
                "delayed_bus_westbound": (
                    "buses_westbound",
                    "Perda de tempo · autocarro westbound",
                    "bus_westbound_time_loss_replication_significance",
                ),
                "congested_delayed_bus": (
                    "buses_westbound",
                    "Perda de tempo · autocarro westbound (saturado)",
                    "bus_westbound_time_loss_replication_significance",
                ),
            }
            _sig_by_scen = scenario_focus_significance(scenario_dir)
            _VERDICT_GLYPH = {
                "significant_improvement": "melhoria significativa",
                "significant_regression": "regressão significativa",
                "inconclusive_ci_includes_zero": "inconclusivo (IC95 inclui 0)",
            }
            focus_rows = []
            for _scen, (_scope, _flabel, _sigkey) in SCENARIO_FOCUS.items():
                if _scen not in scen_list:
                    continue
                _b = _agg(_scen, baseline_rt, _scope, "mean_time_loss_s")
                _t = _agg(_scen, tsp_rt, _scope, "mean_time_loss_s")
                if _b is None or _t is None:
                    continue
                _sig = (_sig_by_scen.get(_scen) or {}).get(_sigkey) or {}
                _lo, _hi = _sig.get("ci95_low"), _sig.get("ci95_high")
                _ci = (
                    f"[{_lo:+.1f}, {_hi:+.1f}] s"
                    if isinstance(_lo, (int, float)) and isinstance(_hi, (int, float))
                    else "—"
                )
                focus_rows.append(
                    {
                        "Cenário": label_map.get(_scen, _scen),
                        "Métrica-foco": _flabel,
                        "Baseline (s)": _b,
                        "TSP (s)": _t,
                        "Δ% perda tempo": pct(_b, _t),
                        "IC95 melhoria (s)": _ci,
                        "Veredicto emparelhado": _VERDICT_GLYPH.get(
                            _sig.get("verdict"), "— (precisa ≥2 seeds)"
                        ),
                    }
                )
            if focus_rows:
                section("Foco por cenário — métrica específica com IC95 emparelhado")
                fdf = pd.DataFrame(focus_rows)

                def _style_focus(frame):
                    styles = pd.DataFrame("", index=frame.index, columns=frame.columns)
                    if "Δ% perda tempo" in frame.columns:
                        styles["Δ% perda tempo"] = frame["Δ% perda tempo"].map(_delta_color)
                    return styles

                st.dataframe(
                    fdf.style.apply(_style_focus, axis=None),
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "Baseline (s)": st.column_config.NumberColumn(format="%.1f"),
                        "TSP (s)": st.column_config.NumberColumn(format="%.1f"),
                        "Δ% perda tempo": st.column_config.NumberColumn(
                            format="%+.1f%%",
                            help="Variação da métrica-foco (TSP vs baseline). Verde = melhoria.",
                        ),
                    },
                )
                insight(
                    "Calculada com a mesma régua dos restantes KPIs; o veredicto emparelhado é "
                    "significativo só quando o IC95 exclui zero. Cenários sem foco dedicado "
                    "(ex.: bunching) ficam cobertos pela visão geral acima."
                )
                download_csv(fdf, "kpis_foco_cenario.csv", key="dl_focus")

            # ═════════════════════════════════════════════════════════════════
            # ③ DETALHE POR CENÁRIO
            # ═════════════════════════════════════════════════════════════════
            section("Detalhe por cenário")
            col_scen, col_class = st.columns([3, 2], vertical_alignment="bottom")
            with col_scen:
                selected_scenario = st.selectbox(
                    "Cenário",
                    scen_list,
                    format_func=lambda s: label_map.get(s, s),
                    key="scen_detail",
                )
            with col_class:
                st.selectbox(
                    "Classe de veículo (filtro global)",
                    options=CLASS_OPTIONS,
                    index=CLASS_OPTIONS.index(selected_class),
                    key="kpis_class_select",
                    on_change=_sync_vehicle_class_kpis,
                    help="Aplica-se às outras tabs. O trade-off abaixo mostra sempre "
                    "autocarro e tráfego geral, independente deste filtro.",
                )
            sel = selected_scenario
            sel_label = label_map.get(sel, sel)

            # ── scenario objective card (catalog source of truth) ─────────────
            _scen_meta = scen_meta.get(sel) or {}
            # Sections this scenario's catalog kpi_focus flags as primary — drives
            # the "Foco" pill on the matching section headers below.
            _focus_sections = {
                kpi_focus_section[k]
                for k in (_scen_meta.get("kpi_focus") or [])
                if k in kpi_focus_section
            }
            if _scen_meta:
                _rows_html = ""
                if _scen_meta.get("expected_use"):
                    _rows_html += (
                        '<div class="scen-obj-row"><div class="scen-obj-lbl">Objetivo</div>'
                        f'<p class="scen-obj-txt">{_scen_meta["expected_use"].strip()}</p></div>'
                    )
                if _scen_meta.get("realism_basis"):
                    _rows_html += (
                        '<div class="scen-obj-row"><div class="scen-obj-lbl muted">'
                        "Porquê é realista</div>"
                        f'<p class="scen-obj-txt muted">{_scen_meta["realism_basis"].strip()}</p></div>'
                    )
                _chips_html = ""
                _focus = _scen_meta.get("kpi_focus") or []
                if _focus:
                    _chips = "".join(
                        f'<span class="scen-chip">'
                        f"{kpi_focus_pt.get(k, k.replace('_', ' ').capitalize())}</span>"
                        for k in _focus
                    )
                    _chips_html = (
                        '<div class="scen-obj-row"><div class="scen-obj-lbl muted">Foco de KPIs'
                        f'</div><div class="scen-obj-chips">{_chips}</div></div>'
                    )
                _desc = (_scen_meta.get("description") or "").strip()
                st.markdown(
                    '<div class="scen-obj">'
                    f'<div class="scen-obj-head">'
                    f'<span class="scen-obj-desc">{_desc}</span></div>'
                    f"{_rows_html}{_chips_html}</div>",
                    unsafe_allow_html=True,
                )

            # ── ▸ Trade-off: autocarro vs tráfego geral ───────────────────────
            section(f"Trade-off TSP · {sel_label}", focus="tradeoff" in _focus_sections)
            _tradeoff = ["mean_time_loss_s", "mean_waiting_time_s", "mean_duration_s"]

            def _render_class_row(scope_key):
                cols = st.columns(len(_tradeoff))
                for col, mk in zip(cols, _tradeoff, strict=False):
                    render_kpi_card(
                        col,
                        mk,
                        _agg(sel, tsp_rt, scope_key, mk),
                        _agg(sel, baseline_rt, scope_key, mk),
                    )

            st.markdown(
                '<div class="class-tag">Autocarro &nbsp;·&nbsp; baseline → TSP</div>',
                unsafe_allow_html=True,
            )
            _render_class_row("buses")
            st.markdown(
                '<div class="class-tag">Tráfego geral &nbsp;·&nbsp; baseline → TSP</div>',
                unsafe_allow_html=True,
            )
            _render_class_row("general_traffic")
            insight(
                "O TSP é um <strong>trade-off</strong>: ganha tempo para o autocarro, "
                "idealmente sem penalizar o tráfego geral. Verde = melhoria, vermelho = custo "
                "(cada cartão mostra o valor TSP e o delta vs baseline)."
            )

            # ── ▸ Fiabilidade / cauda (P95) ───────────────────────────────────
            section(
                f"Fiabilidade · {sel_label} — cauda da distribuição (P95)",
                focus="reliability" in _focus_sections,
            )
            _rel = ["p95_time_loss_s", "p95_duration_s"]
            st.markdown('<div class="class-tag">Autocarro</div>', unsafe_allow_html=True)
            _rb = st.columns(len(_rel))
            for col, mk in zip(_rb, _rel, strict=False):
                render_kpi_card(
                    col, mk, _agg(sel, tsp_rt, "buses", mk), _agg(sel, baseline_rt, "buses", mk)
                )
            st.markdown('<div class="class-tag">Tráfego geral</div>', unsafe_allow_html=True)
            _rg = st.columns(len(_rel))
            for col, mk in zip(_rg, _rel, strict=False):
                render_kpi_card(
                    col,
                    mk,
                    _agg(sel, tsp_rt, "general_traffic", mk),
                    _agg(sel, baseline_rt, "general_traffic", mk),
                )
            insight(
                "O P95 descreve o pior caso para 95% dos veículos. Aproximar o P95 da média "
                "significa viagens mais previsíveis — não só mais rápidas."
            )

            # ── ▸ Saúde da rede & segurança ───────────────────────────────────
            section(f"Saúde da rede & segurança · {sel_label}", focus="network" in _focus_sections)
            # Thresholds mirror scenario.sumo_quality_thresholds (identical across the
            # suite) — the pipeline's own documented quality gates, not new criteria.
            safety_specs = [
                ("network", "max_queue_vehicles", "≤ 30", lambda t: t is None or t <= 30),
                ("network", "edge_intervals_above_8_veh", "—", None),
                ("safety", "teleports_total", "≤ 3", lambda t: t is None or t <= 3),
                ("safety", "teleports_jam", "0", lambda t: not t),
                ("safety", "collisions", "0", lambda t: not t),
                ("safety", "emergency_braking", "≤ 150", lambda t: t is None or t <= 150),
                ("safety", "max_waiting_to_insert", "≤ 150 s", lambda t: t is None or t <= 150),
                ("safety", "final_waiting", "≤ 150", lambda t: t is None or t <= 150),
            ]
            srows = []
            for scope_key, mk, thr, ok in safety_specs:
                b = _agg(sel, baseline_rt, scope_key, mk)
                t = _agg(sel, tsp_rt, scope_key, mk)
                if b is None and t is None:
                    continue
                lbl, unit, _ = KPI_META.get(mk, (mk, "", ""))
                state = "—"
                if ok is not None and t is not None:
                    state = "ok" if ok(t) else "excede"
                srows.append(
                    {
                        "Indicador": lbl + (f" ({unit})" if unit else ""),
                        "Baseline": b,
                        "TSP": t,
                        "Δ": _absdelta(b, t),
                        "Limiar": thr,
                        "Estado": state,
                    }
                )
            if srows:
                st.dataframe(
                    pd.DataFrame(srows),
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "Baseline": st.column_config.NumberColumn(format="%.0f"),
                        "TSP": st.column_config.NumberColumn(format="%.0f"),
                        "Δ": st.column_config.NumberColumn(format="%+.0f"),
                    },
                )
                insight(
                    "Os limiares são os gates de qualidade do próprio pipeline "
                    "(<code>scenario.sumo_quality_thresholds</code>). O objetivo do TSP é "
                    "melhorar o autocarro <strong>sem</strong> empurrar nenhum destes para fora do limiar."
                )

            # ── ▸ Regularidade dos autocarros (headways) ──────────────────────
            section(
                f"Regularidade dos autocarros · {sel_label} — headways",
                focus="headways" in _focus_sections,
            )
            hd = tdf[(tdf["Cenário"] == sel) & (tdf["scope"] == "headway")]
            if hd.empty:
                st.info("Sem dados de headway para este cenário.")
            else:
                lines = sorted(x for x in hd["Linha"].dropna().unique())

                def _hv(line_id, rt, mk):
                    if rt is None:
                        return None
                    m = (
                        (tdf["Cenário"] == sel)
                        & (tdf["scope"] == "headway")
                        & (tdf.get("Linha") == line_id)
                        & (tdf["Run type"] == rt)
                        & (tdf["metric_key"] == mk)
                    )
                    vals = tdf.loc[m, "Valor"]
                    return float(vals.mean()) if len(vals) else None

                hrows = []
                for ln in lines:
                    hrows.append(
                        {
                            "Linha": ln,
                            "Headway médio base (s)": _hv(ln, baseline_rt, "mean_headway_s"),
                            "Headway médio TSP (s)": _hv(ln, tsp_rt, "mean_headway_s"),
                            "Amplitude base (s)": _hv(ln, baseline_rt, "headway_amplitude_s"),
                            "Amplitude TSP (s)": _hv(ln, tsp_rt, "headway_amplitude_s"),
                        }
                    )
                hdf = pd.DataFrame(hrows)
                st.dataframe(
                    hdf,
                    width="stretch",
                    hide_index=True,
                    column_config={
                        c: st.column_config.NumberColumn(format="%.0f")
                        for c in hdf.columns
                        if c != "Linha"
                    },
                )
                amp = hdf[["Linha", "Amplitude base (s)", "Amplitude TSP (s)"]].melt(
                    "Linha", var_name="Arm", value_name="Amplitude (s)"
                )
                amp["Arm"] = amp["Arm"].map(
                    {"Amplitude base (s)": "Baseline", "Amplitude TSP (s)": "TSP"}
                )
                fig_h = px.bar(
                    amp.dropna(subset=["Amplitude (s)"]),
                    x="Linha",
                    y="Amplitude (s)",
                    color="Arm",
                    barmode="group",
                    color_discrete_map={"Baseline": BASE_COL, "TSP": TSP_COL},
                )
                fig_h.update_layout(legend_title="", margin={"t": 44, "b": 30, "l": 8, "r": 8})
                chart_layout(fig_h, f"Amplitude de headway por linha — {sel_label}", height=300)
                st.plotly_chart(fig_h, width="stretch", config={"displayModeBar": False})
                insight(
                    "A amplitude (máx − mín) é um proxy <em>coarse</em> de bunching "
                    "(poucas partidas por linha, só média/mín/máx em disco). "
                    "Menor amplitude = serviço mais regular."
                )

            # ── ▸ Emissões & qualidade do ar ──────────────────────────────────
            section(f"Emissões & qualidade do ar · {sel_label}", focus="emissions" in _focus_sections)
            em_species = [
                ("total_co2_mg_per_vehicle_km", "CO2"),
                ("total_fuel_mg_per_vehicle_km", "Combustível"),
                ("total_nox_mg_per_vehicle_km", "NOx"),
                ("total_pmx_mg_per_vehicle_km", "PMx"),
            ]
            erows = []
            for mk, name in em_species:
                b = _agg(sel, baseline_rt, "emissions", mk)
                t = _agg(sel, tsp_rt, "emissions", mk)
                if b is None and t is None:
                    continue
                erows.append({"Poluente": name, "Baseline": b, "TSP": t, "Δ (%)": pct(b, t)})
            if not erows:
                st.info("Sem dados de emissões para este cenário.")
            else:
                edf = pd.DataFrame(erows)
                col_tbl, col_chart = st.columns([2, 3])
                with col_tbl:
                    st.dataframe(
                        edf,
                        width="stretch",
                        hide_index=True,
                        column_config={
                            "Baseline": st.column_config.NumberColumn(
                                "Baseline (mg/veíc-km)", format="%.0f"
                            ),
                            "TSP": st.column_config.NumberColumn("TSP (mg/veíc-km)", format="%.0f"),
                            "Δ (%)": st.column_config.NumberColumn(format="%+.1f%%"),
                        },
                    )
                with col_chart:
                    ed = edf.dropna(subset=["Δ (%)"]).copy()
                    if ed.empty:
                        st.info("Sem pares baseline + TSP para o gráfico de emissões.")
                    else:
                        ed["Efeito"] = ed["Δ (%)"].map(
                            lambda v: "Melhoria" if v < 0 else ("Custo" if v > 0 else "Neutro")
                        )
                        fig_e = px.bar(
                            ed,
                            x="Poluente",
                            y="Δ (%)",
                            color="Efeito",
                            color_discrete_map={"Melhoria": GOOD, "Custo": BAD, "Neutro": GREY},
                        )
                        fig_e.update_traces(
                            texttemplate="%{y:+.1f}%",
                            textposition="outside",
                            hovertemplate="%{x}: %{y:+.1f}%<extra></extra>",
                        )
                        fig_e.update_layout(
                            legend_title="", margin={"t": 44, "b": 30, "l": 8, "r": 8}
                        )
                        chart_layout(
                            fig_e,
                            f"Δ emissões por poluente (TSP vs baseline) · {sel_label}",
                            height=320,
                        )
                        st.plotly_chart(fig_e, width="stretch", config={"displayModeBar": False})
                insight(
                    "Valores por <strong>veículo-km</strong> (a normalização comparável entre arms). "
                    "Negativo = o TSP reduziu o poluente. NOx e PMx são os poluentes mais relevantes "
                    "para a saúde urbana."
                )


# ═══════════════════════════════════════════════════════════════════════════════
# TAB 6 — Metodologia
# ═══════════════════════════════════════════════════════════════════════════════

elif _active == "Documentação":
    with st.container(horizontal=True):
        st.badge("Dados reais SUMO/TraCI", icon=":material/verified:", color="green")
        st.badge("Fonte de verdade rastreável", icon=":material/dataset:", color="blue")
        st.badge("Modelo microscópico de tráfego", icon=":material/hub:", color="violet")

    # ── orientação primeiro: como ler, depois o vocabulário ───────────────────
    section("Guia de leitura")
    for step in STEPS:
        st.markdown(
            f'<div class="step-card">'
            f'<div class="step-num">{step["n"]}</div>'
            f"<div>"
            f'<div class="step-title">{step["title"]}</div>'
            f'<div class="step-body">{step["body"]}</div>'
            f"</div></div>",
            unsafe_allow_html=True,
        )

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
            if gloss_query.lower() in g["term"].lower() or gloss_query.lower() in g["def"].lower()
        ]
        if gloss_query
        else GLOSSARY
    )
    if not gloss_filtered:
        st.caption("Nenhuma métrica encontrada.")
    else:
        st.caption(f"A mostrar {len(gloss_filtered)} de {len(GLOSSARY)} métricas.")
        gloss_cols = st.columns(2)
        for gi, entry in enumerate(gloss_filtered):
            with gloss_cols[gi % 2]:
                st.markdown(
                    f'<div class="gloss-card">'
                    f'<div class="gloss-term">{entry["term"]}'
                    f'<span class="gloss-unit">{entry["unit"]}</span></div>'
                    f'<div class="gloss-def">{entry["def"]}</div></div>',
                    unsafe_allow_html=True,
                )

    # ── proveniência: o que foi corrido e de onde vêm os dados ─────────────────
    section("Configuração da simulação")
    if demo:
        sel_run_meta = st.selectbox("Run", list(demo.get("runs", {}).keys()), key="meta_run")
        summ_m = demo["runs"][sel_run_meta].get("summary", {})
        col_a, col_b = st.columns(2)
        with col_a:
            spec_table(
                [
                    ("Modo", str(summ_m.get("mode", "—"))),
                    ("Passos (steps)", str(summ_m.get("steps", "—"))),
                    ("Cenário", str(summ_m.get("scenario_id", "—"))),
                    ("Política runtime", str(summ_m.get("policy_mode", "—"))),
                    ("Actuação activa", str(summ_m.get("actuation_enabled", "—"))),
                    ("Runtime policy carregada", str(summ_m.get("runtime_policy_loaded", "—"))),
                ]
            )
        with col_b:
            sp_ver = summ_m.get("signal_program_verification", {})
            st.markdown("**Verificação do programa semafórico**")
            problems = sp_ver.get("problems", [])
            if not problems:
                st.success("Sem problemas no programa semafórico.", icon=":material/check_circle:")
            else:
                for p in problems:
                    st.error(p, icon=":material/error:")
            if summ_m.get("actuation_downgraded") or sp_ver.get("actuation_downgraded"):
                st.warning("Actuação foi downgraded para modo seguro.", icon=":material/warning:")
    else:
        st.caption("Sem report carregado — corre o demonstrador no separador Simulação.")

    section("Fontes de dados")
    data_policy = demo.get("data_policy", {}) if demo else {}
    spec_table(
        [
            ("Fonte operacional", str(data_policy.get("operational_data_source", "—"))),
            ("Dados sintéticos", str(data_policy.get("synthetic_operational_data", "—"))),
            ("Rede viária", "sumo/plain/corredor.{nod,edg}.xml — geometria manual da Boavista"),
            ("Paragens", "sumo/additional/bus_stops.add.xml"),
            ("Rotas", "sumo/routes/routes.rou.xml — randomTrips com semente controlada"),
        ]
    )

    # ── ressalvas + reprodutibilidade ─────────────────────────────────────────
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
        st.markdown(f'<div class="doc-limit">{lim}</div>', unsafe_allow_html=True)

    if demo:
        section("Caminhos de evidência")
        with st.expander("Ver caminhos dos artefactos gerados", icon=":material/folder:"):
            ev_rows = []
            for run_name, paths in demo.get("evidence_paths", {}).items():
                for atype, path in paths.items():
                    if atype != "root":
                        ev_rows.append({"Run": run_name, "Artefacto": atype, "Path": path})
            if ev_rows:
                st.dataframe(pd.DataFrame(ev_rows), width="stretch", hide_index=True)
            else:
                st.caption("Sem caminhos de evidência registados nesta run.")

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 7 — Simulação
# ═══════════════════════════════════════════════════════════════════════════════

elif _active == "Simulação":
    st.markdown(
        "Lança simulações SUMO directamente a partir da dashboard — visualmente no "
        "SUMO-GUI ou em modo headless para regenerar os reports de análise."
    )
    render_simulation_panel()
