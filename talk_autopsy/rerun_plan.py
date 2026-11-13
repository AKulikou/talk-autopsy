"""
What a run will really do, read from the cache before it starts (no API call):
which steps come from the cache, how many correction and translation blocks
will be sent, whether the MP4 outputs will be reused, and what that costs.

The estimate screen used the price of a full run even when nearly everything
was cached (0.55 USD shown for a re-run that cost 0.11; bug, 2026-11-13). It
overstated exactly the case where a re-run is useful, and for a long video it
could ask for the over-limit confirmation for nothing.

How: the pipeline skips a step when its output's stamp equals the step's
inputs (`pipeline.up_to_date`), and sends only the blocks whose request would
change (`pipeline.reusable_answers`). This module computes the same inputs
from the files the earlier run left, step by step, with the pipeline's own
functions. `tests/test_rerun_plan.py` checks that right after a real pipeline
run every step is planned as cached, so the two cannot drift apart silently.

Where the plan cannot know: once a step will run, the steps after it get new
inputs. Before the correction runs nobody knows which translation blocks its
new text will change, so the translation counts in full and the total is an
upper bound (`upper`, reason `translation_after_correction`). When the speech
recognition or the audio would run again (nothing cached, or another source
file), there is no plan: the usual per-hour estimate applies.

Speech recognition is priced as two equal passes (the first pass and the
pass with key terms) of the per-hour estimate; Claude steps by the stages'
own estimators on the cached transcript, scaled by the share of blocks sent.
"""

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from talk_autopsy import blocks, correct, glossary_auto, outputs, pipeline, translate, units, youtube
from talk_autopsy.glossary import Glossary, load_glossary
from talk_autopsy.segments import load_transcript
from talk_autopsy.stt.chunked import file_sha256

logger = logging.getLogger(__name__)


@dataclass
class RerunPlan:
    cached: list[str] = field(default_factory=list)  # from the cache: "download", pipeline steps, "outputs"
    claude_usd: float = 0.0
    stt_usd: float = 0.0
    upper: bool = False  # the total is "up to": part of it depends on what an earlier step will produce
    reasons: list[str] = field(default_factory=list)  # why it is an upper bound (codes)
    blocks: dict[str, list[int]] = field(default_factory=dict)  # step -> [blocks to send, blocks]

    @property
    def total_usd(self) -> float:
        return self.claude_usd + self.stt_usd

    def as_dict(self) -> dict:
        return {"cached": self.cached, "upper": self.upper, "reasons": self.reasons, "blocks": self.blocks}


def _context(work: Path, extra: str) -> blocks.VideoContext:
    """The context the Claude steps will see: meta.json, with the run's own description (pipeline.run)."""
    meta_path = work / "meta.json"
    meta = (json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file()
            else {"title": "", "description": "", "chapters": []})
    if extra:
        meta["description"] = extra
    chapters = [c.get("title", "") if isinstance(c, dict) else str(c) for c in meta.get("chapters") or []]
    return blocks.VideoContext(title=meta.get("title") or "", description=meta.get("description") or "",
                               chapters=[c for c in chapters if c])


def _blocks_to_send(output: Path, inputs: dict, stage: str, current, step_plan) -> tuple[int, int]:
    """(blocks the step will send, blocks in all) for a step that is not up to date."""
    parts = step_plan(current)
    earlier = pipeline.reusable_answers(output, inputs, stage, current, {inputs["input"]: current}, step_plan)
    done = earlier[0] if earlier else {}
    send = sum(1 for block, _ in parts if not all(s.id in done for s in block.segments))
    return send, len(parts)


def plan(settings: pipeline.RunSettings, fresh: youtube.Estimate, formats: list[str], dest: Path,
         stem: str) -> RerunPlan | None:
    """
    The plan of a run of the web page's steps (stt, glossary, stt2, align, fix,
    translate, srt; engine and models from `settings`), or None when nothing
    usable is cached. `fresh` is the per-hour estimate of a full run.
    """
    work, source = settings.work_dir, Path(settings.source)
    if not (work.is_dir() and source.is_file()):
        return None
    lang = settings.target_lang
    audio, raw, raw2 = work / "audio.wav", work / "raw.json", work / "raw2.json"
    aligned, fixed, translated = work / "aligned.json", work / "fixed.json", work / f"{lang}.json"
    glossary_path = work / "glossary.md"
    result = RerunPlan()
    if (dest / f"{stem}.mp4").is_file():
        result.cached.append("download")

    # speech recognition: without the cached first pass there is nothing to plan on
    source_stamp = {"source": str(source.resolve()), "size": str(source.stat().st_size),
                    "mtime": str(source.stat().st_mtime_ns)}
    if not pipeline.up_to_date(audio, source_stamp):
        return None
    audio_hash = file_sha256(audio)
    inputs = {"audio": audio_hash, "engine": settings.engine,
              "gemini_cache_only": str(settings.gemini_cache_only), "code": pipeline.code_hash("stt")}
    if not pipeline.up_to_date(raw, inputs):
        return None
    result.cached.append("stt")
    stt_pass = fresh.stt_usd / 2

    user_glossary = load_glossary(settings.glossary) if settings.glossary else None
    user_hash = (pipeline._text_hash(Path(settings.glossary).read_text(encoding="utf-8"))
                 if settings.glossary else "")
    context = _context(work, settings.context)
    context_hash = pipeline._text_hash(context.text())
    raw_transcript = load_transcript(raw)

    inputs = {"raw": file_sha256(raw), "context": pipeline._text_hash(settings.context),
              "user_glossary": user_hash, "model": settings.helper_model, "lang": lang,
              "prompt": glossary_auto.PROMPT_VERSION,
              "code": pipeline.code_hash("glossary", glossary_auto.PROMPT_VERSION)}
    chain_ok = pipeline.up_to_date(glossary_path, inputs)  # every step so far comes from the cache
    if chain_ok:
        result.cached.append("glossary")
        glossary_now = load_glossary(glossary_path)
        glossary_hash = pipeline._text_hash(glossary_path.read_text(encoding="utf-8"))
    else:
        result.claude_usd += glossary_auto.estimate(raw_transcript, lang, settings.context,
                                                    settings.helper_model).usd
        glossary_now, glossary_hash = user_glossary or Glossary(), ""

    if chain_ok:
        inputs = {"audio": audio_hash, "raw": file_sha256(raw), "glossary": glossary_hash,
                  "engine": settings.engine, "code": pipeline.code_hash("stt2"), "user_glossary": user_hash}
        chain_ok = pipeline.up_to_date(raw2, inputs)
    if chain_ok:
        result.cached.append("stt2")
        inputs = {"input": file_sha256(raw2), "audio": audio_hash, "code": pipeline.code_hash("align")}
        chain_ok = pipeline.up_to_date(aligned, inputs)
    else:
        result.stt_usd += stt_pass
    if chain_ok:
        result.cached.append("align")

    english = load_transcript(aligned) if chain_ok else raw_transcript
    full_fix = correct.estimate(english, glossary_now, context, settings.correction_model).usd
    if chain_ok:
        inputs = {"input": file_sha256(aligned), "glossary": glossary_hash, "context": context_hash,
                  "model": settings.correction_model, "prompt": correct.PROMPT_VERSION,
                  "code": pipeline.code_hash("fix", correct.PROMPT_VERSION)}
        if pipeline.up_to_date(fixed, inputs):
            result.cached.append("fix")
        else:
            send, total = _blocks_to_send(fixed, inputs, "correction", english, pipeline._correction_plan)
            result.blocks["fix"] = [send, total]
            result.claude_usd += full_fix * send / max(total, 1)
            chain_ok = False
    else:
        result.claude_usd += full_fix

    version = settings.translation_version
    base = load_transcript(fixed) if "fix" in result.cached else english
    full_translate = translate.estimate(base, glossary_now, lang, context, settings.translation_model,
                                        version=version).usd
    if "fix" in result.cached and version != translate.V3:
        inputs = {"input": file_sha256(fixed), "glossary": glossary_hash, "context": context_hash,
                  "lang": lang, "model": settings.translation_model, "prompt": version,
                  "units": units.VERSION, "code": pipeline.code_hash("translate", version)}
        if pipeline.up_to_date(translated, inputs):
            result.cached.append("translate")
        else:
            send, total = _blocks_to_send(translated, inputs, "translation", base, pipeline._translation_plan)
            result.blocks["translate"] = [send, total]
            result.claude_usd += full_translate * send / max(total, 1)
    else:
        result.claude_usd += full_translate
        if result.blocks.get("fix"):
            result.upper = True  # which translation blocks the new correction changes is known only after it
            result.reasons.append("translation_after_correction")

    if "translate" in result.cached and _outputs_reused(settings, formats, dest, stem, source):
        result.cached.append("outputs")
    result.claude_usd = round(result.claude_usd, 4)
    result.stt_usd = round(result.stt_usd, 4)
    return result


def _outputs_reused(settings: pipeline.RunSettings, formats: list[str], dest: Path, stem: str,
                    video: Path) -> bool:
    """With the subtitles cached, every MP4 asked for would be reused (outputs.up_to_date)."""
    mp4 = [f for f in formats if f in ("mp4-soft", "mp4-burned")]
    if not mp4:
        return True
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return False
    work, lang, vid = settings.work_dir, settings.target_lang, settings.video_id
    target_srt, english_srt = work / f"{vid}.{lang}.srt", work / f"{vid}.en.srt"
    target_ass = target_srt.with_suffix(".ass")
    for fmt in mp4:
        if fmt == "mp4-soft":
            if not (target_srt.is_file() and english_srt.is_file()):
                return False
            wanted = outputs.recipe(fmt, ffmpeg, video, [target_srt, english_srt], outputs.soft_command(
                "ffmpeg", Path("<video>"), Path("<target.srt>"), Path("<english.srt>"), Path("<out>"), lang))
            out = dest / f"{stem}.subs.mp4"
        else:
            if not target_ass.is_file():
                return False
            wanted = outputs.recipe(fmt, ffmpeg, video, [target_ass], outputs.burn_command(
                "ffmpeg", Path("<video>"), outputs.BURN_FILTER_NAME, Path("<out>")))
            out = dest / f"{stem}.{lang}.mp4"
        if not outputs.up_to_date(out, wanted):
            return False
    return True
