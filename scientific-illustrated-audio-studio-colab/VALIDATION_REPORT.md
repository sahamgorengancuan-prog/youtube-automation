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
