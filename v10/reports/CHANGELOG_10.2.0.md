# Scientific Motion Studio — 10.2.0 (Control Center edition)

Builds on the hardened 10.1.0 package. No architecture was replaced; the
provider locks, resumable runtime, cache, provenance and security model are
unchanged and re-verified.

## Added

* **`scistudio_v10/notebook_ui.py`** — ipywidgets Control Center
  (`launch_control_center()`): 9 tabs (Project, Execution, Providers &
  Secrets, Model & Generation, Temporal & Rendering, Advanced Config,
  Preflight & Tests, Run Monitor, Results & Artifacts), summary cards,
  status badges (✓/⚠/✕/○/●), progressive disclosure, contextual warnings,
  disabled-until-eligible primary action, video upload/preview, config
  form↔JSON round-trip with diff/import/export, manifest comparison,
  one-click ZIP. Graceful `HeadlessControlCenter` fallback when ipywidgets
  is unavailable.
* **`scistudio_v10/notebook_runner.py`** — unified
  `NotebookRunController` + Pydantic `PipelineRunRequest` +
  `PipelineAction` enum (14 actions). All UIs (notebook, Streamlit,
  headless) dispatch through it. Includes: 7 validated config presets
  (production presets provider-locked), readable/stable `generate_job_id`,
  standard workspace layout (`configs/ jobs/ reports/ smoke_tests/ exports/
  logs/`), per-run `run_request.json` + `resolved_config.json` (redacted),
  `error_report.json` on failure, scoped force-rerun
  (all / failed+downstream / selected+downstream), path-safe artifact ZIP,
  shared `security_source_sweep`, `SecretsManager`
  (Colab Secrets → env → manual password widgets; memory-only, redaction-
  registered), `RunMonitorState` and `CancellationToken`.
* **`scistudio_v10/preflight.py`** — structured preflight
  (PASS/WARNING/FAIL/SKIPPED): Python/OS/CPU/RAM/GPU/disk, workspace write
  probe, package imports, config + provider-lock validation, per-action
  secret requirements, reference-video validation, ffmpeg/ffprobe,
  node/npm/npx + Remotion prerequisites, live-only network check,
  temporal-shell and publishing safety, existing job manifest/lock;
  persisted as `preflight_report.json`. Tool lookup injectable for tests.
* **Paid-run safety gate** — live actions require checkbox + typed
  `RUN LIVE` + configured keys + zero preflight FAILs + reviewed request
  summary (request *counts*, never currency estimates).
* **Pipeline progress/cancellation** — `run(..., progress_callback=,
  cancellation_token=)` (both optional; old signature untouched). Stage
  events (`cached/started/completed/failed`) flow from the job runtime; a
  set token stops after the current safe stage with `JobCancelledError`
  (job stays resumable). The stop button is labelled accordingly — no fake
  cancel.
* **Layered exceptions** — `PreflightError`,
  `ProviderAuthenticationError`, `ProviderRateLimitError`,
  `ProviderTimeoutError`, `ReferenceVideoError`, `RenderDependencyError`,
  `PipelineStageError`, `PublishingError`, `JobCancelledError`, plus
  `classify_provider_error()` for user-facing translation.
* **`scistudio_v10/tests_notebook.py`** — 52 new offline checks (presets,
  round-trips, paid gate, missing keys, reference validation, preflight
  tool injection, dispatch routing, quick smoke, resume, cancellation,
  scoped force, ZIP + traversal safety, secret leak scan over all reports,
  backward compatibility, non-Colab import, ipywidgets fallback, widget
  gate behavior). Wired into `run_all_tests` (total now 263).
* `pyproject.toml`: new `notebook` extra (`ipywidgets`, `IPython`).

## Changed

* **Notebook** rebuilt into 13 documented sections (Overview/Safety →
  Bootstrap → Package Generation → Dependencies → Import/Reload → Presets →
  Secrets → **Control Center** → Headless Usage → Offline Validation →
  Optional Live Smoke → Summary → Export). Every Run-all cell is offline.
* **`webui.py`** now drives the same `NotebookRunController` (config
  import, upload, preflight, paid gate, progress bar, redacted errors) —
  no separate pipeline logic.
* `job_runtime.ResumableJobRuntime` accepts optional `on_stage_event` /
  `cancellation_token` (backward compatible).
* Version 10.1.0 → 10.2.0.

## Unchanged (verified)

* `ScientificMotionStudioV10(config).run(topic, reference, plan_only=…,
  render_video=…, force=…, job_id=…)` and the
  `scistudio-v10 "topic" ref.mp4 --config config.json` CLI.
* Production provider locks, security sweeps, provenance, asset registry,
  candidate tournament, temporal routing, Remotion rendering, publishing.
* All 211 pre-existing checks still pass (92 regression + 119 finalization).
