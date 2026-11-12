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

export function usd(value, digits = 2) {
  return `${(Number(value) || 0).toFixed(digits)} USD`;
}
