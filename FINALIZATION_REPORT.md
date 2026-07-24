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
`["openrouter"]`), `openrouter_vision_model`,
`openrouter_vision_escalation_model` (`google/gemini-2.5-flash`),
`vision_escalation_enabled`, `vision_escalation_confidence`. Production now
authorizes `openai`/`openrouter`/`gemini` for **vision only**; local/procedural
vision review is still rejected.

**Provider routing is by key, per the operator's directive:** OpenAI reasoning
uses `OPENAI_API_KEY`; **both Qwen VL and Gemini 2.5 Flash vision go through the
single `OPENROUTER_API_KEY`** (Gemini is *not* a separate key — the escalation
tier is `google/gemini-2.5-flash` on OpenRouter); BFL images use `BFL_API_KEY`.
When OpenRouter is absent, vision review falls back to OpenAI. Covered by
`_test_vision_two_tier_escalation`, `_test_director_review_salvage` and the
updated provider-lock tests.

## 12. Addendum — animation architecture rework (grounded motion, 10.2.0)

The earlier finisher produced technically-valid MP4s that did not *read* as
explanatory animation: art direction was a single degrading editorial-ink
canon, shot states were often degenerate (first frame == last frame), and no
object was ever actually separated from its background. The rework rebuilt the
motion path around one principle — **the video is alive when the causal object
changes, not when every pixel jitters** — and enforces it in strict priority
order. Reasoning stays OpenAI-only and images stay BFL-only; nothing here
introduces a new production reasoning or image provider.

### P0 — Correctness fixes (foundation)

* `motion_eval.py`: removed the `or bool(events)` false-positive so a shot with
  only supporting events (camera/particles/captions) no longer counts as object
  motion; the causal-clarity gate now fails "supporting-only" shots and passes
  only a real object state change or a *declared* hold.
* `scene_architect.py`: deleted keyword→method inference
  (`rain/wind/water → texture_loop`); a seam's method is chosen from whether the
  scene has figures (`replacement_pose` vs `semantic_mask`), and the scientific
  HUD / annotation overlays are **opt-in** (`enable_scientific_overlay`), off by
  default — English audio narrates, captions are not forced.

### P1 — Object grounding before segmentation (`object_grounding.py`)

`ObjectGrounder` authors an `ObjectManifest` *before* any mask is cut: per
object a bbox, positive/negative points, a transform pivot, a depth band and
declared start/end states, with the causal subject marked. The vision director
(Qwen VL via the LLM router) proposes it; a deterministic fallback derives one
object per motion seam with boxes spread across the frame. This replaces feeding
SAM2 the image centre every time.

### P2 — Mask quality gate with retry (`sam2_segment.py`)

`ObjectSegmenter.mask_for(...)` is prompted by the grounded bbox + points, not a
centre point. `mask_qc(...)` then *rejects* a mask that is empty,
near-full-canvas, too small, mismatched to its bbox, or a duplicate of another
object's mask (IoU). A rejected mask retries once (bbox-clipped heuristic) and
the QC verdict is recorded on the layer — a written file is no longer treated as
proof the object was separated.

### P3 — Clean plate + object-local layers (`hybrid_package.py`)

Moving objects are removed from the background into an inpainted **clean plate**
(OpenCV Telea, PIL edge-bleed fallback) so a foreground object leaves the plate
behind it, not a hole. Layers carry the grounded depth as a real z-index (no
longer flattened to a single plane) and an object pivot, so rotate/scale happen
about the object rather than the canvas centre.

### P4 — Renderer parity (`hybrid_render.py`)

The offline PIL renderer and the production Remotion template now consume the
**same** authored DSL. The rewritten Remotion `_typescript()` iterates *all*
events per layer (previously `.find()` took only the first), applies shared
easing, transforms about each layer's pivot/`transformOrigin`, and executes the
opt-in camera / particle / caption directives — so the offline preview and the
production render agree on what moves.

### P5 — Post-render QC (`post_render_qc.py`)

`render_verified` is decided from the **rendered pixels**, not from JSON
presence: `verify(...)` measures change inside vs. outside the causal object's
mask between the shot's real first and last frames. An action shot must show the
object region actually change (else `render_verified=False`, reason "planned
object motion did not produce a visible change (static render)"); a declared
hold must *not* move. Wired into `pipeline.py` as a guarded pass that writes
`20_render_qc/*.json`. An optional Qwen VL before/after gate is GPU/API-gated.

### Honesty about what is offline-validated vs. gated

The full offline suite (**360** checks: 92 regression + 216 finalization + 52
notebook) passes, and the linear pipeline runs end-to-end offline with zero
errors, emitting the QC reports. The SAM2 backend, Qwen VL grounding/QC, the
Node/Remotion render and the vision QC gate are **GPU/API/Node-gated** and are
exercised in Colab; offline they run through deterministic fallbacks. Those
fallbacks are validated to behave correctly — including the honest negative
result: on the static deterministic path, post-render QC correctly reports
`render_verified=False`, proving the gate catches a render that did not move the
object rather than rubber-stamping it. Version: package **10.2.0**.

## 13. Addendum — target-stack audit response (articulated motion, 10.3.0-dev)

An operator audit found the previous "all features" claim overstated: of the
requested stack (FLUX object PNG, SAM2, Rig Builder, Rive, PixiJS, Remotion,
WAN 2.2 / SkyReels), several were absent or partial. That claim was wrong. The
stack is now being closed in real, validated increments — "linear" is the
delivery format, not a feature ceiling. This addendum tracks honest status.

### Increment 1 — real segmentation install + articulated Rig Builder (done)

* **SAM2 real install (audit #2 → wired).** The linear setup cell now installs
  Ultralytics (SAM2 + YOLO-pose) when a GPU is present (`INSTALL_VISION_MODELS =
  "auto"`, or forced), prefetching `sam2_b.pt` / `yolov8n-pose.pt` so the first
  run does not stall mid-pipeline. Install failure is non-fatal (falls back to
  the heuristic segmenter / proportional rig). Previously the cell installed only
  pydantic/Pillow/requests/openai, so SAM2 could never load on a fresh runtime.

* **Rig Builder (audit #3 → done).** New `rig_builder.py` produces an articulated
  anatomical skeleton — head, spine, upper-arm, forearm, hand, thigh, calf, foot
  (14 bones) with a correct parent chain, per-bone pivots, and per-part masks
  carved (capsule ∩ object mask) from the object's own silhouette. Pose backend:
  Ultralytics YOLO-pose (COCO-17 keypoints → bones). Deterministic fallback: a
  canonical humanoid laid out by proportion inside the object bbox — so rigging
  works and is testable with no GPU. Non-figures get a single-root rig (motion
  degrades to whole-object).

* **Custom 2D cutout skeletal rig — NOT Rive (audit #4 → PIL done, Remotion
  pending).** Correction to an earlier overstatement: this is a *custom
  articulated 2D rig* (PNG part cutouts rotated about pivots by forward
  kinematics), **not** Rive, and it does not satisfy a "Rive" requirement — there
  is no artboard, bone/mesh skin deformation, constraint or state machine, and no
  `.riv` is read or written. On Rive specifically: `.riv` is a binary exported
  from the Rive Editor; there is no practical external Python/Colab API to
  synthesize a whole rig `.riv`, but Colab *can* run the Rive Web Runtime, load a
  pre-made `human_template.riv`, drive its state machine and swap image assets —
  so a real-Rive path via a template is possible and remains optional/tracked
  separately. What is implemented: `skeletal_deform.py` executes an *authored*
  per-bone pose (named gesture or explicit angle deltas) on a `skeletal_pose`
  motion event; the deformer invents nothing (unknown/empty pose holds still).
  Wired end-to-end on the **PIL** path only. The **Remotion** JS forward-kinematics
  executor is pending, so the production Remotion path still renders the character
  cutout without limb articulation — an explicit, documented parity gap.

### Increment 2 — explicit CharacterManifest + hard quality gate (done)

The previous increment's rig never activated on a normal run: figure detection
was a brittle narration-keyword gate, and the offline LLM stub's plan bypassed
the fallback that authored the pose. Both are fixed:

* **CharacterManifest (`character_director.py`).** An explicit per-scene contract
  — `characters:[{character_id, present, bbox, body_orientation,
  requires_articulation, pose_intent}]`. Production authors it from the beauty
  frame with a VL model (Qwen via the router); offline derives it from the
  architecture's authored `figure_construction` (structured data), not a keyword
  scan.
* **Articulation is guaranteed post-plan.** `AnimationDirector._ensure_articulation`
  runs after the plan is built (whether it came from the LLM or the fallback):
  for every character with `requires_articulation`, it guarantees a
  `skeletal_pose` on that character's figure layer (matched by bbox overlap).
  This is what makes the rig activate on a real run rather than only on
  hand-forced architecture.
* **Hard quality gate (pipeline).** After packaging each scene: if a character
  `requires_articulation` but `rig_count == 0`, the run **fails** with a clear
  error rather than shipping a static cutout (override:
  `allow_unrigged_characters`).
* **Proven on a real topic (not forced input).** The offline pipeline run for
  *"What happens to a human body in zero gravity?"* produced, from the topic
  alone: 3 CharacterManifests (`requires_articulation=true`), 3 `*_rig.json`
  (14-bone rigs), a `skeletal_pose` on every scene, and a valid H.264 MP4, with
  the quality gate satisfied (`rig_count=3`). This is `topic → storyboard →
  architecture → CharacterManifest → rig → animation DSL → render`, end to end.

### Still open (honest status, being built next)

* **Anatomical part separation is still coarse (audit #4b).** Part masks are
  carved from the single character mask via bone capsules — good enough to move,
  but it can leave seams at elbows/knees and does not do per-limb SAM2 refinement,
  occlusion ordering or clothing/hair handling. Whole-char SAM2 + pose keypoints +
  per-limb refinement + clean-plate is the planned upgrade.
### Increment 3 — real anatomical part separation + perceptual QC (audit P2)

Capsule carving is removed as accepted production output. New modules:

* **`part_segmentation.py` (AnatomicalPartSegmenter).** Produces all 14 named
  parts — head, spine(torso), upper_arm, forearm, hand, thigh, calf, foot (L+R) —
  each as a mask + a beauty-frame cutout, with `parent`, `pivot`, `rest_rotation`,
  anatomical `z-order` (occlusion) and a `confidence`. Every part is intersected
  with the whole-character mask, so **background leakage is zero by construction**.
  Production path: **per-limb SAM2 refinement** — SAM2 is prompted per limb with a
  tight box + a positive point on the limb axis + *negative* points at the other
  joints, so the forearm excludes the torso and the far arm (no torso-drag). Joint
  overlap covers the seam so a rotating child leaves no gap.
* **`part_qc.py` (perceptual QC).** Renders a **contact sheet** (rest + 3
  articulated poses) and runs structural checks — joint continuity (no gap at
  shoulder/elbow/hip/knee), background leakage, occlusion order, torso-drag — plus
  an optional **Qwen VL perceptual gate** on the contact sheet. It surfaces issues
  instead of rubber-stamping.
* **Production refuses coarse output.** The pipeline QC gate hard-fails a
  production run when `part_source != "sam2"` or any structural issue is present;
  a truly failed part (empty/escaped) always hard-fails. Offline/test keeps the
  geometric fallback as a **clearly-flagged preview** (source=`geometric`, reduced
  confidence, issues recorded as warnings).
* **Real-topic run.** The zero-gravity topic emits, per scene, all 14 part PNGs +
  cutouts, a contact sheet, and a `part_qc` report — end to end from the topic.

**Honest limitation:** with no GPU in this environment, SAM2 per-limb refinement
cannot run, so the offline preview uses the geometric fallback, and the perceptual
QC (correctly) flags residual joint gaps at the extremities and arm/torso overlap
on the crude deterministic demo art. The "no gap / no torso-drag" acceptance is
met by the **production SAM2 path**, which is GPU-gated and therefore not verified
here — this is stated rather than glossed. The geometric fallback is never
presented as clean production output; production refuses it.

### Increment 4 — Remotion nested-FK skeletal executor (audit P4)

Articulation is no longer PIL-only. The Remotion template now interprets the same
rig the PIL renderer uses:

* **`_export_rig` (Python).** For a rigged layer, every bone's part cutout is
  copied into `public/`, and the authored `skeletal_pose` is resolved to explicit
  per-bone angle deltas + a timing window — so the JS reads only numbers.
* **Nested forward kinematics (TypeScript).** `SkeletalLayer`/`Bone` build a DOM
  tree matching the bone hierarchy. **CSS nested transforms compose parent→child**,
  so each bone applies only its own `rotate()` about its pivot and the chain
  accumulates automatically (true FK). Own image + child subtrees are interleaved
  by z-order, so the torso sits behind and arms/head in front.
* **Actually rendered (not just compiled).** With Node 22 present, `npm install`
  + `npx remotion render` produced an MP4 whose character **articulates** — 18.5 %
  of pixels change and the change is localized to the character band (3025 inside
  vs 926 outside).
* **Full pipeline → Remotion, end to end.** The offline pipeline for the
  zero-gravity topic, forced to `render.backend="remotion"`, ran
  `topic → storyboard → architecture → CharacterManifest → rig → DSL → Remotion
  render` with 0 errors and produced a valid H.264 MP4 whose character
  articulates (33.6 % change). `_render_remotion` gained an optional
  `--browser-executable` (config `remotion_browser_executable` or
  `SCISTUDIO_REMOTION_BROWSER`) so locked-egress environments point at a
  pre-installed Chrome; Colab still auto-downloads Remotion's shell. Nothing is
  disabled.

The production Remotion path now shows clean object-level articulation on the
final MP4 — the phase-completion bar for P4.

### Still open (honest status, being built next)

* **Anatomical cleanliness on the SAM2 path** is still GPU-gated and unverified
  offline (the geometric preview is QC-flagged, never accepted as production).
* **PixiJS effects, WAN 2.2 / SkyReels, FLUX isolated hero assets, and the
  optional Rive `.riv` template** remain — in that order — and are not started.

* **PixiJS (audit #5)** — real GPU particle/shader layer in the Remotion render
  (npm `pixi.js`): not yet present; current particles are PIL / React-div.
* **Remotion as default (audit #6) — DONE (F5).** `render.backend` now defaults
  to `"auto"`: `select_render_backend()` picks the Remotion production path when
  the Node toolchain is present and degrades to the deterministic PIL renderer
  (with a reason) when it is not, so a run never crashes just because Node is
  missing. The setup cell reports/ensures Node; explicit `"remotion"` can be made
  strict. See matrix `remotion_auto_default_f5`.
* **WAN 2.2 / SkyReels (audit #7)** — only a generic external temporal adapter
  exists; the diffusers install + model invocation + GPU-memory management are
  pending. These need a large-GPU runtime and cannot be validated in this
  offline environment — they will be wired with honest GPU-gating.
* **FLUX separate object PNG (audit #1) — DONE (F7, opt-in).** The default is
  unchanged: object PNGs are mask-cutouts of the one beauty frame (best
  pixel/identity consistency). `hero_asset.py` adds an **opt-in**
  (`flux_studio.hero_isolated_asset`) path that renders a hero subject alone on a
  flat background via FLUX and keys the background to a transparent PNG. It is
  honestly gated: it runs only with a real image generator (never fabricates
  offline). The deterministic background-keying is unit-tested. See matrix
  `hero_isolated_asset_f7`.

The **optional Rive template** path (`rive_runtime.py`) is also now provided as
an explicit **last-resort, non-core** integration: with no `.riv` supplied it is
a no-op and the custom 2D cutout rig stays in control; given a real
`human_template.riv` + `rive.enabled`, it builds a state-machine driver plan and
emits a `RiveLayer.tsx` loader (Rive Web Runtime, `@rive-app/canvas`) that drives
the provided template in the Remotion render. A binary `.riv` must still be
authored in the Rive Editor — the repo ships the integration, not a template.
See matrix `rive_optional_integration`.

Unit tests `_test_rig_builder` and `_test_skeletal_deform` cover the new modules
in the aggregate suite; the end-to-end figure→rig→articulated-render chain is
validated against `linear/src`. Nothing here adds a new production reasoning or
image provider: reasoning stays OpenAI-only, images stay BFL-only.

**Increments 5–6 status update (audit #5 / #7 now done).** The two "still open"
items above have since been built and are wired into the Remotion project + the
setup cell — see the matrix keys `pixijs_effects` and `wan_skyreels_temporal`.
PixiJS is a real `pixi.js` 7.4.2 GPU particle/shader layer (verified rendering);
WAN 2.2 / SkyReels are real diffusers image-to-video adapters, honestly GPU-gated
(unverifiable offline). Remotion-as-default (#6) and the FLUX isolated hero PNG
(#1) remain opt-in / deprioritized per the operator's ordering.

## 14. Addendum — production reliability layer (P1–P7, 10.3.0-dev)

A real run crashed when BFL rejected one prompt on content-moderation grounds
and took the whole pipeline down. The fix was not just to catch that error but
to build the reliability layer the operator specified — so a run is resumable,
provider failures are recovered in a scoped way, the science is traceable,
motion lands on the audio, continuity is actively checked, publish is gated, and
the spend is budgeted. Each priority is a real, unit-tested module that emits
lineage artifacts; none is a stub.

* **P1 — Artifact Graph + deterministic resume (`artifact_graph.py`).** A DAG of
  `ArtifactNode`s keyed by `artifact_key = hash(stage, input_hash, prompt_hash,
  model, model_version, config_hash)`, with a node state machine
  (`PENDING → RUNNING → VALID`, plus `REPAIRING`/`RETRYING`/`FAILED`),
  `is_valid()` (key + artifact + checksum), dependency-aware
  `invalidate_downstream()`, and `classify_failure()` routing
  (TRANSIENT/MODERATION/QUALITY_FAILURE/INVALID_INPUT/PROVIDER_FAILURE/FATAL →
  repair strategy). Emitted per run under `graph/`. All **5 acceptance tests**
  pass: moderation recovery, resume-after-crash, single-shot repair, upstream
  invalidation, and chaos-kill idempotence (no duplicate provider calls).
* **P2 — Visual provider router + moderation recovery (`visual_provider.py`,
  `prompt_safety.py`).** A `VisualProvider` interface + `VisualProviderRouter`
  with granular `ErrorClass` classification and per-class, **scene-scoped**
  recovery: moderation → safe prompt rewrite → retry BFL; transient/rate-limit →
  backoff + retry; quality → reseed; then an opt-in fallback provider; then a
  scene-scoped raise. BFL stays primary and default — alternates are opt-in, never
  a whole-video fallback. `flux_studio.generate` delegates to the router.
* **P3 — Scientific claim graph (`claim_graph.py`).** Assembles
  sentence → claim → evidence(Fact + Source + confidence) → scene → visualization
  lineage from existing artifacts and flags `unsupported_claim` / `low_confidence`
  / `visual_without_claim` / `unsourced_evidence`. Optional `require_evidence`
  gate. Emitted at `04b_claim_graph/claim_graph.json`.
* **P4 — Audio-first timeline (`audio_timeline.py`).** Derives per-scene audio
  windows + impact/accent/settle beats from the voice word-timings, and
  `primary_impact_frame` snaps the character gesture onto the spoken emphasis word
  (via `_ensure_articulation`) instead of a fixed 20 % offset. Emitted at
  `05b_audio_timeline/audio_timeline.json`.
* **P5 — Continuity validator (`continuity_validator.py`).** Turns the canon into
  an active check — flags character reappear-after-gap, size/proportion jumps,
  focal drift, and **causal-state regressions** (e.g. intact → damaged → intact)
  from authored per-scene records. `allow_reset` suppresses an authored reset;
  optional VL check is GPU-gated. Emitted at `21_continuity/continuity_report.json`.
* **P6 — Hierarchical quality gate (`quality_gate.py`).** Rolls up 5 levels into
  one publish decision — L1 Technical (ffprobe), L2 Structural (part_qc +
  post-render QC), L3 Continuity, L4 Scientific (claim graph), L5 Editorial —
  and sets `publishable=False` when a blocking level fails; publishing is skipped
  on a not-publishable video. Emitted at `22_quality_gate/quality_gate.json`.
* **P7 — Resource + cost orchestrator (`resource_orchestrator.py`).** Classifies
  each stage by resource (CPU / API / GPU / A100), schedules the independent
  per-scene work into a parallel wave (gating GPU/A100 stages when no accelerator
  is present), estimates the run cost from a price table (OpenAI per 1K tokens,
  BFL per image, organic-video per second), and enforces a `max_cost_usd` +
  `max_retries` **budget** (`can_afford`/`charge`/`allow_retry`). `parallel_map`
  is a real budget-aware `ThreadPool` executor for the per-scene API work. The
  plan — resource classes + estimated spend + parallel waves + budget — is emitted
  at `00_orchestration/orchestration_plan.json` so a run's cost and schedule are
  inspectable before anything is incurred. Unit-tested (`_test_resource_orchestrator_p7`):
  cost estimate, budget affordance/exhaustion + retry cap, parallel scene wave +
  GPU gating, real concurrency, budget-limited dispatch, and plan emission.

* **P8 — Autonomous topic engine (`topic_engine.py`).** Proposes candidate
  topics (LLM brainstorm in production; deterministic domain × question-frame
  offline), scores each on `scientific_richness` / `visual_potential` /
  `novelty` / `audience_appeal` / `safety` with configurable weights, and
  selects the strongest **unproduced** one. A history file drives novelty, so an
  already-made topic is fully penalized and not re-selected while fresh options
  remain; moderation-risky framings are penalized on the safety axis, so the run
  is less likely to crash later on a rejected image prompt. The pipeline records
  why *this* topic was chosen (its score + alternatives) at
  `00_topic_engine/topic_provenance.json` (opt-in); `propose()` emits
  `topic_candidates.json`.
* **P9 — Automated metadata + publishing (`metadata_engine.py`).** Assembles the
  full upload package from artifacts the pipeline already produced — title (from
  the script, capped at YouTube's 100 chars), a **sourced** description (research
  summary + key facts + source URLs + hashtags), keyword tags, **timestamp
  chapters** from the storyboard scene durations (first chapter guaranteed at
  `0:00`), and a 9:16 thumbnail brief. Deterministic assembly (optional LLM
  polish is opt-in). Emitted at `23_metadata/metadata.json` and **merged into the
  publisher metadata**, so the existing LocalArchive / Upload-Post adapters ship a
  real title/description/tags/chapters instead of `{title, topic, job_id}` —
  still behind the P6 publish gate.

Every P1–P9 module is exercised by the aggregate suite and rolls its artifacts
into the pipeline lineage; the P6 gate governs publish. Providers are unchanged:
reasoning stays OpenAI-only, images stay BFL-only. The production reliability
roadmap (P1–P9) is now complete end to end.
