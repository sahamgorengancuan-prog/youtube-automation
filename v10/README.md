# Scientific Motion Studio V10 (10.1.0) — Agentic Production Architecture

## Executive summary

Scientific Motion Studio turns a science topic plus a style-reference video
into a short editorial science animation. Version 10.1 is the hardened,
production-finalized revision of the V10 agentic architecture: a resumable
job runtime, retrieval-based visual continuity, multi-candidate FLUX
tournaments, an executive vision art director, semantic layer separation and
hybrid Remotion rendering — now with validated configuration, locked
production providers, classified retries, safe subprocess execution, atomic
state, secret redaction and a layered offline test suite (210 checks).

## Provider responsibility matrix

| Responsibility | Production provider | Fallback in production |
|---|---|---|
| Narrative architecture, research synthesis, script + criticism, storyboard, scene illustration architecture, continuity planning, candidate ranking, structured JSON decisions | **OpenAI GPT** (Responses API, `OPENAI_API_KEY`) | **None — fails clearly** |
| Visual critique / approval (vision) | **OpenAI GPT (vision)** | **None — fails clearly** |
| Master style anchors, beauty frames, reference-conditioned scenes, local revisions, pose/state variants, continuity-preserving edits | **BFL FLUX Kontext** (`BFL_API_KEY`, `flux-kontext-pro`/`-max`) | **None — fails clearly** |
| Narration | Edge-TTS / espeak (local tooling) | Silence with warning |
| Rendering | Remotion (Node) or deterministic PIL preview | Explicit config choice |

Anthropic/Claude is **not** a runtime provider: no SDK dependency, no API
calls, no `ANTHROPIC_API_KEY`. Gemini, OpenRouter and local Qwen/diffusers
code paths exist for **development mode only** and are rejected by
configuration validation and by the runtime router in production mode.
Deterministic/fake providers are limited to `test`/`development` modes; the
pipeline constructor rejects injected providers in production.

## Execution modes

* `production` — provider locks enforced at config-validation time and again
  at runtime. Missing/failed OpenAI or BFL ⇒ `ProviderUnavailableError`; the
  run stops. No silent fallback of any kind.
* `development` — alternative providers permitted if explicitly configured;
  deterministic fallbacks allowed but cached separately and marked.
* `test` — like development; intended for injected deterministic providers.

## Architecture and data flow

```
topic + reference.mp4
  → 01 style reference extraction (ffmpeg frames → style board)
  → 02 public research (Wikipedia/Crossref/OpenAlex/arXiv) + GPT evidence pack
  → 03 script (GPT) → 04 storyboard (GPT) → 05 audio (Edge-TTS)
  → 06 art-direction bible + 07 continuity canon (GPT, locked style canon)
  → 08 scene illustration architectures → 09 shot states (GPT)
  → 10 reference retrieval (asset registry) → 11 drawing briefs (FLUX prompts)
  → 12 candidate tournament (FLUX Kontext × N seeds, GPT-vision ranking)
  → 13 executive art-director revisions (FLUX Kontext local edits)
  → 14 semantic layer separation → 15 overlays → 16 animation plans
  → 17 temporal backends (optional sketch-video / deterministic compositor)
  → 18 hybrid packages → 19 Remotion (or PIL) render → MP4
```

Every stage is executed through the resumable job runtime and recorded in
`job_manifest.json`; a provenance manifest (`provenance_manifest.json`) links
all artifacts by SHA-256.

## Installation

```bash
pip install .                 # core production path (OpenAI + BFL)
pip install .[api]            # + FastAPI service
pip install .[webui]          # + Streamlit UI
pip install .[render]         # + SVG rasterization for PIL fallback
pip install .[local-models]   # optional heavy local model support (dev only)
pip install .[dev,test]       # lint/build/coverage/notebook tooling
```

Runtime requirements: Python ≥ 3.11, FFmpeg on PATH. Remotion rendering
additionally needs Node.js + npm.

Credentials come **only** from environment variables (or an explicit secrets
dict): see `.env.example`. Never commit real keys.

## Notebook usage

`Scientific_Motion_Studio_v10_Agentic_Production_FINAL.ipynb` is the
self-contained source of truth: run it top-to-bottom from a clean kernel and
it (1) validates the environment, (2) materializes this exact package,
(3) validates configuration, (4) runs the full offline suite, (5) optionally
runs explicit paid live smoke tests (`SCISTUDIO_RUN_LIVE_TESTS=1`),
(6) shows usage, and (7) writes `final_validation_report.json`. Rerunning the
notebook is idempotent.

## CLI usage

```bash
scistudio-v10 "What happens if it rains nonstop for a year?" reference.mp4 \
    --config config.json                  # plan-only (default, no paid calls)
scistudio-v10 TOPIC reference.mp4 --config config.json --live --render
scistudio-v10 --version
```

## API usage

```python
from scistudio_v10.service_api import create_app
from scistudio_v10 import ScientificMotionStudioV10
import json
app = create_app(lambda: ScientificMotionStudioV10(json.load(open("config.json"))))
# uvicorn module:app --host 127.0.0.1
```

`POST /jobs` returns `202` + job id and runs generation in a background
thread (single-worker; deploy a real queue for multi-instance production —
distributed execution is not implemented). `GET /jobs/{id}` returns status.
Errors are structured and redacted.

## Security model

* **No shell execution of templated commands.** External temporal backends
  use argv token lists with per-token placeholder substitution. An optional
  shell path exists for advanced users only: disabled by default, marked
  unsafe, and rejected in production without
  `security_override_unsafe_shell=true`.
* **SSRF protection.** Provider-supplied URLs (BFL polling/result URLs,
  publishing endpoints) must be https with hosts on a configured allowlist.
* **Download safety.** `raise_for_status`, Content-Type check, byte cap,
  Pillow verification, atomic temp-file + rename.
* **Secret redaction.** Loaded secrets are registered for exact-match
  scrubbing; realistic patterns (`sk-…`, bearer, `x-key`, UUID keys) are also
  redacted from errors, logs, manifests and API responses. Command failures
  are redacted and length-capped.
* **Path safety.** `ensure_within` guards workspace-relative outputs;
  archives are never extracted from untrusted sources.
* **No credentials in images/layers/config files.**

## Cache semantics

* Success cache is content-addressed over every generation input (provider,
  model, endpoint, prompts, schema, temperature/effort/limits, image
  SHA-256, seed, cache schema version). Filenames/sizes are never used as
  identity.
* Transient provider failures are never cached as successes. Development
  fallback values live in a separate `_fallback` namespace, carry an explicit
  marker + failure reason, and expire (`fallback_cache_ttl_s`, default 1 h).
* Corrupt cache files are quarantined (`*.corrupt-<ts>`) and regenerated.
* All writes are atomic.

## Resume semantics

`job_manifest.json` (schema 10.1) records stage status, input hash, output
path + SHA-256, attempts and redacted errors. Resume trusts a completed stage
only if its artifact exists and matches its checksum. Stages left `running`
by a crashed worker are recovered as `interrupted` and re-run. A config-hash
change marks completed stages `stale`. A pid lock file rejects concurrent
workers on one job (stale locks from dead pids are reclaimed). Stage retries
are budgeted (`limits.max_stage_attempts`).

## Failure behavior

Production failures raise typed exceptions (`ProviderUnavailableError`,
`BFLError`, `DownloadPolicyError`, `JobConcurrencyError`, …) with redacted
messages; the job manifest records the failing stage. Retryable HTTP statuses
(408/409/429/5xx) are retried with exponential backoff + jitter, honouring
`Retry-After`. Auth/validation failures are never retried.

## Testing procedure

```bash
python -m scistudio_v10.tests_final          # 210 offline checks, exit code
python -m coverage run --source=scistudio_v10 -m scistudio_v10.tests_final
python -m coverage report
ruff check scistudio_v10/
python -m build && pip check
```

No test spends live API credits. The only live calls are the notebook's
explicit opt-in smoke cells gated by `SCISTUDIO_RUN_LIVE_TESTS=1`.

## Deployment

```bash
docker build -t scistudio-v10 .
docker run --rm -e OPENAI_API_KEY -e BFL_API_KEY \
    -v "$PWD/work:/work" scistudio-v10 \
    "TOPIC" /work/reference.mp4 --config /work/config.json
```

The image is multi-stage, runs as a non-root user, reads credentials only
from the runtime environment and ships a `--version` healthcheck.

## Limitations and paid-provider boundary

* Live FLUX Kontext output quality and live OpenAI responses are **not**
  validated by the offline suite; run the opt-in live smoke tests.
* The FastAPI shell is single-worker; distributed job execution is not
  implemented.
* Sketch-controlled temporal video requires an external, user-supplied model
  command; without it, high-complexity motion intentionally fails rather
  than degrading.
* Docker build/run is validated only where Docker is available.

## Reproducibility statement

All creative stages are cached content-addressed; identical inputs (prompts,
seeds, models, config) reuse identical cached outputs. The notebook
regenerates this package byte-for-byte, and the provenance manifest links
every published artifact to its input hashes.

## Validation evidence

See `FINALIZATION_REPORT.md` and `final_validation_report.json` for the
exact command matrix (PASS/FAIL/NOT RUN), coverage, and the security sweep
results for this revision.
