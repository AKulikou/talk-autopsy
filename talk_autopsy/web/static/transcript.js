// The waiting screen's text: English on the left (recognised, then corrected, with each change
// shown in place), the translation on the right, one row per sentence group. The server builds
// the rows (web/transcript.py); this module only fetches a bounded window of them and renders it.
// It follows the newest text until the reader scrolls up; "Jump to latest" follows again.
// The panel with the rows is collapsed by default (the owner's choice, 2026-11-13); the summary
// line and the warning stay. While it is collapsed only the summary is fetched and no row is built;
// opening it fetches the newest window, folded on the server from every event so far. The choice
// is remembered in this browser when storage is available.
import { api } from "./api.js";
import { Follower, anchorOf, needsLater, scrollToKeep, scrollToShow } from "./follow.js";
import { clock, gapsWarning, t } from "./i18n.js";

const WINDOW = 120; // segments per window: the DOM never holds an hour of text
const STEP = 100; // how far "show earlier text", or reaching the end of the rows, moves the window
const REFRESH_MS = 400; // at most this often while text keeps arriving
const MAX_TECH_LINES = 60; // technical details kept on the page
const STORAGE_KEY = "talk-autopsy.live-text"; // "shown" when the reader opened the text panel

let els = null;
let jobId = null;
let follower = new Follower();
let windowStart = 0;
let last = null; // the last window from the server
let timer = null;
let inFlight = false;
let again = false;
let shown = false; // the text panel is open
let earlierFrom = null; // "show earlier text" asked for the window starting here: show its start

const node = (tag, className, text) => {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
};

// A hidden word for screen readers, so a change is not told by styling alone.
const spoken = (key) => node("span", "sr", `${t(key)} `);

export function reset(elements, id, targetName) {
  els = elements;
  jobId = id;
  follower = new Follower();
  windowStart = 0;
  last = null;
  clearTimeout(timer);
  timer = null;
  els.rows.replaceChildren();
  showPanel();
  els.target.textContent = t("col_target", { lang: targetName });
  els.status.textContent = t("live_wait_stt");
  els.notices.replaceChildren();
  els.techList.replaceChildren();
  els.tech.hidden = true;
  showWarning("");
  earlierFrom = null;
  navigation();
  refresh();
}

// The text changed on the server: fetch the shown window again (throttled, never two at once).
export function changed() {
  if (!jobId || timer) return;
  timer = setTimeout(() => {
    timer = null;
    refresh();
  }, REFRESH_MS);
}

// A core message: a short sentence in the interface language when its kind is known
// (web/notices.py); the core's own line only under "Technical details".
export function notice(event) {
  if (!els) return;
  if (event.message) {
    els.notices.append(node("li", null, noticeText(event)));
    while (els.notices.childElementCount > 6) els.notices.firstElementChild.remove();
  }
  if (event.text) tech(event.text);
}

function noticeText(event) {
  const p = event.params || {};
  if (event.message.startsWith("notice_subtitles")) {
    const which = t(p.lang === "en" ? "subs_which_en" : "subs_which_target");
    return t(event.message, { which, cues: p.cues, fast: p.fast });
  }
  return t(event.message, p);
}

// One line of technical detail (a core message, a step's own description), collapsed by default.
export function tech(text) {
  if (!els || !text) return;
  els.techList.append(node("li", null, text));
  while (els.techList.childElementCount > MAX_TECH_LINES) els.techList.firstElementChild.remove();
  els.tech.hidden = false;
}

function showWarning(text) {
  els.warning.textContent = text;
  els.warning.hidden = !text;
}

export function stop() {
  clearTimeout(timer);
  timer = null;
  jobId = null;
}

async function refresh() {
  if (!jobId) return;
  if (inFlight) {
    again = true;
    return;
  }
  inFlight = true;
  const withRows = shown;
  let query = "rows=false";
  if (withRows) query = follower.following ? `count=${WINDOW}&follow=true` : `start=${windowStart}&count=${WINDOW}`;
  const id = jobId;
  const { ok, data } = await api("GET", `/api/jobs/${encodeURIComponent(id)}/transcript?${query}`);
  inFlight = false;
  if (ok && id === jobId) {
    if (withRows !== shown) again = true; // opened or closed meanwhile: fetch what is needed now
    else if (withRows) render(data);
    else showSummary(data.summary);
  }
  if (again) {
    again = false;
    refresh();
  }
}

function piecesOf(segment) {
  const p = node("p", "seg");
  for (const piece of segment.pieces) {
    if (piece.k === "same") {
      p.append(`${piece.t} `);
    } else {
      const mark = node(piece.k === "del" ? "del" : "ins");
      mark.append(spoken(piece.k === "del" ? "live_removed" : "live_added"), piece.t);
      p.append(mark, " ");
    }
  }
  if (segment.failed) p.append(node("span", "flag", t("live_not_corrected")));
  return p;
}

function rowOf(row) {
  const div = node("div", "row");
  div.setAttribute("role", "listitem");
  div.dataset.first = row.first;
  if (row.newest) div.dataset.newest = "true";
  if (row.segments.some((s) => s.changed)) div.dataset.changed = "true";
  div.append(node("span", "time", row.start === null ? "" : clock(row.start)));
  const en = node("div", "en");
  en.lang = "en";
  en.append(...row.segments.map(piecesOf));
  const tr = node("div", "tr");
  const done = row.units.filter((u) => u.text);
  if (done.length) {
    tr.append(node("p", "seg", done.map((u) => u.text).join(" ")));
  }
  if (row.units.some((u) => u.failed)) {
    tr.append(node("span", "flag", t("live_not_translated")));
  } else if (!done.length) {
    const wait = node("span", "pending", "…");
    wait.append(node("span", "sr", t("live_pending")));
    tr.append(wait);
  }
  div.append(en, tr);
  return div;
}

function statusText(summary) {
  if (!summary.segments) return t("live_wait_stt");
  const parts = [];
  if (summary.source === "stt") parts.push(t("live_first_pass"));
  if (summary.correction_blocks) parts.push(t("live_corrections", { n: summary.edits }));
  if (summary.translation_blocks) parts.push(t("live_translated", { n: summary.units }));
  if (summary.truncated) parts.push(t("live_truncated"));
  return parts.join(" · ");
}

// The box's scrolling area and a row, as viewport rectangles (follow.js).
function boxRect(box) {
  const r = box.getBoundingClientRect();
  return { top: r.top + box.clientTop, scrollTop: box.scrollTop, clientHeight: box.clientHeight,
    scrollHeight: box.scrollHeight };
}

function rowRect(row) {
  const r = row.getBoundingClientRect();
  return { key: row.dataset.first, top: r.top, bottom: r.bottom };
}

function showSummary(summary) {
  els.status.textContent = statusText(summary);
  showWarning(gapsWarning(summary.untranslated, summary.uncorrected));
}

function render(data) {
  last = data;
  const box = els.rows;
  // keep the reader's place: the first visible row and its offset
  const anchor = follower.following ? null : anchorOf(boxRect(box), [...box.children].map(rowRect));
  box.replaceChildren(...data.rows.map(rowOf));
  showSummary(data.summary);
  windowStart = data.start;
  if (earlierFrom !== null && !follower.following && data.start <= earlierFrom) {
    box.scrollTop = 0; // the earlier part just loaded: show its start, not the old place
    earlierFrom = null;
  } else if (follower.following) {
    const newest = box.querySelector('[data-newest="true"]') || box.lastElementChild;
    if (newest) box.scrollTop = scrollToShow(boxRect(box), rowRect(newest));
  } else if (anchor) {
    const same = box.querySelector(`[data-first="${anchor.key}"]`);
    if (same) box.scrollTop = scrollToKeep(boxRect(box), rowRect(same), anchor.offset);
  }
  follower.placed(box.scrollTop);
  navigation();
}

// Both buttons stay in place: "show earlier text" is off when nothing is earlier, "jump to the
// latest text" while the newest text is already followed (visible).
function navigation() {
  els.earlier.disabled = !last || !last.more_before;
  els.jump.disabled = follower.following;
}

// Only the reader's own scrolling stops or resumes the following (follow.js). Reading earlier text
// and reaching the end of the rows shown loads the next ones (no "show later text" button).
function onScroll() {
  if (follower.scrolled(boxRect(els.rows), Date.now(), !last || last.more_after)) navigation();
  const loading = !last || windowStart !== last.start; // a window is on its way
  if (!loading && needsLater(boxRect(els.rows), follower.following, last.more_after)) {
    windowStart = last.start + STEP;
    refresh();
  }
}

// The text panel open or closed, with its button and the page layout (app.css).
function showPanel() {
  els.panel.hidden = !shown;
  els.toggle.setAttribute("aria-expanded", String(shown));
  els.toggle.dataset.i18n = shown ? "live_hide" : "live_show";
  els.toggle.textContent = t(els.toggle.dataset.i18n);
  document.body.dataset.liveText = shown ? "shown" : "hidden";
}

function setShown(value) {
  shown = value;
  try {
    localStorage.setItem(STORAGE_KEY, shown ? "shown" : "hidden");
  } catch {
    // no storage (private window, blocked site data): the choice lasts until the page is reloaded
  }
  showPanel();
  if (shown) {
    follower = new Follower(); // opened: the newest text, as on a fresh page
    windowStart = 0;
  } else {
    els.rows.replaceChildren(); // closed: no rows kept or updated
    last = null;
    navigation();
  }
  if (jobId) refresh();
}

function storedShown() {
  try {
    return localStorage.getItem(STORAGE_KEY) === "shown";
  } catch {
    return false;
  }
}

export function wire(elements) {
  els = elements;
  shown = storedShown();
  showPanel();
  els.toggle.addEventListener("click", () => setShown(!shown));
  els.rows.addEventListener("scroll", onScroll, { passive: true });
  for (const type of ["wheel", "touchstart", "pointerdown", "keydown"]) {
    els.rows.addEventListener(type, () => follower.input(Date.now()), { passive: true });
  }
  els.jump.addEventListener("click", () => {
    follower.jump();
    earlierFrom = null;
    els.rows.focus({ preventScroll: true }); // before the button is disabled, so focus is not lost
    navigation();
    refresh();
  });
  els.earlier.addEventListener("click", () => {
    follower.stop();
    windowStart = Math.max(0, (last?.start || 0) - STEP);
    earlierFrom = windowStart;
    navigation();
    refresh();
  });
}
