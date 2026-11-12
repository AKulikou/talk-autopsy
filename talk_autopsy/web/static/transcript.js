// The waiting screen's text: English on the left (recognised, then corrected, with each change
// shown in place), the translation on the right, one row per sentence group. The server builds
// the rows (web/transcript.py); this module only fetches a bounded window of them and renders it.
// It follows the newest text until the reader scrolls up; "Jump to latest" follows again.
import { api } from "./api.js";
import { clock, t } from "./i18n.js";

const WINDOW = 120; // segments per window: the DOM never holds an hour of text
const STEP = 100; // how far "show earlier / later" moves the window
const REFRESH_MS = 400; // at most this often while text keeps arriving

let els = null;
let jobId = null;
let following = true;
let windowStart = 0;
let last = null; // the last window from the server
let timer = null;
let inFlight = false;
let again = false;
let lastScrollTop = 0;
let userInput = 0; // time of the reader's last wheel, touch, key or pointer input on the text

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
  following = true;
  windowStart = 0;
  last = null;
  clearTimeout(timer);
  timer = null;
  els.rows.replaceChildren();
  els.target.textContent = t("col_target", { lang: targetName });
  els.status.textContent = t("live_wait_stt");
  els.notices.replaceChildren();
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

export function notice(event) {
  if (!els) return;
  const li = node("li", null, event.text || "");
  els.notices.append(li);
  while (els.notices.childElementCount > 6) els.notices.firstElementChild.remove();
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
  const query = following ? `count=${WINDOW}&follow=true` : `start=${windowStart}&count=${WINDOW}`;
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
  if (summary.failed_blocks) parts.push(t("live_failed_blocks", { n: summary.failed_blocks }));
  if (summary.truncated) parts.push(t("live_truncated"));
  return parts.join(" · ");
}

function render(data) {
  last = data;
  const box = els.rows;
  // keep the reader's place: the first visible row and its offset
  let anchor = null;
  if (!following) {
    for (const child of box.children) {
      if (child.offsetTop + child.offsetHeight > box.scrollTop) {
        anchor = { first: child.dataset.first, offset: child.offsetTop - box.scrollTop };
        break;
      }
    }
  }
  box.replaceChildren(...data.rows.map(rowOf));
  els.status.textContent = statusText(data.summary);
  els.earlier.hidden = !data.more_before;
  els.later.hidden = !data.more_after;
  windowStart = data.start;
  if (following) {
    const newest = box.querySelector('[data-newest="true"]') || box.lastElementChild;
    if (newest) box.scrollTop = Math.max(0, newest.offsetTop + newest.offsetHeight - box.clientHeight + 8);
  } else if (anchor) {
    const same = box.querySelector(`[data-first="${anchor.first}"]`);
    if (same) box.scrollTop = same.offsetTop - anchor.offset;
  }
  lastScrollTop = box.scrollTop;
  els.jump.hidden = following;
}

function onScroll() {
  const box = els.rows;
  // only the reader's own scrolling counts, not the page moving the text or the browser clamping it
  if (Date.now() - userInput > 1500) {
    lastScrollTop = box.scrollTop;
    return;
  }
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 12;
  if (box.scrollTop < lastScrollTop - 4 && following) {
    following = false; // the reader went back: stop moving the text under them
    els.jump.hidden = false;
  } else if (!following && atBottom && last && !last.more_after) {
    following = true; // back at the newest text by scrolling
    els.jump.hidden = true;
  }
  lastScrollTop = box.scrollTop;
}

export function wire(elements) {
  els = elements;
  els.rows.addEventListener("scroll", onScroll, { passive: true });
  for (const type of ["wheel", "touchstart", "pointerdown", "keydown"]) {
    els.rows.addEventListener(type, () => { userInput = Date.now(); }, { passive: true });
  }
  els.jump.addEventListener("click", () => {
    following = true;
    els.jump.hidden = true;
    refresh();
    els.rows.focus({ preventScroll: true });
  });
  els.earlier.addEventListener("click", () => {
    following = false;
    windowStart = Math.max(0, (last?.start || 0) - STEP);
    refresh();
  });
  els.later.addEventListener("click", () => {
    windowStart = (last?.start || 0) + STEP;
    following = false;
    refresh();
  });
}
