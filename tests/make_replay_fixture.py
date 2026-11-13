"""
Writes the committed replay fixtures (tests/fixtures/replay/):

    synthetic-talk.jsonl         a finished link-mode job, profile tv, Russian
    synthetic-talk-failed.jsonl  the same job, failing during translation (an unexpected error)
    synthetic-talk-unavailable.jsonl  the same job, stopped during translation: the Claude API
                                 answers HTTP 503 to every try of block 2 (pipeline-v3.5 retries,
                                 waits skipped), so the Failed screen names the service
    synthetic-talk-untranslated.jsonl  the same job, finished, its first translation block
                                 left in English (the model answers in English twice);
                                 `english_block` is the first unit id of that block
    synthetic-talk-reverted.jsonl  the same job, finished; both answers of correction block 1
                                 leave segment 12 empty and both answers of translation block 1
                                 leave unit 6 in English, so only those two are kept as they were
                                 (pipeline-v3.4: per-segment revert), the rest is corrected and
                                 translated

The talk is invented (about six minutes, a made-up scheduler "Quorvex"; no
sentence comes from a real video), and so are its Russian translations. The
events are real: the JobRunner with its recorder, the real `pipeline.run` for
correction, translation and subtitles (fake model answers), and simulated
step durations on a fake clock so a replay at natural speed takes about two
minutes. Regenerate after a change to the event format:

    uv run python -m tests.make_replay_fixture
"""

import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

import anthropic
import httpx2

from talk_autopsy import pipeline, youtube
from talk_autopsy.core import claude_client, transient
from talk_autopsy.correct import CorrectionAnswer, Edit
from talk_autopsy.correct import SegmentText as FixSegment
from talk_autopsy.segments import Segment, Transcript, Word, save_transcript
from talk_autopsy.translate import SegmentText as UnitText
from talk_autopsy.translate import TranslationAnswer
from talk_autopsy.web import jobs
from talk_autopsy.web.jobs import Core, JobRequest, JobRunner
from tests.conftest import FakeClaude, placeholder, response

OUT = Path(__file__).parent / "fixtures" / "replay"
URL = "https://www.youtube.com/watch?v=Synthetic01"
TITLE = "Scheduling tiny jobs (invented talk)"
SEGMENT_SECONDS = 8.5

# (corrected English, Russian); the recognized text has the mis-heard forms of MISHEARD.
TALK = [
    ("Hi everyone, thanks for coming to the last talk before lunch.",
     "Всем привет, спасибо, что пришли на последний доклад перед обедом."),
    ("Today I want to show how Quorvex schedules thousands of tiny jobs.",
     "Сегодня я хочу показать, как Quorvex планирует тысячи крошечных задач."),
    ("A tiny job here means anything that finishes in under a second,",
     "Крошечная задача здесь — это всё, что завершается меньше чем за секунду,"),
    ("like resizing an image or sending one notification.",
     "например, изменение размера картинки или отправка одного уведомления."),
    ("Two years ago every worker pulled from a single Brindle queue.",
     "Два года назад каждый воркер брал задачи из одной Brindle queue."),
    ("That worked well until we passed about forty workers.",
     "Это хорошо работало, пока у нас не стало больше сорока воркеров."),
    ("After that the queue itself became the slowest part of the system.",
     "После этого сама очередь стала самой медленной частью системы."),
    ("Our first idea was a flinter mesh between the workers.",
     "Первой нашей идеей была сетка флинтера между воркерами."),
    ("Each worker would ask its neighbours for work before asking the queue.",
     "Каждый воркер сначала просил работу у соседей и только потом у очереди."),
    ("On paper it removed the bottleneck completely.",
     "На бумаге это полностью убирало узкое место."),
    ("In practice it doubled the median latency.",
     "На практике это удвоило медианную задержку."),
    ("The workers spent more time talking to each other than doing work.",
     "Воркеры тратили больше времени на разговоры друг с другом, чем на работу."),
    ("So we rolled it back after two weeks,",
     "Поэтому через две недели мы всё откатили"),
    ("and went looking for something simpler.",
     "и стали искать что-то попроще."),
    ("The fix that stuck is almost embarrassing.",
     "Решение, которое прижилось, почти неловко простое."),
    ("Quorvex now gives every worker a small local buffer.",
     "Теперь Quorvex даёт каждому воркеру небольшой локальный буфер."),
    ("A worker takes eight jobs at once instead of one.",
     "Воркер берёт восемь задач сразу, а не одну."),
    ("That cuts the traffic to the Brindle queue by a factor of eight.",
     "Это в восемь раз сокращает обращения к Brindle queue."),
    ("The obvious risk is that one slow job blocks the other seven.",
     "Очевидный риск — одна медленная задача блокирует остальные семь."),
    ("We handle that with a simple rule:",
     "Мы решаем это простым правилом:"),
    ("if a job runs longer than the buffer's deadline, the rest go back.",
     "если задача выполняется дольше срока буфера, остальные возвращаются в очередь."),
    ("Let me show you the numbers from our test cluster.",
     "Давайте я покажу цифры с нашего тестового кластера."),
    ("On a quiet day the median job waits about forty milliseconds.",
     "В спокойный день медианная задача ждёт около сорока миллисекунд."),
    ("On a busy day that grows to roughly two hundred.",
     "В загруженный день это вырастает примерно до двухсот."),
    ("Before the buffers, a busy day meant almost two seconds.",
     "До буферов загруженный день означал почти две секунды."),
    ("The tail looks even better than the median.",
     "Хвост распределения выглядит даже лучше медианы."),
    ("The slowest one percent of jobs used to wait for ten seconds,",
     "Самый медленный один процент задач раньше ждал по десять секунд,"),
    ("and now it is under one.",
     "а теперь меньше одной."),
    ("Now, the flinter mesh idea is not dead.",
     "При этом идея сетки флинтера не умерла."),
    ("We think it helps when workers sit in different regions.",
     "Мы думаем, что она помогает, когда воркеры находятся в разных регионах."),
    ("There the network is slow and the queue is far away.",
     "Там сеть медленная, а очередь далеко."),
    ("The next release of Quorvex will try a hybrid:",
     "Следующий релиз Quorvex попробует гибрид:"),
    ("buffers inside a region and a flinter mesh between regions.",
     "буферы внутри региона и сетку флинтера между регионами."),
    ("Three lessons from all this.",
     "Три урока из всего этого."),
    ("First, measure the queue before you replace it.",
     "Во-первых, измерьте очередь, прежде чем её заменять."),
    ("Second, clever coordination is expensive coordination.",
     "Во-вторых, умная координация — это дорогая координация."),
    ("Third, small buffers fix more problems than you would expect.",
     "В-третьих, небольшие буферы решают больше проблем, чем кажется."),
    ("The code for the buffer rule is about sixty lines,",
     "Код правила буфера занимает около шестидесяти строк,"),
    ("and you can read it in the Quorvex repository.",
     "и его можно прочитать в репозитории Quorvex."),
    ("Thank you, and I am happy to take questions.",
     "Спасибо, с радостью отвечу на вопросы."),
]
# segment number (1-based) -> (heard, meant)
QUORVEX, BRINDLE = ("core vex", "Quorvex"), ("brindle cue", "Brindle queue")
MESH = ("flinter mash", "flinter mesh")
MISHEARD = {2: QUORVEX, 5: BRINDLE, 8: MESH, 16: ("Core vex", "Quorvex"), 18: BRINDLE, 29: MESH, 32: QUORVEX,
            33: MESH}
GLOSSARY_MD = """<!-- glossary of this run: invented terms -->

| term | keep | ru | notes |
|---|---|---|---|
| Quorvex | yes | | invented product name |
| Brindle queue | yes | | invented component |
| flinter mesh | no | сетка флинтера | invented networking idea |
"""
RUSSIAN = dict(TALK)
# simulated seconds spent before each event reaches the job, by (stage, event)
DELAYS = {("audio", "step-start"): 0.5, ("stt", "step-start"): 2.0, ("stt", "step-done"): 14.0,
          ("glossary", "estimate"): 0.2, ("glossary", "step-start"): 0.1, ("glossary", "step-done"): 9.0,
          ("stt2", "step-start"): 0.2, ("stt2", "step-done"): 13.0, ("align", "step-start"): 0.1,
          ("align", "step-done"): 0.1, ("fix", "step-start"): 0.3, ("fix", "block-done"): 12.0,
          ("fix", "step-done"): 0.2, ("translate", "step-start"): 0.3, ("translate", "block-done"): 14.0,
          ("translate", "step-done"): 0.2, ("srt", "srt"): 0.3, ("srt", "step-done"): 0.2}


def recognized_text(number: int, text: str) -> str:
    heard = MISHEARD.get(number)
    return text.replace(heard[1], heard[0], 1) if heard else text


def transcript() -> Transcript:
    segments = []
    for n, (text, _) in enumerate(TALK, start=1):
        text = recognized_text(n, text)
        start = round((n - 1) * SEGMENT_SECONDS, 2)
        end = round(start + SEGMENT_SECONDS - 0.4, 2)
        tokens = text.split()
        step = (end - start) / len(tokens)
        words = [Word(start=round(start + k * step, 3), end=round(start + (k + 1) * step - 0.05, 3), text=w)
                 for k, w in enumerate(tokens)]
        segments.append(Segment(id=n, start=start, end=end, text=text, words=words, confidence=0.93))
    return Transcript(segments=segments, language="en", source="assemblyai (invented)",
                      meta={"engine": "assemblyai", "timestamps": "measured", "cost_usd": 0.0068,
                            "audio_seconds": len(TALK) * SEGMENT_SECONDS})


def unavailable() -> anthropic.ServiceUnavailableError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.ServiceUnavailableError("Error code: 503", body=None,
                                             response=httpx2.Response(503, request=request))


def model(fail_translation_block: int | None, english_block: int | None = None,
          revert: tuple[int, int] | None = None, failure=None):
    """`revert`: (segment id whose correction comes back empty, unit id whose translation stays English)."""
    translation_blocks = [0]

    def handle(kwargs):
        user = kwargs["messages"][0]["content"]
        if kwargs["output_format"] is CorrectionAnswer:
            body = user.split("Segments to correct:")[1]
            segments, edits = [], []
            for i, text in re.findall(r"^\[(\d+)\] (.*)$", body, re.M):
                heard = MISHEARD.get(int(i))
                if heard and heard[0] in text:
                    text = text.replace(heard[0], heard[1])
                    edits.append(Edit(id=int(i), before=heard[0], after=heard[1], reason="glossary term"))
                segments.append(FixSegment(id=int(i), text="" if revert and int(i) == revert[0] else text))
            answer = CorrectionAnswer(segments=segments, edits=edits)
            return response(answer, input_tokens=1100 + len(user) // 4, output_tokens=900 + len(user) // 4,
                            cache_write=2600)
        body = user.split("Segments to translate:")[1]
        first = re.search(r"^\[(\d+)\]", body, re.M)
        if first and int(first.group(1)) == 1:
            translation_blocks[0] = 1
        elif first:
            translation_blocks[0] = 2
        if translation_blocks[0] == fail_translation_block:
            raise failure() if failure else RuntimeError("the answer could not be read")
        body = user.split("Segments to translate:")[1]
        found = re.findall(r"^\[(\d+)\] (.*)$", body, re.M)
        if english_block is not None and found and int(found[0][0]) == english_block:  # left in English
            units = [UnitText(id=int(i), text=text) for i, text in found]
            return response(TranslationAnswer(segments=units), input_tokens=1000 + len(user) // 4,
                            output_tokens=900 + len(user) // 4, cache_write=2200)
        units = []
        for i, text in re.findall(r"^\[(\d+)\] (.*)$", body, re.M):
            parts = sorted((text.find(en), ru) for en, ru in RUSSIAN.items() if en in text)
            kept_english = revert and int(i) == revert[1]
            russian = " ".join(ru for _, ru in parts) or text
            units.append(UnitText(id=int(i), text=text if kept_english else russian))
        return response(TranslationAnswer(segments=units), input_tokens=1000 + len(user) // 4,
                        output_tokens=1200 + len(user) // 3, cache_write=2200)
    return handle


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.lock = threading.Lock()

    def __call__(self) -> float:
        with self.lock:
            return self.now

    def advance(self, seconds: float) -> None:
        with self.lock:
            self.now += seconds


def pe(stage, event, detail="", data=None):
    fields = (stage, event, 0, 7, 0.0, 0.0, 0.0, "pipeline", detail)
    return pipeline.PipelineEvent(*fields, data=data) if data is not None else pipeline.ProgressEvent(*fields)


class GenCore(Core):
    def __init__(self, root: Path, clock: Clock):
        self.root, self.clock = root, clock

    def output_root(self) -> Path:
        return self.root / "output"

    def youtube_runtimes(self) -> list[str]:
        return ["deno"]

    def fetch(self, url, runtimes):
        return youtube.VideoInfo(id="Synthetic01", url=url, title=TITLE, upload_date="2026-11-12",
                                 duration=len(TALK) * SEGMENT_SECONDS,
                                 description="An invented talk for the page's replay.")

    def download(self, video, folder, runtimes, progress=None):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{youtube.video_file_stem(video)}.mp4"
        path.write_bytes(b"not a video")
        total = 41_800_000
        for k in range(1, 17):
            self.clock.advance(0.5)
            if progress is not None:
                progress(min(total, k * total // 16), total)
        return path

    def check_keys(self, settings) -> None:
        pass

    def user_glossary(self, domain, work_dir):
        return None

    def run_pipeline(self, settings, progress):
        settings.work_root = self.root / "work"
        work = settings.work_dir
        work.mkdir(parents=True, exist_ok=True)
        save_transcript(transcript(), work / "raw.json")
        save_transcript(transcript(), work / "raw2.json")
        (work / "glossary.md").write_text(GLOSSARY_MD, encoding="utf-8")
        (work / "audio.wav").write_bytes(b"placeholder: align only hashes it (times already measured)")

        def forward(event):
            self.clock.advance(DELAYS.get((event.stage, event.event), 0.05))
            progress(event)

        tokens = {"input": 1400, "output": 620, "cache_write": 0, "cache_read": 0}
        for event in [
            pe("audio", "step-start", "extracting 16 kHz mono audio"),
            pe("stt", "step-start", "AssemblyAI, compressed audio upload"),
            pe("stt", "step-done", "raw.json, speech recognition 0.0068 USD",
               {"skipped": False, "seconds": 16.0, "cost_usd": 0.0, "stt_cost_usd": 0.0068, "tokens": None}),
            pe("glossary", "estimate", "1 block(s), about 1,900 input and 700 output tokens, about 0.005 USD",
               {"usd": 0.005, "blocks": 1, "input_tokens": 1900, "output_tokens": 700, "part": "glossary"}),
            pe("glossary", "step-start", "glossary-auto-v2, claude-haiku-5-5"),
            pe("glossary", "step-done", "glossary.md, 0.0045 USD; 3 auto terms",
               {"skipped": False, "seconds": 9.0, "cost_usd": 0.0045, "stt_cost_usd": 0.0, "tokens": tokens}),
            pe("stt2", "step-start", "AssemblyAI with 3 key terms"),
            pe("stt2", "step-done", "raw2.json, speech recognition 0.0068 USD",
               {"skipped": False, "seconds": 13.0, "cost_usd": 0.0, "stt_cost_usd": 0.0068, "tokens": None}),
        ]:
            forward(event)
        run = pipeline.RunSettings(source=settings.source, target_lang=settings.target_lang,
                                   steps=["align", "fix", "translate", "srt"], work_root=settings.work_root,
                                   name=settings.name, context=settings.context)
        report = pipeline.run(run, progress=forward)
        report.steps[:0] = [pipeline.StepRecord("stt", "raw.json", False, 0.0, 16.0, 0.0068),
                            pipeline.StepRecord("glossary", "glossary.md", False, 0.0045, 9.0),
                            pipeline.StepRecord("stt2", "raw2.json", False, 0.0, 13.0, 0.0068)]
        return report

    def write_outputs(self, settings, formats, dest, stem, video, progress, position=None, duration=None):
        dest.mkdir(parents=True, exist_ok=True)
        written = []
        if "mp4-burned" in formats:
            lang = settings.target_lang
            progress(f"mp4-burned: drawing the {lang} subtitles into the picture (H.264 re-encode)")
            for k in range(1, 41):
                self.clock.advance(0.5)
                if position is not None:
                    position(min(duration, k * duration / 40), duration)
        names = {"mp4-burned": f"{stem}.{settings.target_lang}.mp4", "srt": f"{stem}.srt"}
        for fmt in formats:
            if fmt in names:
                path = dest / names[fmt]
                path.write_text("x", encoding="utf-8")
                written.append(jobs.outputs.Written(fmt, path))
        if "srt" in formats:
            path = dest / f"{stem}.en.srt"
            path.write_text("x", encoding="utf-8")
            written.append(jobs.outputs.Written("srt", path))
        return written

    def mark_done(self, folder, files, cost):
        pass


def generate(name: str, fail_translation_block: int | None, expected: str, english_block: int | None = None,
             revert: tuple[int, int] | None = None, failure=None) -> Path:
    root = Path(tempfile.mkdtemp(prefix="replay-fixture-"))
    try:
        fake = FakeClaude(model(fail_translation_block, english_block, revert, failure), simulate_cache=True)
        claude_client.get_client = lambda: fake
        clock = Clock()
        runner = JobRunner(core=GenCore(root, clock), history_file=root / "history.json", clock=clock,
                           tick_s=3600.0, record_dir=root / "recordings")
        job = runner.start(JobRequest(mode="url", url=URL, profile="tv", domain="none", target_lang="ru"))
        for _ in range(600):
            if job.state != "running":
                break
            time.sleep(0.05)
        recording = next((root / "recordings").glob("*.jsonl"))
        text = recording.read_text(encoding="utf-8")
        for path in (root.as_posix(), str(root), json.dumps(str(root))[1:-1]):
            text = text.replace(path, "output-root")
        assert "replay-fixture-" not in text, "a temporary path is left in the fixture"
        lines = [json.loads(line) for line in text.splitlines()]
        lines[0]["summary"]["started"] = "2026-11-12T12:00:00"
        lines[0]["summary"]["id"] = name
        OUT.mkdir(parents=True, exist_ok=True)
        out = OUT / f"{name}.jsonl"
        body = "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
        out.write_text(body, encoding="utf-8", newline="\n")
        print(f"{out}: {len(lines) - 1} events, ends {lines[-1]['type']} at {lines[-1]['t']} s")
        assert lines[-1]["type"] == expected, lines[-1]
        return out
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main() -> int:
    os.environ["ANTHROPIC_API_KEY"] = placeholder("ANTHROPIC_API_KEY")
    for variable in ("CORRECTION_MODEL", "TRANSLATION_MODEL", "HELPER_MODEL", "TRANSLATION_VERSION"):
        os.environ.pop(variable, None)
    generate("synthetic-talk", None, "finished")
    transient.sleep = lambda seconds: None  # the retries are real, their waits are not
    generate("synthetic-talk-failed", 2, "failed")
    generate("synthetic-talk-unavailable", 2, "failed", failure=unavailable)
    generate("synthetic-talk-untranslated", None, "finished", english_block=1)
    generate("synthetic-talk-reverted", None, "finished", revert=(12, 6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
