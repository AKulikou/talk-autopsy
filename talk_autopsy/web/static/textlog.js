// The text block of the running screen: recognized, corrected and translated text and notices,
// in arrival order. Plain on purpose (v1.1 session 1); session 2 replaces this module and the
// #textlog element with the side-by-side view. Interface: reset(element), add(event).
import { t } from "./i18n.js";

const MAX_LINES = 400; // the DOM stays small on an hour-long video
let list = null;

export function reset(element) {
  list = element;
  list.replaceChildren();
}

function line(kindKey, extraClass) {
  const li = document.createElement("li");
  if (extraClass) li.className = extraClass;
  if (kindKey) {
    const kind = document.createElement("span");
    kind.className = "kind";
    kind.textContent = t(kindKey);
    li.append(kind);
  }
  return li;
}

export function add(event) {
  if (!list) return;
  let li = null;
  if (event.type === "text" && event.kind === "edit") {
    li = line("text_edit");
    const before = document.createElement("span");
    before.className = "before";
    before.textContent = event.before || "";
    li.append(before, " → ", event.text || "");
  } else if (event.type === "text") {
    li = line(event.kind === "translated" ? "text_translated" : "text_recognized");
    li.append(event.text || "");
  } else if (event.type === "notice") {
    li = line(null, "notice");
    li.append(event.text || "");
  }
  if (!li) return;
  const atBottom = list.scrollTop + list.clientHeight >= list.scrollHeight - 8;
  list.append(li);
  while (list.childElementCount > MAX_LINES) list.firstElementChild.remove();
  if (atBottom) list.scrollTop = list.scrollHeight;
}
