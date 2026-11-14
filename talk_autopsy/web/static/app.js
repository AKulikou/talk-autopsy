// Talk Autopsy page: four states on one page (form, estimate, running, done / failed).
// The server enforces every rule (cost gate, one job at a time, paths); the page only asks.
import { api } from "./api.js";
import { applyStrings, clock, duration, errorText, excludedSteps, gapsWarning, setStrings, t } from "./i18n.js";
import { amount, cachedParts, money, stepService, usdAmount } from "./money.js";
import { followJob } from "./events.js";
import * as steps from "./steps.js";
import * as transcript from "./transcript.js";
import * as player from "./player.js";
import { allSelection, courseFraction, failedPositions, lessonPlace, leftPositions, rangeSelection } from "./course.js";

const $ = (id) => document.getElementById(id);
const VIEWS = ["view-form", "view-estimate", "view-course", "view-run", "view-course-done", "view-done", "view-failed"];
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
  course: null, // the course screen: { request, preview, selected: Set of positions }
  courseRun: null, // the running (or last) course's summary
  coursePoll: null,
  courseFolder: "", // the Done screen of a lesson: its course folder
  subsChoice: null, // the subtitle choice kept from one lesson to the next
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
  $("link-choice").hidden = true;
  if (state.mode === "url") {
    const url = $("url").value.trim();
    const { ok, data } = await api("POST", "/api/link", { url });
    if (!ok) {
      setError("form-error", errorText(data));
      return;
    }
    if (data.kind === "playlist") return openCourse(url);
    if (data.kind === "video_in_playlist") {
      $("link-choice").hidden = false; // "only this video" or "the whole playlist"
      $("choice-course").focus();
      return;
    }
  }
  return estimateVideo();
}

async function estimateVideo() {
  setError("form-error", "");
  $("link-choice").hidden = true;
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
  if (time.excludes?.length) text += `; ${excludedSteps(time.excludes)}`;
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
  if (state.courseRun && state.coursePoll) {
    $("course-head-between").hidden = false; // the next lesson starts, or the course ends (pollCourse)
    pollCourse();
    return;
  }
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
  lessonNav(null);
  show("view-done");
  if (event.result) player.show(playerElements(), await player.fetchResult(event.result));
  else player.show(playerElements(), null);
}

// A lesson of a course: "← previous lesson / next lesson →", its place, "Open the course folder".
function lessonNav(info) {
  const nav = info?.course;
  $("lesson-nav").hidden = !nav;
  $("open-course-folder").hidden = !nav;
  state.courseFolder = nav?.folder || "";
  if (!nav) return;
  $("lesson-nav").setAttribute("aria-label", nav.title);
  $("lesson-place").textContent = t("lesson_place", { n: nav.number, count: nav.count, title: nav.title });
  $("lesson-prev").disabled = !nav.prev;
  $("lesson-next").disabled = !nav.next;
  $("lesson-prev").onclick = () => nav.prev && watch(nav.prev);
  $("lesson-next").onclick = () => nav.next && watch(nav.next);
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
  lessonNav(info);
  show("view-done");
  // in a course the subtitle choice goes with the reader from one lesson to the next
  player.show(playerElements(), info, info.course
    ? { preferred: state.subsChoice, onChoice: (choice) => { state.subsChoice = choice; } } : {});
}

function shortDate(iso) {
  return iso ? iso.replace("T", " ").slice(0, 16) : "";
}

function recentButton(label, onClick, disabled = false) {
  const b = Object.assign(document.createElement("button"), { type: "button", className: "secondary",
    textContent: label, disabled });
  b.addEventListener("click", onClick);
  return b;
}

async function openPath(path, errorId) {
  const { ok, data } = await api("POST", "/api/open-folder", { path });
  setError(errorId, ok ? "" : errorText(data));
}

function courseItem(r) {
  const li = document.createElement("li");
  const text = document.createElement("div");
  const lang = state.config?.languages.target.find((l) => l.code === r.target_lang)?.name || r.target_lang;
  const meta = [shortDate(r.finished), t("recent_course", { count: r.lessons.length }),
    lang ? t("recent_lang", { lang }) : ""].filter(Boolean).join(" · ");
  text.append(Object.assign(document.createElement("strong"), { textContent: r.title }),
    Object.assign(document.createElement("span"), { className: "meta", textContent: meta }));
  const actions = document.createElement("div");
  actions.className = "recent-actions";
  const first = r.lessons.find((x) => x.media);
  actions.append(recentButton(t("recent_watch"), () => first && watch(first.id), !first),
    recentButton(t("course_open_folder"), () => openPath(r.folder, "recent-error")));
  const list = document.createElement("ul");
  list.className = "course-lessons";
  for (const lesson of r.lessons) {
    const item = document.createElement("li");
    item.append(Object.assign(document.createElement("span"), { textContent: lesson.title }), " ",
      recentButton(t("recent_watch"), () => watch(lesson.id), !lesson.media));
    list.append(item);
  }
  const details = document.createElement("details");
  details.append(Object.assign(document.createElement("summary"), { textContent: t("course_lessons") }), list);
  li.append(text, actions, details);
  details.style.flexBasis = "100%";
  return li;
}

async function loadUnfinished() {
  const { ok, data } = await api("GET", "/api/course/unfinished");
  const items = ok ? data.courses : [];
  $("unfinished").hidden = !items.length;
  $("unfinished-list").replaceChildren(...items.map((c) => {
    const li = document.createElement("li");
    const text = document.createElement("div");
    text.append(Object.assign(document.createElement("strong"), { textContent: c.title }),
      Object.assign(document.createElement("span"), { className: "meta",
        textContent: t("unfinished_meta", { done: c.done, count: c.count, left: c.left.length }) }));
    const actions = document.createElement("div");
    actions.className = "recent-actions";
    actions.append(recentButton(t("course_continue"), () => openCourse(c.url, {
      selected: c.left, profile: c.profile, domain: c.domain, target_lang: c.target_lang,
      course_glossary: c.course_glossary })),
    recentButton(t("course_open_folder"), () => openPath(c.folder, "recent-error")));
    li.append(text, actions);
    return li;
  }));
}

async function loadRecent() {
  loadUnfinished();
  const items = await player.fetchRecent();
  setError("recent-error", "");
  $("recent").hidden = !items.length;
  $("recent-list").replaceChildren(...items.map((r) => {
    if (r.group === "course") return courseItem(r);
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

// A failure in words in the interface language (the Failed screen and a course's failed lesson).
function failureText(event) {
  const hint = event?.hint || "log";
  return hint === "service"
    ? t(`fail_service_${event.kind || "unavailable"}`, { service: t(`service_${event.service || "network"}`),
      status: event.status ?? "" })
    : t(`fail_${hint}`);
}

async function showFailed(event) {
  if (state.job && event.step) steps.fail(event.step);
  // a sentence in the interface language for the kind of failure; the core's own line (English, for
  // developers) only under "Technical details"
  // no exception class in the main text (owner, 2026-11-13): it is in the technical details
  const hint = event.hint || "log";
  const service = hint === "service";
  $("failed-reason").textContent = failureText(event);
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
  clearInterval(state.coursePoll);
  state.coursePoll = null;
  $("course-head").hidden = true;
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
  $("choice-video").addEventListener("click", estimateVideo);
  $("choice-course").addEventListener("click", () => openCourse($("url").value.trim()));
  $("course-all").addEventListener("click", () => setSelection(allSelection(state.course.preview.lessons)));
  $("course-none").addEventListener("click", () => setSelection([]));
  $("course-range").addEventListener("click", () => setSelection(
    rangeSelection(state.course.preview.lessons, $("course-from").value, $("course-to").value)));
  $("course-glossary").addEventListener("change", () => refreshCourse(true));
  $("course-confirm").addEventListener("change", updateCourseStart);
  $("course-start").addEventListener("click", startCourse);
  $("course-back").addEventListener("click", () => show("view-form"));
  $("course-stop").addEventListener("click", stopCourse);
  $("course-retry").addEventListener("click", () => reopenCourse(failedPositions(state.courseRun)));
  $("course-continue").addEventListener("click", () => reopenCourse(leftPositions(state.courseRun)));
  $("course-done-open").addEventListener("click", () => openPath(state.courseRun?.folder, "course-done-error"));
  $("course-done-another").addEventListener("click", backToForm);
  $("open-course-folder").addEventListener("click", () => openPath(state.courseFolder, "done-error"));
}

// --- courses (a playlist) ---------------------------------------------------------------------------

function courseOptions(overrides = {}) {
  const base = currentRequest();
  return { profile: overrides.profile || base.profile, domain: overrides.domain || base.domain,
    target_lang: overrides.target_lang || base.target_lang };
}

// The course screen for a playlist link; `opts` (Continue): the earlier run's options and lessons.
async function openCourse(url, opts = {}) {
  setError("form-error", "");
  $("link-choice").hidden = true;
  if (opts.profile) {
    const radio = document.querySelector(`input[name="profile"][value="${opts.profile}"]`);
    if (radio) radio.checked = true;
  }
  if (opts.domain) $("web3").checked = opts.domain === "web3";
  if (opts.target_lang) $("target-lang").value = opts.target_lang;
  const request = { url, ...courseOptions(opts) };
  if (opts.course_glossary !== undefined) request.course_glossary = opts.course_glossary;
  if (opts.selected) request.selected = opts.selected;
  const button = $("estimate");
  busy(button, true, "estimating");
  const { ok, data } = await api("POST", "/api/course/preview", request);
  busy(button, false, "estimating");
  if (!ok) {
    setError(opts.errorId || "form-error", errorText(data));
    return false;
  }
  request.course_glossary = data.course_glossary;
  state.course = { request, preview: data, selected: new Set(data.selected) };
  renderCourse();
  show("view-course");
  return true;
}

function reopenCourse(positions) {
  const r = state.courseRun?.request;
  if (!r) return backToForm();
  return openCourse(r.url, { ...r, selected: positions, errorId: "course-done-error" });
}

function lessonMark(lesson) {
  if (!lesson.available) return { text: t(`course_reason_${lesson.reason}`), cls: "bad" };
  if (lesson.ready) return { text: t("course_ready"), cls: "ready" };
  return null;
}

function renderCourse() {
  const { preview, request } = state.course;
  $("course-title").textContent = preview.title;
  const profile = t(`profile_${request.profile}`);
  const lang = state.config?.languages.target.find((l) => l.code === request.target_lang)?.name || request.target_lang;
  $("course-options").textContent = t("course_options_text", { profile, lang,
    web3: t(request.domain === "web3" ? "yes" : "no") });
  $("course-folder").textContent = preview.folder + (preview.folder_exists ? "" : ` ${t("course_folder_new")}`);
  $("course-more").hidden = !preview.more;
  $("course-list").replaceChildren(...preview.lessons.map((lesson) => {
    const li = document.createElement("li");
    li.dataset.available = String(lesson.available);
    const id = `lesson-${lesson.position}`;
    const box = Object.assign(document.createElement("input"), { type: "checkbox", id,
      checked: state.course.selected.has(lesson.position), disabled: !lesson.available });
    box.addEventListener("change", () => {
      if (box.checked) state.course.selected.add(lesson.position);
      else state.course.selected.delete(lesson.position);
      refreshCourse(false);
    });
    const label = Object.assign(document.createElement("label"), { htmlFor: id });
    label.append(Object.assign(document.createElement("span"), { className: "num", textContent: lesson.position }),
      Object.assign(document.createElement("span"), { className: "title", textContent: lesson.title }),
      Object.assign(document.createElement("span"), { className: "len",
        textContent: lesson.duration_s ? clock(lesson.duration_s) : "—" }));
    li.append(box, label);
    const mark = lessonMark(lesson);
    if (mark) li.append(Object.assign(document.createElement("span"), { className: `mark ${mark.cls}`,
      textContent: mark.text }));
    return li;
  }));
  $("course-from").max = $("course-to").max = String(preview.lessons.length);
  $("course-glossary").checked = Boolean(request.course_glossary);
  renderCourseTotals(preview);
}

function renderCourseTotals(p) {
  const totals = p.totals;
  const available = p.lessons.filter((l) => l.available).length;
  $("course-selected").textContent = t("course_selected", { count: totals.count, total: available })
    + (totals.ready ? `. ${t("course_ready_count", { count: totals.ready })}` : "");
  $("course-cost").textContent = totals.known
    ? money({ claude: totals.claude_usd, stt: totals.stt_usd }, { prefix: "" }) : t("est_cost_none");
  $("course-time").textContent = totals.time_s === null ? t("est_time_none") : `≈ ${duration(totals.time_s)}`;
  const glossary = state.courseRun?.glossary;
  $("course-glossary-state").textContent = p.folder_exists && glossary?.file
    ? t("course_glossary_terms", { count: glossary.terms }) : t("course_glossary_later");
  const link = $("course-glossary-link");
  link.hidden = !p.folder_exists;
  link.href = `/api/course/glossary/${encodeURIComponent(p.folder.split(/[\\/]/).pop())}`;
  const notes = [];
  if (p.notes.includes("quality_not_measured")) notes.push(t("lang_not_measured"));
  if (p.busy) notes.push(t("est_busy"));
  $("course-notes").replaceChildren(...notes.map((n) => Object.assign(document.createElement("li"), { textContent: n })));
  const limit = amount(p.limit_usd);
  $("course-confirm").checked = false;
  $("course-confirm-box").hidden = !p.needs_confirmation;
  if (p.needs_confirmation) {
    $("course-confirm-text").textContent = totals.known ? t("course_over_limit", { limit }) : t("est_cost_none");
    $("course-confirm-label").textContent = totals.known
      ? t("course_confirm", { amount: amount(totals.total_usd) }) : t("course_confirm_none", { limit });
  }
  setError("course-error", "");
  updateCourseStart();
}

function updateCourseStart() {
  const p = state.course?.preview;
  $("course-start").disabled = !p || !p.totals.count || (p.needs_confirmation && !$("course-confirm").checked);
}

function setSelection(positions) {
  state.course.selected = new Set(positions);
  for (const lesson of state.course.preview.lessons) {
    const box = $(`lesson-${lesson.position}`);
    if (box) box.checked = state.course.selected.has(lesson.position);
  }
  refreshCourse(false);
}

// The totals of the selection (and, with `full`, the lessons again: the glossary option changes them)
// come from the server, which keeps the playlist it read; the gate uses the same numbers.
let refreshToken = 0;
async function refreshCourse(full) {
  const mine = ++refreshToken;
  const request = { ...state.course.request, selected: [...state.course.selected].sort((a, b) => a - b),
    course_glossary: $("course-glossary").checked };
  $("course-start").disabled = true;
  const { ok, data } = await api("POST", "/api/course/preview", request);
  if (mine !== refreshToken) return;
  if (!ok) {
    setError("course-error", errorText(data));
    return;
  }
  state.course.request = request;
  state.course.preview = data;
  if (full) renderCourse();
  else renderCourseTotals(data);
}

async function startCourse() {
  const p = state.course.preview;
  const request = { ...state.course.request, selected: [...state.course.selected].sort((a, b) => a - b),
    course_glossary: $("course-glossary").checked };
  if (p.needs_confirmation && $("course-confirm").checked) {
    if (p.totals.known) request.confirmed_cost_usd = p.totals.total_usd;
    else request.confirmed_no_estimate = true;
  }
  $("course-start").disabled = true;
  const { ok, data } = await api("POST", "/api/course/start", request);
  if (!ok) {
    updateCourseStart();
    setError("course-error", errorText(data));
    return;
  }
  api("POST", "/api/settings", { target_lang: request.target_lang });
  attachCourse(data.course);
}

function attachCourse(course) {
  state.courseRun = course;
  state.attachedJob = null;
  $("course-head").hidden = false;
  $("course-head-stopping").hidden = !course.stop_requested;
  $("course-stop").disabled = Boolean(course.stop_requested);
  renderCourseHead(course);
  show("view-run");
  clearInterval(state.coursePoll);
  state.coursePoll = setInterval(pollCourse, 1500);
  pollCourse();
}

function statusLine(lesson) {
  const parts = [t(`status_${lesson.status}`)];
  if (lesson.status === "failed" && lesson.error) parts.push(failureText(lesson.error));
  return parts.join(": ");
}

function renderCourseHead(c) {
  $("course-head-title").textContent = `${t("course_title")}: ${c.title}`;
  const place = lessonPlace(c);
  $("course-head-lesson").textContent = t("course_lesson_of", place);
  $("course-bar").value = courseFraction(c, state.lessonShare || 0);
  $("course-head-cost").textContent = money({ claude: c.cost_usd, stt: c.stt_cost_usd }, { prefix: "" });
  $("course-head-left").textContent = c.time_left_s === null ? t("run_left_none") : `≈ ${duration(c.time_left_s)}`;
  $("course-head-progress").textContent = t("course_progress", { done: c.done, count: c.count });
  $("course-head-list").replaceChildren(...c.lessons.map((lesson) => {
    const li = document.createElement("li");
    li.dataset.status = lesson.status;
    li.append(Object.assign(document.createElement("span"), { className: "num", textContent: lesson.position }),
      Object.assign(document.createElement("span"), { className: "title", textContent: lesson.title }),
      Object.assign(document.createElement("span"), { className: `mark ${lesson.status === "failed" ? "bad"
        : lesson.status === "finished" ? "ready" : ""}`, textContent: statusLine(lesson) }));
    return li;
  }));
}

async function pollCourse() {
  const { ok, data } = await api("GET", "/api/course/current");
  if (!ok || !data.course) return;
  const c = data.course;
  state.courseRun = c;
  renderCourseHead(c);
  $("course-head-stopping").hidden = !c.stop_requested || c.state !== "running";
  if (c.state !== "running") {
    clearInterval(state.coursePoll);
    state.coursePoll = null;
    state.stop?.();
    showCourseDone(c);
    return;
  }
  if (c.current_job && c.current_job !== state.attachedJob) {
    const job = await api("GET", "/api/jobs/current");
    if (job.ok && job.data.job && job.data.job.id === c.current_job) {
      state.attachedJob = c.current_job;
      $("course-head-between").hidden = true;
      attach(job.data.job);
    }
  }
}

async function stopCourse() {
  const id = state.courseRun?.id;
  if (!id) return;
  $("course-stop").disabled = true;
  const { ok } = await api("POST", "/api/course/stop", { id });
  if (ok) $("course-head-stopping").hidden = false;
  else $("course-stop").disabled = false;
}

function showCourseDone(c) {
  clearInterval(state.timer);
  $("course-head").hidden = true;
  $("course-done-heading").textContent = t(c.state === "finished" ? "course_done_heading" : "course_stopped_heading");
  $("course-done-title").textContent = c.title;
  const left = leftPositions(c).length;
  $("course-done-summary").textContent = t("course_done_summary", { finished: c.finished, count: c.count,
    failed: c.failed, left });
  $("course-done-cost").textContent = money({ claude: c.cost_usd, stt: c.stt_cost_usd });
  $("course-done-glossary").hidden = !c.glossary?.file;
  if (c.glossary?.file) $("course-done-glossary").textContent = t("course_glossary_terms", { count: c.glossary.terms });
  $("course-done-folder").textContent = c.folder;
  $("course-done-list").replaceChildren(...c.lessons.map((lesson) => {
    const li = document.createElement("li");
    li.dataset.status = lesson.status;
    li.append(Object.assign(document.createElement("span"), { className: "num", textContent: lesson.position }),
      Object.assign(document.createElement("span"), { className: "title", textContent: lesson.title }),
      Object.assign(document.createElement("span"), { className: "len",
        textContent: lesson.duration_s ? clock(lesson.duration_s) : "" }),
      Object.assign(document.createElement("span"), { className: `mark ${lesson.status === "failed" ? "bad"
        : lesson.status === "finished" ? "ready" : ""}`, textContent: statusLine(lesson) }));
    if (lesson.result) {
      const actions = Object.assign(document.createElement("span"), { className: "actions-inline" });
      actions.append(recentButton(t("course_watch"), () => watch(lesson.result)));
      li.append(actions);
    }
    return li;
  }));
  $("course-retry").hidden = !c.failed;
  $("course-continue").hidden = !left;
  setError("course-done-error", "");
  show("view-course-done");
}

async function main() {
  wire();
  transcript.wire(liveElements());
  const ui = new URLSearchParams(window.location.search).get("ui"); // e.g. ?ui=lt, without saving it
  if (!(await loadConfig(ui))) return;
  setMode("url");
  const course = await api("GET", "/api/course/current");
  if (course.ok && course.data.course?.state === "running") return attachCourse(course.data.course);
  const { ok, data } = await api("GET", "/api/jobs/current");
  if (ok && data.job && data.job.state === "running") attach(data.job); // a reload reattaches
  else loadRecent(); // the last results stay reachable after a reload
}

main();
