// How money is written on every screen (owner, 2026-11-13): the total first, then each service;
// two decimals, with the decimal comma where the language uses one; an amount above zero but below
// one cent is "< 0.01"; "(from cache)" after a part that cost nothing because its steps were reused.
//   Spent: 0.40 USD — Claude 0.40 + speech recognition 0.00 (from cache)
// web/money.py writes the console's line by the same rule. DOM-free: tests/js/money.test.mjs.
import { t } from "./i18n.js";

export const CLAUDE_STEPS = ["glossary", "fix", "translate"];
export const STT_STEPS = ["stt", "stt2"];

// 0.4 -> "0.40" ("0,40" in ru and lt), 0.004 -> "< 0.01", 0 -> "0.00"
export function amount(value) {
  const v = Number(value) || 0;
  const below = v > 0 && v < 0.01;
  const point = t("num_decimal");
  const text = (below ? 0.01 : v).toFixed(2).replace(".", point === "num_decimal" ? "." : point);
  return below ? `< ${text}` : text;
}

export function usdAmount(value) {
  return `${amount(value)} USD`;
}

// The service a step is paid to ("claude", "stt"), or null for a free step.
export function stepService(step) {
  if (CLAUDE_STEPS.includes(step)) return "claude";
  if (STT_STEPS.includes(step)) return "stt";
  return null;
}

// Per service: every paid step of it that finished came from the cache. `paid`: step -> skipped.
export function cachedParts(paid) {
  const reused = (steps) => {
    const done = steps.filter((s) => s in paid).map((s) => paid[s]);
    return done.length > 0 && done.every(Boolean);
  };
  return { claude: reused(CLAUDE_STEPS), stt: reused(STT_STEPS) };
}

// parts: {claude, stt}; a part left out (undefined or null) is not shown.
// prefix: "spent" ("Spent: …"), "upper" ("up to …") or "" (the line alone, under its own label).
export function money(parts, { cached = {}, prefix = "spent" } = {}) {
  const shown = [];
  let total = 0;
  for (const [key, name] of [["claude", "money_claude"], ["stt", "money_stt"]]) {
    if (parts[key] === undefined || parts[key] === null) continue;
    const value = Number(parts[key]) || 0;
    total += value;
    const reused = cached[key] && !value ? ` ${t("money_from_cache")}` : "";
    shown.push(t(name, { amount: amount(value) }) + reused);
  }
  const text = t("money_line", { total: amount(total), parts: shown.join(" + ") });
  return prefix ? t(`money_${prefix}`, { text }) : text;
}
