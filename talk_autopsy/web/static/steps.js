// The list of pipeline steps with their state. A progress bar and percentage appear only for a
// step that reports a real position (download bytes, correction and translation blocks, Gemini
// chunks, burn-in seconds); any other running step shows a spinner and its elapsed time.
// Nothing is estimated here.
import { t, clock } from "./i18n.js";

const ICONS = { waiting: "○", done: "✓", skipped: "✓", failed: "✕" };
let rows = new Map();

function cell(className, text = "") {
  const span = document.createElement("span");
  span.className = className;
  span.textContent = text;
  return span;
}

export function build(element, steps) {
  rows = new Map();
  element.replaceChildren();
  for (const step of steps) {
    const li = document.createElement("li");
    li.dataset.state = "waiting";
    const icon = cell("icon", ICONS.waiting);
    icon.setAttribute("aria-hidden", "true");
    const state = cell("state", t("state_waiting"));
    const detail = cell("detail");
    li.append(icon, cell("name", t(`step_${step}`)), state, detail);
    element.append(li);
    rows.set(step, { li, icon, state, detail, started: null, position: null });
  }
}

const megabytes = (bytes) => (bytes / 1e6).toFixed(1);

// "block 2/5", "12.3 / 40.0 MB", "1:05 / 6:34" (encoded / video length)
function amount(event) {
  if (event.unit === "byte") {
    return event.total > 0
      ? `${megabytes(event.position)} / ${megabytes(event.total)} ${t("unit_mb")}`
      : `${megabytes(event.position)} ${t("unit_mb")}`;
  }
  if (event.unit === "second") {
    return event.total > 0 ? `${clock(event.position)} / ${clock(event.total)}` : clock(event.position);
  }
  return `${t(`unit_${event.unit}`)} ${event.position}/${event.total}`;
}

function setState(r, state) {
  r.li.dataset.state = state;
  r.icon.replaceChildren();
  if (state === "running") {
    r.icon.append(cell("spinner"));
  } else {
    r.icon.textContent = ICONS[state] || ICONS.waiting;
  }
}

export function apply(event) {
  const r = rows.get(event.step);
  if (!r) return;
  if (event.type === "step_started") {
    if (r.started === null) r.started = event.t;
    setState(r, "running");
    if (r.position === null) r.state.textContent = t("state_running");
    if (event.detail && r.position === null) r.detail.textContent = event.detail;
  } else if (event.type === "step_progress") {
    r.position = event.position;
    if (r.li.dataset.state !== "running") setState(r, "running");
    const known = event.total > 0;
    if (known) {
      const percent = Math.round((100 * Math.min(event.position, event.total)) / event.total);
      const bar = document.createElement("progress");
      bar.max = event.total;
      bar.value = Math.min(event.position, event.total);
      bar.setAttribute("aria-label", `${t(`step_${event.step}`)} ${percent}%`);
      r.detail.replaceChildren(bar);
      r.state.textContent = `${percent}% (${amount(event)})`;
    } else {
      r.detail.replaceChildren();
      r.state.textContent = amount(event); // a download of unknown size: bytes so far, no bar
    }
  } else if (event.type === "step_finished") {
    setState(r, event.skipped ? "skipped" : "done");
    r.state.textContent = event.skipped ? t("state_skipped") : `${t("state_done")} · ${clock(event.seconds)}`;
    const parts = [];
    if (event.cost_usd) parts.push(`${event.cost_usd.toFixed(4)} USD`);
    if (event.stt_cost_usd) parts.push(`${event.stt_cost_usd.toFixed(4)} USD (${t("step_stt")})`);
    if (event.tokens) {
      parts.push(t("tokens", { input: event.tokens.input.toLocaleString(), output: event.tokens.output.toLocaleString() }));
    }
    r.detail.textContent = parts.join(" · ");
    r.started = null;
  }
}

export function fail(step) {
  const r = rows.get(step);
  if (!r) return;
  setState(r, "failed");
  r.state.textContent = t("state_failed");
}

// The elapsed time of running steps without a position; called once a second with the job's age.
export function tick(jobSeconds) {
  for (const r of rows.values()) {
    if (r.li.dataset.state === "running" && r.position === null && r.started !== null) {
      r.state.textContent = `${t("state_running")} · ${clock(jobSeconds - r.started)}`;
    }
  }
}
