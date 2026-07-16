# Scientific Motion Studio V10 — Issue Register (pre-fix audit)

Audit basis: full read of all 45 generated files reconstructed from
`Scientific_Motion_Studio_v10_Agentic_Production_executed.ipynb` (50 cells,
41 Python modules, pyproject.toml, Dockerfile, config.example.json, README.md).
Supporting ZIP repositories (caveman-main, obsidian-skills-main) were **not
present in the uploaded files** and could not be extracted; the audit was
performed directly.

## CRITICAL

| ID | Location | Finding |
|----|----------|---------|
| C1 | `temporal_backends.py:53-54` | `config["command"].format(**values)` then `subprocess.run(command, shell=True)`. `motion_prompt` is LLM-generated and scene-derived → shell/command injection. Explicitly named in the mandate. |
| C2 | `llm.py:97-101` | `generate_json` silently returns the deterministic fallback on any provider failure **and caches it in the same namespace as real provider output** (cache poisoning: a transient outage permanently masquerades as a model answer). No execution modes; production cannot fail clearly. |
| C3 | `llm.py:166-252` | `generate_reference_image` silently falls through BFL → Gemini/Imagen → OpenAI Images → local diffusers. Violates the FLUX-Kontext-exclusive production art mandate. |
| C4 | `llm.py:113` | Vision critic cache key = image **filename + file size** + prompt. Same-size re-render reuses a stale approval. Mandate explicitly forbids filename+size identification. |
| C5 | `llm.py:330-356` | BFL polling URL and result `sample` URL are fetched with no scheme/host policy (SSRF), no `raise_for_status` on the final download, no Content-Type check, no size bound, no Pillow verification, non-atomic cache write (partial/corrupt PNG cached permanently). |

## HIGH

| ID | Location | Finding |
|----|----------|---------|
| H1 | `llm.py` (all providers) | No retry/backoff/jitter; single attempt then fallback. No 408/409/429/5xx classification; retries would also re-run non-retryable 401s if added naively. No request-ID/usage capture. |
| H2 | `utils.py:62-66`, `cache_store.py` | `save_json` and `put_file` are non-atomic (`write_text`/`copy2` in place). A killed process leaves truncated JSON; `load_json` then raises and permanently breaks resume. No corruption quarantine. |
| H3 | `job_runtime.py` | Manifest: no schema version; `config_hash` recorded but **never compared** (config changes silently reuse stale outputs); no recovery of a stage left `running` after a crash; no retry limit; no lock against concurrent workers; full traceback stored in the manifest (leak channel); non-atomic writes. |
| H4 | `service_api.py:29` | FastAPI returns `HTTPException(500, detail=str(exc))` → raw exception leakage; blocking synchronous generation inside the request; no job IDs, no status endpoint, no 4xx/5xx distinction. |
| H5 | `webui.py:30` | `st.exception(exc)` prints full traceback to the browser; unvalidated config path; unvalidated reference-video path. |
| H6 | `security.py` | Redaction misses realistic secret formats (`sk-…`, BFL UUID keys, long random tokens); `x-key` header name not covered; `utils.run_command` failure text includes full stdout/stderr unredacted. |
| H7 | `pyproject.toml` | Production deps missing: `requests` and `openai` are not dependencies at all — the documented production path (OpenAI + BFL) cannot import. No license/classifiers/metadata; no dependency groups for openai/http/dev/test. |
| H8 | `publisher.py:38-45` | `UploadPostPublisher` posts the bearer token to an arbitrary, unvalidated endpoint (plain `http:` accepted) → token exfiltration/SSRF. |
| H9 | `research.py` | Responses parsed with `.json()` without `raise_for_status()`; no response-size bounds; broad `except Exception` printing. |
| H10 | `Dockerfile` | Runs as root; installs `.[api,render]` which lacks `requests`/`openai` (broken image); no non-root user, no healthcheck, no writable workspace convention; Debian npm/node drift. |
| H11 | `pipeline.py` (constructor) | Injected `llm_router` / `image_generator` / `mask_generator` test doubles are accepted unconditionally — nothing stops fake providers in production mode (no modes exist). |

## MEDIUM

| ID | Location | Finding |
|----|----------|---------|
| M1 | `llm.py:1`, `utils.py:1`, `audio.py:1`, `research.py:1`, `script_critic.py:1`, `llm.py:517` | Stale `scistudio_v8` cell headers and "v8" product strings. |
| M2 | `temporal_backends.py`, `webui.py`, `cli.py`, `pipeline.py` live section, `job_runtime.py` | Dense semicolon one-liners; missing docstrings/annotations. |
| M3 | ~20 modules | Broad `except Exception` with `print()` diagnostics; failures often swallowed. |
| M4 | `config.example.json:2` | Hard-coded Colab `/content` workspace; `provider_order` includes `gemini` (contradicts the production provider boundary). |
| M5 | `utils.retry` | Retries every exception type; linear sleep; no jitter. |
| M6 | `flux_studio.py`, `candidate_tournament.py` | Magic numbers (1024-byte "real image" check, 9973 seed stride) unnamed. |
| M7 | `observability.py` | Events lack provider/model/request-id/attempt/input-hash/output-hash/cache-hit fields; no provenance manifest. |
| M8 | `cache_store.py` | No corruption detection; `get_json` raises on corrupt file. |
| M9 | `job_runtime.py` | Stage output has no checksum → "completed" can point at a modified/corrupt artifact. |

## LOW

| ID | Location | Finding |
|----|----------|---------|
| L1 | `llm.py` BFL cache key | Missing endpoint/base-url and code-version discriminators. |
| L2 | `cli.py` | No `--help` epilog, no explicit `--plan-only`; prints entire result JSON. |
| L3 | `EventLogger.emit` | Non-locked append (single-process assumption undocumented). |
| L4 | `tests.py:255` | BFL mock returns 2 KB of `b"x"` as the "image" — masks the absence of real image validation. Fixture must produce a real PNG once validation exists. |

## DOCUMENTATION

| ID | Finding |
|----|---------|
| D1 | README lacks: security model, provider responsibility matrix, cache semantics, resume semantics, failure behavior, paid-provider boundary, validation evidence. |
| D2 | No `.env.example`; no environment-variable documentation. |
| D3 | No statement separating offline-verified vs mock-verified vs live-verified claims. |

## Fix order
C1→C5 and H1→H11 are implemented before any cosmetic work; M/L/D follow.
