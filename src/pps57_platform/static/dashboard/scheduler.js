const tasks = new Map();
const timers = new Map();

export function registerTask(key, runFn) {
  tasks.set(key, runFn);
}

export function applySchedule(intervalsByKey) {
  clearTimers();
  for (const [key, intervalMs] of Object.entries(intervalsByKey)) {
    const run = tasks.get(key);
    if (!run || !intervalMs) continue;
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      try {
        const result = run();
        if (result && typeof result.then === "function") result.catch(() => {});
      } catch {
        /* swallow — caller is expected to surface errors */
      }
    }, intervalMs);
    timers.set(key, timer);
  }
}

export function stopSchedule() {
  clearTimers();
}

function clearTimers() {
  for (const timer of timers.values()) window.clearInterval(timer);
  timers.clear();
}
