// The Done screen's player and the start page's recent results. The server picks what to play
// (a video without burned-in subtitles when there is one) and serves only files inside the output
// folder; subtitles come as WebVTT converted from the result's .srt. An audio result shows its
// text beside the player, following the audio.
import { api } from "./api.js";
import { t } from "./i18n.js";

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
  els.box.hidden = true;
  els.note.hidden = true;
  els.none.hidden = true;
}

export function show(els, info) {
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
    els.note.textContent = t("player_burned");
    els.note.hidden = false;
  }
  if (info.kind === "audio" && trackElements.length) followText(els, media, trackElements[0]);
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
