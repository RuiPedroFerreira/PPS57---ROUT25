export function fmt(value, unit = "") {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return "-";
    const rendered = Number.isInteger(value) ? value : value.toFixed(3);
    return unit ? `${rendered} ${unit}` : `${rendered}`;
  }
  return String(value);
}

export function formatDuration(seconds) {
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

export function deltaClass(row, value) {
  if (!Number.isFinite(value) || value === 0) return "delta-neutral";
  const isGood = row.lower_is_better ? value < 0 : value > 0;
  return isGood ? "delta-good" : "delta-bad";
}

const CARD_PREFERRED_METRICS = [
  "buses.mean_time_loss_s",
  "buses.mean_waiting_time_s",
  "general_traffic.mean_time_loss_s",
  "detectors.network_queue.max_queue_vehicles",
];

export function metricRowsForCards(rows) {
  if (!Array.isArray(rows) || !rows.length) return [];
  const index = new Map(rows.map((row) => [row.source, row]));
  const cards = [];
  for (const source of CARD_PREFERRED_METRICS) {
    const row = index.get(source);
    if (row) cards.push(row);
  }
  return cards;
}

export function barWidth(value, maxValue) {
  if (!Number.isFinite(value) || !Number.isFinite(maxValue) || maxValue <= 0) return 0;
  return Math.max(3, Math.min(100, Math.round((Math.abs(value) / maxValue) * 100)));
}
