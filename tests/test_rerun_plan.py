"""
The cache-aware estimate (talk_autopsy/rerun_plan.py): what a run will really do, read from the cache
before it starts (bug, 2026-11-13: the page showed 0.55 USD for a re-run that cost 0.11).

The plan mirrors the pipeline's own skip decisions, so the first test pins them together: right after
a real pipeline run (fake services), every step is planned as cached and the plan costs nothing.
"""

import json

import pytest

from talk_autopsy import pipeline, rerun_plan, youtube
from talk_autopsy.correct import CorrectionAnswer
from talk_autopsy.segments import load_transcript, save_transcript
from talk_autopsy.translate import TranslationAnswer
from tests.test_pipeline import claude_handler, default_path  # noqa: F401  (fixture)

FRESH = youtube.Estimate(10.0, 0.14, 0.08, 1.7)  # a per-hour estimate of a full run


def _fail_block_1(settings, name, stage):
    """As if block 1 of `name` had failed: listed in its result, its stamp marked incomplete."""
    output = settings.work_dir / name
    result = load_transcript(output)
    result.meta[stage]["failed_blocks"] = [{"block": 1, "ids": result.ids(), "error": "test"}]
    save_transcript(result, output)
    stamp = settings.work_dir / f"{name}.stamp.json"
    data = json.loads(stamp.read_text(encoding="utf-8"))
    stamp.write_text(json.dumps({**data, pipeline.INCOMPLETE: "failed blocks 1"}), encoding="utf-8")


def _sent(fake, kind):
    return sum(1 for r in fake.requests if r["output_format"] is kind)


def _plan(settings, tmp_path, formats=("srt",)):
    return rerun_plan.plan(settings, FRESH, list(formats), tmp_path / "out", "sine")


def test_after_a_run_every_step_is_planned_from_the_cache(default_path, fake_claude, tmp_path):  # noqa: F811
    settings, calls = default_path
    fake = fake_claude(claude_handler)
    pipeline.run(settings)
    plan = _plan(settings, tmp_path)
    assert plan.cached == ["stt", "glossary", "stt2", "align", "fix", "translate", "outputs"]
    assert (plan.claude_usd, plan.stt_usd, plan.upper, plan.blocks) == (0.0, 0.0, False, {})
    report = pipeline.run(settings)  # and the pipeline agrees: nothing is sent
    assert len(fake.requests) == 3 and len(calls) == 2 and report.cost_usd == 0.0


def test_nothing_cached_means_no_plan(default_path, tmp_path):  # noqa: F811
    settings, _ = default_path
    assert _plan(settings, tmp_path) is None  # the usual per-hour estimate applies


def test_a_changed_glossary_plans_the_steps_after_it(default_path, fake_claude, tmp_path):  # noqa: F811
    settings, _ = default_path
    fake_claude(claude_handler)
    pipeline.run(settings)
    settings.glossary.write_text("| term | keep |\n|---|---|\n| Three | yes |\n", encoding="utf-8")
    plan = _plan(settings, tmp_path)
    assert plan.cached == ["stt"]
    assert plan.claude_usd > 0 and plan.stt_usd == pytest.approx(FRESH.stt_usd / 2)  # the key-term pass


def test_a_correction_to_redo_makes_the_translation_an_upper_bound(default_path, fake_claude,  # noqa: F811
                                                                     tmp_path):
    settings, _ = default_path
    fake_claude(claude_handler)
    pipeline.run(settings)
    _fail_block_1(settings, "fixed.json", "correction")
    plan = _plan(settings, tmp_path)
    assert plan.cached == ["stt", "glossary", "stt2", "align"]
    assert plan.blocks == {"fix": [1, 1]} and plan.upper and plan.reasons == ["translation_after_correction"]
    assert plan.claude_usd > 0 and plan.stt_usd == 0.0
    fake = fake_claude(claude_handler)
    pipeline.run(settings)
    assert _sent(fake, CorrectionAnswer) == 1  # as planned


def test_a_translation_to_finish_counts_only_its_blocks(default_path, fake_claude, tmp_path):  # noqa: F811
    settings, _ = default_path
    fake_claude(claude_handler)
    pipeline.run(settings)
    _fail_block_1(settings, "ru.json", "translation")
    plan = _plan(settings, tmp_path)
    assert "fix" in plan.cached and "translate" not in plan.cached and not plan.upper
    assert plan.blocks == {"translate": [1, 1]}  # its only block failed, so it is sent again
    fake = fake_claude(claude_handler)
    pipeline.run(settings)
    assert (_sent(fake, CorrectionAnswer), _sent(fake, TranslationAnswer)) == (0, 1)  # as planned


def test_mp4_outputs_count_as_cached_only_when_they_would_be_reused(default_path, fake_claude,  # noqa: F811
                                                                      tmp_path):
    settings, _ = default_path
    fake_claude(claude_handler)
    pipeline.run(settings)
    plan = _plan(settings, tmp_path, formats=("srt", "mp4-burned"))
    assert "outputs" not in plan.cached  # never made: it will be encoded
