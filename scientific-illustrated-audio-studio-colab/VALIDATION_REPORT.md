# Validation report

Date: 2026-07-27 · Branch: claude/scientific-animation-studio-mvp-65rpn4 ·
Environment: Linux, Python 3.11, ffmpeg/ffprobe present, NO API keys.

| Check | Command | Result |
|---|---|---|
| Lint | `ruff check src tests tools` | **PASS** (0 errors) |
| Colab-layer tests | `pytest tests -q` | **17/17 PASS** (unit + mocked provider contracts incl. Higgsfield/canary + notebook suite) |
| Engine tests (untouched) | `pytest` in ../scientific-illustrated-audio-studio | **52/52 PASS** |
| Notebook JSON + parse + secret scan | `python tools/validate_notebook.py` | **PASS** (3 notebooks) |
| Keyless top-to-bottom plan execution | `python tools/execute_plan_notebook.py` | **PASS** — 40 cells, 0 errors, 0 paid sections armed, "Panggilan berbayar: 0" |
| Render smoke test | `python tools/local_render_smoke_test.py` | **PASS** — audio+video streams, duration Δ 0.0 s, 0 black frames, watermarked placeholders rejected by production QC 4/4 |
| Paid calls made during build | — | **0** |
| Secrets exposed in any output | secret-pattern scan in validate_notebook | **none** |

Notes: the plan execution deletes all six secret env vars before running, so
the run proves fresh-runtime keyless behavior. The smoke MP4 is at
`workspace_smoke/smoke.mp4` (gitignored; regenerate with `make smoke`).

## Addendum — One-Click notebook

| Check | Command | Result |
|---|---|---|
| run_all() offline single call | `pytest tests/integration/test_oneclick.py::test_run_all_offline_single_call` | **PASS** — PREVIEW mode, QC PASS, MP4+SRT+manifest+ZIP, 0 paid calls, all scene PNGs carry the placeholder marker |
| One-click notebook keyless Run All | `python tools/execute_oneclick_notebook.py` | **PASS** — 5 cells, 0 errors, MODE: PREVIEW, final.mp4 produced |
| Full suite after addition | `pytest tests -q` | **19/19 PASS** |

## Addendum — One-Click FULL (Diamond-integrated)

| Check | Result |
|---|---|
| `run_all()` 10-stage single call (offline) | **PASS** — routing snapshot, consistency flags, pre-reveal silence, audio checks, SRT+ASS, Diamond gate, manifest, ZIP |
| ASS Diamond subtitles | **PASS** — Default/Emphasis/Reveal/SciTerm/Note styles emitted, chunked dialogues |
| Diamond Editorial gate | **PASS** — 5 pillars evaluated (all PASS on the preview episode), 14 checks, 6 human gates held PENDING by default |
| Human-gate flag | **PASS** — `human_gates_approved=True` is the only path to Diamond `PASS` |
| Keyless notebook Run All | **PASS** — 0 errors, MODE: PREVIEW, MP4 produced |
| Full suite | **36/36 PASS**, ruff clean, 0 paid calls |

## Addendum — One-Click **STANDALONE** + simulated-provider preflight

The notebook now carries its own engine and is proven by running it, not by
describing it.

| Check | Command | Result |
|---|---|---|
| Notebook is genuinely standalone | `pytest tests/unit/test_standalone_notebook.py` | **PASS** — no `git clone` / `github.com` / `curl` / `wget` in any code cell; 104 source files + `configs/default.yaml` embedded |
| Embedded payload matches the repo | `test_payload_is_in_sync_with_the_repo_sources` | **PASS** — declared SHA-256 equals a fresh deterministic rebuild (release-drift guard) |
| Inputs stay minimal | `test_inputs_stay_minimal_and_defaults_stay_locked` | **PASS** — exactly `TOPIC`, `LANGUAGE`, `RUN_LIVE`, `HUMAN_GATES_APPROVED`; model/voice/budget/scale/workspace locked |
| Preflight gates the run | `test_preflight_runs_before_any_paid_stage` | **PASS** — simulation cell precedes `run_all` and asserts `PASS` |
| Simulated provider responses | `pytest tests/unit/test_preflight_simulation.py` | **PASS** — 17 scenarios; the suite also proves a mishandled response makes the preflight fail |
| Keyless headless Run All | `python tools/execute_standalone_notebook.py` | **PASS** — 8 cells, 0 errors, payload SHA verified, `MODE: PREVIEW`, QC PASS, MP4 574 KB + SRT + ASS + ZIP |
| Full LIVE path on simulated providers | `test_live_path_end_to_end_against_simulated_providers` | **PASS** — `LIVE`, QC PASS, Diamond PASS, Δ=0.0 s, dual review ran per scene, **0 paid calls** |
| Colab-layer suite | `pytest tests -q` | **55/55 PASS** |
| Engine suite | `pytest -q` in ../scientific-illustrated-audio-studio | **62/62 PASS** |
| Lint | `ruff check src tests tools` | **PASS** |
| Paid calls made | — | **0** |

### Two live-only defects the simulation found (and fixed)

Both were unreachable from PREVIEW and would have crashed a real paid run.

1. **`wave.Error: unknown format: 65534`** — FFmpeg writes
   WAVE_FORMAT_EXTENSIBLE for filtered output, so the `-16 LUFS` loudnorm
   pre-master could not be read by any of the audio gates (`wav_stats`,
   `assert_not_silent`, `audio_checks`). Fixed with `sias.audio.wavio.open_wav`,
   which normalises the `fmt ` chunk when the subformat really is PCM and
   refuses anything else, plus `-c:a pcm_s16le` pinned on the mastering
   commands.
2. **Accumulating frame drift → final duration gate failure** — each clip's
   length was rounded independently, losing up to half a frame per scene; over
   seven scenes the video came out 0.090 s short against a ±0.08 s tolerance.
   Fixed with `render.ffmpeg.quantized_frames`, which derives each clip's frame
   count from *cumulative* timeline positions and pins it with `-frames:v`, so
   the clips always sum to the rounded total (Δ now 0.0 s).

## Addendum — v5 Institutional Lab Notebook visual identity

Redesign of the visual identity to the approved reference sheet. Full policy in
`docs/visual_policy.md`.

| Check | Command | Result |
|---|---|---|
| Identity dispatch, closed palette, derivations, compositor, prompt, rubrics | `pytest tests/unit/test_institutional_identity.py` | **33/33 PASS** |
| One spec composes every delivery format | `test_panel_composes_in_both_orientations` | **PASS** — 16:9, 9:16, 1:1 from the same `PanelSpec` |
| Headline never overflows the safe margin | `test_headline_never_overflows_the_safe_margin` | **PASS** — long titles shrink, never clip |
| Split panel keeps the headline on the white side | `test_split_panel_headline_stays_on_the_white_side` | **PASS** — no ink in the dark headline band |
| Readout never fabricates a measurement | `test_readout_without_a_metric_reports_status_not_a_number` | **PASS** — no digits without `panel_metric` |
| Prompt forbids all typography and spells out the palette | `test_institutional_prompt_forbids_typography_and_locks_the_palette` | **PASS** |
| Legacy identity still compiles | `test_legacy_identity_still_compiles_its_own_prompt` | **PASS** — v5 is a dispatch, not a deletion |
| Engine suite | `pytest -q` | **95/95 PASS** |
| Colab suite | `pytest -q` | **55/55 PASS** |
| Keyless headless Run All | `python tools/execute_standalone_notebook.py` | **PASS** — 8 cells, 0 errors, QC PASS, 698 KB MP4 |
| Lint | `ruff check src tests tools` | **PASS** |
| Paid calls made | — | **0** |

### Three typography defects found by rendering real episodes

Each was visible only by composing a full episode and looking at it.

1. **Apostrophes split words** — `[A-Za-z…]+` turned "isn't" into two tokens, so a
   panel read `REAL SURPRISE ISN T`. The word pattern now keeps apostrophes.
2. **Stranded ampersands** — converting "and" to "&" left `SPINNING &` and
   `& SYSTEMS` after truncation. Ampersands are now trimmed unless they join two
   kept words.
3. **Planner scaffolding reached headlines** — `visual_objective` is
   `"fact_1 beat for <topic>"` in keyless plan mode, which produced
   `FACT 1 BEAT WHAT`. Scaffolding is now recognised and narration is preferred
   as the headline source. The same fix stopped every preview panel drawing the
   same globe: narration restates the topic in every scene, so it is a poor
   discriminator for schematic choice and is excluded from it.
