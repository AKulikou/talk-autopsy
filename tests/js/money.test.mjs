// How money is written (talk_autopsy/web/static/money.js), in every interface language, run by
// tests/test_web_follow.py with `node --test`; built-in modules only, loaded from data URLs.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const read = (path) => readFileSync(new URL(`../../talk_autopsy/web/static/${path}`, import.meta.url), "utf-8");
const dataUrl = (source) => `data:text/javascript,${encodeURIComponent(source)}`;
const i18nUrl = dataUrl(read("i18n.js"));
const i18n = await import(i18nUrl);
const { amount, cachedParts, money } = await import(dataUrl(read("money.js").replace("./i18n.js", i18nUrl)));
const strings = (lang) => JSON.parse(read(`strings/${lang}.json`));

test("the owner's line, word for word, in English and Russian", () => {
  i18n.setStrings(strings("en"));
  assert.equal(money({ claude: 0.4, stt: 0 }, { cached: { stt: true } }),
    "Spent: 0.40 USD — Claude 0.40 + speech recognition 0.00 (from cache)");
  i18n.setStrings(strings("ru"));
  assert.equal(money({ claude: 0.4, stt: 0 }, { cached: { stt: true } }),
    "Потрачено: 0,40 USD — на Claude 0,40 + на распознавание речи 0,00 (из кэша)");
});

test("Lithuanian: decimal comma, the same order", () => {
  i18n.setStrings(strings("lt"));
  assert.equal(money({ claude: 1.234, stt: 0.06 }),
    "Išleista: 1,29 USD — Claude 1,23 + kalbos atpažinimui 0,06");
});

test("below one cent is '< 0.01', zero is 0.00, in every language", () => {
  for (const [lang, point] of [["en", "."], ["ru", ","], ["lt", ","]]) {
    i18n.setStrings(strings(lang));
    assert.equal(amount(0.004), `< 0${point}01`);
    assert.equal(amount(0), `0${point}00`);
    assert.equal(amount(0.01), `0${point}01`);
    assert.equal(amount(12.345), `12${point}35`);
  }
});

test("'from cache' only after a part that cost nothing", () => {
  i18n.setStrings(strings("en"));
  assert.equal(money({ claude: 0.02, stt: 0 }, { cached: { claude: true, stt: false } }),
    "Spent: 0.02 USD — Claude 0.02 + speech recognition 0.00");
  assert.equal(money({ claude: 0, stt: 0 }, { cached: { claude: true, stt: true }, prefix: "" }),
    "0.00 USD — Claude 0.00 (from cache) + speech recognition 0.00 (from cache)");
});

test("a step shows only its own service; an estimate can be an upper bound", () => {
  i18n.setStrings(strings("en"));
  assert.equal(money({ stt: 0.0068 }), "Spent: < 0.01 USD — speech recognition < 0.01");
  assert.equal(money({ claude: 0 }, { cached: { claude: true } }), "Spent: 0.00 USD — Claude 0.00 (from cache)");
  i18n.setStrings(strings("ru"));
  assert.equal(money({ claude: 0.31, stt: 0 }, { cached: { stt: true }, prefix: "upper" }),
    "до 0,31 USD — на Claude 0,31 + на распознавание речи 0,00 (из кэша)");
});

test("a service counts as cached only when every paid step of it came from the cache", () => {
  assert.deepEqual(cachedParts({ stt: true, stt2: true, glossary: true, fix: false }),
    { claude: false, stt: true });
  assert.deepEqual(cachedParts({}), { claude: false, stt: false });
});
