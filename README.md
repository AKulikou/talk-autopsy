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
- **Baselines:** raw speech recognition (faster-whisper large-v3, AssemblyAI,
  Gemini) on the same fragments; a plain Gemini translation in the T6
  translation check. Whisper + Google Translate: code ready, waits for an API
  key; VideoLingo: attempted, not run (see the comparison table below).
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

On Windows, without the command line: double-click `scripts\windows\Talk Autopsy.bat`
(or the desktop shortcut `scripts\windows\create-shortcut.ps1` makes); it asks, in
Russian, for a YouTube link or a video file, shows the estimate and writes a TV file and
subtitles.

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
  baselines/               Whisper + Google Translate baseline (evaluation tooling)
  secret_registry.py       the pipeline's secret names + the evaluation tooling's
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

## Results

Every number below is in [results/benchmark.csv](results/benchmark.csv) or the
[experiment log](docs/experiments.md).

**In short.** The project's strength is **term recognition**: an automatic
glossary drafted from a first transcript, sent back to the speech recognition
service as key terms, puts the technical terms, product names and commands in
place (T2 94–96%, hold-out T6 100%), and the end-to-end tool around it
(YouTube link in, subtitles or a TV-ready MP4 out, 1.1–1.3 USD and about 10
minutes per hour of video). The **translation is on par with a plain Gemini
translation** of the same English, not better (T6, blind, 4.42 against 4.33).
The references had no native English listener, and T6's topic is close to T1
and T2: transfer to another domain is not measured yet.

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

**The shipped path** (`pipeline-v3`, frozen as a git tag on 2026-11-11):
AssemblyAI, an auto-glossary drafted by Haiku from that first transcript,
AssemblyAI again with the glossary as key terms, then Claude correction. On
T2, three repeat runs from a clean checkout of the tag; on T6, the hold-out,
**one run, never repeated** ([T6 result](docs/experiments.md#t6-result-2026-11-11)):

| | Term accuracy (v2) | macro | without the top term | WER |
|---|---|---|---|---|
| T2: AssemblyAI, no key terms | 80.4% | 81.8% | 89.9% | 6.97% |
| T2: AssemblyAI + a glossary checked against the reference (not available for a new video) | 99.1% | 97.4% | 98.7% | 4.43% |
| T2: `pipeline-v3`, three repeats | **94.4–96.3%** (mean 95.3%) | 84.6–89.7% | 92.4–94.9% | 4.24–4.48% |
| T6 (hold-out): `pipeline-v3`, one run, against references J / M | **100% / 100%** (72/72, 71/71) | 100% | 100% | 4.70% / 2.89% |

T6 has two references because its disputed spans were decided by a Gemini
audio judge that failed its calibration: J takes the judge's text, M the
engines' majority. Counting the 6 term occurrences inside spans where they
disagree as misses gives a lower bound of 91.7% (Wilson 95% lower limit
83.0%), so the verdict written before the run is **"pass (not
demonstrated)"**: above the 85% target, but 72 occurrences cannot demonstrate
it. No overfitting signal (T6 is not below the T2 repeats).

**What does the work:** the key terms. **Correction made 0 edits in the four
T1/T2 `pipeline-v3` runs and 1 (correct) edit on T6**, at about 0.065 USD per
10 minutes. It stays in the pipeline as insurance for the fallback engines:
on faster-whisper input it lifted T2 from 61.7% to 93.5% (correction-v1, B1).

**Against the baselines, on one set (T2 and T6)** ([details](docs/experiments.md#baselines-on-one-set-t2-and-t6)).
English side, and the owner's blind 1–5 rating of the translations on 12 T6 units (filled in by the scorer once he has rated the Google Translate items).

<!-- baselines-table:start -->
| | T2 term accuracy (v2) | T2 WER | T6 term accuracy (v2), J / M | T6 WER, J / M | translation rating |
|---|---|---|---|---|---|
| Talk Autopsy (`pipeline-v3`) | **94.4–96.3%** (3 runs) | 4.24–4.48% | **100% / 100%** (1 run) | 4.70% / 2.89% | *pending* (owner's rating) |
| raw Whisper (faster-whisper large-v3) | 61.7% | 6.19% | 87.5% / 88.7% | 8.01% / 5.97% | — (no translation) |
| Whisper + Google Translate | 61.7% (raw Whisper's transcript) | 6.19% | 87.5% / 88.7% | 8.01% / 5.97% | *pending* (owner's rating) |
| Gemini translation (plain prompt, of Talk Autopsy's corrected English) | as Talk Autopsy | | as Talk Autopsy | | *pending* (owner's rating) |
| VideoLingo | attempted, not run | | attempted, not run | | *pending* |
<!-- baselines-table:end -->

Raw Whisper's numbers are biased in its favour: it was one of the three engines
that built both references. Whisper + Google Translate uses the same raw Whisper
transcript, so its English side is the same; only its translation differs.
VideoLingo (now Qwen3-ASR by default) could not be installed in this session:
the session's permission policy refused running its downloaded installer.

**Translation.** On T6, 12 random sentence units (at least 6 words each) were
translated by the pipeline and, as a baseline, by Gemini (`gemini-4-carbon`,
a plain "translate into natural Russian" prompt, the same corrected English);
the owner rated the 22 shuffled items blind, one at a time, without having
watched T6 ([T6 translation check](docs/experiments.md#t6-translation-check-result-2026-11-11)):

| T6 translation check, 12 units | mean (1–5) | scores 5 / 4 / 3 |
|---|---|---|
| `pipeline-v3` (translation-v2.1) | **4.42** | 8 / 1 / 3 |
| Gemini, plain prompt | 4.33 | 7 / 2 / 3 |
| difference | +0.08, bootstrap 95% interval −0.33 … +0.50 | 2 units each way, 8 ties |

The pre-registered rule (pipeline mean ≥ 4.0) is met. The pipeline is **not
shown to be better** than a plain Gemini translation. Earlier, on T2:
translation-v1 alone scored 3.85; translation-v2 was preferred to v1 in a
blind pairwise comparison (8 pairs to 3).

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

## Status and roadmap

Status: **stage 3, the CLI tool (v1.0).** Stage 2 closed on 2026-11-11 (owner's
decision, [Gate 2 -> 3](docs/experiments.md#gate-2---3-2026-11-11)): the
hold-out passed (not demonstrated), the blind translation score is 4.42, and
external audit #1 is done ([findings and responses](docs/audit-2026-11-11.md)).
`pipeline-v3` stays frozen.

**Next** (the specification's [v1.0 checklist](docs/experiments.md#v10-checklist)):
the baselines on one set (T2 and T6) and the owner's blind rating of all
systems' translations, external audit #2, tag `v1.0`. Then T3 on a new domain.

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
- **One rater** for translation (the owner, not a native English speaker), on
  12 units of T6 (single-rated, blind) and earlier T2 comparisons that were
  pairwise and not fully blind; the rater reads the English first.
- **Translation is not better than a plain Gemini translation** (T6: +0.08,
  interval −0.33 … +0.50); the pipeline's measured edge is term recognition.
- **Correction adds little** on the main path (0 edits in four T1/T2 runs, 1
  on T6) at about 0.39 USD per hour; its value is shown on fallback-engine
  input.
- **Baselines incomplete:** Whisper + Google Translate is coded but not run (no
  API key yet); VideoLingo was not run; no translation of any baseline but a
  plain Gemini one has been rated.
- **Reading speed** is above the usual guideline on fast speakers; nothing is cut.
- **Timing** of subtitles is not validated against hand-labelled onsets.
- **Next-video failure modes** not handled: several speakers (no
  diarization), music, videos much longer than 40 minutes (untested).
- **Costs** are list prices; AssemblyAI ran on its free credit.

## Not done

- Whisper + Google Translate run (code ready) and VideoLingo run; the owner's
  blind rating of all systems' translations.
- Fragments T3–T5 (no fragment from another domain); hypotheses A5, B3,
  C2–C5; the specification's C1 against Google Translate and VideoLingo
  (the owner's three-system rating).
- Timing drift and term accuracy of the translation (metrics not implemented).
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
