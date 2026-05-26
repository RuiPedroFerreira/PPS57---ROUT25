import { $, append, clear, el, safeClassToken, setActive, tableCell } from "./dom.js";
import { barWidth, deltaClass, fmt, formatDuration, metricRowsForCards } from "./formatters.js";
import { DEFAULT_STEPS, STAGE_INFO, TABS, modeNotes, runTypeLabels, state } from "./state.js";

export function setMessage(value, kind = "info") {
  const node = $("message");
  node.textContent = value || "";
  node.dataset.kind = kind;
}

export function clearError() {
  const node = $("message");
  if (node.dataset.kind === "error") {
    node.textContent = "";
    node.dataset.kind = "info";
  }
}

export function readSteps() {
  const raw = $("steps").value.trim();
  if (!raw) return null;
  const value = Number(raw);
  return Number.isFinite(value) && value >= 1 ? Math.floor(value) : null;
}

export function updateStepsHelp() {
  const steps = readSteps() ?? DEFAULT_STEPS;
  $("steps-help").textContent = `1 passo = 1 segundo. ${formatDuration(steps)}`;
}

export function setActiveTab(tabId) {
  if (!TABS.includes(tabId)) return;
  state.activeTab = tabId;
  for (const tab of document.querySelectorAll(".tab")) {
    const isActive = tab.dataset.tab === tabId;
    tab.classList.toggle("active", isActive);
    tab.setAttribute("aria-selected", isActive ? "true" : "false");
    tab.setAttribute("tabindex", isActive ? "0" : "-1");
  }
  for (const view of document.querySelectorAll(".tab-view")) {
    const isActive = view.id === tabId;
    view.classList.toggle("active", isActive);
    view.toggleAttribute("hidden", !isActive);
  }
}

export function applyStage(stage) {
  if (!STAGE_INFO[stage]) return;
  state.stage = stage;
  document.body.classList.remove("stage-setup", "stage-running", "stage-report");
  document.body.classList.add(`stage-${stage}`);
  $("stage-title").textContent = STAGE_INFO[stage].title;
  $("stage-subtitle").textContent = STAGE_INFO[stage].subtitle;
}

export function renderModeNote() {
  const note = modeNotes[state.selectedRunType] || modeNotes.comparison;
  $("mode-note").replaceChildren(
    el("strong", { textContent: note.title }),
    document.createTextNode(note.body),
  );
  for (const button of document.querySelectorAll(".mode-button")) {
    setActive(button, button.dataset.runType === state.selectedRunType);
  }
}

export function selectScenario(scenarioId) {
  state.selectedScenarioId = scenarioId || null;
  const select = $("scenario");
  if (scenarioId && select.value !== scenarioId) select.value = scenarioId;
  renderSelectedScenario();
}

function currentScenario() {
  if (!state.selectedScenarioId) return null;
  return state.scenarios.find((item) => item.scenario_id === state.selectedScenarioId) || null;
}

export function renderSelectedScenario() {
  const scenario = currentScenario();
  $("scenario-title").textContent = scenario ? scenario.display_name || scenario.scenario_id : "-";
  $("scenario-description").textContent = scenario ? scenario.description || "" : "Seleciona um cenário.";

  const chips = scenario && Array.isArray(scenario.kpi_focus) ? scenario.kpi_focus : [];
  $("scenario-kpis").replaceChildren(...chips.map((item) => el("span", { className: "chip", textContent: item })));
  renderOverviewBars();
}

export function renderOverviewBars() {
  const scenario = currentScenario();
  const cars = scenario ? Number(scenario.estimated_car_departures) || 0 : 0;
  const buses = scenario ? Number(scenario.estimated_bus_departures) || 0 : 0;
  const maxValue = Math.max(cars, buses, 1);
  const steps = readSteps() ?? DEFAULT_STEPS;
  $("overview-bars").replaceChildren(
    barRow("Partidas carro estimadas", cars, maxValue),
    barRow("Partidas autocarro estimadas", buses, maxValue),
    barRow("Horizonte simulado", steps, DEFAULT_STEPS),
  );
}

export function renderScenarios() {
  const scenarioSelect = $("scenario");
  const reportSelect = $("report-scenario");
  clear(scenarioSelect);
  clear(reportSelect);

  for (const scenario of state.scenarios) {
    const label = `${scenario.display_name || scenario.scenario_id} (${scenario.scenario_id})`;
    scenarioSelect.add(new Option(label, scenario.scenario_id));
    reportSelect.add(new Option(label, scenario.scenario_id));
  }

  if (!state.selectedScenarioId && state.scenarios.length) {
    state.selectedScenarioId = state.scenarios[0].scenario_id;
  }
  if (state.selectedScenarioId) scenarioSelect.value = state.selectedScenarioId;

  if (!state.selectedReportId && state.scenarios.length) {
    state.selectedReportId = state.scenarios[0].scenario_id;
  }
  if (state.selectedReportId) reportSelect.value = state.selectedReportId;

  renderSelectedScenario();
  $("scenario-rows").replaceChildren(...state.scenarios.map(scenarioTableRow));
}

function runSignature(run) {
  return JSON.stringify({
    s: run.status,
    id: run.run_id,
    rc: run.returncode,
    m: run.message,
    sc: run.scenario_id,
    rt: run.run_type,
    g: run.gui,
  });
}

export function renderRun(run, onTransitionToReport) {
  const previous = state.run || { status: "idle" };
  state.run = run;
  const status = run.status || "idle";

  if (status === "running" && previous.status !== "running") {
    applyStage("running");
    setActiveTab("overview");
  } else if (
    ["completed", "failed"].includes(status) &&
    run.run_id &&
    previous.status === "running" &&
    state.stage === "running"
  ) {
    applyStage("report");
    setActiveTab("report");
    if (run.scenario_id) {
      state.selectedReportId = run.scenario_id;
      $("report-scenario").value = run.scenario_id;
    }
    if (typeof onTransitionToReport === "function") onTransitionToReport();
  }

  const signature = runSignature(run);
  if (signature !== state.runSignature) {
    state.runSignature = signature;
    paintRun(run, status);
  }

  if (run.message && $("message").dataset.kind !== "error") {
    setMessage(run.message);
  }
}

function paintRun(run, status) {
  const safeStatus = safeClassToken(status);
  $("status-dot").className = `dot ${safeStatus}`;
  $("status-text").textContent = `${status}${run.message ? `: ${run.message}` : ""}`;
  $("box-status").textContent = status;
  $("box-run-type").textContent = runTypeLabels[run.run_type] || run.run_type || "-";

  const scenario = state.scenarios.find((item) => item.scenario_id === run.scenario_id);
  $("box-scenario").textContent = (scenario && scenario.display_name) || run.scenario_id || "-";
  $("box-returncode").textContent = fmt(run.returncode);
  $("stop").disabled = status !== "running";
  $("run").disabled = status === "running";
  $("new-run").disabled = status === "running";

  $("gui-status").textContent = run.gui
    ? "A SUMO GUI foi pedida para esta execução. A janela visual abre fora do browser e a dashboard mantém os dados de estado e reporte."
    : "A simulação corre em background. Ativa `Abrir sumo-gui` antes de correr para abrir a janela SUMO.";
  $("gui-pill").textContent = run.gui ? "sumo-gui" : "background";
  $("gui-pill").className = `pill ${run.gui ? "running" : "not_run"}`;
}

export function renderReports() {
  const selected = state.selectedReportId || (state.scenarios[0] && state.scenarios[0].scenario_id);
  if (selected && $("report-scenario").value !== selected) {
    $("report-scenario").value = selected;
  }
  const report = state.reports.find((item) => item.scenario_id === selected);
  const verdict = report && report.verdict ? report.verdict.status : "not_run";
  const rows = (report && report.comparison && report.comparison.rows) || [];

  const signature = JSON.stringify({ selected, verdict, count: rows.length, mtime: report && report.mtime });
  if (signature === state.reportSignature) return;
  state.reportSignature = signature;

  $("report-verdict").value = verdict;
  const cards = metricRowsForCards(rows);
  renderReportCards(cards);
  renderReportCharts(cards);
  renderReportRows(rows);
}

export function renderLogs(payload) {
  const selected = (payload && payload[state.activeLog]) || {};
  const label = selected.path ? `${selected.path}${selected.truncated ? " (tail)" : ""}` : "Sem ficheiro de log.";
  const content = selected.content && selected.content.trim() ? selected.content : label;

  const signature = `${state.activeLog}|${selected.path || ""}|${content.length}`;
  if (signature !== state.logSignature) {
    state.logSignature = signature;
    $("log-output").textContent = content;
  }

  for (const button of document.querySelectorAll("[data-log]")) {
    setActive(button, button.dataset.log === state.activeLog);
  }
}

function renderReportCards(cards) {
  if (!cards.length) {
    $("report-kpi-cards").replaceChildren(
      el("div", { className: "metric-card" }, [
        el("span", { textContent: "KPIs" }),
        el("strong", { textContent: "-" }),
        el("small", { textContent: "Sem relatório para este cenário." }),
      ]),
    );
    return;
  }

  $("report-kpi-cards").replaceChildren(...cards.map((row) => {
    const delta = row.delta_with_vs_baseline;
    return el("div", { className: "metric-card" }, [
      el("span", { textContent: row.metric }),
      el("strong", { textContent: fmt(row.with_algorithm, row.unit) }),
      append(el("small"), "TSP vs baseline: ", el("b", {
        className: deltaClass(row, delta),
        textContent: fmt(delta, row.unit),
      })),
      el("small", {
        textContent: `Baseline ${fmt(row.baseline, row.unit)} · Shadow ${fmt(row.without_algorithm, row.unit)}`,
      }),
    ]);
  }));
}

function renderReportCharts(cards) {
  $("report-charts").replaceChildren(...cards.map((row) => {
    const values = [row.baseline, row.without_algorithm, row.with_algorithm].map((v) => Number(v));
    const maxValue = Math.max(...values.filter(Number.isFinite).map(Math.abs), 1);
    return el("div", { className: "chart-card" }, [
      el("h3", { textContent: row.metric }),
      barRow("Baseline", values[0], maxValue),
      barRow("Shadow", values[1], maxValue),
      barRow("TSP ativo", values[2], maxValue),
    ]);
  }));
}

function renderReportRows(rows) {
  if (!rows.length) {
    $("report-rows").replaceChildren(
      el("tr", {}, [tableCell("td", "Sem relatório para este cenário.", { className: "muted", colSpan: 6 })]),
    );
    return;
  }

  $("report-rows").replaceChildren(...rows.map((row) => el("tr", {}, [
    tableCell("td", [
      row.metric,
      el("br"),
      el("span", { className: "muted mono", textContent: row.source }),
    ]),
    tableCell("td", fmt(row.baseline, row.unit)),
    tableCell("td", fmt(row.without_algorithm, row.unit)),
    tableCell("td", fmt(row.with_algorithm, row.unit)),
    tableCell("td", fmt(row.delta_with_vs_baseline, row.unit)),
    tableCell("td", fmt(row.delta_with_vs_without, row.unit)),
  ])));
}

function scenarioTableRow(item) {
  return el("tr", {}, [
    tableCell("td", [
      item.display_name || item.scenario_id,
      el("br"),
      el("span", { className: "muted mono", textContent: item.scenario_id }),
      el("br"),
      el("span", { className: "muted", textContent: item.description || "" }),
    ]),
    tableCell("td", item.realism_basis || "-"),
    tableCell("td", [
      `carros~${fmt(item.estimated_car_departures)}`,
      el("br"),
      `autocarros~${fmt(item.estimated_bus_departures)}`,
    ]),
    tableCell("td", Array.isArray(item.kpi_focus) ? item.kpi_focus.join(", ") : "-"),
    tableCell("td", pill(item.has_report ? "completed" : "not_run")),
  ]);
}

function pill(value) {
  const token = value || "unknown";
  return el("span", { className: `pill ${safeClassToken(token)}`, textContent: token });
}

function barRow(label, value, maxValue) {
  const width = barWidth(value, maxValue);
  return el("div", { className: "bar-row" }, [
    el("span", { textContent: label }),
    el("div", { className: "bar-track" }, [
      el("div", { className: "bar-fill", style: { width: `${width}%` } }),
    ]),
    el("strong", { textContent: fmt(value) }),
  ]);
}
