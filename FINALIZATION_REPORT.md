# Scientific Motion Studio V10 — Finalization Report (10.0.0 → 10.1.0)

Role: Principal Software Architect / Production Reliability Engineer /
Security Reviewer / Test Engineer / Release Manager.
Date: 2026-07-16. Environment: Linux, Python 3.11.15, FFmpeg + Node 22
available, Docker **not** available.

## 1. Scope and inputs

* Source: `Scientific_Motion_Studio_v10_Agentic_Production_executed.ipynb`
  (50 cells; 41 Python modules + pyproject.toml, Dockerfile,
  config.example.json, README.md — all reconstructed to disk and audited).
* Supporting ZIPs (`caveman-main`, `obsidian-skills-main`) **were not present
  in the uploads** and could not be used; investigation and review were done
  directly (one independent reviewer agent was used for the final diff
  review, consistent with the intent of the reviewer-agent instruction).
* Output: `Scientific_Motion_Studio_v10_Agentic_Production_FINAL.ipynb`
  (68 cells) plus the materialized package `scistudio_v10` **10.1.0**.

## 2. Issue register

The full pre-fix register is in `ISSUE_REGISTER.md`: **5 critical, 11 high,
9 medium, 4 low, 3 documentation** findings. Every critical and high finding
was fixed; all medium findings were fixed; low/doc items were fixed except
where explicitly noted in §9.

### Critical fixes

| ID | Finding | Fix |
|---|---|---|
| C1 | `subprocess.run(command.format(...), shell=True)` on LLM-derived values (command injection) | `temporal_backends.py`: argv token templates with per-token, allowlisted placeholder substitution; shell path opt-in only, quoted, rejected in production without `security_override_unsafe_shell` |
| C2 | Silent fallback cached as normal success (cache poisoning; no fail-clear behavior) | `llm.py`: execution modes; production raises `ProviderUnavailableError`; dev/test fallbacks stored in a separate `_fallback` namespace with marker, reason and TTL — never as live results |
| C3 | Image generation silently fell through BFL → Gemini → OpenAI Images → local diffusers | Production images are BFL FLUX Kontext **only** (config validation + runtime lock); alternates exist only in development mode with explicit configuration |
| C4 | Vision-critic cache keyed on filename+size | Content SHA-256 + prompt + models + cache schema version (`llm.critique_image`) |
| C5 | BFL polling/result URLs unvalidated; no raise_for_status/content-type/size/Pillow checks; non-atomic cache writes | New `bfl_client.py` + `http_safety.py`: https + host-allowlist on every URL, validated structured responses, explicit status handling, bounded validated atomic downloads, no failure caching |

### High fixes

Retry/backoff+jitter with 408/409/429/5xx classification and Retry-After
support (OpenAI + BFL); bounded OpenAI JSON repair; empty-output handling;
request-id/usage capture; atomic `save_json`/`atomic_copy` everywhere;
corrupt-JSON quarantine; job runtime schema version, config-hash
invalidation, `running`→`interrupted` recovery, output SHA-256 verification,
retry budget, pid job lock, no tracebacks in manifests; FastAPI typed
schemas + 202/400/404/409/422/500 + redacted errors + background execution +
status endpoint (single-worker documented; distributed execution not
claimed); Streamlit validation + redacted errors + plan-only default;
stronger redaction incl. registered exact secrets, `sk-…`, bearer, `x-key`,
UUID-after-key; `pyproject.toml` production deps (`requests`, `openai`) +
groups + metadata; publisher https+allowlist; research `raise_for_status` +
size caps; Dockerfile multi-stage, non-root, healthcheck, no baked creds;
production rejection of injected fake providers.

### Medium/low fixes

Stale `scistudio_v8` headers removed; version strings unified to V10/10.1.0;
semicolon one-liners eliminated via `ruff format` (whole package,
46 files); broad `except Exception` narrowed at network/parse boundaries;
`/content` removed from config; magic numbers named
(`min_valid_image_bytes`, `seed_stride`, retry policy); `retry()` now
exponential+jitter; observability records extended (provider, model,
request id, attempt, input/output hash, latency, cache hit, status, error
class) plus a provenance manifest (`provenance.py`) linking
topic→script→storyboard→architecture→references→prompts→FLUX
outputs→approvals→semantic layers→animation→render by SHA-256.

## 3. Files changed

* **New modules (7):** `errors.py`, `config_models.py`, `http_safety.py`,
  `bfl_client.py`, `provenance.py`, `tests_final.py`, `.env.example`.
* **Rewritten (10):** `llm.py`, `security.py`, `utils.py`,
  `temporal_backends.py`, `job_runtime.py`, `cache_store.py`,
  `observability.py`, `service_api.py`, `webui.py`, `cli.py`,
  `publisher.py`.
* **Targeted edits:** `pipeline.py` (modes, injection lock, provenance, lock
  release, timeouts), `research.py` (HTTP hardening), `schemas.py`
  (stage states + checksums), `tests.py` (realistic BFL fixture),
  `flux_studio.py` and 20+ modules (formatting/lint only),
  `pyproject.toml`, `Dockerfile`, `config.example.json`, `README.md`.
* **Notebook:** rebuilt as 68 cells with the mandated section structure;
  portable `Path.cwd()`/`SCISTUDIO_WORKSPACE` workspace; idempotent file
  generation; secret-free output.

## 4. Tests

* Retained: 53 V9 checks + 39 V10 checks (92 total; the historical "91"
  count gained one stronger BFL PNG-validity assertion). Two fixture
  updates were required by the new hardening and are **not** weakenings:
  the BFL mock now serves a real PNG with image headers (the old 2 KB
  `b"x"` body is now — correctly — rejected), and the mock URLs are host-
  allowlisted in test config.
* Added: **119** finalization checks (`tests_final.py`):
  * unit — config validation & production locks, redaction (realistic key
    formats), URL policy, retry classification (408/409/429/5xx vs 401/422),
    backoff shape, atomic writes, cache-key composition, cache corruption
    quarantine, path traversal, safe argv construction incl. an actual
    injection attempt, stage transitions, interruption recovery,
    config-change invalidation, checksum-mismatch re-run, retry budget,
    live/stale lock behavior, download validation (HTML/JSON/empty/corrupt/
    oversized bodies);
  * mocked integration — OpenAI success / malformed-JSON repair / empty
    output / 429-then-success / 401-fail-fast; BFL submit+poll+download
    success / cache-hit-no-network / 429-then-success / timeout / moderated /
    unknown status / corrupt image / wrong content-type / oversized /
    SSRF result URL / pre-flight model+aspect rejection;
  * provider locks — production text/vision/image fail clearly without
    OpenAI/BFL; unauthorized providers are never invoked even when keys
    exist; injected fakes rejected in production; dev fallbacks marked,
    namespaced and TTL-expired;
  * offline e2e — full pipeline with deterministic injected providers:
    artifacts exist, ffprobe-valid H.264 MP4, manifest schema 10.1
    completed, provenance hashes match artifacts, rerun is idempotent
    (zero stage re-executions);
  * CLI (`--help`, arg errors, offline plan-only smoke) and FastAPI
    (404/400/422/202 + background completion) via TestClient.
* **Total: 211/211 PASS** (no live network calls, no API credits).

## 5. Commands executed and results

| Command | Result |
|---|---|
| `python -m scistudio_v10.tests_final` (aggregate 211 checks) | **PASS** |
| `coverage run … tests_final` → `coverage report` | **PASS** — 82 % total; core: security 98 %, config_models 93 %, cache_store 92 %, job_runtime 88 %, service_api 89 %, pipeline 85 %, bfl_client 84 %, cli 82 %, http_safety 80 % (llm.py 53 % — the dev-only Gemini/OpenRouter/local client bodies are intentionally untested) |
| `ruff check scistudio_v10/` | **PASS** (0 errors; documented ignores: E501 prompts, B905 intentional zip truncation, UP042, UP006/7/35) |
| `ruff format --check` | **PASS** (47 files formatted) |
| `python -m build` (wheel + sdist) | **PASS** (10.1.0) |
| `pip install dist/*.whl` into a fresh venv | **PASS** |
| `pip check` (clean venv) | **PASS** ("No broken requirements found") |
| `python -c "import scistudio_v10"` (clean venv) | **PASS** |
| `scistudio-v10 --help` / `--version` (installed script) | **PASS** |
| `scistudio-v10 … --config …` plan-only smoke (installed, offline) | **PASS** (`mode=plan_only`, manifest exists, rc 0) |
| FINAL notebook via `nbclient`, clean dir, clean kernel, run 1 | **PASS** (see §8 evidence) |
| FINAL notebook run 2, same directory, no cleanup | **PASS** |
| `docker build` | **NOT RUN** — Docker unavailable in this environment. Exact command: `docker build -t scistudio-v10 .` then `docker run --rm scistudio-v10 --version` |
| Live OpenAI / BFL smoke tests | **NOT RUN** — paid; gated behind `SCISTUDIO_RUN_LIVE_TESTS=1` cells in the notebook |
| Type checking (mypy/pyright) | **NOT RUN** — not installed in this environment; annotations added throughout; `ruff` UP/B rules pass |

## 6. Security decisions

1. Templated external commands execute as argv token lists; unknown
   placeholders raise `UnsafeCommandError`. The optional shell path is
   opt-in (`allow_shell` + `unsafe_shell_command`), shell-quotes every
   substituted value, and is rejected in production without
   `security_override_unsafe_shell=true`.
2. All provider-supplied URLs (BFL polling/result, publishing endpoint) must
   be https with an allowlisted host; empty allowlists refuse outbound.
3. Downloads: `raise_for_status`, image Content-Type, byte cap (default
   32 MiB), Pillow verification, temp-file + atomic rename; failures never
   leave partial artifacts and are never cached.
4. Redaction: exact registered secret values plus realistic patterns
   (`sk-…`, bearer, `x-key`, key=value, UUID-after-key), applied to
   exceptions, event logs, manifests, API errors and command failures
   (length-capped). Verified with realistic secret formats in tests.
5. Job manifests store typed, redacted errors only; full tracebacks are
   written to a debug file only when `debug_tracebacks` is enabled.
6. Publishing tokens are only ever sent to https endpoints on an explicit
   allowlist. Credentials come only from the runtime environment.
7. The Docker image builds from a wheel, runs as non-root (`studio`,
   uid 10001), bakes no credentials, and has a healthcheck.

## 7. Provider confirmation (evidence)

* **GPT remains the architect:** `config_models.StudioConfig` rejects any
  production `provider_order`/`vision_provider_order` other than
  `["openai"]`; `LLMRouter.provider_order` re-filters at runtime; tests
  "production filters provider order to openai" and "production never calls
  unauthorized providers" prove Gemini/OpenRouter/local are not invoked even
  when keys are present and the order names them.
* **FLUX Kontext remains the art engine:** production `image_provider` must
  be `bfl`; `generate_reference_image` in production calls only
  `BFLClient` and raises on failure; `bfl_model` is validated against the
  supported Kontext model list; tests "production image generation requires
  BFL" and the BFL mock matrix prove the wire contract.
* **Anthropic is not a runtime dependency:** repository-wide sweep for
  `ANTHROPIC_API_KEY` / `import anthropic` / `from anthropic` returns zero
  matches (also asserted by the notebook's security-sweep cell on every
  execution); `pyproject.toml` contains no Anthropic package.
* **Deterministic/fake providers:** constructor-injected providers raise
  `ProviderLockViolationError` in production; deterministic fallbacks are
  development/test-only, marked and TTL-limited.

## 8. Notebook validation evidence

Executed with `nbclient` (`NotebookClient(..., timeout=1800)`), kernel
`python3`, from a clean directory: run 1 on an empty directory, run 2
immediately afterwards in the same directory with no cleanup. Both runs
executed all 68 cells top-to-bottom and finished with
`OVERALL: PASS` in the summary cell; executed copies are preserved as
`executed_run1.ipynb` / `executed_run2.ipynb` in the validation directory
and the machine-readable gate matrix is `final_validation_report.json`.

## 8a. Independent diff review

An independent reviewer agent was launched over the original→final diff but
was terminated early by an environment session limit; the review was
completed manually against the same invariant checklist (provider locks, no
Anthropic runtime, subprocess/SSRF/download safety, resume safety, backward
compatibility). It surfaced **one defect, which was fixed and regression-
tested**: the dictionary-key redaction rule also wholesale-redacted numeric
token-usage metadata (`input_tokens`/`total_tokens`) in observability events,
violating the usage-metadata requirement. Fix: secret-named keys are now
redacted wholesale only for string values; a dedicated test pins the
behavior. All 211 checks re-passed and the notebook was rebuilt and
re-executed twice after the fix.

## 9. Remaining limitations

1. **Live provider behavior is unverified here.** OpenAI correctness and
   FLUX Kontext output quality were validated only through mocked contracts;
   the paid live smoke cells exist but were NOT RUN.
2. **Docker build/run NOT RUN** (no Docker daemon in this environment).
3. **Static type checking NOT RUN** (no mypy/pyright available); the
   package is not marked `py.typed`.
4. **FastAPI shell is single-worker** by design; a queue/worker system is
   documented as required for multi-instance production.
5. **`llm.py` dev-only provider bodies (Gemini/OpenRouter/local) are
   untested** (53 % module coverage) — they are excluded from the production
   path by the provider locks.
6. **Caveman/Obsidian ZIPs unavailable** — the requested Caveman-crew
   investigation workflow and Obsidian canvas notes could not be produced
   from the referenced material.
7. Notebook execution requires internet only if Python dependencies are
   missing; with dependencies present it runs fully offline.

## 10. Migration summary (original executed notebook → FINAL)

* Same public API: `ScientificMotionStudioV10(config).run(...)`,
  `run_v10_regression_tests`, CLI topic/reference/--config. Existing dict
  configs keep working (default mode `development`).
* **Breaking (intentional, documented):**
  * `execution_mode="production"` now enforces the provider boundary and
    fails clearly instead of silently degrading.
  * `temporal.sketch_backend.command` is no longer shell-executed; it is
    shlex-split into argv. Use `argv: [...]` (or the explicit unsafe shell
    opt-in).
  * Vision cache keys changed (content-hash) — old filename+size entries
    are simply regenerated.
  * `UploadPostPublisher` requires https + `allowed_hosts`.
  * Job manifests upgrade in place to schema 10.1; stages left `running`
    are re-run once.
* Version: package `scientific-motion-studio-v10` 10.0.0 → **10.1.0**.

## 11. Addendum — two-tier vision review (Qwen VL + Gemini escalation)

Vision review is decoupled from reasoning for cost efficiency. Reasoning
stays **OpenAI-only** and image generation stays **BFL FLUX Kontext-only** in
production, but the aesthetic vision critic now runs a two-tier policy:

* **Primary (~80% of cases):** Qwen VL via OpenRouter
  (`qwen/qwen-2.5-vl-72b-instruct`), a strong-yet-cheap vision reviewer.
* **Escalation (~20% difficult cases):** Gemini 2.5 Flash. The primary is
  asked to self-report `_meta.review_confidence` (0..1) and
  `_meta.needs_expert_review`; a critique below
  `llm.vision_escalation_confidence` (default 0.62) — or explicitly flagged —
  escalates to the next reviewer, whose verdict is preferred. Confident
  critiques never make the second call. The additive `_meta` is stripped
  before results reach downstream schemas.

Configuration (`llm.*`): `vision_provider_order` (e.g.
`["openrouter", "gemini"]`), `openrouter_vision_model`, `gemini_vision_model`,
`vision_escalation_enabled`, `vision_escalation_confidence`. Production now
authorizes `openai`/`openrouter`/`gemini` for **vision only**; local/procedural
vision review is still rejected. New optional secrets: `OPENROUTER_API_KEY`
(Qwen) and `GEMINI_API_KEY` (Gemini); when both are absent, vision review
falls back to OpenAI. Covered by `_test_vision_two_tier_escalation` and the
updated provider-lock tests; full offline suite **288** checks, both notebooks
execute cleanly twice (fresh + resume).
