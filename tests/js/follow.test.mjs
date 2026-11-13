// Following the newest text (talk_autopsy/web/static/follow.js), run by tests/test_web_follow.py
// with `node --test`. The module is loaded from a data URL, so no package.json is needed.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const source = readFileSync(new URL("../../talk_autopsy/web/static/follow.js", import.meta.url), "utf-8");
const { Follower, anchorOf, needsLater, scrollToKeep, scrollToShow } = await import(
  `data:text/javascript,${encodeURIComponent(source)}`);

// A box whose scrolling area starts `top` pixels down the viewport, and a row at `at` pixels
// inside the scrolled content: their viewport rectangles, as the browser gives them.
const box = (top, scrollTop, clientHeight = 400, scrollHeight = 6000) =>
  ({ top, scrollTop, clientHeight, scrollHeight });
const row = (b, at, height = 60, key = "0") =>
  ({ key, top: b.top + at - b.scrollTop, bottom: b.top + at - b.scrollTop + height });
const shows = (b, at, height, scrollTop) => at >= scrollTop && at + height <= scrollTop + b.clientHeight;

test("the newest row in the middle of the window ends at the bottom of the box", () => {
  // correction: the newest row is followed by later rows, and the box sits far down the page
  // (one-column layout), so a position from the page's top would overshoot by a thousand pixels
  for (const top of [0, 250, 1000, -300]) {
    for (const scrollTop of [0, 2000, 5600]) {
      const b = box(top, scrollTop);
      const target = scrollToShow(b, row(b, 3000));
      assert.equal(target, 3000 + 60 - 400 + 8);
      assert.ok(shows(b, 3000, 60, target));
    }
  }
});

test("the last row is shown at the end, clamped to the scrollable height", () => {
  const b = box(1000, 0, 400, 3068);
  assert.equal(scrollToShow(b, row(b, 3000, 60)), 3068 - 400);
});

test("a row taller than the box is shown from its start", () => {
  const b = box(120, 0);
  assert.equal(scrollToShow(b, row(b, 3000, 500)), 3000 - 8);
});

test("the first row is never scrolled above zero", () => {
  const b = box(120, 900);
  assert.equal(scrollToShow(b, row(b, 0, 60)), 0);
});

test("the reader's place is kept when the rows are rebuilt", () => {
  const before = box(700, 1210);
  const rows = [row(before, 1100, 60, "10"), row(before, 1160, 60, "11"), row(before, 1220, 60, "12")];
  const anchor = anchorOf(before, rows);
  assert.deepEqual(anchor, { key: "11", offset: 1160 - 1210 });
  // a correction made the rows above taller: row 11 now starts 140 pixels further down
  const after = box(700, 1210);
  const target = scrollToKeep(after, row(after, 1300, 60, "11"), anchor.offset);
  assert.equal(target, 1300 - (1160 - 1210));
});

test("the page's own scrolling, up or down, never stops the following", () => {
  const f = new Follower();
  f.input(1000); // the reader just touched the text
  f.placed(5200);
  assert.equal(f.scrolled({ scrollTop: 5200, clientHeight: 400, scrollHeight: 6000 }, 1100, true), false);
  f.placed(2700); // the next correction block: the newest row is above the old position
  assert.equal(f.scrolled({ scrollTop: 2700, clientHeight: 400, scrollHeight: 6000 }, 1200, true), false);
  assert.equal(f.following, true);
});

test("the reader scrolling up stops the following; the newest text at the bottom resumes it", () => {
  const f = new Follower();
  f.placed(2700);
  f.input(5000);
  assert.equal(f.scrolled({ scrollTop: 2400, clientHeight: 400, scrollHeight: 6000 }, 5050, true), true);
  assert.equal(f.following, false);
  // down to the bottom of a window with newer text after it: still not following
  f.input(6000);
  assert.equal(f.scrolled({ scrollTop: 5600, clientHeight: 400, scrollHeight: 6000 }, 6050, true), false);
  // down to the bottom of the newest text: following again
  assert.equal(f.scrolled({ scrollTop: 5600, clientHeight: 400, scrollHeight: 6000 }, 6100, false), true);
  assert.equal(f.following, true);
});

test("a scroll long after any input is not the reader's (the browser clamping the box)", () => {
  const f = new Follower();
  f.placed(2700);
  f.input(1000);
  assert.equal(f.scrolled({ scrollTop: 2200, clientHeight: 400, scrollHeight: 2600 }, 9000, true), false);
  assert.equal(f.following, true);
});

test("jump to latest: the wheel just before the click does not undo it", () => {
  const f = new Follower();
  f.placed(2668);
  f.input(1000);
  f.scrolled({ scrollTop: 300, clientHeight: 400, scrollHeight: 6000 }, 1010, true); // the reader went up
  assert.equal(f.following, false);
  f.input(1400); // ... then down past the newest row, then clicked the button
  f.jump();
  assert.equal(f.following, true);
  f.placed(2668); // the page scrolls up to the newest row
  assert.equal(f.scrolled({ scrollTop: 2668, clientHeight: 400, scrollHeight: 6000 }, 1500, true), false);
  // the window is rebuilt around the newest row and the browser settles one pixel off
  assert.equal(f.scrolled({ scrollTop: 2600, clientHeight: 400, scrollHeight: 6000 }, 1550, true), false);
  assert.equal(f.following, true);
});

test("reading earlier text, the end of the rows loads the next ones (no 'show later' button)", () => {
  const end = { scrollTop: 5600, clientHeight: 400, scrollHeight: 6000 };
  const middle = { scrollTop: 3000, clientHeight: 400, scrollHeight: 6000 };
  assert.equal(needsLater(end, false, true), true); // at the end, newer rows exist
  assert.equal(needsLater(middle, false, true), false); // not at the end yet
  assert.equal(needsLater(end, false, false), false); // nothing newer: following resumes instead
  assert.equal(needsLater(end, true, true), false); // following: the window is the newest already
});
