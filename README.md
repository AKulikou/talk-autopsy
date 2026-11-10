# Talk Autopsy [![CI](https://github.com/AKulikou/talk-autopsy/actions/workflows/ci.yml/badge.svg)](https://github.com/AKulikou/talk-autopsy/actions/workflows/ci.yml)

Subtitles in your language for English technical talks, with every stage of the
pipeline measured on a fixed test set.

Part of the Autopsy series: [Startup Autopsy](https://github.com/AKulikou/startup-autopsy) ·
[Stock Autopsy](https://github.com/AKulikou/stock-autopsy)

Machine subtitles for technical talks fail where it matters most. Whisper followed
by Google Translate mangled the technical terms, product names and shell commands,
and broke down on speakers with a strong accent: the words a viewer of a technical
talk most needs are the ones it got wrong.

The field is crowded: VideoLingo, pyvideotrans and other tools already chain
speech recognition, an LLM and subtitles. This project's contribution is
measurement. Every stage is benchmarked on a fixed set of fragments against
baselines, with a sealed hold-out and an external audit, so each claim of "better"
comes with a number and the method behind it.

## How it is tested

- **Test set:** six fragments of English technical speech, 5–10 minutes each: a
  native speaker, a strong accent, another accent, a fast screen demo full of code,
  a slow lecture, and a sealed hold-out that runs once, at the gate, with the
  prompts already frozen. See [data/test-set/README.md](data/test-set/README.md).
- **Baselines:** raw Whisper, Whisper + Google Translate, and VideoLingo, run on
  the same fragments.
- **Metrics:**

  | Metric | What it measures |
  |---|---|
  | WER | word error rate of the English transcript against a reference built from three engines' agreement, with every disagreement decided by ear |
  | Term accuracy | share of glossary terms that come out right in the transcript and the translation |
  | False-edit rate | share of correction edits that change words that were already right |
  | Blind translation score | 1–5 rating by a reader who does not know which system wrote it |
  | Reading speed | characters per second a viewer must read per subtitle |
  | Timing drift | how far subtitles are from the speech they belong to (median, ms) |
  | Cost | USD per run (Claude API) |
  | Time | wall-clock minutes per run |

- **Gates:** a stage passes only when its numbers clear the threshold set in
  advance (for example, WER ≤ 15% on the accented fragment for speech recognition);
  then its prompts and engine freeze. Runs per hypothesis and money per stage are
  capped in advance.
- **Unit tests** cover the code around the models: the data format, audio
  extraction on generated audio, the glossary format, the CLI, and the shared
  Claude request code with a scripted fake client. No network, no API.

Every measured run is a row in [results/benchmark.csv](results/benchmark.csv) and
an entry in [docs/experiments.md](docs/experiments.md).

## How it works

```
video URL or local file
      |
      v
  ffmpeg: 16 kHz mono audio            work/<run>/audio.wav (+ audio.flac, the copy
      |                                that is uploaded) + meta.json
      v
  speech recognition, first pass       work/<run>/raw.json
  (AssemblyAI; fallbacks: Gemini +
  forced alignment, faster-whisper)
      |
      v
  auto-glossary from the first pass    work/<run>/glossary.md
  (Haiku), merged with your glossary
      |
      v
  speech recognition, second pass      work/<run>/raw2.json
  with the glossary as key terms
      |
      v
  Claude correction: terms, names,     work/<run>/fixed.json
  accent-distorted words, never
  across segment boundaries
      |
      v
  sentence units: segments joined
  until a sentence ends, split
  above 25 s
      |
      v
  Claude translation of the units      work/<run>/<lang>.json
  in blocks of 5 minutes with
  context, every clause kept, one
  glossary per video
      |
      v
  subtitles, up to 3 lines, timed      <name>.<lang>.srt/.ass, <name>.en.srt/.ass
  by the English words, shown into
  the following pause
      |
      v
  later: dubbed audio (edge-tts +      <name>.<lang>.dub.m4a
  ffmpeg)
```

Every stage reads and writes the same JSON format: timed segments. Recognition
and correction keep the same segment ids, so their files can be compared segment
by segment; the translation's segments are sentence units, each listing the
segment ids it covers.
Only text (transcript segments, the glossary, the context you give) goes to the
Claude API. The audio goes to the speech recognition service as compressed
FLAC; the video never leaves the machine.

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/) (it installs Python 3.13) and
[ffmpeg](https://ffmpeg.org/download.html) on PATH. An
[Anthropic API key](https://console.anthropic.com/) is needed only for the Claude
stages.

```bash
git clone <repository-url>
cd talk-autopsy
uv sync

cp .env.example .env              # then set ANTHROPIC_API_KEY in .env

# 16 kHz mono WAV of the first ten minutes of a video
uv run talk-autopsy cut talk.mp4 --start 00:00 --end 10:00 --out talk.wav
```

Speech recognition and the metrics work on their own:

```bash
# faster-whisper large-v3 on the CPU (downloads about 3.1 GB on first use)
uv run talk-autopsy transcribe talk.wav --out work/talk/raw.whisper-large-v3.json

# WER, term accuracy and false-edit rate against a test-set reference
uv run talk-autopsy eval T2 --hyp work/T2/raw.whisper-large-v3.json
```

The whole pipeline on a local video (needs `ASSEMBLYAI_API_KEY` and
`ANTHROPIC_API_KEY`; `GEMINI_API_KEY` and `uv sync --extra align` only for the
Gemini fallback):

```bash
uv run talk-autopsy run talk.mp4 --lang ru --context "Title: ..., description: ..."
# -> work/talk/talk.ru.srt + .ass and work/talk/talk.en.srt + .ass, with a cost report;
#    --glossary my.glossary.md adds your terms (they win over the auto-glossary);
#    a rerun skips every step whose inputs did not change
```

To watch with VLC: open the video, then Subtitle -> Add Subtitle File... and
pick the `.ass` file (it fits three lines in a smaller font); the `.srt` works
in any player.

## Project structure

```
talk_autopsy/
  cli.py                   command line: cut, transcribe, glossary-auto, fix, translate, srt, eval, run, ...
  config.py                API key check, target language, models, prices, paths
  segments.py              Transcript / Segment / Word: the format every stage uses
  ingest.py                audio extraction and cut (ffmpeg), duration (ffprobe)
  glossary.py              per-video glossary as a Markdown table
  glossary_auto.py         auto-glossary from the first transcript (Haiku), coverage, merge
  stt/                     speech recognition engines: faster-whisper, AssemblyAI, Gemini, chunked Gemini
  metrics.py               WER, term accuracy, false-edit rate, reading speed
  evaluation.py            scoring a transcript file against the test set, benchmark rows
  adjudicate.py            reference transcripts by three-engine consensus
  proofread.py             draft export and import (superseded by adjudicate)
  align.py                 forced alignment: measured word times for any transcript
  blocks.py                five-minute blocks, video context, cost estimates
  correct.py               Claude correction (stage 2), prompts/correction-v2.md (v1 kept)
  translate.py             Claude translation (stage 2), prompts/translation-v2.md (v1 kept)
  subtitles.py             .srt and .ass output, up to 3 lines, reading speed report
  pipeline.py              run: stt, glossary, stt2, align, fix, translate, srt; fallbacks; skip-if-unchanged
  tts/                     dubbing (stage 4)
  core/                    shared Claude request code from the Autopsy template
tests/                     unit tests with a fake Claude client
data/test-set/             test set description and glossaries (media stay local)
docs/decisions.md          decision log
docs/experiments.md        experiment log and backlog
results/benchmark.csv      one row per measured run
```

`results/` (except `benchmark.csv`), `logs/` and `work/` are generated and
git-ignored.

## Testing

```bash
uv run pytest                     # unit tests: no API calls, no network
uv run ruff check .               # lint, targeting Python 3.13
```

The audio tests generate a two-second sine wave with ffmpeg and check what the
extraction makes of it: sample rate, channels, length, and an error instead of a
shorter file when a cut runs past the end. GitHub Actions runs ruff and pytest on
Python 3.13 for every push.

## Status and roadmap

Status: **stage 2, Claude correction and translation; the pipeline is frozen
as `pipeline-v2` (git tag, 2026-11-10) for the hold-out run.** Every number below is
in [results/benchmark.csv](results/benchmark.csv) or the
[experiment log](docs/experiments.md).

**Stage 1 (speech recognition)**, measured on T1 (native speaker) and T2
(strong accent) against references built by three-engine consensus
([stage 1 summary](docs/experiments.md#stage-1-summary)); T1 rows re-scored
after the `@claude` reference fix:

| Speech recognition | WER T2 | Term accuracy T2 | WER T1 | Term accuracy T1 |
|---|---|---|---|---|
| faster-whisper large-v3 (local) | 6.19% | 61.7% | 11.20% | 54.2% |
| AssemblyAI Universal-3.5 Pro | 6.97% | 80.4% | 1.41% | 94.2% |
| Gemini `gemini-3.8-flash` | 2.34% | 93.5% | 1.41% | 97.5% |

AssemblyAI and Gemini helped build the references, so their numbers are
biased in their favour. Every engine is under the stage 1 limit (WER <= 15% on
T2). **Gate 1 -> 2 passed with AssemblyAI + key terms as the main engine**
(owner's choice, [decisions](docs/decisions.md)); Gemini + forced alignment and
faster-whisper are the fallbacks.

**Term accuracy with key terms: reference glossary against auto-glossary.** The
first number used a glossary checked against the reference, which a new video
does not have; the second is the realistic one, with a glossary Haiku drafted
from the first transcript (B5):

| | T2 | T1 |
|---|---|---|
| AssemblyAI, no key terms | 80.4% | 94.2% |
| AssemblyAI + reference glossary as key terms (A3) | 99.1% | 100.0% |
| AssemblyAI + **auto-glossary** as key terms (B5) | **96.3%** | **98.3%** |
| ... + correction-v2 with the auto-glossary (B1 v2) | **96.3%** | **98.3%** |
| auto-glossary coverage of the reference glossary (two runs) | 74.4% / 79.5% | 56.8% / 62.2% |

The stage 2 term target is >= 85%. On this input correction-v2 adds nothing
measurable (0 edits on T2); it did most of its work on faster-whisper input
(T2 61.7% -> 94.4% with correction-v1, B1). False-edit rate 0 in every
correction run.

**Translation (C1): met.** The owner rated translation-v1 at a mean of 3.85
of 5 (target 4.0) on 20 T2 segments; the three lowest scores all came from its
compression rule. translation-v2 keeps every clause. In the blind pairwise
comparison (15 pairs, not fully blind: the owner had seen the v1 texts) v1
scored 4.40 and **v2 4.67**; v2 was better in 8 pairs, v1 in 3, 4 ties.
translation-v2 is adopted. Its two weak items were half sentences cut at a
segment boundary; translation now runs on sentence units (D1 (setup)).

**Whole videos** through `run` (AssemblyAI two passes, auto-glossary,
correction-v2, translation-v2 on sentence units, cues shown up to 1.5 s into
the following pause), [operational runs](docs/experiments.md#operational-runs):

| | T2.source (talk, strong accent, 27:52) | real-01 (interview, 38:30) | stress-01 (interview, two accents, 29:50) |
|---|---|---|---|
| wall time | 4.6 min (9.8 min per hour of video) | 5.8 min (9.1 min per hour) | 4.9 min (9.9 min per hour) |
| AssemblyAI, list price | 0.47 USD per hour | 0.47 USD per hour | 0.47 USD per hour |
| Claude | 0.82 USD per hour | 0.67 USD per hour | 0.73 USD per hour |
| Russian cues above 17 / 25 characters per second, before -> after sentence units and longer display | 76.8% / 22.5% -> 62.4% / 17.6% | 50.9% / 13.7% -> 49.2% / 14.8% | 51.3% / 14.8% (after) |

The plan's limit is 30 minutes per hour of video. Nothing is cut for reading
speed (owner's decision): cues hold up to three lines and every `.srt` has an
`.ass` twin with a smaller font. On real-01 the longer display is outweighed by
timing cues by the English words, which makes reading speed uneven inside a
long sentence (D1 (setup)); subtitle layout stays open after the freeze.

**Pending for the stage 2 gate:** the owner adjudicates the sealed hold-out T6
(`data/test-set/T6.adjudication.md`, prepared 2026-11-10 from three engines);
a later session resolves the reference and runs the frozen `pipeline-v2` on T6
once; then the external audit. Dubbing is a stub.

| Stage | Content | Gate |
|---|---|---|
| 0 | Foundation: repository, data format, audio cut, test set | — |
| 1 | Speech recognition engine chosen on the test set | WER ≤ 15% on the accented fragment |
| 2 | Claude correction and translation | term accuracy ≥ 85%, blind translation score ≥ 4/5, hold-out run once, external audit |
| 3 | CLI tool with subtitles (v1.0) | external audit before release |
| 4 | Dubbed audio track | — |

After that: a local web UI (v1.1) over the same core. Russian is the only target
language measured; the code takes any language as a parameter.

## License

MIT, see [LICENSE](LICENSE).
