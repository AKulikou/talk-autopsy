"""
A fake core for the web tests: scripted progress events, transcripts in
memory, files created in a temporary folder. No network, no ffmpeg, no API.
"""

import threading
from pathlib import Path

from talk_autopsy import outputs, pipeline, youtube
from talk_autopsy.segments import Segment, Transcript
from talk_autopsy.stt.chunked import ProgressEvent
from talk_autopsy.time_estimate import MachineKey


def pe(stage: str, event: str, detail: str = "") -> ProgressEvent:
    return ProgressEvent(stage, event, 0, 7, 0.0, 0.0, 0.0, "pipeline", detail)


FULL_RUN = [
    pe("audio", "step-start", "extracting 16 kHz mono audio"),
    pe("stt", "step-start", "AssemblyAI, compressed audio upload"),
    pe("stt", "step-done", "raw.json, speech recognition 0.0400 USD"),
    pe("glossary", "estimate", "1 block(s), about 1,000 input and 500 output tokens, about 0.010 USD"),
    pe("fix", "estimate", "2 block(s), about 9,000 input and 9,000 output tokens, about 0.050 USD"),
    pe("translate", "estimate", "2 block(s), about 9,000 input and 9,000 output tokens, about 0.080 USD"),
    pe("glossary", "step-start", "glossary-auto-v1, claude-haiku-5-5"),
    pe("glossary", "step-done", "glossary.md, 0.0090 USD; 12 auto terms, 1 with corrected mis-recognitions"),
    pe("stt2", "step-start", "AssemblyAI with 12 key terms"),
    pe("stt2", "step-done", "raw2.json, speech recognition 0.0400 USD"),
    pe("align", "step-start", "times already measured; passing the transcript through"),
    pe("align", "step-done", "aligned.json"),
    pe("fix", "step-start", "correction-v2, claude-sonnet-5-5"),
    pe("fix", "block-done", "block 1/2"),
    pe("fix", "block-done", "block 2/2"),
    pe("fix", "step-done", "fixed.json, 0.0400 USD"),
    pe("translate", "step-start", "translation-v2.1, claude-sonnet-5-5"),
    pe("translate", "block-done", "block 1/1"),
    pe("translate", "step-done", "lt.json, 0.0700 USD"),
    pe("srt", "srt", "talk.lt.srt + .ass: 2 cues"),
    pe("srt", "step-done", "talk.lt.srt"),
    pe("pipeline", "done", "0.1190 USD Claude, 0.0800 USD speech recognition"),
]

CACHED_RUN = [pe(step, "step-skipped", f"{name}") for step, name in
              (("stt", "raw.json"), ("glossary", "glossary.md"), ("stt2", "raw2.json"),
               ("align", "aligned.json"), ("fix", "fixed.json"), ("translate", "lt.json"))] + [
    pe("srt", "srt", "talk.lt.srt + .ass: 2 cues"), pe("srt", "step-done", "talk.lt.srt"),
    pe("pipeline", "done", "0.0000 USD Claude, 0.0000 USD speech recognition")]


def transcripts(lang: str) -> dict[str, Transcript]:
    english = [Segment(id=1, start=0.0, end=2.0, text="Hello agents."),
               Segment(id=2, start=2.0, end=4.0, text="We use cloud code.")]
    fixed = [english[0], Segment(id=2, start=2.0, end=4.0, text="We use Claude Code.")]
    translated = [Segment(id=1, start=0.0, end=4.0, text="Sveiki.", source_ids=[1, 2])]
    usage = {"input_tokens": 9000, "output_tokens": 4000}
    return {
        "raw.json": Transcript(segments=english, language="en", source="fake"),
        "fixed.json": Transcript(segments=fixed, language="en", source="fake", meta={"correction": {
            "usage": usage, "edits": [{"id": 2, "before": "cloud code", "after": "Claude Code",
                                       "reason": "term"}]}}),
        f"{lang}.json": Transcript(segments=translated, language=lang, source="fake",
                                   meta={"translation": {"usage": usage}}),
    }


class FakeCore:
    def __init__(self, root: Path, script=None, duration: float | None = 600.0, playlist: bool = False,
                 fail: Exception | None = None, fail_keys: Exception | None = None,
                 report: pipeline.RunReport | None = None):
        self.root = root
        self.script = FULL_RUN if script is None else script
        self.duration = duration
        self.playlist = playlist
        self.fail = fail
        self.fail_keys = fail_keys
        self.report = report
        self.gate: threading.Event | None = None  # set: run_pipeline waits for it
        self.settings: pipeline.RunSettings | None = None
        self.calls: list[str] = []
        self.done_marked: list = []
        self.opened: list[Path] = []

    def output_root(self) -> Path:
        return self.root / "output"

    def youtube_runtimes(self) -> list[str]:
        return ["deno"]

    def fetch(self, url: str, runtimes):
        self.calls.append("fetch")
        video = youtube.VideoInfo(id="abc123", url=url, title="A talk", duration=self.duration,
                                  upload_date="2026-11-01")
        if self.playlist:
            return youtube.PlaylistInfo(id="PL1", title="list", entries=[video])
        return video

    def download(self, video, folder: Path, runtimes) -> Path:
        self.calls.append("download")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{youtube.video_file_stem(video)}.mp4"
        path.write_bytes(b"video")
        return path

    def probe(self, path: Path) -> float:
        self.calls.append("probe")
        if self.duration is None:
            raise RuntimeError("ffprobe returned no duration")
        return self.duration

    def user_glossary(self, domain: str, work_dir: Path):
        self.calls.append(f"glossary:{domain}")
        return None

    def check_keys(self, settings) -> None:
        self.calls.append("check_keys")
        if self.fail_keys:
            raise self.fail_keys

    def run_pipeline(self, settings, progress) -> pipeline.RunReport:
        self.calls.append("pipeline")
        self.settings = settings
        if self.gate is not None:
            assert self.gate.wait(10), "the test never released the fake pipeline"
        for event in self.script:
            progress(event)
        if self.fail:
            raise self.fail
        if self.report is not None:
            return self.report
        cost = sum(c for c in (0.009, 0.04, 0.07) if self.script is FULL_RUN)
        report = pipeline.RunReport()
        report.steps.append(pipeline.StepRecord("all", "x", False, round(cost, 4), 1.0,
                                                0.08 if self.script is FULL_RUN else 0.0))
        return report

    def read_transcript(self, path: Path):
        return transcripts(self.settings.target_lang if self.settings else "lt").get(path.name)

    def write_outputs(self, settings, formats, dest: Path, stem: str, video: Path, progress):
        self.calls.append("outputs")
        dest.mkdir(parents=True, exist_ok=True)
        names = {"srt": f"{stem}.srt", "ass": f"{stem}.ass", "bilingual": f"{stem}.bilingual.ass",
                 "mp4-soft": f"{stem}.subs.mp4", "mp4-burned": f"{stem}.{settings.target_lang}.mp4"}
        written = []
        for fmt in formats:
            if fmt == "mp4-burned":
                progress("mp4-burned: drawing the subtitles into the picture (H.264 re-encode)")
            path = dest / names[fmt]
            path.write_text("x", encoding="utf-8")
            written.append(outputs.Written(fmt, path))
        return written

    def mark_done(self, folder: Path, files, cost) -> None:
        self.done_marked.append((folder, files, cost))

    def machine_key(self, engine: str) -> MachineKey:
        return MachineKey(engine, "fake-model", "cloud", "cpu", 4)
