// Interface wording helpers (talk_autopsy/web/static/i18n.js), run by tests/test_web_follow.py with
// `node --test`; built-in modules only, loaded from data URLs.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = (path) => readFileSync(new URL(`../../talk_autopsy/web/static/${path}`, import.meta.url), "utf-8");
const dataUrl = (source) => `data:text/javascript,${encodeURIComponent(source)}`;
const i18n = await import(dataUrl(read("i18n.js")));
const strings = (lang) => JSON.parse(read(`strings/${lang}.json`));

test("the steps an estimate leaves out are named in mid-sentence, in lower case", () => {
  i18n.setStrings(strings("ru"));
  assert.equal(i18n.excludedSteps(["download", "outputs_burn"]), "без учёта: загрузка с YouTube, файлы (и видео для ТВ)");
  i18n.setStrings(strings("en"));
  assert.equal(i18n.excludedSteps(["download"]), "not counting: download from YouTube");
  i18n.setStrings(strings("lt"));
  assert.ok(!/: \p{Lu}/u.test(i18n.excludedSteps(["download"])));
  assert.equal(i18n.excludedSteps([]), "");
});

test("an acronym at the start of a step name keeps its capitals", () => {
  assert.equal(i18n.midSentence("MP4 files"), "MP4 files");
  assert.equal(i18n.midSentence("Перевод"), "перевод");
  assert.equal(i18n.midSentence(""), "");
});
