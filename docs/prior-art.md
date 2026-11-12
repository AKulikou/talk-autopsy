# Prior art

A survey of open projects that turn a talk or video into translated subtitles
(and often a dubbed track), made on 2026-11-09, before the correction and
translation stages were built. Repository facts are as found that day and may
have changed since.

**Summary.** The field is crowded: "Whisper → LLM → subtitles → TTS" exists in
a dozen open projects, some of them mature. Almost none publishes quality
measurements. Talk Autopsy's contribution is therefore **measurement, not
another pipeline**: each stage scored on fixed fragments against references,
with the rules written before the results and the negative results kept.

| Project | What it is | Licence | Useful ideas | Missing |
|---|---|---|---|---|
| VideoLingo (github.com/automcn/VideoLingo) | yt-dlp → WhisperX (now Qwen3-ASR by default) → NLP + LLM subtitle splitting to Netflix norms → manual and automatic glossary → translate / reflect / adapt → TTS with pacing; any OpenAI-compatible LLM | Apache-2.0 | subtitle splitting rules; automatic glossary; three-step translation | no separate, measured correction stage; no metrics |
| pyvideotrans (github.com/jianchang512/pyvideotrans) | desktop all-in-one: several speech recognition engines, translators (Google, Microsoft, DeepL, LLMs), TTS engines, UVR5 vocal separation | not verified | vocal / background separation for voice-over | translation without context or glossary; no metrics |
| KrillinAI | translation and dubbing for 120+ languages, aimed at social-media creators | not recorded | — | not about technical terms |
| Root1V/ai-video-dubbing-pipeline | fully local: faster-whisper + VAD → pyannote → local LLM with a glossary and rolling history → voice cloning → ffmpeg; clean architecture, fakes in tests, CI | MIT | batch check by line count with retry or split; rolling history; TTS pacing | states that it has no quality metrics |
| pratts/video-translator | WhisperX / MLX → GPT translation with a glossary | not verified | glossary in Whisper's initial prompt; glossary content in the cache key | checked by eye |
| AutoDub, video-dubbing-translator, AutoSubtitle | variants of Whisper + an LLM or Google + TTS | various | confirm that the assembly is a solved problem | no metrics |
| subtitle-correction (Jonathangadeaharder) | fine-tunes a small LLM (LoRA) to correct Whisper subtitles against references | not recorded | the only one that treats transcript correction as a separate task with a reference | a local fine-tuned model, not prompting |

**References.**

- OpenAI Cookbook, "Whisper prompting vs post-processing to correct
  misspellings": prompt hints help partly; LLM post-processing is more
  reliable but introduces its own errors (hence this project's false-edit
  rate).
- OpenAI Cookbook, "Pre- and post-processing for Whisper".

**What this project took from them.** A glossary in the speech recogniser's
prompt (tested as A2 with faster-whisper: it did not help on T2) and as
AssemblyAI key terms (A3, B5: it did); an automatic glossary (B5); glossary
content and code in the cache key (`pipeline-v3`); correction as its own
stage with a false-edit metric (B1, B2). What it did not take: subtitle
splitting to Netflix norms and multi-step translation (translation is one
step on sentence units). VideoLingo, the closest project, was not run as a
baseline (out of v1.0 scope; [decisions](decisions.md), 2026-11-11).
