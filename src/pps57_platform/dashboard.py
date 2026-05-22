#!/usr/bin/env python3
"""Embedded HTML dashboard for local scenario runs."""

DASHBOARD_HTML = r"""<!doctype html>
<html lang="pt">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PPS57 Scenario Dashboard</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #eef1f4;
      --surface: #ffffff;
      --surface-soft: #f7f9fb;
      --line: #d6dde6;
      --line-strong: #b9c5d2;
      --text: #141a22;
      --muted: #667085;
      --accent: #176b87;
      --accent-soft: #e7f4f7;
      --accent-strong: #0e5268;
      --amber: #b54708;
      --green: #067647;
      --red: #b42318;
      --blue: #315ea8;
      --shadow: 0 10px 30px rgba(22, 32, 43, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        linear-gradient(180deg, #f8fafc 0, #eef1f4 260px),
        var(--bg);
      color: var(--text);
      font: 14px/1.45 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    button, input, select { font: inherit; }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      padding: 16px 22px;
      border-bottom: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.94);
      backdrop-filter: blur(10px);
      position: sticky;
      top: 0;
      z-index: 5;
    }
    h1 {
      margin: 0;
      font-size: 19px;
      font-weight: 720;
      letter-spacing: 0;
    }
    .subhead {
      margin-top: 2px;
      color: var(--muted);
      font-size: 13px;
    }
    .status-line {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-width: 220px;
      justify-content: flex-end;
      color: var(--muted);
    }
    .dot {
      width: 9px;
      height: 9px;
      border-radius: 50%;
      background: var(--muted);
      flex: 0 0 auto;
    }
    .dot.running { background: var(--amber); }
    .dot.completed { background: var(--green); }
    .dot.failed { background: var(--red); }
    main {
      display: grid;
      grid-template-columns: minmax(320px, 430px) minmax(0, 1fr);
      gap: 18px;
      padding: 18px;
      max-width: 1680px;
      margin: 0 auto;
    }
    body.stage-setup main {
      grid-template-columns: minmax(320px, 520px);
      justify-content: center;
      max-width: 760px;
    }
    body.stage-running main,
    body.stage-report main {
      grid-template-columns: minmax(0, 1fr);
    }
    body.stage-setup #workspace-panel,
    body.stage-running #setup-panel,
    body.stage-report #setup-panel {
      display: none;
    }
    aside, section.panel {
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }
    aside {
      align-self: start;
      position: sticky;
      top: 74px;
      overflow: hidden;
    }
    .panel-title {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 10px;
      padding: 15px 16px;
      border-bottom: 1px solid var(--line);
      background: var(--surface-soft);
    }
    .panel-title h2 {
      margin: 0;
      font-size: 14px;
      font-weight: 760;
    }
    .panel-title p {
      margin: 3px 0 0;
      color: var(--muted);
      font-size: 12px;
    }
    .control-body { padding: 16px; }
    label {
      display: grid;
      gap: 6px;
      margin: 0 0 12px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }
    select, input {
      width: 100%;
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 10px;
      background: #fff;
      color: var(--text);
    }
    select:focus, input:focus, button:focus {
      outline: 2px solid rgba(23, 107, 135, 0.22);
      outline-offset: 1px;
    }
    .grid-2 {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
    }
    .mode-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin: 8px 0 14px;
    }
    .mode-button {
      min-height: 72px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: #fff;
      color: var(--text);
      cursor: pointer;
      text-align: left;
    }
    .mode-button strong {
      display: block;
      font-size: 13px;
      margin-bottom: 3px;
    }
    .mode-button span {
      display: block;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.3;
    }
    .mode-button.active {
      border-color: var(--accent);
      background: var(--accent-soft);
      box-shadow: inset 0 0 0 1px rgba(23, 107, 135, 0.18);
    }
    .note {
      border-left: 3px solid var(--accent);
      background: #f7fbfc;
      padding: 10px 11px;
      color: var(--muted);
      border-radius: 6px;
      margin: 0 0 14px;
    }
    .note strong {
      display: block;
      color: var(--text);
      margin-bottom: 2px;
    }
    .scenario-note {
      border-top: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
      padding: 13px 16px;
      background: #fbfcfe;
    }
    .scenario-note h3 {
      margin: 0 0 4px;
      font-size: 14px;
    }
    .scenario-note p {
      margin: 0;
      color: var(--muted);
    }
    .kpi-chips {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 10px;
    }
    .chip {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 3px 8px;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--muted);
      background: #fff;
      font-size: 12px;
      font-weight: 650;
    }
    .check-row {
      display: flex;
      align-items: center;
      gap: 9px;
      color: var(--text);
      font-size: 13px;
      font-weight: 650;
      text-transform: none;
      margin: 2px 0 0;
    }
    .check-row input {
      width: 18px;
      min-height: 18px;
      height: 18px;
      margin: 0;
    }
    .advanced {
      margin-top: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fff;
    }
    .advanced summary {
      cursor: pointer;
      padding: 10px 12px;
      color: var(--accent-strong);
      font-weight: 720;
    }
    .advanced-body {
      display: grid;
      gap: 10px;
      padding: 0 12px 12px;
    }
    .advanced-body label {
      margin-bottom: 0;
    }
    .field-help {
      margin: -6px 0 12px;
      color: var(--muted);
      font-size: 12px;
    }
    .actions {
      display: flex;
      gap: 9px;
      margin-top: 15px;
    }
    button {
      min-height: 40px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 13px;
      background: #fff;
      color: var(--text);
      font-weight: 720;
      cursor: pointer;
    }
    button.primary {
      flex: 1;
      border-color: var(--accent);
      background: var(--accent);
      color: #fff;
    }
    button.primary:hover { background: var(--accent-strong); }
    button.danger {
      color: var(--red);
      border-color: #fecdca;
      background: #fff;
    }
    button:disabled {
      opacity: 0.55;
      cursor: not-allowed;
    }
    .message {
      margin-top: 12px;
      min-height: 42px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--surface-soft);
      color: var(--muted);
    }
    .tabs {
      display: flex;
      gap: 6px;
      padding: 10px;
      border-bottom: 1px solid var(--line);
      background: var(--surface-soft);
    }
    .tab {
      min-height: 36px;
      border: 1px solid transparent;
      background: transparent;
      color: var(--muted);
      border-radius: 6px;
      padding: 8px 11px;
    }
    .tab.active {
      border-color: var(--line-strong);
      background: #fff;
      color: var(--accent-strong);
    }
    .workspace-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      background: var(--surface-soft);
    }
    .workspace-head h2 {
      margin: 0;
      font-size: 16px;
      font-weight: 760;
    }
    .workspace-head p {
      margin: 3px 0 0;
      color: var(--muted);
    }
    .secondary {
      background: #fff;
      color: var(--accent-strong);
      border-color: var(--line-strong);
    }
    .execution-visual {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fbfcfe;
      padding: 13px;
      margin-bottom: 14px;
    }
    .execution-visual h3 {
      margin: 0;
      font-size: 14px;
    }
    .execution-visual p {
      margin: 3px 0 0;
      color: var(--muted);
    }
    .tab-view { display: none; padding: 16px; }
    .tab-view.active { display: block; }
    .summary-grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(150px, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }
    .metric-box {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fff;
      min-height: 78px;
    }
    .metric-box span {
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }
    .metric-box strong {
      display: block;
      margin-top: 5px;
      font-size: 19px;
      line-height: 1.2;
      overflow-wrap: anywhere;
    }
    .report-toolbar {
      display: grid;
      grid-template-columns: minmax(240px, 1fr) 180px;
      gap: 10px;
      margin-bottom: 12px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      table-layout: fixed;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }
    th, td {
      padding: 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      word-break: break-word;
    }
    th {
      background: #f7f9fb;
      color: var(--muted);
      font-size: 11px;
      font-weight: 780;
      text-transform: uppercase;
    }
    tr:last-child td { border-bottom: 0; }
    .mono {
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
    }
    .muted { color: var(--muted); }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 3px 8px;
      border-radius: 999px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--muted);
      font-size: 12px;
      font-weight: 720;
    }
    .pill.pass, .pill.completed { color: var(--green); border-color: #abefc6; background: #ecfdf3; }
    .pill.fail, .pill.failed { color: var(--red); border-color: #fecdca; background: #fef3f2; }
    .pill.inconclusive, .pill.running { color: var(--amber); border-color: #fedf89; background: #fffaeb; }
    .pill.not_run { color: var(--blue); border-color: #c7d7fe; background: #eef4ff; }
    @media (max-width: 980px) {
      main { grid-template-columns: 1fr; }
      aside { position: static; }
      .summary-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 620px) {
      header { align-items: flex-start; flex-direction: column; }
      .status-line { justify-content: flex-start; }
      .grid-2, .mode-grid, .report-toolbar, .summary-grid { grid-template-columns: 1fr; }
      .actions { flex-direction: column; }
    }
  </style>
</head>
<body class="stage-setup">
  <header>
    <div>
      <h1>PPS57 Scenario Dashboard</h1>
      <div class="subhead">Execução de cenários SUMO e comparação KPI baseline / shadow / TSP ativo</div>
    </div>
    <div class="status-line"><span id="status-dot" class="dot"></span><span id="status-text">A ligar...</span></div>
  </header>

  <main id="app-main">
    <aside id="setup-panel">
      <div class="panel-title">
        <div>
          <h2>Nova execução</h2>
          <p>Escolhe o cenário, o modo e os parâmetros de simulação.</p>
        </div>
      </div>

      <div class="control-body">
        <label>
          Cenário
          <select id="scenario"></select>
        </label>
      </div>

      <div class="scenario-note">
        <h3 id="scenario-title">-</h3>
        <p id="scenario-description">Seleciona um cenário.</p>
        <div id="scenario-kpis" class="kpi-chips"></div>
      </div>

      <div class="control-body">
        <label>Modo de execução</label>
        <div class="mode-grid">
          <button class="mode-button active" type="button" data-run-type="comparison">
            <strong>Comparação KPI</strong>
            <span>Baseline, shadow mode e TSP ativo.</span>
          </button>
          <button class="mode-button" type="button" data-run-type="baseline">
            <strong>Baseline SUMO</strong>
            <span>Tráfego sem C-ITS e sem algoritmo.</span>
          </button>
          <button class="mode-button" type="button" data-run-type="tsp_no_actuation">
            <strong>Shadow mode</strong>
            <span>Algoritmo decide, mas não atua.</span>
          </button>
          <button class="mode-button" type="button" data-run-type="tsp_actuation">
            <strong>TSP ativo</strong>
            <span>Algoritmo decide e controla os semáforos.</span>
          </button>
        </div>
        <div id="mode-note" class="note"></div>

        <label>
          Duração da simulação
          <input id="steps" inputmode="numeric" placeholder="7200 passos">
        </label>
        <div id="steps-help" class="field-help">1 passo = 1 segundo. 7200 passos = 2 h.</div>

        <label class="check-row">
          <input id="gui" type="checkbox">
          <span>Abrir sumo-gui</span>
        </label>

        <details class="advanced">
          <summary>Opções avançadas</summary>
          <div class="advanced-body">
            <label class="check-row">
              <input id="all-scenarios" type="checkbox">
              <span>Correr todos os cenários</span>
            </label>
            <div class="grid-2">
              <label>
                TraCI port
                <input id="traci-port" inputmode="numeric" placeholder="auto">
              </label>
              <label>
                SUMO binary
                <input id="sumo-binary" value="sumo">
              </label>
            </div>
          </div>
        </details>

        <div class="actions">
          <button id="run" class="primary">Correr cenário</button>
          <button id="stop" class="danger">Parar</button>
        </div>
        <div id="message" class="message">Sem execução ativa.</div>
      </div>
    </aside>

    <section id="workspace-panel" class="panel">
      <div class="workspace-head">
        <div>
          <h2 id="stage-title">Execução em curso</h2>
          <p id="stage-subtitle">A acompanhar a simulação e os artefactos gerados.</p>
        </div>
        <button id="new-run" class="secondary" type="button">Nova execução</button>
      </div>
      <div class="tabs">
        <button class="tab active" data-tab="overview">Execução</button>
        <button class="tab" data-tab="report">Reporte</button>
        <button class="tab" data-tab="scenarios">Cenários</button>
      </div>

      <div id="overview" class="tab-view active">
        <div class="execution-visual">
          <div>
            <h3>Visualização SUMO</h3>
            <p id="gui-status">A simulação corre em background. Ativa `Abrir sumo-gui` antes de correr para abrir a janela SUMO.</p>
          </div>
          <span id="gui-pill" class="pill not_run">background</span>
        </div>
        <div class="summary-grid">
          <div class="metric-box"><span>Estado</span><strong id="box-status">idle</strong></div>
          <div class="metric-box"><span>Modo</span><strong id="box-run-type">-</strong></div>
          <div class="metric-box"><span>Cenário</span><strong id="box-scenario">-</strong></div>
          <div class="metric-box"><span>Return code</span><strong id="box-returncode">-</strong></div>
        </div>
        <table>
          <tbody id="run-details"></tbody>
        </table>
      </div>

      <div id="report" class="tab-view">
        <div class="report-toolbar">
          <label>
            Relatório
            <select id="report-scenario"></select>
          </label>
          <label>
            Veredito
            <input id="report-verdict" readonly>
          </label>
        </div>
        <table>
          <thead>
            <tr>
              <th>KPI</th>
              <th>Baseline SUMO</th>
              <th>Shadow mode</th>
              <th>TSP ativo</th>
              <th>Delta TSP vs baseline</th>
              <th>Delta TSP vs shadow</th>
            </tr>
          </thead>
          <tbody id="report-rows"></tbody>
        </table>
      </div>

      <div id="scenarios" class="tab-view">
        <table>
          <thead>
            <tr>
              <th>Cenário</th>
              <th>Base realista</th>
              <th>Procura</th>
              <th>KPIs</th>
              <th>Reporte</th>
            </tr>
          </thead>
          <tbody id="scenario-rows"></tbody>
        </table>
      </div>
    </section>
  </main>

  <script>
    const state = { scenarios: [], reports: [], selectedRunType: "comparison", stage: "setup" };
    const $ = (id) => document.getElementById(id);
    const runTypeLabels = {
      comparison: "Comparação KPI",
      all: "Todos os modos",
      baseline: "Baseline SUMO",
      tsp_no_actuation: "Shadow mode",
      tsp_actuation: "TSP ativo",
      cits: "Diagnóstico C-ITS",
    };
    const modeNotes = {
      comparison: {
        title: "Comparação KPI",
        body: "Corre a baseline, o shadow mode e o TSP ativo para comparar o impacto do algoritmo com a mesma base de procura.",
      },
      baseline: {
        title: "Baseline SUMO",
        body: "É a referência pura: só tráfego SUMO, sem C-ITS, sem algoritmo TSP e sem alterações aos semáforos.",
      },
      tsp_no_actuation: {
        title: "Shadow mode",
        body: "C-ITS e TSP correm em observação. O sistema gera pedidos, decisões e logs, mas não altera os semáforos.",
      },
      tsp_actuation: {
        title: "TSP ativo",
        body: "C-ITS, TSP e Safety Layer correm com atuação TraCI. As decisões aprovadas podem alterar os semáforos.",
      },
      all: {
        title: "Todos os modos",
        body: "Executa baseline, C-ITS, shadow mode e TSP ativo. Usa isto quando também precisares de evidência técnica C-ITS.",
      },
      cits: {
        title: "Diagnóstico C-ITS",
        body: "Valida mensagens MAPEM, SPATEM, SREM e SSEM. Não é o modo principal para medir impacto de tráfego.",
      },
    };

    function fmt(value, unit = "") {
      if (value === null || value === undefined || value === "") return "-";
      if (typeof value === "number") return `${Number.isInteger(value) ? value : value.toFixed(3)}${unit ? " " + unit : ""}`;
      return String(value);
    }

    function formatDuration(seconds) {
      if (!Number.isFinite(seconds) || seconds <= 0) return "1 passo = 1 segundo.";
      const rounded = Math.round(seconds);
      const hours = Math.floor(rounded / 3600);
      const minutes = Math.floor((rounded % 3600) / 60);
      const secs = rounded % 60;
      const parts = [];
      if (hours) parts.push(`${hours} h`);
      if (minutes) parts.push(`${minutes} min`);
      if (secs || parts.length === 0) parts.push(`${secs} s`);
      return `${rounded} passos = ${parts.join(" ")} de simulação.`;
    }

    function updateStepsHelp() {
      const raw = $("steps").value.trim();
      const steps = raw ? Number(raw) : 7200;
      $("steps-help").textContent = `1 passo = 1 segundo. ${formatDuration(steps)}`;
    }

    function statusPill(value) {
      const text = value || "unknown";
      return `<span class="pill ${text}">${text}</span>`;
    }

    function setMessage(text) {
      $("message").textContent = text || "";
    }

    function setActiveTab(tabId) {
      document.querySelectorAll(".tab").forEach((item) => {
        item.classList.toggle("active", item.dataset.tab === tabId);
      });
      document.querySelectorAll(".tab-view").forEach((item) => {
        item.classList.toggle("active", item.id === tabId);
      });
    }

    function applyStage(stage) {
      state.stage = stage;
      document.body.classList.remove("stage-setup", "stage-running", "stage-report");
      document.body.classList.add(`stage-${stage}`);
      if (stage === "setup") {
        $("stage-title").textContent = "Execução";
        $("stage-subtitle").textContent = "Configura e lança um cenário.";
      } else if (stage === "running") {
        $("stage-title").textContent = "Simulação em curso";
        $("stage-subtitle").textContent = "A acompanhar SUMO, TraCI e artefactos de execução.";
      } else {
        $("stage-title").textContent = "Relatório da simulação";
        $("stage-subtitle").textContent = "Comparação de KPIs para a execução concluída.";
      }
    }

    function selectedScenario() {
      return state.scenarios.find((item) => item.scenario_id === $("scenario").value);
    }

    function renderSelectedScenario() {
      const scenario = selectedScenario();
      $("scenario-title").textContent = scenario ? scenario.display_name || scenario.scenario_id : "-";
      $("scenario-description").textContent = scenario ? scenario.description || "" : "Seleciona um cenário.";
      const chips = scenario && Array.isArray(scenario.kpi_focus) ? scenario.kpi_focus : [];
      $("scenario-kpis").innerHTML = chips.map((item) => `<span class="chip">${item}</span>`).join("");
    }

    function renderModeNote() {
      const note = modeNotes[state.selectedRunType] || modeNotes.comparison;
      $("mode-note").innerHTML = `<strong>${note.title}</strong>${note.body}`;
      document.querySelectorAll(".mode-button").forEach((button) => {
        button.classList.toggle("active", button.dataset.runType === state.selectedRunType);
      });
    }

    function renderScenarios() {
      const scenarioSelect = $("scenario");
      const reportSelect = $("report-scenario");
      scenarioSelect.innerHTML = "";
      reportSelect.innerHTML = "";
      for (const scenario of state.scenarios) {
        const label = `${scenario.display_name || scenario.scenario_id} (${scenario.scenario_id})`;
        scenarioSelect.add(new Option(label, scenario.scenario_id));
        reportSelect.add(new Option(label, scenario.scenario_id));
      }
      renderSelectedScenario();
      $("scenario-rows").innerHTML = state.scenarios.map((item) => `
        <tr>
          <td>${item.display_name || item.scenario_id}<br><span class="muted mono">${item.scenario_id}</span><br><span class="muted">${item.description || ""}</span></td>
          <td>${item.realism_basis || "-"}</td>
          <td>carros~${fmt(item.estimated_car_departures)}<br>autocarros~${fmt(item.estimated_bus_departures)}</td>
          <td>${Array.isArray(item.kpi_focus) ? item.kpi_focus.join(", ") : "-"}</td>
          <td>${item.has_report ? statusPill("completed") : statusPill("not_run")}</td>
        </tr>
      `).join("");
    }

    function renderRun(run) {
      const status = run.status || "idle";
      if (status === "running") {
        applyStage("running");
        setActiveTab("overview");
      } else if (["completed", "failed"].includes(status) && run.run_id && state.stage === "running") {
        applyStage("report");
        setActiveTab("report");
        if (run.scenario_id) {
          $("report-scenario").value = run.scenario_id;
        }
        refreshReports();
      }
      $("status-dot").className = `dot ${status}`;
      $("status-text").textContent = `${status}: ${run.message || ""}`;
      $("box-status").textContent = status;
      $("box-run-type").textContent = runTypeLabels[run.run_type] || run.run_type || "-";
      const scenario = state.scenarios.find((item) => item.scenario_id === run.scenario_id);
      $("box-scenario").textContent = run.all_scenarios ? "todos" : ((scenario && scenario.display_name) || run.scenario_id || "-");
      $("box-returncode").textContent = fmt(run.returncode);
      $("stop").disabled = status !== "running";
      $("run").disabled = status === "running";
      $("new-run").disabled = status === "running";
      $("gui-status").textContent = run.gui
        ? "A SUMO GUI foi pedida para esta execução. A janela visual abre fora do browser e a dashboard mantém os dados de estado e reporte."
        : "A simulação corre em background. Ativa `Abrir sumo-gui` antes de correr para abrir a janela SUMO.";
      $("gui-pill").textContent = run.gui ? "sumo-gui" : "background";
      $("gui-pill").className = `pill ${run.gui ? "running" : "not_run"}`;
      $("run-details").innerHTML = [
        ["Comando", Array.isArray(run.command) ? run.command.join(" ") : ""],
        ["Início", run.started_at],
        ["Fim", run.ended_at],
        ["stdout", run.stdout_log],
        ["stderr", run.stderr_log],
      ].map(([key, value]) => `<tr><th>${key}</th><td class="mono">${fmt(value)}</td></tr>`).join("");
      setMessage(run.message || "");
    }

    function renderReports() {
      const selected = $("report-scenario").value || (state.scenarios[0] && state.scenarios[0].scenario_id);
      const report = state.reports.find((item) => item.scenario_id === selected);
      if (!report) return;
      $("report-verdict").value = report.verdict ? report.verdict.status : "not_run";
      const rows = (report.comparison && report.comparison.rows) || [];
      $("report-rows").innerHTML = rows.map((row) => `
        <tr>
          <td>${row.metric}<br><span class="muted mono">${row.source}</span></td>
          <td>${fmt(row.baseline, row.unit)}</td>
          <td>${fmt(row.without_algorithm, row.unit)}</td>
          <td>${fmt(row.with_algorithm, row.unit)}</td>
          <td>${fmt(row.delta_with_vs_baseline, row.unit)}</td>
          <td>${fmt(row.delta_with_vs_without, row.unit)}</td>
        </tr>
      `).join("") || `<tr><td colspan="6" class="muted">Sem relatório para este cenário.</td></tr>`;
    }

    async function refreshScenarios() {
      const response = await fetch("/api/scenarios");
      const payload = await response.json();
      state.scenarios = payload.scenarios || [];
      renderScenarios();
    }

    async function refreshRun() {
      const response = await fetch("/api/runs/current");
      renderRun(await response.json());
    }

    async function refreshReports() {
      const response = await fetch("/api/reports");
      const payload = await response.json();
      state.reports = payload.reports || [];
      renderReports();
    }

    async function startRun() {
      const steps = $("steps").value.trim();
      const traciPort = $("traci-port").value.trim();
      const payload = {
        scenario_id: $("scenario").value,
        all_scenarios: $("all-scenarios").checked,
        run_type: state.selectedRunType,
        steps: steps ? Number(steps) : null,
        traci_port: traciPort ? Number(traciPort) : null,
        sumo_binary: $("sumo-binary").value.trim() || "sumo",
        gui: $("gui").checked,
      };
      const response = await fetch("/api/runs/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        setMessage(err.detail || "Falha ao iniciar execução.");
        return;
      }
      const run = await response.json();
      applyStage("running");
      setActiveTab("overview");
      renderRun(run);
    }

    async function stopRun() {
      const response = await fetch("/api/runs/stop", { method: "POST" });
      renderRun(await response.json());
    }

    document.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        setActiveTab(tab.dataset.tab);
      });
    });
    document.querySelectorAll(".mode-button").forEach((button) => {
      button.addEventListener("click", () => {
        state.selectedRunType = button.dataset.runType;
        renderModeNote();
      });
    });
    $("scenario").addEventListener("change", renderSelectedScenario);
    $("steps").addEventListener("input", updateStepsHelp);
    $("run").addEventListener("click", startRun);
    $("stop").addEventListener("click", stopRun);
    $("new-run").addEventListener("click", () => {
      applyStage("setup");
      setActiveTab("overview");
    });
    $("report-scenario").addEventListener("change", renderReports);

    async function boot() {
      try {
        renderModeNote();
        updateStepsHelp();
        await refreshScenarios();
        await refreshRun();
        await refreshReports();
        setInterval(refreshRun, 2000);
        setInterval(refreshReports, 5000);
      } catch (err) {
        setMessage(`Erro ao carregar dashboard: ${err}`);
      }
    }
    boot();
  </script>
</body>
</html>
"""
