// The list of pipeline steps with their state. A progress bar and percentage appear only for a
// step that reports a real position (correction and translation blocks, Gemini chunks); any
// other running step shows a spinner and its elapsed time. Nothing is estimated here.
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
    const percent = Math.round((100 * event.position) / Math.max(event.total, 1));
    const bar = document.createElement("progress");
    bar.max = event.total;
    bar.value = event.position;
    bar.setAttribute("aria-label", `${t(`step_${event.step}`)} ${percent}%`);
    r.detail.replaceChildren(bar);
    r.state.textContent = `${percent}% (${t(`unit_${event.unit}`)} ${event.position}/${event.total})`;
  } else if (event.type === "step_finished") {
    setState(r, event.skipped ? "skipped" : "done");
    r.state.textContent = event.skipped ? t("state_skipped") : `${t("state_done")} · ${clock(event.seconds)}`;
    const parts = [];
    if (event.cost_usd) parts.push(`${event.cost_usd.toFixed(4)} USD`);
    if (event.stt_cost_usd) parts.push(`${event.stt_cost_usd.toFixed(4)} USD (${t("step_stt")})`);
    if (event.tokens) parts.push(`${event.tokens.input.toLocaleString()} / ${event.tokens.output.toLocaleString()} tokens`);
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
