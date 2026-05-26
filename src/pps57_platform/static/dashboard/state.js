export const TABS = ["overview", "report", "scenarios"];
export const DEFAULT_STEPS = 7200;

export const state = {
  scenarios: [],
  reports: [],
  selectedRunType: "comparison",
  selectedScenarioId: null,
  selectedReportId: null,
  activeTab: "overview",
  stage: "setup",
  activeLog: "stdout",
  run: { status: "idle" },
  pollMode: null,
  runSignature: "",
  logSignature: "",
  reportSignature: "",
};

export const runTypeLabels = {
  comparison: "Comparação KPI",
  all: "Todos os modos",
  baseline: "Baseline SUMO",
  tsp_no_actuation: "Shadow mode",
  tsp_actuation: "TSP ativo",
  cits: "Diagnóstico C-ITS",
};

export const modeNotes = {
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

export const STAGE_INFO = {
  setup: { title: "Execução", subtitle: "Configura e lança um cenário." },
  running: { title: "Simulação em curso", subtitle: "A acompanhar SUMO, TraCI e artefactos de execução." },
  report: { title: "Relatório da simulação", subtitle: "Comparação de KPIs para a execução concluída." },
};

export const POLL_INTERVALS = {
  running: { run: 2000, logs: 3000, reports: 5000 },
  idle: { run: 8000, logs: 10000, reports: 20000 },
};
