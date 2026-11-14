// Course helpers (talk_autopsy/web/static/course.js) and the kept subtitle choice (subs.js), run by
// tests/test_web_follow.py with `node --test`; built-in modules only, loaded from data URLs.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = (path) => readFileSync(new URL(`../../talk_autopsy/web/static/${path}`, import.meta.url), "utf-8");
const dataUrl = (source) => `data:text/javascript,${encodeURIComponent(source)}`;
const course = await import(dataUrl(read("course.js")));
const subs = await import(dataUrl(read("subs.js")));

const lessons = [1, 2, 3, 4, 5].map((position) => ({ position, available: position !== 3 }));

test("a range selects the available lessons in it, in either order", () => {
  assert.deepEqual(course.rangeSelection(lessons, 2, 4), [2, 4]);
  assert.deepEqual(course.rangeSelection(lessons, "4", "1"), [1, 2, 4]);
  assert.deepEqual(course.rangeSelection(lessons, "", "2"), [2]);
  assert.deepEqual(course.rangeSelection(lessons, "", ""), []);
  assert.deepEqual(course.allSelection(lessons), [1, 2, 4, 5]);
});

test("lesson n of count and the overall bar", () => {
  const run = { lessons: [{ status: "finished" }, { status: "failed" }, { status: "running" }, { status: "queued" }] };
  assert.deepEqual(course.lessonPlace(run), { n: 3, count: 4 });
  assert.equal(course.courseFraction(run, 0.5), 2.5 / 4);
  assert.deepEqual(course.failedPositions({ lessons: [{ position: 7, status: "failed" }] }), [7]);
  const between = { lessons: [{ status: "finished" }, { status: "queued" }] };
  assert.deepEqual(course.lessonPlace(between), { n: 2, count: 2 });
  assert.equal(course.courseFraction(between, 0.9), 0.5);
});

test("the subtitle choice is kept between lessons", () => {
  const tracks = [{ srclang: "ru", default: true }, { srclang: "en", default: false }];
  assert.equal(subs.initialTrack(tracks, false, null), 0);
  assert.equal(subs.initialTrack(tracks, false, "en"), 1);
  assert.equal(subs.initialTrack(tracks, false, "off"), -1);
  const burned = [{ srclang: "en", default: false }];
  assert.equal(subs.initialTrack(burned, true, "target"), -1); // the translation is in the picture
  assert.equal(subs.initialTrack(burned, true, "en"), 0);
  assert.equal(subs.preferenceOf(tracks, 1), "en");
  assert.equal(subs.preferenceOf(tracks, -1), "off");
  assert.equal(subs.preferenceOf(tracks, 0), "target");
});
