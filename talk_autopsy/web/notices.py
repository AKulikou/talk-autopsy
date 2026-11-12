"""
Core messages -> what the page may show (v1.1 session 4, after the owner's first real run).

The core speaks English to developers ("....ru.srt + .ass: 191 cues; reading
speed max 49.2 cps, 27.2% above 17 cps...", "mp4-burned: drawing the ru
subtitles into the picture (H.264 re-encode)"). The page never shows such a line
in its main view: a known kind of message gets a `message` key and `params`
that the page turns into a short sentence in the interface language; the
original line stays in `text`, which the page shows only under "Technical
details". `kind` is "info" for all of them: none asks the user to act (the one
real warning, a part of the video left untranslated, is not a notice; see
`failed_ranges`).
"""

import re

_SUBTITLES = re.compile(r"^(?:.*?\.)?(?P<lang>[A-Za-z]{2,3}(?:-[A-Za-z]+)?)\.srt \+ \.ass: (?P<cues>\d+) cues"
                        r"(?:; reading speed max [\d.]+ cps, (?P<fast>[\d.]+)% above 17 cps)?")
FAST_SHARE_SHOWN = 20.0  # percent of cues above 17 characters per second worth a word to the viewer


def describe(code: str, text: str) -> dict:
    """{"message": key or None, "params": {...}, "kind": "info"} for a notice's code and core text."""
    text = text or ""
    if code == "srt":
        match = _SUBTITLES.match(text)
        if match:
            fast = float(match.group("fast")) if match.group("fast") else 0.0
            return {"message": "notice_subtitles_fast" if fast >= FAST_SHARE_SHOWN else "notice_subtitles",
                    "params": {"lang": match.group("lang"), "cues": int(match.group("cues")),
                               "fast": round(fast)},
                    "kind": "info"}
    elif code == "outputs":
        if text.startswith("mp4-burned:"):
            return {"message": "notice_burn_in", "params": {}, "kind": "info"}
        if text.startswith("mp4-soft:"):
            return {"message": "notice_soft_tracks", "params": {}, "kind": "info"}
    elif code == "fallback":
        return {"message": "notice_fallback", "params": {}, "kind": "info"}
    elif code == "progress_display_error":
        return {"message": "notice_display_error", "params": {}, "kind": "info"}
    elif code == "text_truncated":
        return {"message": "notice_truncated", "params": {}, "kind": "info"}
    return {"message": None, "params": {}, "kind": "info"}


def merge_ranges(ranges: list[tuple[float, float]], gap: float = 2.0) -> list[list[float]]:
    """Time ranges sorted and joined where they touch (within `gap` seconds), rounded to 0.1 s."""
    out: list[list[float]] = []
    for start, end in sorted((float(s), float(max(s, e))) for s, e in ranges):
        if out and start - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], end)
        else:
            out.append([start, end])
    return [[round(s, 1), round(e, 1)] for s, e in out]


def failed_ranges(transcript, stage: str) -> list[list[float]]:
    """
    The time ranges of a result's failed blocks (`meta.<stage>.failed_blocks`,
    stage "translation" or "correction"), from the times of their segments.
    """
    if transcript is None:
        return []
    failed = {int(i) for b in (transcript.meta.get(stage) or {}).get("failed_blocks") or []
              for i in b.get("ids") or []}
    return merge_ranges([(s.start, s.end) for s in transcript.segments if s.id in failed])
