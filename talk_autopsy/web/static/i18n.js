// Interface strings from /api/config (strings/<lang>.json on the server) and number formats.
let strings = {};

export function setStrings(next) {
  strings = next || {};
}

export function t(key, vars = {}) {
  let text = strings[key] ?? key;
  for (const [name, value] of Object.entries(vars)) text = text.replaceAll(`{${name}}`, String(value));
  return text;
}

export function applyStrings(root = document) {
  root.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  root.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
}

// A name used in mid-sentence: first letter in lower case, unless it starts an acronym ("MP4").
export function midSentence(name) {
  if (!name) return "";
  const [first, second] = [...name];
  if (second && second === second.toUpperCase() && second !== second.toLowerCase()) return name;
  return first.toLowerCase() + name.slice(first.length);
}

// "not counting: download from YouTube" for the steps a time estimate leaves out, or "".
export function excludedSteps(excludes) {
  if (!excludes?.length) return "";
  const names = [...new Set(excludes.map((s) => t(`step_${s.startsWith("outputs") ? "outputs" : s}`)))];
  return t("est_time_excludes", { steps: names.map(midSentence).join(", ") });
}

// An error from the API in words: the string for its code, else the server's own short message.
export function errorText(data) {
  const code = data?.error;
  const key = `err_${code}`;
  if (code && strings[key]) return t(key);
  return data?.message || t("err_internal");
}

// 75 -> "1 min 15 s", 3700 -> "1 h 1 min"
export function duration(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return `${h} ${t("unit_h")} ${m} ${t("unit_min")}`;
  if (m) return m < 10 && s % 60 ? `${m} ${t("unit_min")} ${s % 60} ${t("unit_s")}` : `${m} ${t("unit_min")}`;
  return `${s} ${t("unit_s")}`;
}

// 75 -> "1:15", 3700 -> "1:01:40" (a running clock)
export function clock(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(s / 3600);
  const mm = String(Math.floor((s % 3600) / 60)).padStart(h ? 2 : 1, "0");
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

// [[0, 299.1], [600, 640]] -> "0:00–4:59, 10:00–10:40"
export function ranges(list) {
  return (list || []).map(([a, b]) => `${clock(a)}–${clock(b)}`).join(", ");
}

// The plain-language warning about parts of the video left as they were (failed blocks), or "".
export function gapsWarning(untranslated, uncorrected) {
  const parts = [];
  if (untranslated?.length) parts.push(t("warn_untranslated", { ranges: ranges(untranslated) }));
  if (uncorrected?.length) parts.push(t("warn_uncorrected", { ranges: ranges(uncorrected) }));
  if (!parts.length) return "";
  // A part is left as it was only when the model's answer failed the checks twice (an API or network
  // failure stops the whole run instead), so a re-run would most likely fail the same way and cost
  // money again: say what happened, suggest nothing (owner's bug report, 2026-11-13).
  parts.push(t("warn_checked"));
  return parts.join(" ");
}
