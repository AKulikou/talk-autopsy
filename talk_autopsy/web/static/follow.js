// Following the newest text in the waiting screen's text box (used by transcript.js). Kept free
// of the DOM so it can be tested on its own (tests/js/follow.test.mjs).
//
// Positions are viewport rectangles (getBoundingClientRect), turned into positions inside the
// box here. A row's offsetTop is measured from its offset parent, which is not the box: in the
// one-column layout the box sits about a thousand pixels down the page, so a scroll position
// computed from offsetTop overshot and hid the newest row whenever it was not the last one
// (during correction and translation; bug report 2026-11-13).
//
// The reader stops the following by scrolling up; the page's own scrolling never does.

const MARGIN = 8; // pixels of space kept below the newest row
const INPUT_MS = 1500; // a scroll this soon after the reader's wheel, touch, key or pointer input is theirs

/**
 * The scroll position that shows `row` at the bottom of the box (its start, if it is taller
 * than the box). box: {top, scrollTop, clientHeight, scrollHeight}, top = the top edge of the
 * box's scrolling area in viewport coordinates; row: {top, bottom} in viewport coordinates.
 */
export function scrollToShow(box, row) {
  const top = row.top - box.top + box.scrollTop;
  const bottom = row.bottom - box.top + box.scrollTop;
  const target = bottom - top + 2 * MARGIN > box.clientHeight
    ? top - MARGIN
    : bottom - box.clientHeight + MARGIN;
  return clamp(target, box);
}

/** The first row the reader can see, and how far below the box's top it starts. */
export function anchorOf(box, rows) {
  for (const row of rows) {
    if (row.bottom > box.top) return { key: row.key, offset: row.top - box.top };
  }
  return null;
}

/** The scroll position that puts the anchored row back where the reader saw it. */
export function scrollToKeep(box, row, offset) {
  return clamp(box.scrollTop + (row.top - box.top) - offset, box);
}

/** The box is scrolled to its end (within a few pixels). */
export function atEnd(box) {
  return box.scrollTop + box.clientHeight >= box.scrollHeight - 12;
}

/**
 * The reader, reading earlier text, reached the end of the rows shown while newer rows exist:
 * the page loads the next rows itself (there is no "show later text" button; owner, 2026-11-13).
 */
export function needsLater(box, following, moreAfter) {
  return !following && moreAfter && atEnd(box);
}

function clamp(value, box) {
  return Math.max(0, Math.min(Math.round(value), Math.max(0, box.scrollHeight - box.clientHeight)));
}

/** Whether the page follows the newest text, from the scroll events and the reader's input. */
export class Follower {
  constructor() {
    this.following = true;
    this.userInput = -Infinity;
    this.placedAt = null; // where the page itself last scrolled the box
    this.lastTop = 0;
  }

  /** The reader touched the text (wheel, touch, key, pointer). */
  input(now) {
    this.userInput = now;
  }

  /** "Jump to latest": follow again; the input that led to the click does not count as scrolling. */
  jump() {
    this.following = true;
    this.userInput = -Infinity;
  }

  /** The reader chose to read earlier text. */
  stop() {
    this.following = false;
  }

  /** The page set the box's scroll position itself. */
  placed(scrollTop) {
    this.placedAt = scrollTop;
    this.lastTop = scrollTop;
  }

  /**
   * A scroll event. box: {scrollTop, clientHeight, scrollHeight}; moreAfter: newer text exists
   * beyond the rows shown. Returns true when `following` changed.
   */
  scrolled(box, now, moreAfter) {
    const before = this.following;
    const ours = this.placedAt !== null && Math.abs(box.scrollTop - this.placedAt) <= 1;
    if (ours) {
      this.placedAt = null;
    } else if (now - this.userInput <= INPUT_MS) {
      if (this.following && box.scrollTop < this.lastTop - 4) this.following = false;
      else if (!this.following && atEnd(box) && !moreAfter) this.following = true;
    }
    this.lastTop = box.scrollTop;
    return this.following !== before;
  }
}
