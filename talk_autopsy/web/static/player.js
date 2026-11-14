// The Done screen's player and the start page's recent results. The server picks what to play
// (a video without burned-in subtitles when there is one) and serves only files inside the output
// folder; subtitles come as WebVTT converted from the result's .srt. An audio result shows its
// text beside the player, following the audio. A video gets its own subtitle buttons under the
// player (target language / English / off), kept in step with the browser's own subtitle menu in
// both directions: a choice in either shows in the other (the tracks' "change" event). A video with
// the translation burned in gets no target-language track (the server leaves it out): English or
// off only, with one line saying the translation is already in the picture.
import { api } from "./api.js";
import { t } from "./i18n.js";
import { SubtitleChoice, initialTrack, preferenceOf } from "./subs.js";

const enc = encodeURIComponent;

const node = (tag, className, text) => {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
};

export async function fetchResult(id) {
  const { ok, data } = await api("GET", `/api/results/${enc(id)}`);
  return ok ? data : null;
}

export async function fetchRecent() {
  const { ok, data } = await api("GET", "/api/results");
  return ok ? data.results : [];
}

// Stop and remove any player (leaving the Done screen must not keep a video playing).
export function clear(els) {
  for (const media of els.media.querySelectorAll("video, audio")) {
    media.pause();
    media.removeAttribute("src");
    media.load();
  }
  els.media.replaceChildren();
  els.text.replaceChildren();
  els.text.hidden = true;
  els.subs.hidden = true;
  els.subsButtons.replaceChildren();
  els.subsState.textContent = "";
  els.box.hidden = true;
  els.note.hidden = true;
  els.none.hidden = true;
}

// opts.preferred: the subtitle choice to start with ("target" / "en" / "off"; a course keeps it from one
// lesson to the next); opts.onChoice(preference) hears every choice.
export function show(els, info, opts = {}) {
  clear(els);
  if (!info || !info.media) {
    els.none.hidden = false;
    return;
  }
  const base = `/api/results/${enc(info.id)}`;
  const media = document.createElement(info.kind === "audio" ? "audio" : "video");
  media.controls = true;
  media.preload = "metadata";
  media.src = `${base}/media/${enc(info.media)}`;
  const trackElements = info.tracks.map((tr) => {
    const track = document.createElement("track");
    track.kind = "subtitles";
    track.srclang = tr.srclang;
    track.label = tr.label;
    track.src = `${base}/vtt/${enc(tr.file)}`;
    if (tr.default || (info.kind === "audio" && tr === info.tracks[0])) track.default = true;
    media.append(track);
    return track;
  });
  els.media.append(media);
  els.box.hidden = false;
  if (info.burned) {
    const lang = info.burned_lang || info.target_lang;
    const name = lang ? t(`langname_${lang}`) : "";
    els.note.textContent = t("player_burned", { lang: name.startsWith("langname_") ? lang : name });
    els.note.hidden = false;
  }
  if (info.kind === "audio" && trackElements.length) followText(els, media, trackElements[0]);
  else if (trackElements.length) subtitleButtons(els, media, info, trackElements, opts);
}

// Subtitle choice under a video: one button per track and "off". A button sets the tracks' modes;
// the browser's own menu sets them too, so the buttons and the words next to them follow the modes
// whoever changed them (bug: the page said "off" while the browser's menu showed English).
function subtitleButtons(els, media, info, trackElements, opts = {}) {
  const names = info.tracks.map((tr) => (tr.srclang === "en" ? t("subs_english") : tr.label || tr.srclang));
  const choices = [...names.map((name, index) => ({ name, index })), { name: t("subs_off"), index: -1 }];
  // the target language by default (the server marks it), off when the translation is burned in
  const choice = new SubtitleChoice(initialTrack(info.tracks, info.burned, opts.preferred || null));
  const buttons = choices.map((c) => {
    const b = node("button", "secondary", c.name);
    b.type = "button";
    b.addEventListener("click", () => {
      choice.pick(c.index);
      apply();
    });
    return b;
  });
  const showState = () => {
    const chosen = choice.chosen;
    buttons.forEach((b, k) => b.setAttribute("aria-pressed", String(choices[k].index === chosen)));
    els.subsState.textContent = chosen < 0 ? t("subs_state_off") : t("subs_state_on", { name: names[chosen] });
    opts.onChoice?.(preferenceOf(info.tracks, chosen));
  };
  const apply = () => {
    trackElements.forEach((el, k) => { el.track.mode = k === choice.chosen ? "showing" : "disabled"; });
    showState();
  };
  els.subsButtons.replaceChildren(...buttons);
  els.subs.hidden = false;
  apply();
  // the browser changes modes by itself while the media and the tracks load: put the choice back,
  // and count the browser's menu as the reader's only once everything has loaded (subs.js)
  const loaded = () => media.readyState >= 1 && trackElements.every((el) => el.readyState >= 2);
  const onLoad = () => {
    apply();
    if (loaded()) choice.settle();
  };
  media.addEventListener("loadedmetadata", onLoad);
  for (const el of trackElements) el.addEventListener("load", onLoad);
  media.textTracks.addEventListener("change", () => {
    const action = choice.changed(trackElements.map((el) => el.track.mode));
    if (action === "apply") apply();
    else if (action === "follow") showState();
  });
}

// Audio: the subtitle cues as a list beside the player; the current one is marked and kept in view.
function followText(els, media, trackElement) {
  const track = trackElement.track;
  track.mode = "hidden"; // load the cues without drawing them
  const items = new Map();
  const build = () => {
    if (!track.cues || items.size) return;
    for (const cue of track.cues) {
      const li = node("li", null, cue.text.replace(/<[^>]+>/g, ""));
      li.addEventListener("click", () => {
        media.currentTime = cue.startTime;
        media.play();
      });
      items.set(cue, li);
      els.text.append(li);
    }
    els.text.hidden = false;
  };
  trackElement.addEventListener("load", build);
  media.addEventListener("loadedmetadata", build);
  track.addEventListener("cuechange", () => {
    build();
    for (const [cue, li] of items) li.classList.toggle("active", Boolean([...track.activeCues].includes(cue)));
    const active = els.text.querySelector(".active");
    if (active) active.scrollIntoView({ block: "nearest" });
  });
}
