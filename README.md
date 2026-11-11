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
speech recognition, an LLM and subtitles. This project's contribution is meant
to be measurement: each stage scored on fixed fragments, with a sealed hold-out
and an external audit. What is measured so far, and what is not, is listed below.

## How it is tested

- **Test set:** two scored fragments of English technical speech, about 10
  minutes each: T1 (a native speaker, to be confirmed) and T2 (a strong
  non-native accent). The hold-out T6 (another accent, a workshop) was
  run once on 2026-11-11 (pass, not demonstrated); T3–T5 are not selected. All three are
  about AI-assisted software development: one topic. See
  [data/test-set/README.md](data/test-set/README.md).
- **References** are built from three engines' agreement (faster-whisper,
  AssemblyAI, Gemini). Their disagreements were decided differently per
  fragment: on T2, 19 of 114 spans by the owner listening, about 50 by rules
  (the product name, spelling conventions), the rest by engine majority; on T1,
  all 73 by the assistant (a Claude model) without listening; on T6, by a
  Gemini audio judge that failed its calibration, so T6 is scored against two
  references with bounds. No reference was proofread in full by a fluent
  listener. Details: [who decided each reference](docs/experiments.md#who-decided-each-reference).
- **Baseline:** raw speech recognition (faster-whisper large-v3, AssemblyAI,
  Gemini) on the same fragments. Whisper + Google Translate and VideoLingo were
  planned and **not run**.
- **Metrics** (definitions in one place: [Metrics](docs/experiments.md#metrics)):

  | Metric | What it measures |
  |---|---|
  | WER | word error rate of the English transcript against the reference |
  | Term accuracy (v2) | share of the reference's glossary-term occurrences that the transcript has in the same place, spelled right; reported with a macro-average, the average without the most frequent term, and precision |
  | False-edit rate | share of the correction's word edits that move the text away from the reference (inserted words count) |
  | Blind translation score | the owner's 1–5 rating; one rater |
  | Reading speed | characters per second a viewer must read per subtitle |
  | Cost | USD per run (Claude API, speech recognition at list price) |
  | Time | wall-clock minutes per run |

  Not implemented: timing drift (no hand-labelled timestamps) and term
  accuracy of the translation.
- **Gates and limits** set in advance: a stage passes when its numbers clear
  the threshold (for example WER ≤ 15% on the accented fragment); then its
  prompts and engine freeze. Every acceptance criterion of the specification
  and whether it was met is in the
  [spec criteria table](docs/experiments.md#spec-criteria).
- **Unit tests** cover the code around the models: the data format, audio
  extraction on generated audio, the glossary format, the metrics (including
  the audit's probes as regression tests), the CLI, and the shared Claude
  request code with a scripted fake client. No network, no API.

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

A YouTube link or playlist works the same way, with no separate downloader:

```bash
uv run talk-autopsy doctor        # ffmpeg, a JavaScript runtime for YouTube, yt-dlp age, keys (names only)
uv run talk-autopsy run "https://www.youtube.com/watch?v=..." --domain web3 --output mp4-burned
# -> output/<upload-date> <title>/<title>.ru.mp4: Russian subtitles drawn into the picture (for a TV);
#    <title>.mp4 + <title>.srt next to it for players that load same-named subtitles
uv run talk-autopsy run "https://www.youtube.com/playlist?list=..." --domain web3 --max-videos 5 --output all
# -> one folder per video under output/<playlist title>/; finished videos are skipped,
#    and the run stops before a video that would cross --max-total-cost (default 10 USD)
```

`--translation v3` (an option; the owner's blind rating kept v2 as the default) renders ordinary words in Russian
and keeps only names and code in English; jargon gets the English term in
brackets once per video.

`--output` takes `srt`, `ass`, `bilingual` (English small on top, Russian
below), `mp4-soft` (MP4 with Russian and English subtitle tracks) and
`mp4-burned`, or `all`. The video's title, description and chapter titles are
the context for the auto-glossary. `--domain web3` adds `glossaries/web3.md`
(smart contracts and their testing) and, if present, your personal additions in
`glossaries/local/web3.md` (git-ignored). YouTube needs a JavaScript runtime:
if `doctor` reports none, install Deno (`winget install DenoLand.Deno`).
YouTube download is for personal use; you are responsible for complying with
YouTube's terms of service.

## Project structure

```
talk_autopsy/
  cli.py                   command line: cut, transcribe, glossary-auto, fix, translate, srt, eval, run, ...
  config.py                API key check, target language, models, prices, paths
  segments.py              Transcript / Segment / Word: the format every stage uses
  ingest.py                audio extraction and cut (ffmpeg), duration (ffprobe)
  glossary.py              per-video glossary as a Markdown table
  stt/                     speech recognition engines: faster-whisper, AssemblyAI, Gemini, chunked Gemini
  metrics.py               WER, term accuracy (v2, in place), false-edit rate, Wilson interval, reading speed
  evaluation.py            scoring a transcript file against the test set, benchmark rows
  adjudicate.py            reference transcripts by three-engine consensus
  proofread.py             draft export and import (superseded by adjudicate; kept, unused)
  align.py                 forced alignment: measured word times for any transcript
  blocks.py                five-minute blocks, video context, cost estimates
  glossary_auto.py         auto-glossary (Haiku), prompts/glossary-auto-v2.md (v1 kept)
  correct.py               Claude correction (stage 2), prompts/correction-v2.md (v1 kept)
  translate.py             Claude translation (stage 2), prompts/translation-v2.1.md (v1, v2, v3 kept)
  translation_check.py     the pre-registered T6 translation check (evaluation tooling)
  subtitles.py             .srt and .ass output, up to 3 lines, reading speed report
  pipeline.py              run: stt, glossary, stt2, align, fix, translate, srt; fallbacks; skip-if-unchanged
  tts/                     dubbing (stage 4; a stub)
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

Status: **stage 2, Claude correction and translation. The pipeline is frozen
as `pipeline-v3` (git tag, 2026-11-11) for the one hold-out run on T6, done on
2026-11-11: term accuracy 100% against both references, lower bound 91.7%,
verdict "pass (not demonstrated)", no overfitting signal
([T6 result](docs/experiments.md#t6-result-2026-11-11)).** External audit #1 is done:
[findings and responses](docs/audit-2026-11-11.md). Every number below is in
[results/benchmark.csv](results/benchmark.csv) or the
[experiment log](docs/experiments.md).

**Speech recognition** (stage 1) on T2, the accented fragment, against the
consensus reference ([summary](docs/experiments.md#summary)). AssemblyAI and
Gemini helped build the reference, so their numbers are biased in their
favour.

| Speech recognition on T2 | WER | Term accuracy (v2) |
|---|---|---|
| faster-whisper large-v3 (local) | 6.19% | 61.7% |
| AssemblyAI Universal-3.5 Pro | 6.97% | 80.4% |
| Gemini `gemini-3.8-flash` | 2.34% | 93.5% |

Every engine is under the stage 1 limit (WER ≤ 15% on T2). AssemblyAI was
chosen as the main engine (owner's choice) for its key-term option; Gemini +
forced alignment and faster-whisper are the fallbacks. T1's numbers are in the
appendix.

**The shipped path on T2**: AssemblyAI, an auto-glossary drafted by Haiku from
that first transcript, AssemblyAI again with the glossary as key terms, then
Claude correction. The `pipeline-v3` rows are three repeat runs from a clean
checkout of the tag:

| T2 | Term accuracy (v2) | macro | without `Claude Code` | WER |
|---|---|---|---|---|
| AssemblyAI, no key terms | 80.4% | 81.8% | 89.9% | 6.97% |
| AssemblyAI + a glossary checked against the reference (not available for a new video) | 99.1% | 97.4% | 98.7% | 4.43% |
| `pipeline-v3`, three repeats (auto-glossary, key terms, correction) | **94.4–96.3%** (mean 95.3%) | 84.6–89.7% | 92.4–94.9% | 4.24–4.48% |

**What does the work:** the key terms. **Correction made 0 edits in all four
`pipeline-v3` runs** (0 and 2 in the earlier single runs), at about 0.065 USD
per 10 minutes. It stays in the pipeline as insurance for the fallback engines:
on faster-whisper input it lifted T2 from 61.7% to 93.5% (correction-v1, B1).
The stage 2 target is ≥ 85%; on T2 the pipeline is above it in every repeat,
but T2 shaped the pipeline, so the hold-out T6 decides.

**Translation:** translation-v1, rated alone by the owner on 20 T2 segments,
scored 3.85 of 5 against a target of 4.0; its three 2s all came from a
compression rule. translation-v2 keeps every clause; in a blind pairwise
comparison (15 pairs, not fully blind: the owner had seen the v1 texts) v2 was
preferred to v1 (8 pairs to 3, means 4.67 and 4.40). **The absolute target is
not demonstrated**: pairwise scores run about a point higher than single
ratings, and no baseline was rated. `translation-v2.1` (v2 with neutral
examples) is frozen; a single-rated check against a Gemini baseline is
pre-registered for T6.

**Whole videos** through `run` (AssemblyAI two passes, auto-glossary,
correction, translation on sentence units, cues shown up to 1.5 s into the
following pause; `pipeline-v2`), [operational runs](docs/experiments.md#operational-runs):

| | T2.source (talk, 27:52) | real-01 (interview, 38:30) | stress-01 (interview, two speakers, 29:50) |
|---|---|---|---|
| wall time | 4.6 min (9.8 min per hour of video) | 5.8 min (9.1 min per hour) | 4.9 min (9.9 min per hour) |
| AssemblyAI, list price | 0.47 USD per hour | 0.47 USD per hour | 0.47 USD per hour |
| Claude | 0.82 USD per hour | 0.67 USD per hour | 0.73 USD per hour |
| Russian cues above 17 / 25 characters per second, before -> after sentence units and longer display | 76.8% / 22.5% -> 62.4% / 17.6% | 50.9% / 13.7% -> 49.2% / 14.8% | 51.3% / 14.8% (after) |

The plan's limits are 30 minutes and 3 USD per hour of video: met. The reading
speed target (at most 5% of cues above 17 cps) is not: nothing is cut for
reading speed (owner's decision, meaning first); cues hold up to three lines
and every `.srt` has an `.ass` twin with a smaller font.

**Next:** the owner's blind rating of the T6 translation check (pre-registered),
then T3 on a new domain.

| Stage | Content | Gate |
|---|---|---|
| 0 | Foundation: repository, data format, audio cut, test set | — |
| 1 | Speech recognition engine chosen on the test set | WER ≤ 15% on the accented fragment |
| 2 | Claude correction and translation | term accuracy ≥ 85%, blind translation score ≥ 4/5, hold-out run once, external audit |
| 3 | CLI tool with subtitles (v1.0) | external audit before release |
| 4 | Dubbed audio track | — |

After that: a local web UI (v1.1) over the same core. Russian is the only target
language measured; the code takes any language as a parameter.

## Known limitations

- **Small, single-topic test set:** two scored fragments (T1, T2), one hold-out
  (T6) run once; all about AI-assisted software development. A result on
  them says nothing yet about other domains.
- **References are not independent.** AssemblyAI and Gemini helped build them
  (their WER is biased low); T1's was decided by a Claude model, which then
  scores the Claude stages (T1 is secondary evidence); T6's by a Gemini judge
  that failed its calibration, and **no human has checked T6's reference**.
- **Glossaries used for scoring** were reviewed against the references (T1,
  T2) or drafted by Gemini (T6); term accuracy with a reference-checked
  glossary as key terms is optimistic for a new video.
- **One rater** for translation (the owner, not a native English speaker), and
  the comparisons so far were pairwise and not fully blind.
- **Correction adds nothing measurable** on the main path (0 edits in four
  runs); its value is only shown on fallback-engine input.
- **Baselines missing:** Whisper + Google Translate and VideoLingo were not run.
- **Reading speed** is above the usual guideline on fast speakers; nothing is cut.
- **Timing** of subtitles is not validated against hand-labelled onsets.
- **Next-video failure modes** not handled: several speakers (no
  diarization), music, videos much longer than 40 minutes (untested).
- **Costs** are list prices; AssemblyAI ran on its free credit.

## Not done

- Whisper + Google Translate and VideoLingo baselines (planned in the
  specification; not run).
- Fragments T3–T5; hypotheses A5, B3, C2–C5; the specification's C1 against
  baselines.
- Timing drift and term accuracy of the translation (metrics not implemented).
- The T6 run and its translation check (protocol written, tooling tested).
- A human check of the T6 reference (no fluent listener available).
- Dubbing (stage 4; a stub) and the web UI (v1.1).

## Appendix: T1

T1's reference was decided by a Claude model without listening, so its numbers
for the Claude stages are secondary evidence and stay out of the tables above.

| T1 | WER | Term accuracy (v2) |
|---|---|---|
| faster-whisper large-v3 | 11.20% | 52.8% |
| AssemblyAI Universal-3.5 Pro | 1.41% | 94.4% |
| Gemini `gemini-3.8-flash` | 1.41% | 97.6% |
| AssemblyAI + a reference-checked glossary as key terms | 0.33% | 100.0% |
| `pipeline-v3`, one run (auto-glossary, key terms, correction: 0 edits) | 0.50% | 99.2% |

## License

MIT, see [LICENSE](LICENSE).
