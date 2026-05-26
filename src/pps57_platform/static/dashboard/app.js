import { ApiError, api } from "./api.js";
import { $ } from "./dom.js";
import {
  applyStage,
  clearError,
  readSteps,
  renderLogs,
  renderModeNote,
  renderOverviewBars,
  renderReports,
  renderRun,
  renderScenarios,
  renderSelectedScenario,
  selectScenario,
  setActiveTab,
  setMessage,
  updateStepsHelp,
} from "./renderers.js";
import { POLL_INTERVALS, state } from "./state.js";
import { applySchedule, registerTask, stopSchedule } from "./scheduler.js";

async function refreshScenarios() {
  const payload = await api.scenarios();
  state.scenarios = payload.scenarios || [];
  renderScenarios();
}

async function refreshRun() {
  const previousStatus = state.run && state.run.status;
  const run = await api.currentRun();
  renderRun(run, () => void guardedRefresh("Erro ao atualizar relatórios", refreshReports));
  if (previousStatus !== state.run.status) syncPollMode();
}

async function refreshReports() {
  const payload = await api.reports();
  state.reports = payload.reports || [];
  renderReports();
}

async function refreshLogs() {
  renderLogs(await api.currentLogs());
}

async function guardedRefresh(label, task) {
  try {
    await task();
    clearError();
  } catch (err) {
    const message = err instanceof ApiError ? err.message : `${label}: ${err.message || err}`;
    setMessage(message, "error");
  }
}

async function startRun() {
  const steps = readSteps();
  if ($("steps").value.trim() && steps === null) {
    setMessage("Duração inválida: usa um inteiro >= 1.", "error");
    return;
  }
  if (!state.selectedScenarioId) {
    setMessage("Seleciona um cenário antes de correr.", "error");
    return;
  }

  const payload = {
    scenario_id: state.selectedScenarioId,
    run_type: state.selectedRunType,
    steps,
    gui: $("gui").checked,
  };

  try {
    const run = await api.startRun(payload);
    clearError();
    applyStage("running");
    setActiveTab("overview");
    renderRun(run);
    syncPollMode();
    await guardedRefresh("Erro ao atualizar logs", refreshLogs);
  } catch (err) {
    const message = err instanceof ApiError ? err.message : err.message || "Falha ao iniciar execução.";
    setMessage(message, "error");
  }
}

async function stopRun() {
  try {
    const run = await api.stopRun();
    clearError();
    renderRun(run);
    syncPollMode();
    await guardedRefresh("Erro ao atualizar logs", refreshLogs);
  } catch (err) {
    const message = err instanceof ApiError ? err.message : err.message || "Falha ao parar execução.";
    setMessage(message, "error");
  }
}

const ACTION_HANDLERS = {
  run: startRun,
  stop: stopRun,
  "new-run": () => {
    applyStage("setup");
    setActiveTab("overview");
  },
};

function bindEvents() {
  document.body.addEventListener("click", (event) => {
    const target = event.target.closest("[data-tab], [data-run-type], [data-log], [data-action]");
    if (!target) return;
    if (target.dataset.tab) {
      setActiveTab(target.dataset.tab);
    } else if (target.dataset.runType) {
      state.selectedRunType = target.dataset.runType;
      renderModeNote();
    } else if (target.dataset.log) {
      state.activeLog = target.dataset.log;
      void guardedRefresh("Erro ao atualizar logs", refreshLogs);
    } else if (target.dataset.action) {
      const handler = ACTION_HANDLERS[target.dataset.action];
      if (handler) void handler();
    }
  });

  $("scenario").addEventListener("change", (event) => {
    selectScenario(event.target.value);
  });
  $("steps").addEventListener("input", () => {
    updateStepsHelp();
    renderOverviewBars();
  });
  $("report-scenario").addEventListener("change", (event) => {
    state.selectedReportId = event.target.value || null;
    state.reportSignature = "";
    renderReports();
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    void guardedRefresh("Erro ao atualizar execução", refreshRun);
    void guardedRefresh("Erro ao atualizar logs", refreshLogs);
  });

  window.addEventListener("beforeunload", stopSchedule);
}

function syncPollMode() {
  const mode = state.run && state.run.status === "running" ? "running" : "idle";
  if (mode === state.pollMode) return;
  state.pollMode = mode;
  applySchedule(POLL_INTERVALS[mode]);
}

function registerPollers() {
  registerTask("run", () => guardedRefresh("Erro ao atualizar execução", refreshRun));
  registerTask("logs", () => guardedRefresh("Erro ao atualizar logs", refreshLogs));
  registerTask("reports", () => guardedRefresh("Erro ao atualizar relatórios", refreshReports));
}

async function boot() {
  try {
    bindEvents();
    renderModeNote();
    updateStepsHelp();
    registerPollers();

    const results = await Promise.allSettled([
      refreshScenarios(),
      refreshRun(),
      refreshReports(),
      refreshLogs(),
    ]);
    const failed = results.filter((r) => r.status === "rejected");
    if (failed.length) {
      const reason = failed[0].reason;
      const message = reason instanceof ApiError ? reason.message : reason && reason.message ? reason.message : String(reason);
      setMessage(`Aviso ao carregar dashboard: ${message}`, "error");
    }
    syncPollMode();
  } catch (err) {
    setMessage(`Erro ao carregar dashboard: ${err.message || err}`, "error");
  }
}

boot();
