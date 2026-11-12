"""
Golden snapshots of everything sent to the model by correction and translation,
and of every file those steps write, on the invented talk of
tests/synthetic_talk.py.

Committed before the observability change of pipeline-v3.1 (block-level progress
events, docs/decisions.md): that change may add events, never alter a request or
an output. A failing test here means a model request or an output changed.

The snapshots are tests/golden/*.json. Regenerate them only on purpose
(a deliberate pipeline change with a new tag): TALK_AUTOPSY_UPDATE_GOLDEN=1 uv run pytest
tests/test_golden_requests.py.
"""

import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from talk_autopsy import blocks, correct, pipeline, translate
from talk_autopsy.glossary import load_glossary
from talk_autopsy.segments import save_transcript
from tests import synthetic_talk

GOLDEN = Path(__file__).parent / "golden"
UPDATE = os.getenv("TALK_AUTOPSY_UPDATE_GOLDEN") == "1"
LONG = 400  # strings longer than this are stored once, by hash, in the snapshot's text table
MODEL_SETTINGS = ("CORRECTION_MODEL", "TRANSLATION_MODEL", "HELPER_MODEL", "TRANSLATION_VERSION")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _plain(value, texts: dict[str, str]):
    """A request kwarg as JSON: classes as their name and schema, long strings by hash."""
    if isinstance(value, type):
        return {"class": value.__name__, "schema": value.model_json_schema()}
    if isinstance(value, str):
        if len(value) <= LONG:
            return value
        key = _sha(value.encode("utf-8"))[:16]
        texts[key] = value
        return f"text:{key}"
    if isinstance(value, dict):
        return {k: _plain(v, texts) for k, v in sorted(value.items())}
    if isinstance(value, list | tuple):
        return [_plain(v, texts) for v in value]
    return value


def snapshot_requests(requests: list[dict]) -> dict:
    texts: dict[str, str] = {}
    plain = [_plain(r, texts) for r in requests]
    return {"requests": plain, "texts": dict(sorted(texts.items()))}


def snapshot_files(folder: Path) -> dict:
    """
    SHA-256 of every file with line endings normalized. A stamp records the raw
    hash of its input file, which differs between a CRLF and an LF platform, so
    such a value is replaced by the input file's name (and checked to match it).
    """
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    raw_hashes = {_sha(p.read_bytes()): p.name for p in files}
    out = {}
    for p in files:
        data = p.read_bytes().replace(b"\r\n", b"\n")
        if p.name.endswith(".stamp.json"):
            stamp = {k: f"sha256 of {raw_hashes[v]}" if v in raw_hashes else v
                     for k, v in json.loads(data).items()}
            data = json.dumps(stamp, sort_keys=True).encode("utf-8")
        out[p.relative_to(folder).as_posix()] = _sha(data)
    return out


def check(name: str, actual: dict) -> None:
    path = GOLDEN / f"{name}.json"
    text = json.dumps(actual, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
    if UPDATE:
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return
    assert path.is_file(), f"{path} is missing; generate it with TALK_AUTOPSY_UPDATE_GOLDEN=1"
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert actual == expected, f"{name}: the model requests or the outputs changed"


@pytest.fixture
def frozen_clock(monkeypatch):
    """Wall times in meta (and so the hashes of files that hold them) do not vary between runs."""
    monkeypatch.setattr(time, "perf_counter", lambda: 1000.0)
    for name in MODEL_SETTINGS:
        monkeypatch.delenv(name, raising=False)


def test_pipeline_fix_translate_srt(tmp_path, fake_claude, frozen_clock):
    fake = fake_claude(synthetic_talk.handler, simulate_cache=True)
    source = tmp_path / "talk.wav"
    source.write_bytes(b"not read: the speech steps are not selected")
    work = tmp_path / "work" / "talk"
    work.mkdir(parents=True)
    save_transcript(synthetic_talk.transcript(), work / "raw2.json")
    (work / "glossary.md").write_text(synthetic_talk.GLOSSARY_MD, encoding="utf-8", newline="\n")
    (work / "meta.json").write_text(json.dumps({"title": "Scheduling small jobs (invented)",
                                                "description": "An invented talk for tests.",
                                                "chapters": []}) + "\n", encoding="utf-8", newline="\n")
    settings = pipeline.RunSettings(source=source, target_lang="ru", steps=["fix", "translate", "srt"],
                                    work_root=tmp_path / "work")
    events = []
    report = pipeline.run(settings, progress=events.append)

    assert len(fake.requests) == 6  # three blocks each
    check("pipeline_requests", snapshot_requests(fake.requests))
    check("pipeline_outputs", {
        "files": snapshot_files(work),
        "report": {"cost_usd": report.cost_usd, "stt_cost_usd": report.stt_cost_usd,
                   "estimate_usd": report.estimate_usd, "fallbacks": report.fallbacks,
                   "steps": [[s.step, Path(s.output).name, s.skipped, s.cost_usd, s.detail]
                             for s in report.steps]},
        # what the CLI prints: every event's fields except the elapsed time
        "events": [[e.stage, e.event, e.chunk, e.chunks, e.engine, e.detail] for e in events],
    })


def test_direct_correct_and_translate(tmp_path, fake_claude, frozen_clock):
    fake = fake_claude(synthetic_talk.handler)
    glossary_file = tmp_path / "glossary.md"
    glossary_file.write_text(synthetic_talk.GLOSSARY_MD, encoding="utf-8", newline="\n")
    glossary = load_glossary(glossary_file)
    context = blocks.VideoContext(title="Scheduling small jobs (invented)")
    fixed = correct.correct(synthetic_talk.transcript(), glossary, context)
    translated = translate.translate(fixed, glossary, "ru", context=context)
    out = tmp_path / "out"
    out.mkdir()
    save_transcript(fixed, out / "fixed.json")
    save_transcript(translated, out / "ru.json")

    assert len(fake.requests) == 6
    check("direct_requests", snapshot_requests(fake.requests))
    check("direct_outputs", {"files": snapshot_files(out),
                             "edits": fixed.meta["correction"]["edits"],
                             "units": translated.meta["units"]})
