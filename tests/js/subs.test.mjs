// The player's subtitle choice (talk_autopsy/web/static/subs.js), run by tests/test_web_follow.py
// with `node --test`; built-in modules only, loaded from a data URL (no package.json).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const source = readFileSync(new URL("../../talk_autopsy/web/static/subs.js", import.meta.url), "utf-8");
const { SubtitleChoice } = await import(`data:text/javascript,${encodeURIComponent(source)}`);

test("a reset by the browser while loading does not turn the default off", () => {
  const c = new SubtitleChoice(0); // the target language
  assert.equal(c.changed(["disabled", "disabled"]), "apply"); // the browser reset the modes on load
  assert.equal(c.chosen, 0);
  assert.equal(c.changed(["showing", "disabled"]), "none"); // the page's own apply
});

test("after loading, a choice in the browser's menu is followed", () => {
  const c = new SubtitleChoice(0);
  c.settle();
  assert.equal(c.changed(["disabled", "showing"]), "follow");
  assert.equal(c.chosen, 1);
  assert.equal(c.changed(["disabled", "disabled"]), "follow");
  assert.equal(c.chosen, -1);
});

test("a page button sets the choice; its own mode change needs nothing", () => {
  const c = new SubtitleChoice(0);
  c.settle();
  c.pick(1);
  assert.equal(c.changed(["disabled", "showing"]), "none");
  c.pick(-1);
  assert.equal(c.changed(["disabled", "disabled"]), "none");
});

test("a burned-in video starts off and stays off while loading", () => {
  const c = new SubtitleChoice(-1);
  assert.equal(c.changed(["showing"]), "apply"); // the browser's automatic selection
  assert.equal(c.chosen, -1);
});

test("two tracks showing at once while loading are put back to the one chosen", () => {
  const c = new SubtitleChoice(0);
  assert.equal(c.changed(["showing", "showing"]), "apply");
});
