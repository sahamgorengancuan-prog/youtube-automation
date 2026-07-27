# Visual policy — SIAS Institutional Lab Notebook (v5)

The identity is a **white simulation-report panel**: a hairline frame, a small
outlined status block top-left, a bracketed instrument readout top-right, one
heavy all-caps headline, and a single flat-vector diagram drawn from a closed
palette. Night subjects invert to a near-black panel or split across a seam.

## The rule that makes it reproducible

**The image model draws the diagram. Code typesets every glyph.**

| Element | Produced by | Why |
|---|---|---|
| Frame, status block, instrument readout, headline | `sias.render.hud.compose_panel` (PIL) | Pixel-identical on every panel of every episode |
| Flat-vector diagram | BFL FLUX.2 (live) / `sias.render.schematic` (preview) | The part a model is actually good at |

The reference sheet this identity was derived from reads **"STOPPED STOPPED
SPINNING"** and **"EXPERIIMENT #034"** — the signature of model-rendered
lettering. Rather than review for that failure, v5 removes it: the prompt
forbids all typography and the reviewers hard-fail `HF_TEXT_HALLUCINATION` on any
drawn glyph, even a correctly spelled one.

## Closed palette

`sias.style.institutional.PALETTE` is the whole colour system. It is **not**
merged from config — changing it is a reviewable code change, and the structural
reviewer is told to fail anything outside it.

| Role | Hex | Used for |
|---|---|---|
| `paper` | `#FFFFFF` | panel background |
| `ink` | `#14161A` | contour, frame, headline |
| `primary` | `#3E7EB8` | the one saturated colour — water, wind, motion |
| `primary_soft` | `#B9D5EA` | fills inside blue objects, windows |
| `graphite` | `#3C4046` | dark masses: land, silhouettes |
| `steel` | `#7C8288` | secondary structures |
| `mist` | `#E4E6E8` | neutral fills |
| `night` | `#23262B` | inverted panel |
| `alert` | `#C0392B` | critical readout marker only |
| `warn` | `#F2C744` | sun / caution accent only |

Flat fills only: one contour weight, no gradient, shadow, gloss or texture.

## Layout

Expressed in fractions, so one `PanelSpec` composes 16:9, 9:16 and 1:1 without a
second set of numbers.

- **Reserved bands:** top 13% and bottom 32% stay visually quiet — the HUD and
  headline are composited into exactly those rows. The prompt quotes the same
  fractions to the model.
- **Headline anchors:** `top_center` (title card), `bottom_left` (in-panel
  label), `bottom_center` (payoff). The content box moves out of the headline's
  way automatically, so type never lands on the subject.
- **Split panels:** the headline is width-constrained to the white side; the
  readout inverts to white because it sits on the dark side.
- **Auto-fit:** headlines shrink before they overflow the safe margin. A long
  title degrades to smaller type, never to clipped type.

## The readout never invents a number

`derive_readout` returns a real figure **only** when the scene supplies one via
`SceneSpec.panel_metric` (which upstream fills from an evidence-backed claim).
With no metric it reports simulation status — `RUNNING`, `CRITICAL`, `COMPLETE` —
never a plausible-looking measurement. A fabricated figure on a science panel is
worse than no figure at all.

## Headline derivation

Source priority: `panel_title` → `scientific_labels` → `narration` →
`visual_objective`. Planner scaffolding (`"fact_1 beat for <topic>"`) is
recognised and skipped. Filler is dropped, apostrophes are preserved, `and`
becomes `&` and stranded ampersands are trimmed, and words are **never
reordered** — silently rewriting an editor's title would be worse than a long
line.

**Known limitation:** in keyless PREVIEW the headline comes from the scaffold
narration, so it reads like a sentence fragment (`GROUND STOPS COOPERATING`)
rather than an editorial title (`ROTATION SPEED`). A live run fills `panel_title`
from the story agent. The layout, typography and safe zones you approve in
PREVIEW are exactly what LIVE reproduces; only the words and the artwork change.

## Preview panels are real panels

`make_preview_panel` composes the same frame, HUD, typography and safe zones as a
live episode, with a deterministic schematic standing in for the artwork. Every
preview panel carries a red watermark bar along the bottom edge — outside the
artwork and outside the headline, so the layout stays reviewable — plus a PNG
`tEXt` marker (`sias_placeholder`) that production QC rejects even if the file is
renamed.

## Review rubrics

Both reviewers are told the diagram carries no typography.

- **Structural (Qwen VL):** `HF_TEXT_HALLUCINATION` for any drawn glyph;
  `HF_STYLE_DRIFT` for gradients, shading, texture or off-palette colour;
  `HF_COMPOSITION_UNREADABLE` for a busy reserved band or competing subjects.
- **Editorial (Gemini):** judges whether the drawing alone communicates the idea
  to someone who cannot read a label, and whether the silhouette still reads once
  the headline covers the lower third.
