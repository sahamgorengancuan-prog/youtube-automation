# Autonomous Scientific Animation Studio

An autonomous studio that turns a **topic** into a finished, upload-ready
scientific short. Delivered as self-contained Google Colab notebooks that also
write an importable `src/autostudio/` package for FastAPI / Docker.

## Two notebooks

| Notebook | Scope | Output |
|---|---|---|
| [`Autonomous_Scientific_Animation_Studio_Full_Pipeline.ipynb`](./Autonomous_Scientific_Animation_Studio_Full_Pipeline.ipynb) | **Phase 1 + Phase 2** | validated **1080×1920 MP4** + all sources |
| [`Autonomous_Scientific_Animation_Studio_MVP.ipynb`](./Autonomous_Scientific_Animation_Studio_MVP.ipynb) | **Phase 1 only** | storyboard + static scene preview |

```text
Topic
  → Free web research (DDGS + Wikipedia + Crossref + OpenAlex + arXiv)
  → Deterministic scientific validation
  → Retention-first script (timed beats)
  → Storyboard JSON  →  Scene plan (asset catalogue)
  → Deterministic SVG asset generation + content-addressed cache
  → Static scene composition
  ── Phase 2 ────────────────────────────────────────────────
  → Voice-over (Edge TTS → espeak-ng fallback)
  → Safe-zone subtitles (SRT + ASS)
  → Procedural music + sound cues (ducked, loudness-normalised)
  → Animated 1080×1920 MP4 (FFmpeg zoompan) with burned-in captions
  → ffprobe self-validation  →  SEO metadata  →  manifest + ZIP
```

## Why these design choices

- **One notebook, one importable package.** Every section writes a real module
  into `src/autostudio/`; nothing lives only in a cell. `StudioPipeline.run()`
  is the single entry point and drops straight into a FastAPI background job.
- **Deterministic SVG.** The LLM never writes SVG — it only names an `asset_type`
  from a whitelist, and Python renders the flat vector from a controlled template.
  Reproducible, editable, cacheable; no gradients/textures/raster.
- **Cache-first, content-addressed.** Web search, LLM responses, SVG assets,
  voice clips, rasters, and rendered scene clips are all keyed by content hash.
  Re-running an identical stage is a cache hit, never a regeneration.
- **Local-first models.** Qwen (HuggingFace) primary; OpenAI is an optional
  fallback used only if `OPENAI_API_KEY` is set and local generation fails.
- **Free-only audio/video.** Edge TTS + espeak-ng for voice; FFmpeg `lavfi`
  synthesises the music bed and sound cues — no external assets, no licensing.
- **Self-validating output.** The final MP4 is probed with `ffprobe` for
  resolution, fps, duration, and audio stream before the run is declared done.
- **Graceful degradation.** Every generative stage has a deterministic fallback,
  so a flaky network never aborts a run.

## Repository layout

```
Autonomous_Scientific_Animation_Studio_Full_Pipeline.ipynb   # Phase 1 + 2 (run in Colab)
Autonomous_Scientific_Animation_Studio_MVP.ipynb             # Phase 1 only
src/autostudio/                                              # importable package (22 modules)
tests/smoke_test.py                                          # Phase-1 offline deterministic test
tests/full_render_smoke.py                                   # full media path -> real MP4 (offline)
requirements.txt
```

## Topic input modes

- **manual** — use the topic verbatim.
- **keyword** — expand a keyword (e.g. `"Black Hole"`) into ranked candidates, select the best.
- **auto** — discover trending science headlines, rank, and select.

## Per-run output

```
output/<run_id>/
  research/{research.json, validation.json}   scripts/script.json
  storyboards/storyboard.json                 metadata/seo.json
  assets/<asset_id>.svg                        scenes/scene01.svg …
  audio/{voice_track.m4a, final_audio.m4a, timeline.json}
  captions/{captions.srt, captions.ass}        video/short.mp4  (+ render_report.json)
  manifest.json                                (+ <run_id>.zip)
```

## Testing (institutional-grade, offline)

Both tests run with **no network and no model** and are the CI gates:

```bash
# System deps for the full render test:
apt-get install -y ffmpeg espeak-ng libcairo2 libpango-1.0-0 fonts-dejavu-core
pip install -r requirements.txt   # or the light subset for the Phase-1 test

python tests/smoke_test.py         # Phase 1: validation → SVG cache → composition
python tests/full_render_smoke.py  # Full: → real ffprobe-valid 1080×1920 MP4
```

`tests/full_render_smoke.py` was validated on FFmpeg 6.1 + CairoSVG, producing a
1080×1920 @ 30fps H.264+AAC MP4 that passes every `ffprobe` check.

## Phase 2 / production integration

`storyboard.json` + the SVG asset cache feed directly into **Motion Canvas** or
**Remotion**; `StudioPipeline.run()` becomes a **FastAPI** job; the same pins +
system packages containerize under **Docker**; `video/short.mp4` + `metadata/seo.json`
are ready for a **YouTube uploader** after a separate OAuth step.
