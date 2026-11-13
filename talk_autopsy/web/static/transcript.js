// The waiting screen's text: English on the left (recognised, then corrected, with each change
// shown in place), the translation on the right, one row per sentence group. The server builds
// the rows (web/transcript.py); this module only fetches a bounded window of them and renders it.
// It follows the newest text until the reader scrolls up; "Jump to latest" follows again.
import { api } from "./api.js";
import { Follower, anchorOf, scrollToKeep, scrollToShow } from "./follow.js";
import { clock, gapsWarning, t } from "./i18n.js";

const WINDOW = 120; // segments per window: the DOM never holds an hour of text
const STEP = 100; // how far "show earlier / later" moves the window
const REFRESH_MS = 400; // at most this often while text keeps arriving
const MAX_TECH_LINES = 60; // technical details kept on the page

let els = null;
let jobId = null;
let follower = new Follower();
let windowStart = 0;
let last = null; // the last window from the server
let timer = null;
let inFlight = false;
let again = false;

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
  els.target.textContent = t("col_target", { lang: targetName });
  els.status.textContent = t("live_wait_stt");
  els.notices.replaceChildren();
  els.techList.replaceChildren();
  els.tech.hidden = true;
  showWarning("");
  els.jump.hidden = true;
  els.earlier.hidden = true;
  els.later.hidden = true;
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
  const query = follower.following ? `count=${WINDOW}&follow=true` : `start=${windowStart}&count=${WINDOW}`;
  const id = jobId;
  const { ok, data } = await api("GET", `/api/jobs/${encodeURIComponent(id)}/transcript?${query}`);
  inFlight = false;
  if (ok && id === jobId) render(data);
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

function render(data) {
  last = data;
  const box = els.rows;
  // keep the reader's place: the first visible row and its offset
  const anchor = follower.following ? null : anchorOf(boxRect(box), [...box.children].map(rowRect));
  box.replaceChildren(...data.rows.map(rowOf));
  els.status.textContent = statusText(data.summary);
  showWarning(gapsWarning(data.summary.untranslated, data.summary.uncorrected));
  els.earlier.hidden = !data.more_before;
  els.later.hidden = !data.more_after;
  windowStart = data.start;
  if (follower.following) {
    const newest = box.querySelector('[data-newest="true"]') || box.lastElementChild;
    if (newest) box.scrollTop = scrollToShow(boxRect(box), rowRect(newest));
  } else if (anchor) {
    const same = box.querySelector(`[data-first="${anchor.key}"]`);
    if (same) box.scrollTop = scrollToKeep(boxRect(box), rowRect(same), anchor.offset);
  }
  follower.placed(box.scrollTop);
  els.jump.hidden = follower.following;
}

// Only the reader's own scrolling stops or resumes the following (follow.js).
function onScroll() {
  if (follower.scrolled(boxRect(els.rows), Date.now(), !last || last.more_after)) {
    els.jump.hidden = follower.following;
  }
}

export function wire(elements) {
  els = elements;
  els.rows.addEventListener("scroll", onScroll, { passive: true });
  for (const type of ["wheel", "touchstart", "pointerdown", "keydown"]) {
    els.rows.addEventListener(type, () => follower.input(Date.now()), { passive: true });
  }
  els.jump.addEventListener("click", () => {
    follower.jump();
    els.jump.hidden = true;
    refresh();
    els.rows.focus({ preventScroll: true });
  });
  els.earlier.addEventListener("click", () => {
    follower.stop();
    windowStart = Math.max(0, (last?.start || 0) - STEP);
    refresh();
  });
  els.later.addEventListener("click", () => {
    windowStart = (last?.start || 0) + STEP;
    follower.stop();
    refresh();
  });
}
