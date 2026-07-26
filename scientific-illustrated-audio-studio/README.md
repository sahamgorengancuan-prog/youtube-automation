# SIAS — Scientific Illustrated Audio Studio

Image-first audiovisual storytelling: **editorial still illustrations
synchronized to natural spoken narration.** Never video-first — no AI video
generation, no temporal diffusion, no character animation, no reference-video
dependency.

A topic becomes a short-form episode: original *Scientific Notebook Cartoon*
illustrations + one natural narration track + accurate scene-to-audio timing +
optional captions + subtle deterministic camera motion → MP4 + SRT + episode
manifest + QC report.

## Architecture (short)

```
topic → research claims → hooks → 8 story beats → spoken script → scene specs
      → style lock (board/character/prop sheets, dual vision review, human approval)
      → BFL candidates → Qwen structural + Gemini editorial review → consensus
      → approved scene library (conservative repair, max 2 attempts)
      → one TTS narration track → transcription → word alignment → scene timings
      → FFmpeg deterministic render (zoom ≤4%, pan ≤3%) → final AV QC → package
```

Audio duration is the source of truth: scene durations are derived from the
generated narration's word timestamps, never assigned beforehand. See
`docs/architecture.md` and `docs/adr/`.

## Quick start

```bash
pip install -e ".[dev]"           # or: pip install pydantic pyyaml pillow pytest
cp .env.example .env               # fill keys only when going live
make test                          # offline suite; no live calls, no keys needed
make plan                          # full plan-mode pipeline for the example topic
```

Requires Python ≥3.10 and `ffmpeg`/`ffprobe` on PATH for rendering/QC.

## Run modes

| mode | paid calls | what runs |
|---|---|---|
| `plan` (default) | **no** | research draft, hooks, beats, script, scene specs |
| `style_lock` | yes | style board, character/prop sheets, dual review |
| `pilot` | yes | 4 pilot scenes + TTS + alignment + render + pilot QC |
| `production` | yes | full pipeline — **locked until pilot QC = PASS** |
| `repair` | yes | selected scene repairs (max 2 per scene) + re-render |
| `render_only` | **no** | timeline, captions, ffmpeg render, local QC |

Production without a passing pilot requires the explicit
`allow_production_without_pilot: true` flag and is recorded as a warning.

## Secrets

Environment only: `BFL_API_KEY`, `OPENAI_API_KEY`, `OPENROUTER_API_KEY`,
`SIAS_WORKSPACE`. Keys are never printed, logged, or serialized; OpenRouter
requests send `data_collection: deny` by default.

## CLI

```bash
sias audit --json
sias plan  --episode examples/nonstop_rain/episode.yaml
sias style-lock --dry-run
sias pilot --dry-run          # shows provider/model/est. calls/budget; spends nothing
sias produce --dry-run
sias render
sias qc --narration-duration 62.5
sias repair --scene S04 --dry-run
```

`--dry-run` never calls paid APIs. Every paid section in the notebook shows
provider, model, estimated calls, budget cap, cache status, run mode, and an
explicit `ARM_PAID_CALLS` guard.

## Notebook workflow

`notebooks/SIAS_Control_Center.ipynb` — 27 operational sections from
environment checks to export package. It opens and runs in plan mode without
any API keys; nothing spends on import.

## Cost safety

`BudgetLedger` enforces `max_image_calls` / `max_vision_calls` /
`max_tts_characters` **before** each paid call; pilot-first economics gate full
production behind a passing 4-scene pilot. See `docs/cost_controls.md`.

## Expected outputs

```
workspace/<episode_id>/render/final.mp4
workspace/<episode_id>/render/final.srt
workspace/<episode_id>/manifests/episode_manifest.json
workspace/<episode_id>/qc/final_qc.json + final_qc.md
workspace/<episode_id>/package/
```

## Provider notes

BFL (images: style boards, sheets, candidates, conservative edits), OpenAI
(story JSON, TTS, transcription), OpenRouter (Qwen structural + Gemini
editorial vision review; model resolver queries the live catalog). All payload
shapes live in `src/sias/providers/` — one adapter module per provider.
Consult current provider docs before live integration; historical endpoint
schemas are not assumed current (`docs/provider_contracts.md`).
