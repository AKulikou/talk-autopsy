// The waiting screen's text panel (talk_autopsy/web/static/transcript.js) across a course's lessons,
// run by tests/test_web_follow.py with `node --test`; the page's API module is replaced by a stub that
// records the requests, and the elements by plain objects (no DOM).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = (path) => readFileSync(new URL(`../../talk_autopsy/web/static/${path}`, import.meta.url), "utf-8");
const dataUrl = (source) => `data:text/javascript,${encodeURIComponent(source)}`;
globalThis.__requests = [];
const apiStub = dataUrl(`export async function api(method, path) {
  globalThis.__requests.push(path);
  return { ok: true, data: { summary: { segments: 0 }, rows: [], start: 0, more_before: false, more_after: false } };
}`);
const i18nUrl = dataUrl(read("i18n.js"));
const followUrl = dataUrl(read("follow.js"));
const transcript = await import(dataUrl(read("transcript.js").replace("./api.js", apiStub)
  .replace("./i18n.js", i18nUrl).replace("./follow.js", followUrl)));

const element = () => ({ textContent: "", hidden: false, disabled: false, dataset: {}, childElementCount: 0,
  replaceChildren() {}, append() {}, setAttribute() {}, addEventListener() {}, focus() {},
  querySelector() { return null; }, getBoundingClientRect() { return { top: 0, bottom: 0 }; },
  get children() { return []; }, scrollTop: 0, clientHeight: 100, scrollHeight: 100, clientTop: 0 });
const els = () => Object.fromEntries(["rows", "status", "target", "notices", "jump", "earlier", "warning", "tech",
  "techList", "toggle", "panel"].map((k) => [k, element()]));
const pause = (ms) => new Promise((done) => setTimeout(done, ms));

test("the end of one lesson does not stop the next lesson's text (course; bug 2026-11-15)", async () => {
  globalThis.document = { body: { dataset: {} } };
  const elements = els();
  transcript.wire(elements);
  transcript.reset(elements, "lesson-1", "Russian");
  transcript.stop("lesson-1"); // lesson 1 ended
  transcript.reset(elements, "lesson-2", "Russian"); // the course attached lesson 2 at once
  transcript.stop("lesson-1"); // the delayed stop of lesson 1 arrives late: it must not stop lesson 2
  globalThis.__requests.length = 0;
  transcript.changed(); // lesson 2's text changed
  await pause(600);
  assert.ok(globalThis.__requests.some((p) => p.includes("/api/jobs/lesson-2/transcript")), globalThis.__requests);
  transcript.stop(); // without a job id: stops whatever runs (leaving the screen)
  globalThis.__requests.length = 0;
  transcript.changed();
  await pause(600);
  assert.deepEqual(globalThis.__requests, []);
});
