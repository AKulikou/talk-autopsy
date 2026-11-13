// Talk Autopsy page: four states on one page (form, estimate, running, done / failed).
// The server enforces every rule (cost gate, one job at a time, paths); the page only asks.
import { api } from "./api.js";
import { applyStrings, clock, duration, errorText, gapsWarning, setStrings, t } from "./i18n.js";
import { amount, cachedParts, money, stepService, usdAmount } from "./money.js";
import { followJob } from "./events.js";
import * as steps from "./steps.js";
import * as transcript from "./transcript.js";
import * as player from "./player.js";

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
  document.body.dataset.view = id; // the running and done screens use the full width
  const heading = $(id).querySelector("h2");
  if (heading) {
    heading.tabIndex = -1;
    heading.focus({ preventScroll: false });
  }
}

function setWarning(id, text) {
  $(id).textContent = text || "";
  $(id).hidden = !text;
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
    const names = [...new Set(time.excludes.map((s) => t(`step_${s.startsWith("outputs") ? "outputs" : s}`)))];
    text += `; ${t("est_time_excludes", { steps: names.join(", ") })}`;
  }
  return text;
}

function renderEstimate(p) {
  $("est-title").textContent = p.title;
  $("est-duration").textContent = p.duration_s ? clock(p.duration_s) : "—";
  $("est-cost").textContent = p.cost
    ? money({ claude: p.cost.claude_usd, stt: p.cost.stt_usd },
      { cached: p.cost.cached || {}, prefix: p.cost.upper ? "upper" : "" })
    : t("est_cost_none");
  $("est-time").textContent = timeText(p.time);
  $("est-formats").replaceChildren(...p.formats.map((f) => {
    const li = document.createElement("li");
    li.textContent = t(FORMAT_PURPOSE[f] || f);
    return li;
  }));
  const notes = [];
  // what the cache already holds (rerun_plan.py): named, so a small amount is not a surprise
  if (p.cache?.cached?.length) {
    const names = [...new Set(p.cache.cached.map((s) => t(`step_${s}`)))];
    notes.push(t("est_cached", { steps: names.join(", ") }));
    if (p.cost?.full_usd) notes.push(t("est_full", { amount: usdAmount(p.cost.full_usd) }));
  }
  if (p.cache?.reasons?.includes("translation_after_correction")) notes.push(t("est_upper_translation"));
  if (p.notes.includes("audio_only")) notes.push(t("est_audio_only"));
  if (p.notes.includes("quality_not_measured")) notes.push(t("lang_not_measured"));
  if (p.busy) notes.push(t("est_busy"));
  $("est-notes").replaceChildren(...notes.map((n) => {
    const li = document.createElement("li");
    li.textContent = n;
    return li;
  }));
  const limit = amount(p.limit_usd);
  $("confirm").checked = false;
  $("confirm-box").hidden = !p.needs_confirmation;
  if (p.needs_confirmation) {
    $("confirm-text").textContent = p.cost ? t("est_over_limit", { limit }) : t("est_cost_none");
    $("confirm-label").textContent = p.cost
      ? t("est_confirm", { amount: amount(p.cost.total_usd) })
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
  state.paid = {};
  $("run-cost").textContent = money({ claude: job.cost_usd, stt: job.stt_cost_usd }, { prefix: "" });
  $("run-left").textContent = timeText(job.preview?.time);
  steps.build($("steps"), job.steps);
  const lang = state.config?.languages.target.find((l) => l.code === job.request.target_lang);
  transcript.reset(liveElements(), job.id, lang?.name || job.request.target_lang);
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
      if (event.type === "step_started" && event.detail) transcript.tech(`${t(`step_${event.step}`)}: ${event.detail}`);
      if (event.type === "step_finished") {
        if (stepService(event.step)) state.paid = { ...state.paid, [event.step]: event.skipped };
        $("run-cost").textContent = money({ claude: event.total_cost_usd, stt: event.total_stt_cost_usd },
          { cached: cachedParts(state.paid || {}), prefix: "" });
      }
      break;
    case "estimate_updated":
      if (event.time) {
        $("run-left").textContent = event.time.seconds === null ? t("run_left_none") : timeText(event.time);
      }
      break;
    case "text_changed":
      transcript.changed();
      break;
    case "notice":
      transcript.notice(event);
      break;
    default:
      break;
  }
}

function liveElements() {
  return { rows: $("live-rows"), status: $("live-status"), target: $("col-target"), notices: $("live-notices"),
    jump: $("live-jump"), earlier: $("live-earlier"), warning: $("run-warning"),
    tech: $("live-tech"), techList: $("live-tech-list"), toggle: $("live-toggle"), panel: $("live-panel") };
}

function onEnd(event) {
  clearInterval(state.timer);
  transcript.changed(); // the last text
  setTimeout(transcript.stop, 1000);
  state.stop = null;
  if (event.type === "finished") showDone(event);
  else showFailed(event);
}

// --- done / failed ---------------------------------------------------------------------------------

function playerElements() {
  return { box: $("player-box"), media: $("player-media"), text: $("player-text"), note: $("player-note"),
    none: $("player-none"), subs: $("player-subs"), subsButtons: $("player-subs-buttons"),
    subsState: $("player-subs-state") };
}

// A result file: what it is for, in words (by kind, web/labels.py), then its name.
function langName(code) {
  const own = t(`langname_${code}`);
  if (own !== `langname_${code}`) return own;
  return state.config?.languages.target.find((l) => l.code === code)?.name || code;
}

function fileItem(name, kind, lang, purpose) {
  const li = document.createElement("li");
  const key = kind && kind !== "other" ? `file_${kind}` : purpose ? `purpose_${purpose}` : "";
  const label = Object.assign(document.createElement("strong"), {
    textContent: key ? t(key, { lang: langName(lang) }) : name });
  li.append(label);
  if (key) li.append(Object.assign(document.createElement("span"), { className: "file-name", textContent: name }));
  return li;
}

// The Done screen's money: this run, and the video's total when failed attempts came before it.
function showCost(info) {
  const known = info.cost_usd !== null && info.cost_usd !== undefined;
  $("done-cost").hidden = !known;
  if (known) {
    $("done-cost").textContent = money({ claude: info.cost_usd, stt: info.stt_cost_usd || 0 },
      { cached: info.cached || {} });
  }
  const all = info.attempts;
  $("done-total").hidden = !all;
  if (all) {
    $("done-total").textContent = t("done_total", {
      count: all.failed, text: money({ claude: all.claude_usd, stt: all.stt_usd }, { prefix: "" }) });
  }
}

async function showDone(event) {
  state.doneFolder = event.folder;
  $("done-title").textContent = state.job?.preview?.title || "";
  showCost(event);
  player.clear(playerElements());
  const lang = state.job?.request.target_lang || "";
  $("done-files").replaceChildren(...event.files.map((f) => fileItem(f.name, f.kind, lang, f.purpose)));
  $("done-folder").textContent = event.folder;
  setWarning("done-warning", gapsWarning(event.untranslated, event.uncorrected));
  setError("done-error", "");
  show("view-done");
  if (event.result) player.show(playerElements(), await player.fetchResult(event.result));
  else player.show(playerElements(), null);
}

// A result from the start page's list: the same Done screen, read from the folder.
async function watch(id) {
  const info = await player.fetchResult(id);
  if (!info) {
    setError("recent-error", t("err_no_result"));
    return;
  }
  state.doneFolder = info.folder;
  $("done-title").textContent = info.title;
  showCost(info);
  $("done-files").replaceChildren(...info.files.map((f) => fileItem(f.name, f.kind, info.target_lang)));
  $("done-folder").textContent = info.folder;
  setWarning("done-warning", gapsWarning(info.untranslated, info.uncorrected));
  setError("done-error", "");
  show("view-done");
  player.show(playerElements(), info);
}

function shortDate(iso) {
  return iso ? iso.replace("T", " ").slice(0, 16) : "";
}

async function loadRecent() {
  const items = await player.fetchRecent();
  setError("recent-error", "");
  $("recent").hidden = !items.length;
  $("recent-list").replaceChildren(...items.map((r) => {
    const li = document.createElement("li");
    const text = document.createElement("div");
    const title = Object.assign(document.createElement("strong"), { textContent: r.title });
    const lang = state.config?.languages.target.find((l) => l.code === r.target_lang)?.name || r.target_lang;
    const meta = [shortDate(r.finished), lang ? t("recent_lang", { lang }) : ""].filter(Boolean).join(" · ");
    text.append(title, Object.assign(document.createElement("span"), { className: "meta", textContent: meta }));
    const actions = document.createElement("div");
    actions.className = "recent-actions";
    const watchButton = Object.assign(document.createElement("button"), {
      type: "button", className: "secondary", textContent: t("recent_watch"), disabled: !r.media });
    watchButton.addEventListener("click", () => watch(r.id));
    const open = Object.assign(document.createElement("button"), {
      type: "button", className: "secondary", textContent: t("open_folder") });
    open.addEventListener("click", async () => {
      const { ok, data } = await api("POST", "/api/open-folder", { path: r.folder });
      setError("recent-error", ok ? "" : errorText(data));
    });
    actions.append(watchButton, open);
    li.append(text, actions);
    return li;
  }));
}

async function showFailed(event) {
  if (state.job && event.step) steps.fail(event.step);
  // a sentence in the interface language for the kind of failure; the core's own line (English, for
  // developers) only under "Technical details"
  // no exception class in the main text (owner, 2026-11-13): it is in the technical details
  const hint = event.hint || "log";
  const service = hint === "service";
  $("failed-reason").textContent = service
    ? t(`fail_service_${event.kind || "unavailable"}`, { service: t(`service_${event.service || "network"}`),
      status: event.status ?? "" })
    : t(`fail_${hint}`);
  $("failed-tech-text").textContent = event.reason || "";
  $("failed-tech").hidden = !event.reason;
  $("failed-hint").textContent = hint === "network" ? "" : t(`hint_${hint}`);
  const spent = event.cost_usd !== undefined && hint !== "network";
  $("failed-cost").hidden = !spent;
  if (spent) {
    $("failed-cost").textContent = money({ claude: event.cost_usd, stt: event.stt_cost_usd || 0 },
      { cached: event.cached || {} });
  }
  // a failure on the service's side: the same job again (the estimate first, as usual)
  $("failed-again").hidden = !(service && state.job?.request);
  $("failed-back").className = service ? "secondary" : "primary";
  setError("failed-error", "");
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

// "Run again" on the Failed screen: the failed job's request, estimated again, then Start as usual.
async function runAgain() {
  const r = state.job?.request;
  if (!r) return backToForm();
  const request = { mode: r.mode, profile: r.profile, domain: r.domain, target_lang: r.target_lang };
  if (r.mode === "url") request.url = r.source;
  else request.path = r.source;
  const button = $("failed-again");
  button.disabled = true;
  const { ok, data } = await api("POST", "/api/estimate", request);
  button.disabled = false;
  if (!ok) {
    setError("failed-error", errorText(data));
    return;
  }
  state.request = request;
  state.preview = data;
  renderEstimate(data);
  show("view-estimate");
}

function backToForm() {
  state.stop?.();
  clearInterval(state.timer);
  player.clear(playerElements());
  show("view-form");
  loadRecent();
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
    if (!$("view-form").hidden) loadRecent();
  });
  $("confirm").addEventListener("change", updateStart);
  $("start").addEventListener("click", start);
  $("back").addEventListener("click", () => show("view-form"));
  $("another").addEventListener("click", backToForm);
  $("failed-back").addEventListener("click", backToForm);
  $("failed-again").addEventListener("click", runAgain);
  $("open-folder").addEventListener("click", openFolder);
}

async function main() {
  wire();
  transcript.wire(liveElements());
  const ui = new URLSearchParams(window.location.search).get("ui"); // e.g. ?ui=lt, without saving it
  if (!(await loadConfig(ui))) return;
  setMode("url");
  const { ok, data } = await api("GET", "/api/jobs/current");
  if (ok && data.job && data.job.state === "running") attach(data.job); // a reload reattaches
  else loadRecent(); // the last results stay reachable after a reload
}

main();
