// Which subtitle track the Done screen's player shows (used by player.js). Kept free of the DOM so
// it can be tested on its own (tests/js/subs.test.mjs).
//
// The default is the target language (none when the translation is burned into the picture). The
// page's buttons and the browser's own subtitle menu both change the tracks' modes; the buttons
// follow the menu. But the browser also changes modes by itself while the video and the tracks
// load (its automatic track selection, resets on load). Before everything has loaded such a change
// is not the reader's: the page puts its choice back. Before this rule a reset during loading was
// taken for the reader choosing "off" (the owner's second check, 2026-11-13: a video with the
// target track offered opened with "Off" selected).

export class SubtitleChoice {
  /** chosen: index of the track to show, -1 for off. */
  constructor(chosen) {
    this.chosen = chosen;
    this.settled = false; // the video's metadata and every track have loaded
  }

  /** The reader pressed a page button. */
  pick(index) {
    this.chosen = index;
  }

  /** Loading finished (the media's metadata and every track element). */
  settle() {
    this.settled = true;
  }

  /**
   * The tracks' modes changed (modes: "showing" / "hidden" / "disabled" per track, in order).
   * Returns "apply" when the page must put its choice back (a change while loading), "follow"
   * when the buttons now follow a choice made in the browser's menu, or "none".
   */
  changed(modes) {
    const showing = modes.indexOf("showing");
    if (showing === this.chosen && modes.filter((m) => m === "showing").length <= 1) return "none";
    if (!this.settled) return "apply";
    this.chosen = showing;
    return "follow";
  }
}

/**
 * The track to show first when the reader chose before (a course: the choice is kept from one
 * lesson to the next). preferred: "target" | "en" | "off" | null. tracks: [{srclang, default}].
 * Without a preference: the target language (the default track), off when burned in.
 */
export function initialTrack(tracks, burned, preferred) {
  const target = tracks.findIndex((tr) => tr.default);
  const english = tracks.findIndex((tr) => tr.srclang === "en");
  if (preferred === "off") return -1;
  if (preferred === "en" && english >= 0) return english;
  if (preferred === "target" && !burned && target >= 0) return target;
  return burned ? -1 : Math.max(0, target);
}

/** The preference to remember for a chosen track index. */
export function preferenceOf(tracks, index) {
  if (index < 0) return "off";
  return tracks[index]?.srclang === "en" ? "en" : "target";
}
