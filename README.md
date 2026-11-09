# Talk Autopsy

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
  ffmpeg: 16 kHz mono audio            work/<run>/audio.wav + meta.json
      |
      v
  speech recognition (pluggable;       work/<run>/raw.json
  first engine: faster-whisper
  large-v3 on the CPU)
      |
      v
  Claude correction: terms, names,     work/<run>/fixed.json
  accent-distorted words, using the
  glossary and the video context
      |
      v
  Claude translation in blocks of      work/<run>/<lang>.json
  5-10 minutes with overlap, one
  glossary for the whole video
      |
      v
  subtitles                            <name>.<lang>.srt, <name>.en.srt
      |
      v
  later: dubbed audio (edge-tts +      <name>.<lang>.dub.m4a
  ffmpeg)
```

Every stage reads and writes the same JSON format: timed segments with ids that
no stage changes, so any two files of a run can be compared segment by segment.
Everything heavy runs locally. Only text (transcript segments and the glossary)
goes to the Claude API; the video never leaves the machine.

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

The whole pipeline on a local video (Gemini needs `GEMINI_API_KEY`, forced
alignment needs `uv sync --extra align`, the Claude steps need
`ANTHROPIC_API_KEY`):

```bash
uv sync --extra align
uv run talk-autopsy run talk.mp4 --lang ru --engine gemini+align --glossary talk.glossary.md
# -> work/talk/talk.ru.srt and work/talk/talk.en.srt, with a cost report;
#    a rerun skips every step whose inputs did not change
```

## Project structure

```
talk_autopsy/
  cli.py                   command line: cut, transcribe, align, fix, translate, srt, eval, run, ...
  config.py                API key check, target language, models, prices, paths
  segments.py              Transcript / Segment / Word: the format every stage uses
  ingest.py                audio extraction and cut (ffmpeg), duration (ffprobe)
  glossary.py              per-video glossary as a Markdown table
  stt/                     speech recognition engines: faster-whisper, AssemblyAI, Gemini, chunked Gemini
  metrics.py               WER, term accuracy, false-edit rate, reading speed
  evaluation.py            scoring a transcript file against the test set, benchmark rows
  adjudicate.py            reference transcripts by three-engine consensus
  proofread.py             draft export and import (superseded by adjudicate)
  align.py                 forced alignment: measured word times for any transcript
  blocks.py                five-minute blocks, video context, cost estimates
  correct.py               Claude correction (stage 2), prompts/correction-v1.md
  translate.py             Claude translation (stage 2), prompts/translation-v1.md
  subtitles.py             .srt output (built in stage 2 as a viewing tool)
  pipeline.py              run: stt, align, fix, translate, srt with skip-if-unchanged
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

Status: **stage 2, Claude correction and translation.** Stage 1 is measured on
T1 and T2 against references built by three-engine consensus
([experiments](docs/experiments.md#stage-1-summary)):

| Speech recognition (no glossary) | WER T2 | Term accuracy T2 | WER T1 | Term accuracy T1 |
|---|---|---|---|---|
| faster-whisper large-v3 (local) | 6.19% | 61.7% | 11.20% | 55.1% |
| AssemblyAI Universal-3.5 Pro | 6.97% | 80.4% | 1.41% | 95.8% |
| Gemini `gemini-3.8-flash` | 2.34% | 93.5% | 1.41% | 99.2% |

AssemblyAI and Gemini helped build the references, so their numbers are
biased in their favour. Every engine is under the stage 1 limit (WER ≤ 15% on
T2). By the owner's decision Gemini is the main engine (free tier), used with
forced alignment for measured word times (`gemini+align`), with faster-whisper
as the fallback ([decisions](docs/decisions.md)).

Stage 2 is built and has its first runs: correction (`correction-v1`) takes term
accuracy on T2 from 61.7% to 94.4% on faster-whisper input and from 93.5% to
96.3% on Gemini input, with no false edits; translation (`translation-v1`)
and the `.srt` writer produce Russian subtitles of a whole talk. The blind
translation rating, the hold-out run and the external audit of the stage 2
gate are still ahead. Dubbing is a stub.

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
