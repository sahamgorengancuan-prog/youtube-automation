# Autonomous Scientific Animation Studio — MVP (Phase 1)

Production-ready foundation for an autonomous studio that turns a **topic** into a
complete, reusable **storyboard with cached SVG assets** and a **static scene
preview**. Everything is delivered as **one Google Colab notebook** —
[`Autonomous_Scientific_Animation_Studio_MVP.ipynb`](./Autonomous_Scientific_Animation_Studio_MVP.ipynb) —
that also writes an importable `src/autostudio/` package for FastAPI / Docker.

```text
Topic
  → Free web research (DDGS + Wikipedia + Crossref + OpenAlex + arXiv)
  → Deterministic scientific validation
  → Retention-first script (timed beats)
  → Storyboard JSON
  → Scene plan (asset catalogue)
  → SVG asset generation (only what this storyboard needs)
  → Content-addressed asset cache (reuse across runs)
  → Static scene composition + storyboard preview
```

## Scope

This is **Phase 1**. It stops at the storyboard and a **static** scene preview.
Video rendering — animation, voice-over, music, subtitles, MP4 — is **Phase 2**.
The `storyboard.json`, the SVG cache, and the per-scene `AnimationPlaceholder`
/ `camera` / `motion` / `transition` fields are the hand-off contract to it.

## Why these design choices

- **One notebook, one importable package.** Every notebook section writes a real
  module into `src/autostudio/`; nothing lives only in a cell. `StudioPipeline.run()`
  is the single entry point and drops straight into a FastAPI background job.
- **Deterministic SVG.** The LLM never writes SVG — it only names an `asset_type`
  from a whitelist, and Python renders the flat vector from a controlled template.
  Result: reproducible, editable, cacheable assets with no gradients/textures/raster.
- **Cache-first.** Web search, LLM responses, and SVG assets are content-addressed.
  Re-running an identical stage is a cache hit. Assets carry metadata + a reuse
  counter and are shared across topics and runs.
- **Local-first models.** Qwen (HuggingFace) primary; OpenAI is an optional
  fallback used only if `OPENAI_API_KEY` is set and local generation fails.
- **Low GPU usage & graceful fallback.** GPU is used only by Qwen; every
  generative stage has a deterministic fallback so a flaky network never aborts a run.

## Repository layout

```
Autonomous_Scientific_Animation_Studio_MVP.ipynb   # the deliverable (run in Colab)
src/autostudio/                                     # importable package (18 modules)
tests/smoke_test.py                                 # offline, no-network deterministic test
requirements.txt
```

## Topic input modes

- **manual** — use the topic verbatim.
- **keyword** — expand a keyword (e.g. `"Black Hole"`) into ranked candidates, select the best.
- **auto** — discover trending science headlines, rank, and select.

## Per-run output

```
output/<run_id>/
  research/{research.json, validation.json}
  scripts/script.json
  storyboards/storyboard.json
  assets/<asset_id>.svg
  scenes/scene01.svg …
  storyboard_contact_sheet.svg (+ .png)   preview.html
  manifest.json                            (+ <run_id>.zip)
```

## Quick offline check (no GPU / no internet)

```bash
pip install pydantic PyYAML svgwrite numpy
python tests/smoke_test.py
```

Validates the full deterministic path — validation → script timing → storyboard
→ scene plan → SVG cache → composition — and proves cache reuse and SVG safety.

## Phase 2 integration

`storyboard.json` + the SVG asset cache feed directly into **Motion Canvas** or
**Remotion**; `StudioPipeline.run()` becomes a **FastAPI** job; the same pins
containerize under **Docker** — all without rewriting the core.
