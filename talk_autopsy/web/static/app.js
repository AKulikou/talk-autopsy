// Talk Autopsy page: four states on one page (form, estimate, running, done / failed).
// The server enforces every rule (cost gate, one job at a time, paths); the page only asks.
import { api } from "./api.js";
import { applyStrings, clock, duration, errorText, setStrings, t, usd } from "./i18n.js";
import { followJob } from "./events.js";
import * as steps from "./steps.js";
import * as textlog from "./textlog.js";

const $ = (id) => document.getElementById(id);
const VIEWS = ["view-form", "view-estimate", "view-run", "view-done", "view-failed"];
const FORMAT_PURPOSE = { srt: "purpose_subtitles", ass: "purpose_subtitles_styled", bilingual: "purpose_bilingual",
  "mp4-soft": "purpose_video_tracks", "mp4-burned": "purpose_tv" };
const DOCTOR_HINTS = new Set(["doctor", "youtube", "outputs"]);

const state = {
  config: null,
  mode: "url",
  file: "", // the path chosen in the dialog
  manual: false, // the dialog is unavailable: the path field is shown
  request: null, // the request the estimate was made for
  preview: null,
  job: null,
  startedAt: null, // Date of the job start (for the running clock)
  stop: null,
  timer: null,
  doneFolder: "",
};

// --- helpers --------------------------------------------------------------------------------

function show(id) {
  for (const view of VIEWS) $(view).hidden = view !== id;
  const heading = $(id).querySelector("h2");
  if (heading) {
    heading.tabIndex = -1;
    heading.focus({ preventScroll: false });
  }
}

function setError(id, text) {
  const el = $(id);
  el.textContent = text || "";
  el.hidden = !text;
}

function busy(button, on, labelKey) {
  button.disabled = on;
  if (labelKey) button.textContent = t(on ? labelKey : button.dataset.i18n);
}

function option(value, text) {
  const o = document.createElement("option");
  o.value = value;
  o.textContent = text;
  return o;
}

// --- configuration and languages -------------------------------------------------------------

async function loadConfig(ui) {
  const { ok, data } = await api("GET", `/api/config${ui ? `?ui=${encodeURIComponent(ui)}` : ""}`);
  if (!ok) {
    setError("global-error", t("err_network"));
    return false;
  }
  state.config = data;
  setStrings(data.strings);
  document.documentElement.lang = data.ui_lang;
  applyStrings();
  const uiSelect = $("ui-lang");
  uiSelect.replaceChildren(...data.languages.ui.map((l) => option(l.code, l.name)));
  uiSelect.value = data.ui_lang;
  const target = $("target-lang");
  const chosen = target.value || data.languages.target_default;
  target.replaceChildren(...data.languages.target.map((l) => option(l.code, l.name)));
  target.value = chosen;
  updateLangNote();
  renderFile();
  return true;
}

function updateLangNote() {
  const lang = state.config?.languages.target.find((l) => l.code === $("target-lang").value);
  $("lang-note").hidden = !lang?.quality_not_measured;
}

// --- form: the two input modes ----------------------------------------------------------------

function setMode(mode, focus = false) {
  state.mode = mode;
  for (const tab of document.querySelectorAll('[role="tab"]')) {
    const selected = tab.dataset.mode === mode;
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
    if (selected && focus) tab.focus();
  }
  $("panel-url").hidden = mode !== "url";
  $("panel-file").hidden = mode !== "file";
  setError("form-error", "");
}

function renderFile() {
  const path = state.file;
  $("file-none").hidden = Boolean(path);
  $("file-name-row").hidden = !path;
  $("file-folder-row").hidden = !path;
  if (path) {
    const cut = Math.max(path.lastIndexOf("\\"), path.lastIndexOf("/"));
    $("file-name").textContent = path.slice(cut + 1);
    $("file-folder").textContent = path.slice(0, cut + 1);
  }
  $("manual").hidden = !state.manual;
}

async function browse() {
  const button = $("browse");
  button.disabled = true;
  $("file-picking").hidden = false;
  const { ok, data } = await api("POST", "/api/pick-file", { ui_lang: state.config?.ui_lang });
  $("file-picking").hidden = true;
  button.disabled = false;
  if (!ok) {
    setError("form-error", errorText(data));
    return;
  }
  if (!data.available) {
    state.manual = true;
    renderFile();
    $("path").focus();
    return;
  }
  if (data.path) {
    state.file = data.path;
    $("path").value = data.path;
    setError("form-error", "");
  }
  renderFile();
}

function currentRequest() {
  const request = {
    mode: state.mode,
    profile: document.querySelector('input[name="profile"]:checked').value,
    domain: $("web3").checked ? "web3" : "none",
    target_lang: $("target-lang").value,
  };
  // only the active mode's value is sent
  if (state.mode === "url") request.url = $("url").value.trim();
  else request.path = (state.manual ? $("path").value : state.file).trim();
  return request;
}

async function estimate(event) {
  event.preventDefault();
  setError("form-error", "");
  const button = $("estimate");
  busy(button, true, "estimating");
  const request = currentRequest();
  const { ok, data } = await api("POST", "/api/estimate", request);
  busy(button, false, "estimating");
  if (!ok) {
    setError("form-error", errorText(data));
    return;
  }
  state.request = request;
  state.preview = data;
  renderEstimate(data);
  show("view-estimate");
}

// --- estimate ---------------------------------------------------------------------------------

function timeText(time) {
  if (!time || time.seconds === null) return t("est_time_none");
  let text = `≈ ${duration(time.seconds)}`;
  if (time.first_run_rough) text += ` (${t("est_time_rough")})`;
  if (time.excludes?.length) {
    const names = [...new Set(time.excludes.map((s) => t(`step_${s === "outputs_burn" ? "outputs" : s}`)))];
    text += `; ${t("est_time_excludes", { steps: names.join(", ") })}`;
  }
  return text;
}

function renderEstimate(p) {
  $("est-title").textContent = p.title;
  $("est-duration").textContent = p.duration_s ? clock(p.duration_s) : "—";
  $("est-cost").textContent = p.cost
    ? `${usd(p.cost.total_usd)} (${t("est_cost_detail", { claude: p.cost.claude_usd.toFixed(2), stt: p.cost.stt_usd.toFixed(2) })})`
    : t("est_cost_none");
  $("est-time").textContent = timeText(p.time);
  $("est-formats").replaceChildren(...p.formats.map((f) => {
    const li = document.createElement("li");
    li.textContent = t(FORMAT_PURPOSE[f] || f);
    return li;
  }));
  const notes = [];
  if (p.notes.includes("audio_only")) notes.push(t("est_audio_only"));
  if (p.notes.includes("quality_not_measured")) notes.push(t("lang_not_measured"));
  if (p.busy) notes.push(t("est_busy"));
  $("est-notes").replaceChildren(...notes.map((n) => {
    const li = document.createElement("li");
    li.textContent = n;
    return li;
  }));
  const limit = p.limit_usd.toFixed(2);
  $("confirm").checked = false;
  $("confirm-box").hidden = !p.needs_confirmation;
  if (p.needs_confirmation) {
    $("confirm-text").textContent = p.cost ? t("est_over_limit", { limit }) : t("est_cost_none");
    $("confirm-label").textContent = p.cost
      ? t("est_confirm", { amount: p.cost.total_usd.toFixed(2) })
      : t("est_confirm_none", { limit });
  }
  setError("est-error", "");
  updateStart();
}

function updateStart() {
  const needs = state.preview?.needs_confirmation;
  $("start").disabled = Boolean(needs) && !$("confirm").checked;
}

async function start() {
  const p = state.preview;
  const request = { ...state.request };
  if (p.needs_confirmation && $("confirm").checked) {
    if (p.cost) request.confirmed_cost_usd = p.cost.total_usd;
    else request.confirmed_no_estimate = true;
  }
  const button = $("start");
  button.disabled = true;
  const { ok, data } = await api("POST", "/api/jobs", request);
  button.disabled = false;
  if (!ok) {
    setError("est-error", errorText(data));
    return;
  }
  api("POST", "/api/settings", { target_lang: request.target_lang }); // remembered for next time
  attach(data.job);
}

// --- running ------------------------------------------------------------------------------------

function attach(job) {
  state.job = job;
  state.startedAt = new Date(job.started);
  $("run-title").textContent = job.preview?.title || job.request.source;
  $("run-cost").textContent = usd(job.cost_usd + job.stt_cost_usd);
  $("run-left").textContent = timeText(job.preview?.time).split(";")[0];
  steps.build($("steps"), job.steps);
  textlog.reset($("log"));
  show("view-run");
  clearInterval(state.timer);
  state.timer = setInterval(tick, 1000);
  tick();
  state.stop?.();
  state.stop = followJob(job.id, 0, onEvent, onEnd, (connected) => {
    setError("global-error", connected ? "" : t("err_network"));
  });
}

function jobSeconds() {
  return state.startedAt ? (Date.now() - state.startedAt.getTime()) / 1000 : 0;
}

function tick() {
  const seconds = jobSeconds();
  $("run-elapsed").textContent = clock(seconds);
  steps.tick(seconds);
}

function onEvent(event) {
  switch (event.type) {
    case "step_started":
    case "step_progress":
    case "step_finished":
      steps.apply(event);
      if (event.type === "step_finished") {
        $("run-cost").textContent = usd((event.total_cost_usd || 0) + (event.total_stt_cost_usd || 0));
      }
      break;
    case "estimate_updated":
      if (event.time) {
        $("run-left").textContent = event.time.seconds === null ? t("run_left_none")
          : `≈ ${duration(event.time.seconds)}`;
      }
      break;
    case "text":
    case "notice":
      textlog.add(event);
      break;
    default:
      break;
  }
}

function onEnd(event) {
  clearInterval(state.timer);
  state.stop = null;
  if (event.type === "finished") showDone(event);
  else showFailed(event);
}

// --- done / failed ---------------------------------------------------------------------------------

function showDone(event) {
  state.doneFolder = event.folder;
  $("done-cost").textContent = `${usd(event.cost_usd, 4)} Claude + ${usd(event.stt_cost_usd, 4)} (${t("step_stt")})`;
  $("done-files").replaceChildren(...event.files.map((f) => {
    const li = document.createElement("li");
    const name = document.createElement("strong");
    name.textContent = f.name;
    const purpose = document.createElement("span");
    purpose.className = "purpose";
    purpose.textContent = ` — ${t(`purpose_${f.purpose}`)}`;
    li.append(name, purpose);
    return li;
  }));
  $("done-folder").textContent = event.folder;
  setError("done-error", "");
  show("view-done");
}

async function showFailed(event) {
  if (state.job && event.step) steps.fail(event.step);
  $("failed-reason").textContent = event.reason || t("err_network");
  const hint = event.hint || "log";
  $("failed-hint").textContent = hint === "network" ? "" : t(`hint_${hint}`);
  $("doctor").hidden = true;
  show("view-failed");
  if (DOCTOR_HINTS.has(hint)) await showDoctor();
}

async function showDoctor() {
  const { ok, data } = await api("GET", "/api/doctor");
  if (!ok) return;
  $("doctor-list").replaceChildren(...data.checks.map((c) => {
    const li = document.createElement("li");
    const status = document.createElement("span");
    const kind = c.ok ? "ok" : c.required ? "missing" : "warning";
    status.className = kind;
    status.textContent = t(`doctor_${kind}`);
    li.append(status, ` ${c.name}: ${c.detail}`);
    if (!c.ok && c.fix) {
      const fix = document.createElement("span");
      fix.className = "fix";
      fix.textContent = `${t("doctor_fix")}: ${c.fix}`;
      li.append(fix);
    }
    return li;
  }));
  $("doctor").hidden = false;
}

async function openFolder() {
  const { ok, data } = await api("POST", "/api/open-folder", { path: state.doneFolder });
  setError("done-error", ok ? "" : errorText(data));
}

function backToForm() {
  state.stop?.();
  clearInterval(state.timer);
  show("view-form");
}

// --- wiring ---------------------------------------------------------------------------------------

function wire() {
  $("form").addEventListener("submit", estimate);
  for (const tab of document.querySelectorAll('[role="tab"]')) {
    tab.addEventListener("click", () => setMode(tab.dataset.mode));
    tab.addEventListener("keydown", (e) => {
      if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) {
        e.preventDefault();
        setMode(state.mode === "url" ? "file" : "url", true);
      }
    });
  }
  $("url").addEventListener("paste", () => setTimeout(() => { $("url").value = $("url").value.trim(); }, 0));
  $("browse").addEventListener("click", browse);
  $("target-lang").addEventListener("change", updateLangNote);
  $("ui-lang").addEventListener("change", async () => {
    const ui = $("ui-lang").value;
    await loadConfig(ui);
    api("POST", "/api/settings", { ui_lang: ui });
    if (state.preview && !$("view-estimate").hidden) renderEstimate(state.preview);
  });
  $("confirm").addEventListener("change", updateStart);
  $("start").addEventListener("click", start);
  $("back").addEventListener("click", () => show("view-form"));
  $("another").addEventListener("click", backToForm);
  $("failed-back").addEventListener("click", backToForm);
  $("open-folder").addEventListener("click", openFolder);
}

async function main() {
  wire();
  if (!(await loadConfig(null))) return;
  setMode("url");
  const { ok, data } = await api("GET", "/api/jobs/current");
  if (ok && data.job && data.job.state === "running") attach(data.job); // a reload reattaches
}

main();
