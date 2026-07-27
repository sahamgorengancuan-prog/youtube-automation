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
