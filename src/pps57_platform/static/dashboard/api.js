export class ApiError extends Error {
  constructor(message, status = 0, payload = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

async function jsonRequest(path, options = {}) {
  let response;
  try {
    response = await fetch(path, options);
  } catch (err) {
    throw new ApiError(`Sem ligação a ${path}: ${err.message || err}`, 0);
  }

  let payload = null;
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    const detail = (payload && (payload.detail || payload.message)) || `${response.status} ${response.statusText}`;
    throw new ApiError(`${path}: ${detail}`, response.status, payload);
  }

  return payload ?? {};
}

export const api = {
  scenarios: () => jsonRequest("/api/scenarios"),
  currentRun: () => jsonRequest("/api/runs/current"),
  reports: () => jsonRequest("/api/reports"),
  currentLogs: () => jsonRequest("/api/runs/current/logs"),
  startRun: (payload) => jsonRequest("/api/runs/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }),
  stopRun: () => jsonRequest("/api/runs/stop", { method: "POST" }),
};
