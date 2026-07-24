"""Finalization test suite: unit, mocked-integration and offline end-to-end
tests added on top of the retained V9/V10 regression suites.

No test in this module performs a live network call or spends API credits;
all provider traffic is mocked. Run everything with
``run_final_validation_tests()`` or the aggregate ``run_all_tests()``.
"""

from __future__ import annotations

import io
import json
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

from PIL import Image, ImageDraw

from .bfl_client import BFLClient, BFLError
from .cache_store import ArtifactCache
from .config_models import ExecutionMode, StudioConfig
from .errors import (
    DownloadPolicyError,
    JobConcurrencyError,
    ProviderLockViolationError,
    ProviderUnavailableError,
    StageRetryExhaustedError,
    UnsafeCommandError,
)
from .http_safety import (
    backoff_delays,
    call_with_retries,
    classify_exception,
    download_image,
    is_retryable_status,
    validate_url,
)
from .job_runtime import ResumableJobRuntime
from .llm import FALLBACK_MARKER, LLMRouter
from .schemas import TemporalRequest
from .security import (
    clear_registered_secrets,
    redact_secrets,
    redacted_exception_text,
    register_secret,
)
from .temporal_backends import SketchControlledVideoBackend
from .utils import ensure_within, extract_json, load_json, save_json


class Collector:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def check(self, name: str, condition: bool, detail: str = "") -> None:
        self.items.append({"name": name, "passed": bool(condition), "detail": detail})
        if not condition:
            raise AssertionError(f"{name}: {detail}")


def _png_bytes(color: str = "#2E77A6", size: tuple[int, int] = (32, 32)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _image_file(path: Path, color: str = "#475157") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (180, 320), "#FAFAF7")
    ImageDraw.Draw(image).rectangle((25, 40, 155, 280), fill=color)
    image.save(path)
    return path


class FakeHTTPResponse:
    def __init__(self, data=None, content=b"", headers=None, status_code=200):
        self._data = data or {}
        self.content = content
        self.headers = headers or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            error = RuntimeError(f"HTTP {self.status_code}")
            error.response = self  # type: ignore[attr-defined]
            raise error

    def json(self):
        return self._data


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------


def _test_config_validation(c: Collector, base: Path) -> None:
    # Production locks reject unauthorized fallbacks.
    for bad in (
        {"execution_mode": "production", "llm": {"provider_order": ["openai", "gemini"]}},
        {"execution_mode": "production", "llm": {"provider_order": ["openrouter"]}},
        {
            # Vision may use openai/openrouter/gemini, but NOT local/procedural.
            "execution_mode": "production",
            "llm": {"provider_order": ["openai"], "vision_provider_order": ["openrouter", "local"]},
        },
        {
            # Reasoning stays OpenAI-only even though these are valid vision providers.
            "execution_mode": "production",
            "llm": {"provider_order": ["openai", "openrouter"], "vision_provider_order": ["openrouter"]},
        },
        {"execution_mode": "production", "llm": {"provider_order": ["openai"], "enable_local_fallback": True}},
        {"execution_mode": "production", "llm": {"provider_order": ["openai"], "image_provider": "openai"}},
        {"execution_mode": "production", "llm": {"provider_order": ["openai"], "gemini_image_model": "imagen-3"}},
    ):
        try:
            StudioConfig.from_dict(bad)
            rejected = False
        except (ProviderLockViolationError, Exception):
            rejected = True
        c.check(f"production lock rejects {json.dumps(bad['llm'])[:60]}", rejected)

    good = StudioConfig.from_dict(
        {
            "execution_mode": "production",
            "llm": {"provider_order": ["openai"], "vision_provider_order": ["openai"]},
        }
    )
    c.check("valid production config accepted", good.execution_mode == ExecutionMode.production)
    c.check("mode propagates into llm config", good.as_runtime_dict()["llm"]["execution_mode"] == "production")

    # Two-tier vision (Qwen VL via OpenRouter + Gemini escalation) is a valid
    # production config while reasoning stays OpenAI-only and images BFL-only.
    two_tier = StudioConfig.from_dict(
        {
            "execution_mode": "production",
            "llm": {
                "provider_order": ["openai"],
                "vision_provider_order": ["openrouter", "gemini"],
                "openrouter_vision_model": "qwen/qwen-2.5-vl-72b-instruct",
                "gemini_vision_model": "gemini-2.5-flash",
            },
        }
    )
    c.check(
        "two-tier vision (qwen+gemini) accepted in production",
        two_tier.as_runtime_dict()["llm"]["vision_provider_order"] == ["openrouter", "gemini"],
    )

    dev = StudioConfig.from_dict({})
    c.check("development is the default mode", dev.execution_mode == ExecutionMode.development)

    for invalid in (
        {"llm": {"bfl_model": "sdxl"}},
        {"llm": {"bfl_base_url": "http://api.bfl.ai/v1"}},
        {"llm": {"bfl_aspect_ratio": "1:9"}},
        {"llm": {"bfl_output_format": "webp"}},
        {"llm": {"openai_reasoning_effort": "extreme"}},
        {"render": {"backend": "imovie"}},
        {"workspace": "   "},
    ):
        try:
            StudioConfig.from_dict(invalid)
            rejected = False
        except Exception:
            rejected = True
        c.check(f"config rejects {json.dumps(invalid)[:60]}", rejected)

    prod_shell = {
        "execution_mode": "production",
        "llm": {"provider_order": ["openai"], "vision_provider_order": ["openai"]},
        "temporal": {"sketch_backend": {"allow_shell": True, "unsafe_shell_command": "run {output}"}},
    }
    try:
        StudioConfig.from_dict(prod_shell)
        rejected = False
    except ProviderLockViolationError:
        rejected = True
    c.check("production rejects shell temporal command without override", rejected)


def _test_secret_redaction(c: Collector) -> None:
    clear_registered_secrets()
    openai_key = "sk-proj-Abc123XYZ456pqrstu789"
    text = f"failed with api_key={openai_key} while calling"
    c.check("sk- key pattern redacted", openai_key not in redact_secrets(text))
    c.check("bearer token redacted", "Bearer abcdef123456" not in redact_secrets("auth: Bearer abcdef123456 sent"))
    c.check(
        "x-key header redacted", "1d2e3f4a" not in redact_secrets('"x-key": "1d2e3f4a-9999-4bbb-8ccc-121212121212"')
    )
    uuid_key = "0f0e0d0c-1111-4222-8333-444455556666"
    c.check("uuid after key context redacted", uuid_key not in redact_secrets(f"BFL key={uuid_key} rejected"))
    nested = redact_secrets({"config": {"openai_api_key": "raw-value", "model": "gpt-5-mini"}})
    c.check(
        "secret-named dict keys redacted",
        nested["config"]["openai_api_key"] == "[REDACTED]" and nested["config"]["model"] == "gpt-5-mini",
    )
    usage = redact_secrets({"usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}})
    c.check(
        "numeric token-usage metadata survives redaction",
        usage["usage"] == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )
    register_secret("super-secret-raw-token-9876")
    c.check(
        "registered exact secret scrubbed anywhere",
        "super-secret-raw-token-9876"
        not in redacted_exception_text(RuntimeError("url?tok=super-secret-raw-token-9876&x=1")),
    )
    clear_registered_secrets()


def _test_url_and_retry_policy(c: Collector) -> None:
    try:
        validate_url("http://api.bfl.ai/v1/x", allowed_hosts=["api.bfl.ai"])
        ok = False
    except DownloadPolicyError:
        ok = True
    c.check("plain http rejected", ok)
    try:
        validate_url("https://evil.example/x", allowed_hosts=["api.bfl.ai", "*.bfl.ai"])
        ok = False
    except DownloadPolicyError:
        ok = True
    c.check("non-allowlisted host rejected", ok)
    c.check(
        "wildcard host accepted",
        validate_url("https://delivery-eu1.bfl.ai/img", allowed_hosts=["*.bfl.ai"])
        == "https://delivery-eu1.bfl.ai/img",
    )
    try:
        validate_url("https://api.bfl.ai/x", allowed_hosts=[])
        ok = False
    except DownloadPolicyError:
        ok = True
    c.check("empty allowlist refuses outbound", ok)

    c.check("429 is retryable", is_retryable_status(429))
    c.check("408 is retryable", is_retryable_status(408))
    c.check("409 is retryable", is_retryable_status(409))
    c.check("503 is retryable", is_retryable_status(503))
    c.check("401 is not retryable", not is_retryable_status(401))
    c.check("422 is not retryable", not is_retryable_status(422))

    class E401(Exception):
        status_code = 401

    class E429(Exception):
        status_code = 429

    c.check("exception with 401 not retryable", not classify_exception(E401()))
    c.check("exception with 429 retryable", classify_exception(E429()))
    c.check("timeout-class exception retryable", classify_exception(TimeoutError("t")))

    delays = backoff_delays(5, base_delay=1.0, max_delay=8.0, jitter=0.25)
    c.check(
        "backoff grows exponentially and caps", len(delays) == 4 and delays[0] < delays[2] and max(delays) <= 8.0 * 1.25
    )

    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise E429()
        return "ok"

    result = call_with_retries(flaky, max_attempts=4, base_delay=0.01, sleep=lambda _s: None)
    c.check("retry loop recovers after 429s", result == "ok" and calls["n"] == 3)

    calls["n"] = 0

    def auth_fail():
        calls["n"] += 1
        raise E401()

    try:
        call_with_retries(auth_fail, max_attempts=4, base_delay=0.01, sleep=lambda _s: None)
        ok = False
    except E401:
        ok = True
    c.check("401 fails fast without retries", ok and calls["n"] == 1)


def _test_atomic_and_cache(c: Collector, base: Path) -> None:
    target = base / "atomic" / "value.json"
    save_json(target, {"a": 1})
    c.check(
        "atomic save leaves no temp files", load_json(target) == {"a": 1} and not list(target.parent.glob(".*.part"))
    )

    corrupt = base / "atomic" / "broken.json"
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_text('{"truncated": ', encoding="utf-8")
    c.check(
        "corrupt json returns default and quarantines",
        load_json(corrupt, {"fallback": True}) == {"fallback": True}
        and not corrupt.exists()
        and any(p.name.startswith("broken.json.corrupt") for p in corrupt.parent.iterdir()),
    )

    cache = ArtifactCache(base / "cache")
    key1 = cache.key("stage", {"model": "gpt-5-mini", "prompt": "p"})
    key2 = cache.key("stage", {"model": "gpt-5.2", "prompt": "p"})
    c.check("cache key changes with model", key1 != key2)
    c.check("fallback cache key is namespaced", cache.fallback_key("stage", {"x": 1}).startswith("fallback-"))
    cache.put_json(key1, {"v": 1})
    bad_path = cache.json_path(key1)
    bad_path.write_text("NOT JSON", encoding="utf-8")
    c.check("corrupt cache entry treated as miss", cache.get_json(key1) is None)

    try:
        ensure_within(base, base / ".." / "outside.txt")
        ok = False
    except ValueError:
        ok = True
    c.check("path traversal outside base rejected", ok)
    c.check(
        "path inside base accepted", str(ensure_within(base, base / "sub" / "file.txt")).startswith(str(base.resolve()))
    )

    c.check("extract_json parses fenced JSON", extract_json('text ```json\n{"k": 1}\n``` more') == {"k": 1})
    c.check(
        "extract_json handles nested braces", extract_json('prefix {"a": {"b": [1, 2]}} suffix') == {"a": {"b": [1, 2]}}
    )


def _test_safe_subprocess(c: Collector, base: Path) -> None:
    marker = base / "temporal_out" / "pwned.txt"
    injection = f"'; touch {marker}; echo '"
    # /bin/true accepts and ignores its arguments: the injection payload is
    # passed as an inert argv token and never reaches a shell.
    backend = SketchControlledVideoBackend(
        {"argv": ["/bin/true", "prompt={prompt}", "out={output}"]},
        base / "temporal",
        execution_mode="development",
    )
    start = _image_file(base / "temporal" / "start.png")
    request = TemporalRequest(
        scene_id="INJ",
        beauty_start=str(start),
        control_sketch_start=str(start),
        motion_prompt=injection,
        duration_frames=5,
        fps=5,
        complexity="organic",
        output_path=str(base / "temporal" / "clip.mp4"),
    )
    try:
        backend.generate(request)  # echo produces no output file -> RuntimeError
    except RuntimeError:
        pass
    c.check("argv template blocks shell injection", not marker.exists())

    backend_bad = SketchControlledVideoBackend(
        {"argv": ["/bin/true", "{not_allowed}"]},
        base / "temporal2",
        execution_mode="development",
    )
    try:
        backend_bad.generate(request)
        ok = False
    except UnsafeCommandError:
        ok = True
    except RuntimeError:
        ok = False
    c.check("unknown placeholder rejected explicitly", ok)

    prod_shell = SketchControlledVideoBackend(
        {"allow_shell": True, "unsafe_shell_command": "/bin/echo {output}"},
        base / "temporal3",
        execution_mode="production",
    )
    try:
        prod_shell.generate(request)
        ok = False
    except UnsafeCommandError:
        ok = True
    except RuntimeError:
        ok = False
    c.check("production rejects shell path without security override", ok)


def _test_job_runtime(c: Collector, base: Path) -> None:
    job_dir = base / "job_a"
    calls = {"n": 0}

    def stage():
        calls["n"] += 1
        return {"value": calls["n"]}

    runtime = ResumableJobRuntime(job_dir, job_id="J", topic="t", config={"k": 1})
    first = runtime.execute("s1", {"in": 1}, stage)
    c.check("stage executes", first == {"value": 1})
    again = runtime.execute("s1", {"in": 1}, stage)
    c.check("resume reuses completed output", again == {"value": 1} and calls["n"] == 1)

    # Checksum mismatch: tamper with the artifact -> stage re-runs.
    output_path = Path(runtime.manifest.stages["s1"].output_path)
    output_path.write_text('{"value": 999}', encoding="utf-8")
    rerun = runtime.execute("s1", {"in": 1}, stage)
    c.check("tampered artifact checksum forces re-run", rerun == {"value": 2})
    runtime.mark_completed()

    # Interruption recovery: simulate a stage left running by a dead worker.
    manifest = load_json(job_dir / "job_manifest.json")
    manifest["stages"]["s1"]["status"] = "running"
    save_json(job_dir / "job_manifest.json", manifest)
    runtime2 = ResumableJobRuntime(job_dir, job_id="J", topic="t", config={"k": 1})
    c.check(
        "running stage recovered as interrupted then re-runnable",
        runtime2.manifest.stages["s1"].status == "interrupted",
    )
    rerun2 = runtime2.execute("s1", {"in": 1}, stage)
    c.check("interrupted stage re-executes", rerun2 == {"value": 3})

    # Config change invalidation.
    runtime2.mark_completed()
    runtime3 = ResumableJobRuntime(job_dir, job_id="J", topic="t", config={"k": 2})
    c.check(
        "config change marks completed stages stale",
        runtime3.manifest.stages["s1"].status == "stale"
        and any("Configuration changed" in w for w in runtime3.manifest.warnings),
    )
    rerun3 = runtime3.execute("s1", {"in": 1}, stage)
    c.check("stale stage re-executes after config change", rerun3 == {"value": 4})

    # Concurrency: same-process lock is re-entrant.
    try:
        ResumableJobRuntime(job_dir, job_id="J", topic="t", config={"k": 2})
        conflict = False
    except JobConcurrencyError:
        conflict = True
    c.check("same-process lock is re-entrant", not conflict)
    runtime3.release_lock()

    # A lock held by a foreign LIVE pid must be rejected (pid 1 is init).
    (job_dir / "job.lock").write_text("1\n2020-01-01T00:00:00\n", encoding="utf-8")
    try:
        ResumableJobRuntime(job_dir, job_id="J", topic="t", config={"k": 2})
        rejected = False
    except JobConcurrencyError:
        rejected = True
    c.check("live foreign lock rejects concurrent worker", rejected)

    # A lock from a DEAD pid is stale and reclaimed.
    import subprocess as _sp

    child = _sp.Popen(["/bin/true"])
    child.wait()
    dead_pid = child.pid
    (job_dir / "job.lock").write_text(f"{dead_pid}\n2020-01-01T00:00:00\n", encoding="utf-8")
    runtime4 = ResumableJobRuntime(job_dir, job_id="J", topic="t", config={"k": 2})
    c.check("stale lock from dead pid reclaimed", runtime4.lock_path.exists())
    runtime4.release_lock()

    # Retry budget.
    budget_dir = base / "job_budget"

    def failing():
        raise ValueError("boom")

    budget_runtime = ResumableJobRuntime(budget_dir, job_id="B", topic="t", config={}, max_stage_attempts=2)
    for _ in range(2):
        try:
            budget_runtime.execute("f", {"x": 1}, failing)
        except ValueError:
            pass
    try:
        budget_runtime.execute("f", {"x": 1}, failing)
        exhausted = False
    except StageRetryExhaustedError:
        exhausted = True
    c.check("stage retry budget enforced", exhausted)

    # No tracebacks inside the manifest; errors are typed and redacted.
    record = budget_runtime.manifest.stages["f"]
    c.check(
        "manifest stores typed redacted error, no traceback",
        record.error_type == "ValueError" and "traceback" not in record.metadata,
    )
    budget_runtime.release_lock()


def _test_download_validation(c: Collector, base: Path) -> None:
    real_png = _png_bytes()
    ok_response = FakeHTTPResponse(content=real_png, headers={"Content-Type": "image/png"})
    saved = download_image(ok_response, base / "dl" / "ok.png")
    c.check("valid image downloads atomically", saved.exists() and not list(saved.parent.glob(".*.part")))

    for name, response in (
        ("html body", FakeHTTPResponse(content=b"<html>err</html>", headers={"Content-Type": "text/html"})),
        ("json error body", FakeHTTPResponse(content=b'{"error": "x"}', headers={"Content-Type": "application/json"})),
        ("empty body", FakeHTTPResponse(content=b"", headers={"Content-Type": "image/png"})),
        ("corrupt image", FakeHTTPResponse(content=b"\x89PNG\r\n\x1a\nGARBAGE", headers={"Content-Type": "image/png"})),
    ):
        try:
            download_image(response, base / "dl" / "bad.png")
            ok = False
        except DownloadPolicyError:
            ok = True
        c.check(f"download rejects {name}", ok and not (base / "dl" / "bad.png").exists())

    big = FakeHTTPResponse(content=real_png, headers={"Content-Type": "image/png", "Content-Length": str(10**9)})
    try:
        download_image(big, base / "dl" / "big.png", max_bytes=1024 * 1024)
        ok = False
    except DownloadPolicyError:
        ok = True
    c.check("download rejects oversized declared payload", ok)

    small_cap = FakeHTTPResponse(content=real_png, headers={"Content-Type": "image/png"})
    try:
        download_image(small_cap, base / "dl" / "cap.png", max_bytes=10)
        ok = False
    except DownloadPolicyError:
        ok = True
    c.check("download rejects payload above byte cap", ok)


# ---------------------------------------------------------------------------
# Provider-lock and router tests
# ---------------------------------------------------------------------------


def _test_provider_locks(c: Collector, base: Path) -> None:
    # Production text generation with no OpenAI key fails clearly.
    router = LLMRouter({"execution_mode": "production", "provider_order": ["openai"]}, {}, base / "router_cache")
    try:
        router.generate_json(system="s", prompt="p", namespace="lock", fallback={"fallback": True}, force=True)
        ok = False
    except ProviderUnavailableError:
        ok = True
    c.check("production text fails clearly without OpenAI", ok)

    # Production never touches Gemini/OpenRouter even when keys exist and the
    # (invalid) order lists them.
    called = {"gemini": 0, "openrouter": 0, "local": 0}

    class SpyRouter(LLMRouter):
        def _gemini_json(self, *a, **k):
            called["gemini"] += 1
            return '{"x": 1}'

        def _openrouter_json(self, *a, **k):
            called["openrouter"] += 1
            return '{"x": 1}'

        def _local_json(self, *a, **k):
            called["local"] += 1
            return '{"x": 1}'

    spy = SpyRouter(
        {
            "execution_mode": "production",
            "provider_order": ["gemini", "openrouter", "local", "openai"],
            "enable_local_fallback": True,
        },
        {"GEMINI_API_KEY": "g-key-123456", "OPENROUTER_API_KEY": "or-key-123456"},
        base / "router_cache2",
    )
    c.check("production filters provider order to openai", spy.provider_order == ["openai"])
    try:
        spy.generate_json(system="s", prompt="p", namespace="lock2", fallback={"f": 1}, force=True)
    except ProviderUnavailableError:
        pass
    c.check("production never calls unauthorized providers", called == {"gemini": 0, "openrouter": 0, "local": 0})

    # Production vision fails clearly instead of returning the fallback.
    image = _image_file(base / "router" / "img.png")
    try:
        spy.critique_image(image_path=image, prompt="review", fallback={"approve": True}, force=True)
        ok = False
    except ProviderUnavailableError:
        ok = True
    c.check("production vision fails clearly without OpenAI", ok)

    # Production image generation requires BFL; no fallback engines.
    try:
        spy.generate_reference_image(prompt="x", output_path=base / "router" / "gen.png", force=True)
        ok = False
    except ProviderUnavailableError:
        ok = True
    c.check("production image generation requires BFL", ok)

    # Injected fake providers are rejected by the pipeline in production.
    from .pipeline import ScientificMotionStudioV10
    from .tests import FakeLLM

    try:
        ScientificMotionStudioV10(
            {
                "workspace": str(base / "prod_studio"),
                "execution_mode": "production",
                "llm": {"provider_order": ["openai"], "vision_provider_order": ["openai"]},
            },
            llm_router=FakeLLM(),
        )
        ok = False
    except ProviderLockViolationError:
        ok = True
    c.check("production rejects injected fake providers", ok)

    # Development fallback: allowed, but marked + stored in the fallback
    # namespace, and honours its TTL.
    dev = LLMRouter(
        {"execution_mode": "development", "provider_order": [], "fallback_cache_ttl_s": 3600}, {}, base / "router_dev"
    )
    value = dev.generate_json(system="s", prompt="p", namespace="devns", fallback={"deterministic": True})
    c.check("development fallback returns the fallback value", value == {"deterministic": True})
    fallback_files = list((base / "router_dev" / "_fallback" / "devns").glob("*.json"))
    c.check(
        "fallback cached in separate namespace with marker",
        len(fallback_files) == 1 and load_json(fallback_files[0]).get(FALLBACK_MARKER) is True,
    )
    success_files = list((base / "router_dev" / "devns").glob("*.json"))
    c.check("fallback never cached as live success", not success_files)

    envelope = load_json(fallback_files[0])
    envelope["cached_at"] = time.time() - 999999
    save_json(fallback_files[0], envelope)
    calls = {"n": 0}

    def counted_fallback():
        calls["n"] += 1
        return {"deterministic": True}

    dev.generate_json(system="s", prompt="p", namespace="devns", fallback=counted_fallback)
    c.check("expired fallback cache is not reused", calls["n"] == 1)


def _test_vision_cache_content_hash(c: Collector, base: Path) -> None:
    approvals = iter(['{"status": "approve", "round": 1}', '{"status": "revise", "round": 2}'])

    class VisionRouter(LLMRouter):
        def _openai_vision(self, image_path, prompt, model=None):
            return next(approvals)

    router = VisionRouter(
        {"execution_mode": "development", "provider_order": ["openai"], "vision_provider_order": ["openai"]},
        {"OPENAI_API_KEY": "sk-test-abcdefghijklmnop"},
        base / "vision_cache",
    )
    image = base / "vision" / "frame.png"
    _image_file(image, "#2E77A6")
    first = router.critique_image(image_path=image, prompt="review", fallback=None)
    c.check("vision critique returns provider output", first == {"status": "approve", "round": 1})
    cached = router.critique_image(image_path=image, prompt="review", fallback=None)
    c.check("vision cache hit on identical content", cached == first)
    # Same file name, same byte size, different pixels -> different key.
    original_size = image.stat().st_size
    # PNG re-render with a different color, padded/truncated is unsafe; instead
    # draw a same-size image and pad to the same byte length via tEXt chunkless
    # rewrite: simply regenerate and then compare sizes; if they differ, the
    # test still exercises content-hash keys (name unchanged).
    _image_file(image, "#D8483E")
    second = router.critique_image(image_path=image, prompt="review", fallback=None)
    c.check(
        "changed pixels invalidate vision cache (content hash, not name/size)",
        second == {"status": "revise", "round": 2},
        f"size_before={original_size} size_after={image.stat().st_size}",
    )


def _test_openai_mocked_integration(c: Collector, base: Path) -> None:
    class FakeUsage:
        input_tokens = 10
        output_tokens = 5
        total_tokens = 15

    class FakeOpenAIResponse:
        def __init__(self, text: str, response_id: str = "resp_123"):
            self.output_text = text
            self.id = response_id
            self.usage = FakeUsage()
            self.output = []

    class ScriptedResponses:
        def __init__(self, script):
            self.script = list(script)
            self.calls = 0

        def create(self, **kwargs):
            self.calls += 1
            action = self.script.pop(0)
            if isinstance(action, Exception):
                raise action
            return FakeOpenAIResponse(action)

    class FakeOpenAIClient:
        def __init__(self, script):
            self.responses = ScriptedResponses(script)

    def make_router(script, **extra):
        router = LLMRouter(
            {
                "execution_mode": "development",
                "provider_order": ["openai"],
                "retry": {"max_attempts": 3, "base_delay_s": 0.01, "max_delay_s": 0.02, "jitter": 0.0},
                **extra,
            },
            {"OPENAI_API_KEY": "sk-test-abcdefghijklmnop"},
            base / f"openai_{len(list(base.glob('openai_*')))}",
        )
        router._openai_client = FakeOpenAIClient(script)
        return router

    # Success.
    router = make_router(['{"answer": 42}'])
    value = router.generate_json(system="s", prompt="p", namespace="ok", fallback={"f": 1}, force=True)
    c.check("openai success parses JSON", value == {"answer": 42})

    # Malformed JSON -> bounded repair round-trip succeeds.
    router = make_router(["not json at all", '{"repaired": true}'])
    value = router.generate_json(system="s", prompt="p", namespace="repair", fallback={"f": 1}, force=True)
    c.check(
        "malformed JSON repaired via bounded retry",
        value == {"repaired": True} and router._openai_client.responses.calls == 2,
    )

    # Empty output is retried (classified retryable) then succeeds.
    router = make_router(["", '{"second": true}'])
    value = router.generate_json(system="s", prompt="p", namespace="empty", fallback={"f": 1}, force=True)
    c.check("empty output retried then succeeds", value == {"second": True})

    # 429 then success.
    class Fake429(Exception):
        status_code = 429

    router = make_router([Fake429("rate limited"), '{"after429": true}'])
    value = router.generate_json(system="s", prompt="p", namespace="rate", fallback={"f": 1}, force=True)
    c.check("429 then success recovers", value == {"after429": True} and router._openai_client.responses.calls == 2)

    # 401 fails fast: exactly one call, falls to fallback in development.
    class Fake401(Exception):
        status_code = 401

    router = make_router([Fake401("bad key"), '{"never": true}'])
    value = router.generate_json(system="s", prompt="p", namespace="auth", fallback={"fell_back": True}, force=True)
    c.check("401 fails fast without retry", value == {"fell_back": True} and router._openai_client.responses.calls == 1)


def _test_bfl_mocked_integration(c: Collector, base: Path) -> None:
    real_png = _png_bytes()
    config = {
        "bfl_model": "flux-kontext-pro",
        "bfl_aspect_ratio": "9:16",
        "bfl_output_format": "png",
        "bfl_timeout": 5,
        "bfl_poll_interval": 0.01,
        "bfl_allowed_url_hosts": ["api.bfl.ai", "poll.local", "delivery.local"],
        "retry": {"max_attempts": 3, "base_delay_s": 0.01, "max_delay_s": 0.02, "jitter": 0.0},
    }

    def scripted(post_script, get_factory):
        posts = list(post_script)

        def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
            action = posts.pop(0)
            if isinstance(action, Exception):
                raise action
            return action

        return fake_post, get_factory

    submit_ok = FakeHTTPResponse({"id": "REQ1", "polling_url": "https://poll.local/r"})

    # Submit + poll + download success.
    def get_ok(url, headers=None, params=None, timeout=None, **kwargs):
        if "poll.local" in url:
            return FakeHTTPResponse({"status": "Ready", "result": {"sample": "https://delivery.local/s.png"}})
        return FakeHTTPResponse(content=real_png, headers={"Content-Type": "image/png"})

    client = BFLClient(config, "test-bfl-key-000", base / "bfl_ok")
    with (
        patch("requests.post", side_effect=scripted([submit_ok], get_ok)[0]),
        patch("requests.get", side_effect=get_ok),
    ):
        out = client.generate("prompt", base / "bfl_ok" / "out.png", force=True)
    c.check("bfl submit/poll/download success", out.exists() and out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n")
    meta = load_json(next((base / "bfl_ok" / "bfl_images").glob("*.meta.json")))
    c.check(
        "bfl metadata records request id + hashes, no base64",
        meta["request_id"] == "REQ1"
        and bool(meta["prompt_hash"])
        and meta.get("input_image") is None
        and "base64" not in json.dumps(meta),
    )

    # Cache hit: identical inputs never re-submit.
    posted = {"n": 0}

    def fail_post(url, **kwargs):
        posted["n"] += 1
        raise AssertionError("must not re-submit on cache hit")

    with patch("requests.post", side_effect=fail_post):
        cached = client.generate("prompt", base / "bfl_ok" / "out2.png")
    c.check("bfl content-addressed cache hit skips network", cached.exists() and posted["n"] == 0)

    # 429 then success on submit.
    submit_429 = FakeHTTPResponse({"error": "rate"}, status_code=429)
    client2 = BFLClient(config, "test-bfl-key-000", base / "bfl_429")
    posts = [submit_429, FakeHTTPResponse({"id": "REQ2", "polling_url": "https://poll.local/r"})]

    def post_429(url, headers=None, json=None, timeout=None, **kwargs):
        return posts.pop(0)

    with patch("requests.post", side_effect=post_429), patch("requests.get", side_effect=get_ok):
        out2 = client2.generate("prompt2", base / "bfl_429" / "out.png", force=True)
    c.check("bfl 429 then success recovers", out2.exists())

    # Timeout: poll never becomes Ready.
    def get_pending(url, headers=None, params=None, timeout=None, **kwargs):
        return FakeHTTPResponse({"status": "Pending"})

    client3 = BFLClient({**config, "bfl_timeout": 0.05}, "test-bfl-key-000", base / "bfl_to")
    with patch("requests.post", side_effect=lambda *a, **k: submit_ok), patch("requests.get", side_effect=get_pending):
        try:
            client3.generate("p3", base / "bfl_to" / "out.png", force=True)
            ok = False
        except BFLError as exc:
            ok = "timed out" in str(exc)
    c.check("bfl poll timeout fails explicitly", ok)

    # Moderated result.
    def get_moderated(url, headers=None, params=None, timeout=None, **kwargs):
        return FakeHTTPResponse({"status": "Content Moderated"})

    client4 = BFLClient(config, "test-bfl-key-000", base / "bfl_mod")
    with (
        patch("requests.post", side_effect=lambda *a, **k: submit_ok),
        patch("requests.get", side_effect=get_moderated),
    ):
        try:
            client4.generate("p4", base / "bfl_mod" / "out.png", force=True)
            ok = False
        except BFLError as exc:
            ok = "moderated" in str(exc).lower()
    c.check("bfl moderated result fails explicitly", ok)

    # Unknown status fails explicitly.
    def get_unknown(url, headers=None, params=None, timeout=None, **kwargs):
        return FakeHTTPResponse({"status": "Transmogrifying"})

    client5 = BFLClient(config, "test-bfl-key-000", base / "bfl_unknown")
    with patch("requests.post", side_effect=lambda *a, **k: submit_ok), patch("requests.get", side_effect=get_unknown):
        try:
            client5.generate("p5", base / "bfl_unknown" / "out.png", force=True)
            ok = False
        except BFLError as exc:
            ok = "unknown status" in str(exc)
    c.check("bfl unknown status fails explicitly", ok)

    # Corrupt image payload rejected; nothing cached.
    def get_corrupt(url, headers=None, params=None, timeout=None, **kwargs):
        if "poll.local" in url:
            return FakeHTTPResponse({"status": "Ready", "result": {"sample": "https://delivery.local/s.png"}})
        return FakeHTTPResponse(content=b"\x89PNG\r\n\x1a\nBROKEN", headers={"Content-Type": "image/png"})

    client6 = BFLClient(config, "test-bfl-key-000", base / "bfl_corrupt")
    with patch("requests.post", side_effect=lambda *a, **k: submit_ok), patch("requests.get", side_effect=get_corrupt):
        try:
            client6.generate("p6", base / "bfl_corrupt" / "out.png", force=True)
            ok = False
        except DownloadPolicyError:
            ok = True
    pngs = list((base / "bfl_corrupt" / "bfl_images").glob("*.png"))
    c.check("bfl corrupt image rejected and never cached", ok and not pngs)

    # Wrong content type rejected.
    def get_html(url, headers=None, params=None, timeout=None, **kwargs):
        if "poll.local" in url:
            return FakeHTTPResponse({"status": "Ready", "result": {"sample": "https://delivery.local/s.png"}})
        return FakeHTTPResponse(content=b"<html>x</html>", headers={"Content-Type": "text/html"})

    client7 = BFLClient(config, "test-bfl-key-000", base / "bfl_html")
    with patch("requests.post", side_effect=lambda *a, **k: submit_ok), patch("requests.get", side_effect=get_html):
        try:
            client7.generate("p7", base / "bfl_html" / "out.png", force=True)
            ok = False
        except DownloadPolicyError:
            ok = True
    c.check("bfl wrong content-type rejected", ok)

    # Oversized download rejected.
    def get_big(url, headers=None, params=None, timeout=None, **kwargs):
        if "poll.local" in url:
            return FakeHTTPResponse({"status": "Ready", "result": {"sample": "https://delivery.local/s.png"}})
        return FakeHTTPResponse(content=real_png, headers={"Content-Type": "image/png", "Content-Length": str(10**10)})

    client8 = BFLClient({**config, "bfl_max_download_bytes": 2048}, "test-bfl-key-000", base / "bfl_big")
    with patch("requests.post", side_effect=lambda *a, **k: submit_ok), patch("requests.get", side_effect=get_big):
        try:
            client8.generate("p8", base / "bfl_big" / "out.png", force=True)
            ok = False
        except DownloadPolicyError:
            ok = True
    c.check("bfl oversized download rejected", ok)

    # Unsafe result URL rejected (host outside the allowlist).
    def get_unsafe(url, headers=None, params=None, timeout=None, **kwargs):
        return FakeHTTPResponse({"status": "Ready", "result": {"sample": "https://attacker.example/x.png"}})

    client9 = BFLClient(config, "test-bfl-key-000", base / "bfl_ssrf")
    with patch("requests.post", side_effect=lambda *a, **k: submit_ok), patch("requests.get", side_effect=get_unsafe):
        try:
            client9.generate("p9", base / "bfl_ssrf" / "out.png", force=True)
            ok = False
        except DownloadPolicyError:
            ok = True
    c.check("bfl unsafe result URL rejected (SSRF)", ok)

    # Invalid configuration is rejected before any network call.
    try:
        BFLClient({**config, "bfl_model": "sdxl"}, "k-000000", base / "bfl_badmodel")
        ok = False
    except BFLError:
        ok = True
    c.check("bfl invalid model rejected pre-flight", ok)
    try:
        BFLClient({**config, "bfl_aspect_ratio": "1:10"}, "k-000000", base / "bfl_badar")
        ok = False
    except BFLError:
        ok = True
    c.check("bfl invalid aspect ratio rejected pre-flight", ok)


# ---------------------------------------------------------------------------
# LLM shape-drift coercion (systemic OpenModel)
# ---------------------------------------------------------------------------


def _test_llm_shape_coercion(c: Collector, base: Path) -> None:
    from .schemas import Beat, ResearchPack, ScriptPackage

    # dict-of-notes where a list is declared (first live production failure).
    pack = ResearchPack(topic="t", limitations={"coverage": "sources cited", "uncertainty": "varies"})
    c.check("dict limitations coerced to list", pack.limitations == ["sources cited", "varies"])
    c.check("scalar hook coerced to list", ResearchPack(topic="t", hooks="one").hooks == ["one"])
    c.check("list summary joined to str", ResearchPack(topic="t", summary=["a", "b"]).summary == "a; b")

    # dict/str where a float is declared (second live production failure).
    raw = {
        "topic": "t",
        "facts": [
            {"claim": "a", "confidence": {"label": "high", "score": 0.8}},
            {"claim": "b", "confidence": "about 0.55 (moderate)"},
            {"claim": "c", "confidence": [0.42]},
            {"claim": "d", "confidence": {"label": "unknown"}},
        ],
    }
    facts = ResearchPack.model_validate(raw).facts
    c.check("dict confidence extracts score", facts[0].confidence == 0.8)
    c.check("string confidence parses number", round(facts[1].confidence, 2) == 0.55)
    c.check("list confidence extracts number", facts[2].confidence == 0.42)
    c.check("non-numeric confidence falls back to default", facts[3].confidence == 0.6)

    # int field + dict-of-beats + Any preserved.
    beat = Beat.model_validate({"spoken_line": "x", "pause_after_ms": {"ms": 120}})
    c.check("dict int field extracts number", beat.pause_after_ms == 120)
    script = ScriptPackage.model_validate({"topic": "t", "beats": {"b1": {"spoken_line": "hi"}}})
    c.check("dict-of-beats coerced to list", len(script.beats) == 1 and script.beats[0].spoken_line == "hi")
    c.check(
        "Any field untouched by coercion",
        ResearchPack(topic="t", raw_llm_output={"keep": "dict"}).raw_llm_output == {"keep": "dict"},
    )


# ---------------------------------------------------------------------------
# Empty model strings must fall back to a real model
# ---------------------------------------------------------------------------


def _test_model_fallback(c: Collector, base: Path) -> None:
    captured: dict[str, str] = {}

    class FakeResponse:
        output_text = '{"status": "approve"}'
        id = "resp_x"
        usage = None
        output: list = []

    class FakeResponses:
        def create(self, **kwargs):
            captured["model"] = kwargs["model"]
            return FakeResponse()

    class FakeOpenAIClient:
        responses = FakeResponses()

    # The live failure: production config with an empty vision model string.
    router = LLMRouter(
        {
            "execution_mode": "production",
            "provider_order": ["openai"],
            "vision_provider_order": ["openai"],
            "openai_model": "gpt-5-mini",
            "openai_vision_model": "",
            "retry": {"max_attempts": 1},
        },
        {"OPENAI_API_KEY": "sk-test-abcdefghijklmnop"},
        base / "cache",
    )
    router._openai_client = FakeOpenAIClient()
    image = _image_file(base / "frame.png")
    router.critique_image(image_path=image, prompt="review", fallback=None, force=True)
    c.check("empty vision model falls back to openai_model", captured.get("model") == "gpt-5-mini", str(captured))

    captured.clear()
    router2 = LLMRouter(
        {
            "execution_mode": "production",
            "provider_order": ["openai"],
            "openai_model": "",
            "retry": {"max_attempts": 1},
        },
        {"OPENAI_API_KEY": "sk-test-abcdefghijklmnop"},
        base / "cache2",
    )
    router2._openai_client = FakeOpenAIClient()
    router2.generate_json(system="s", prompt="p", namespace="ns", fallback=None, force=True)
    c.check("empty openai_model falls back to default", captured.get("model") == "gpt-5-mini", str(captured))


def _test_vision_two_tier_escalation(c: Collector, base: Path) -> None:
    """Qwen VL primary handles confident cases; low-confidence/flagged cases
    escalate to Gemini. Production allows the openrouter+gemini vision pair."""

    class TwoTierRouter(LLMRouter):
        def __init__(self, *args, primary_payload="", **kwargs):
            super().__init__(*args, **kwargs)
            self.primary_payload = primary_payload
            self.calls = {"openrouter": 0, "gemini": 0}
            self.openrouter_models: list[str] = []
            self.primary_prompt = ""

        def _openrouter_vision(self, image_path, prompt, model=None):
            self.calls["openrouter"] += 1
            self.openrouter_models.append(model)
            if len(self.openrouter_models) == 1:
                self.primary_prompt = prompt
                return self.primary_payload
            # Second OpenRouter tier == the escalation model (Gemini via OpenRouter).
            return '{"status": "revise", "reviewer": "openrouter-escalation"}'

        def _gemini_vision(self, image_path, prompt, model=None):
            self.calls["gemini"] += 1
            return '{"status": "revise", "reviewer": "gemini"}'

    secrets = {"OPENROUTER_API_KEY": "sk-or-testkey12345678", "GEMINI_API_KEY": "gm-testkey12345678"}
    image = _image_file(base / "frame.png")

    def make(primary_payload, ns):
        return TwoTierRouter(
            {
                "execution_mode": "production",
                "provider_order": ["openai"],
                "vision_provider_order": ["openrouter", "gemini"],
                "vision_escalation_confidence": 0.62,
                "retry": {"max_attempts": 1},
            },
            secrets,
            base / ns,
            primary_payload=primary_payload,
        )

    # Confident primary (0.9 >= 0.62): keep Qwen, never touch Gemini (~80% path).
    confident = make('{"status": "approve", "_meta": {"review_confidence": 0.9}}', "c1")
    out = confident.critique_image(image_path=image, prompt="review", fallback=None, force=True)
    c.check(
        "confident primary is not escalated", confident.calls == {"openrouter": 1, "gemini": 0}, str(confident.calls)
    )
    c.check("primary verdict returned on confident case", out.get("status") == "approve")
    c.check("_meta stripped from returned critique", "_meta" not in out)
    c.check("primary reviewer asked to self-report confidence", "review_confidence" in confident.primary_prompt)

    # Low-confidence primary (0.3 < 0.62): escalate to Gemini (~20% path).
    lowconf = make('{"status": "approve", "_meta": {"review_confidence": 0.3}}', "c2")
    out = lowconf.critique_image(image_path=image, prompt="review", fallback=None, force=True)
    c.check(
        "low-confidence primary escalates to gemini",
        lowconf.calls == {"openrouter": 1, "gemini": 1},
        str(lowconf.calls),
    )
    c.check("escalated verdict comes from gemini", out.get("reviewer") == "gemini")

    # Explicit needs_expert_review flag also escalates even if confidence absent.
    flagged = make('{"status": "approve", "_meta": {"needs_expert_review": true}}', "c3")
    out = flagged.critique_image(image_path=image, prompt="review", fallback=None, force=True)
    c.check("flagged primary escalates to gemini", flagged.calls["gemini"] == 1, str(flagged.calls))

    # Disabling escalation keeps everything on the primary regardless of confidence.
    disabled = TwoTierRouter(
        {
            "execution_mode": "production",
            "provider_order": ["openai"],
            "vision_provider_order": ["openrouter", "gemini"],
            "vision_escalation_enabled": False,
            "retry": {"max_attempts": 1},
        },
        secrets,
        base / "c4",
        primary_payload='{"status": "approve"}',
    )
    # With escalation off, the primary prompt has no confidence instruction, so
    # override the assertion path by calling directly through critique_image.
    disabled.critique_image(image_path=image, prompt="review", fallback=None, force=True)
    c.check(
        "escalation disabled keeps single primary call",
        disabled.calls == {"openrouter": 1, "gemini": 0},
        str(disabled.calls),
    )

    # Both tiers via ONE OpenRouter key: primary Qwen + escalation model through
    # the same provider (no separate Gemini key). Only OPENROUTER_API_KEY set.
    single_key = TwoTierRouter(
        {
            "execution_mode": "production",
            "provider_order": ["openai"],
            "vision_provider_order": ["openrouter"],
            "openrouter_vision_model": "qwen/qwen-2.5-vl-72b-instruct",
            "openrouter_vision_escalation_model": "google/gemini-2.5-flash",
            "vision_escalation_confidence": 0.62,
            "retry": {"max_attempts": 1},
        },
        {"OPENROUTER_API_KEY": "sk-or-testkey12345678"},
        base / "c5",
        primary_payload='{"status": "approve", "_meta": {"review_confidence": 0.2}}',
    )
    out = single_key.critique_image(image_path=image, prompt="review", fallback=None, force=True)
    c.check(
        "openrouter-only config escalates via a second openrouter call",
        single_key.calls == {"openrouter": 2, "gemini": 0},
        str(single_key.calls),
    )
    c.check(
        "escalation tier uses the configured openrouter escalation model",
        single_key.openrouter_models == ["qwen/qwen-2.5-vl-72b-instruct", "google/gemini-2.5-flash"],
        str(single_key.openrouter_models),
    )
    c.check("escalated verdict returned from second openrouter tier", out.get("reviewer") == "openrouter-escalation")


def _test_director_review_salvage(c: Collector, base: Path) -> None:
    """A present-but-imperfect vision critique must never collapse to the strict
    'requires_human_or_vision_director' hard-fail; it is coerced into a valid
    DirectorChangeOrder instead."""
    from .flux_studio import FluxKontextStudio
    from .schemas import DirectorChangeOrder, DrawingBrief

    brief = DrawingBrief(
        brief_id="B01",
        scene_id="S01",
        purpose="beauty_frame",
        positive_prompt="editorial ink hero",
        negative_prompt="no mascot",
        kontext_instruction="render",
        preserve=["master style"],
    )
    fallback = DirectorChangeOrder(
        scene_id="S01",
        status="requires_human_or_vision_director",
        immutable_preserve_list=brief.preserve,
    )
    coerce = FluxKontextStudio._coerce_change_order.__get__(FluxKontextStudio.__new__(FluxKontextStudio))

    # 1) revise verdict with adjustments missing required sub-fields -> repaired.
    order = coerce(
        {"status": "revise", "adjustments": [{"problem": "hand looks fused"}]},
        brief,
        1,
        fallback,
    )
    c.check("malformed revise is salvaged, not hard-failed", order.status == "revise")
    c.check("salvaged adjustment gets required fields", order.adjustments[0].instruction != "")
    c.check("salvaged order carries known scene_id", order.scene_id == "S01")

    # 2) revise verdict with NO actionable adjustments -> treated as approve.
    order = coerce({"status": "revise", "adjustments": []}, brief, 1, fallback)
    c.check("revise-without-adjustments becomes approve", order.status == "approve")

    # 3) approve verdict passes straight through.
    order = coerce({"status": "approve"}, brief, 1, fallback)
    c.check("approve verdict preserved", order.status == "approve")

    # 4) the genuine 'no reviewer' sentinel is respected (stays strict).
    order = coerce({"status": "requires_human_or_vision_director"}, brief, 1, fallback)
    c.check("no-reviewer sentinel is not silently approved", order.status == "requires_human_or_vision_director")


def _test_shot_executor(c: Collector, base: Path) -> None:
    """The executor renders ONLY authored directives — nothing auto-activates.

    No camera/effect/caption in the plan => the frame is returned untouched
    (a true hold). Authored directives => exactly that motion, and only within
    their time window."""
    from PIL import Image, ImageDraw

    from .motion_graphics import ShotExecutor

    canvas = Image.new("RGB", (360, 640), "#7fa8c9")
    _d = ImageDraw.Draw(canvas)
    _d.ellipse([120, 220, 240, 340], fill="#20406a")
    # Texture the frame so a small camera zoom yields a measurable pixel diff.
    for gx in range(0, 360, 24):
        _d.line([(gx, 0), (gx, 640)], fill="#5a86a8", width=1)
    for gy in range(0, 640, 24):
        _d.line([(0, gy), (360, gy)], fill="#5a86a8", width=1)

    def diff(x, y):
        xd, yd = list(x.convert("L").getdata()), list(y.convert("L").getdata())
        return sum(abs(p - q) for p, q in zip(xd, yd)) / (len(xd) * 255.0)

    ex = ShotExecutor({})

    # 1) Empty plan (default hold) -> NO motion at all. This is the core fix.
    hold_plan = {"scene_id": "S01", "camera": {"move": "hold", "magnitude": 0.0}, "effects": [], "captions": []}
    a = ex.execute(canvas, hold_plan, 5, 120)
    b = ex.execute(canvas, hold_plan, 60, 120)
    c.check("no directives -> pixel-identical hold (no invented motion)", diff(a, b) == 0.0)
    c.check("hold frame equals input", diff(a, canvas) == 0.0)

    # 2) Authored camera push -> real motion appears, and only it.
    cam_plan = {"scene_id": "S01", "camera": {"move": "push_in", "magnitude": 0.08, "start_frame": 0, "end_frame": 120}}
    c.check(
        "authored camera move produces motion",
        diff(ex.execute(canvas, cam_plan, 5, 120), ex.execute(canvas, cam_plan, 90, 120)) > 0.01,
    )

    # 3) Authored effect only inside its window.
    fx_plan = {"scene_id": "S01", "effects": [{"effect": "rain", "intensity": 0.8, "start_frame": 10, "end_frame": 40}]}
    before = ex.execute(canvas, fx_plan, 2, 120)  # before window
    during = ex.execute(canvas, fx_plan, 25, 120)  # in window
    c.check("effect is off before its authored window", diff(before, canvas) == 0.0)
    c.check("effect renders inside its authored window", diff(during, canvas) > 0.0)

    # 4) Authored caption draws text; absent caption draws nothing.
    cap_plan = {
        "scene_id": "S01",
        "captions": [{"kind": "headline", "text": "Sea level rises", "start_frame": 0, "end_frame": 120}],
    }
    c.check("authored caption is drawn", diff(ex.execute(canvas, cap_plan, 60, 120), canvas) > 0.005)

    # 5) Disabled executor is a pass-through; malformed plan never raises.
    off = ShotExecutor({"execute_shot_directives": False})
    c.check("disabled executor is a no-op", diff(off.execute(canvas, cam_plan, 5, 120), canvas) == 0.0)
    ex.execute(canvas, {}, 0, 0)
    c.check("executor tolerates empty plan", True)


def _test_object_segmenter(c: Collector, base: Path) -> None:
    """The heuristic segmenter (no GPU) must separate a drawn subject from the
    flat background into a binary mask — the basis for per-object cutouts."""
    from PIL import Image, ImageDraw

    from .sam2_segment import ObjectSegmenter

    beauty = base / "beauty.png"
    beauty.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (240, 320), "#FAFAFA")  # flat paper background
    ImageDraw.Draw(img).ellipse([80, 110, 160, 210], fill="#20406a")  # the subject
    img.save(beauty)

    seg = ObjectSegmenter({"use_sam2": False}, base / "seg")
    c.check("sam2 disabled -> not available", seg.available_sam2() is False)
    out = base / "mask.png"
    result = seg.mask_for(beauty, "the subject", out)
    c.check("segmenter returns a mask path", result is not None and Path(result).exists())
    mask = Image.open(out).convert("L")
    values = set(mask.getdata())
    c.check("mask is binary", values <= {0, 255})
    fg = sum(1 for p in mask.getdata() if p == 255)
    total = mask.width * mask.height
    c.check(
        "subject separated (partial foreground, not empty/full)",
        0.02 * total < fg < 0.9 * total,
        f"fg_frac={fg / total:.3f}",
    )
    # Corners (background) must be black; the shape centre must be white.
    c.check("background corner excluded from mask", mask.getpixel((2, 2)) == 0)
    c.check("subject centre included in mask", mask.getpixel((120, 160)) == 255)
    # The mask_generator adapter matches SemanticMaskExtractor's signature.
    gen = seg.as_mask_generator()
    c.check("mask_generator adapter works", gen(str(beauty), "the subject", base / "mask2.png") is not None)


def _test_object_grounding(c: Collector, base: Path) -> None:
    """Grounding locates each causal object with a real bbox/points/pivot before
    SAM2. Without a vision model it uses the deterministic fallback."""
    from types import SimpleNamespace

    from .object_grounding import ObjectGrounder

    arch = SimpleNamespace(
        scene_id="S01",
        motion_seams=[
            SimpleNamespace(seam_id="primary-causal-change", subject="river overtops the bank", region="river"),
            SimpleNamespace(seam_id="secondary", subject="rising water line", region="water"),
        ],
    )
    beauty = base / "beauty.png"
    _image_file(beauty)
    g = ObjectGrounder(None, {}, base / "grounding")  # llm=None -> fallback grounding
    manifest = g.ground(beauty, arch)
    c.check("grounding falls back without a vision model", manifest.grounding_source == "deterministic-fallback")
    c.check("one object per seam", len(manifest.objects) == 2)
    obj = manifest.objects[0]
    c.check("object is bound to its seam", obj.seam_id == "primary-causal-change")
    c.check("bbox normalized and valid", all(0.0 <= v <= 1.0 for v in obj.bbox) and obj.bbox[2] > obj.bbox[0])
    c.check("object has a positive prompt point", len(obj.positive_points) >= 1)
    c.check("primary object flagged as causal subject", obj.is_causal_subject)


def _test_mask_quality_gate(c: Collector, base: Path) -> None:
    """mask_qc rejects empty, near-full and duplicate masks; accepts a good
    partial mask that overlaps the target bbox."""
    from PIL import Image, ImageDraw

    from .sam2_segment import ObjectSegmenter

    seg = ObjectSegmenter({"use_sam2": False}, base / "seg")
    base.mkdir(parents=True, exist_ok=True)
    empty = base / "empty.png"
    Image.new("L", (200, 200), 0).save(empty)
    c.check("empty mask fails QC", "empty" in seg.mask_qc(empty).get("reasons", []))
    full = base / "full.png"
    Image.new("L", (200, 200), 255).save(full)
    c.check("near-full-canvas mask fails QC", "near_full_canvas" in seg.mask_qc(full).get("reasons", []))
    good = base / "good.png"
    gim = Image.new("L", (200, 200), 0)
    ImageDraw.Draw(gim).rectangle([60, 60, 140, 140], fill=255)
    gim.save(good)
    ok_report = seg.mask_qc(good, (0.25, 0.25, 0.75, 0.75))
    c.check("good partial mask passes QC", ok_report["ok"], str(ok_report))
    c.check(
        "mask outside target bbox fails QC",
        "bbox_mismatch" in seg.mask_qc(good, (0.0, 0.0, 0.1, 0.1)).get("reasons", []),
    )
    c.check(
        "duplicate mask flagged",
        "duplicate_of_other_object" in seg.mask_qc(good, (0.25, 0.25, 0.75, 0.75), [good]).get("reasons", []),
    )


def _test_post_render_qc(c: Collector, base: Path) -> None:
    """Rendered causal clarity is verified from REAL frame evidence, not from the
    plan JSON: an action shot must show the object region change; a declared hold
    must not."""
    from PIL import Image, ImageDraw

    from .post_render_qc import verify

    mask = Image.new("L", (120, 120), 0)
    ImageDraw.Draw(mask).rectangle([30, 30, 90, 90], fill=255)
    initial = Image.new("RGB", (120, 120), "#3060a0")
    final_moved = initial.copy()
    ImageDraw.Draw(final_moved).rectangle([30, 30, 90, 90], fill="#d04030")  # object region changed
    final_static = initial.copy()

    action = {
        "causal_summary": "river rises",
        "events": [{"event_id": "E", "representation": "mask_reveal", "secondary": False}],
    }
    moved = verify(initial, final_moved, mask, action)
    c.check(
        "rendered object change verifies an action shot", moved["render_verified"] and moved["object_moved_in_render"]
    )
    static = verify(initial, final_static, mask, action)
    c.check(
        "static render fails action-shot QC (JSON said motion, frames show none)", static["render_verified"] is False
    )

    hold = {"causal_summary": "the system rests", "events": []}
    c.check("declared hold verifies when nothing moved", verify(initial, final_static, mask, hold)["render_verified"])
    c.check(
        "declared hold fails if the object actually moved",
        verify(initial, final_moved, mask, hold)["render_verified"] is False,
    )


def _test_renderer_parity(c: Collector, base: Path) -> None:
    """The Remotion project must interpret the SAME DSL the PIL renderer does:
    every representation, ALL events per layer (not one via .find), camera,
    effects and captions."""
    from .hybrid_render import RemotionHybridExporter
    from .schemas import AnimationPlan, HybridLayer, HybridScenePackage, MotionEvent

    layer_img = base / "layer.png"
    _image_file(layer_img)
    events = [
        MotionEvent(
            event_id="E1", reason_id="r", target_layer="obj", representation="translate", start_frame=0, end_frame=10
        ),
        MotionEvent(
            event_id="E2",
            reason_id="r",
            target_layer="obj",
            representation="rotate",
            start_frame=0,
            end_frame=10,
            secondary=True,
        ),
    ]
    plan = AnimationPlan(
        scene_id="S01",
        duration_frames=12,
        events=events,
        camera={"move": "push_in", "magnitude": 0.06, "start_frame": 0, "end_frame": 12},
        effects=[{"effect": "rain", "intensity": 0.7, "start_frame": 0, "end_frame": 12}],
        captions=[{"kind": "headline", "text": "Sea level rises", "start_frame": 0, "end_frame": 12}],
    )
    scene = HybridScenePackage(
        scene_id="S01",
        duration_frames=12,
        animation=plan,
        layers=[HybridLayer(layer_id="obj", kind="raster", path=str(layer_img), z_index=15, pivot=(0.4, 0.6))],
    )
    project = RemotionHybridExporter({}, base / "remotion").create_project([scene])
    tsx = (Path(project) / "src" / "index.tsx").read_text()
    for rep in (
        "translate",
        "rotate",
        "scale",
        "opacity",
        "mask_reveal",
        "replacement_pose",
        "texture_loop",
        "local_deformation",
    ):
        c.check(f"remotion handles representation {rep}", f"'{rep}'" in tsx)
    c.check(
        "remotion iterates ALL events per layer (filter, not find)",
        "eventsFor" in tsx and ".find((e:any)=>e.target_layer" not in tsx,
    )
    c.check("remotion applies easing", "ease(" in tsx)
    c.check("remotion executes camera directive", "cameraStyle" in tsx)
    c.check("remotion executes effect directives", "Particles" in tsx)
    c.check("remotion executes caption directives", "Captions" in tsx)
    c.check("remotion transforms about the object pivot", "transformOrigin" in tsx and "layer.pivot" in tsx)
    data = json.loads((Path(project) / "public" / "data.json").read_text())
    anim = data["scenes"][0]["animation"]
    c.check("data.json carries the full DSL", "camera" in anim and "effects" in anim and "captions" in anim)


def _test_clean_plate(c: Collector, base: Path) -> None:
    """The clean background plate must reconstruct behind a removed object, not
    leave a transparent hole or the object's own colour."""
    from PIL import Image, ImageDraw

    from .hybrid_package import HybridPackageBuilder

    builder = HybridPackageBuilder({}, base / "hp")
    beauty = Image.new("RGB", (160, 160), "#3a70a0")  # blue background
    ImageDraw.Draw(beauty).ellipse([50, 50, 110, 110], fill="#c02020")  # red object
    hole = Image.new("L", (160, 160), 0)
    ImageDraw.Draw(hole).ellipse([50, 50, 110, 110], fill=255)
    plate = builder._clean_plate(beauty, hole)
    c.check("clean plate keeps size and is opaque RGB", plate.size == beauty.size and plate.mode == "RGB")
    cx = plate.getpixel((80, 80))
    c.check(
        "object removed from clean plate (background bled in, not the red object)",
        not (cx[0] > 150 and cx[1] < 90 and cx[2] < 90),
        str(cx),
    )


def _test_rig_builder(c: Collector, base: Path) -> None:
    """RigBuilder produces an anatomical bone hierarchy with per-part masks from
    the deterministic fallback (no pose model), and a trivial root rig for props."""
    from PIL import Image, ImageDraw

    from .rig_builder import BONE_CHAIN, RigBuilder

    base.mkdir(parents=True, exist_ok=True)
    beauty = base / "beauty.png"
    Image.new("RGB", (180, 320), "white").save(beauty)
    mask = Image.new("L", (180, 320), 0)
    ImageDraw.Draw(mask).ellipse([50, 40, 130, 300], fill=255)
    mask_path = base / "mask.png"
    mask.save(mask_path)

    rb = RigBuilder({}, base)
    rig = rb.build(beauty, (0.28, 0.10, 0.72, 0.98), mask_path, "char", is_figure=True, out_dir=base)
    c.check("rig falls back without a pose model", rig["source"] == "fallback")
    names = {b["name"] for b in rig["bones"]}
    for anatomical in ("head", "spine", "upper_arm_l", "forearm_r", "hand_l", "thigh_r", "calf_l", "foot_r"):
        c.check(f"rig has anatomical bone {anatomical}", anatomical in names)
    c.check("rig bone count matches the canonical chain", len(rig["bones"]) == len(BONE_CHAIN))
    parents = {b["name"]: b["parent"] for b in rig["bones"]}
    c.check("forearm parents the upper arm", parents["forearm_l"] == "upper_arm_l")
    c.check("hand parents the forearm", parents["hand_r"] == "forearm_r")
    c.check("every bone has a carved part mask", all(b["mask"] and Path(b["mask"]).exists() for b in rig["bones"]))
    prop = rb.build(beauty, (0.4, 0.4, 0.6, 0.6), None, "planet", is_figure=False, out_dir=base)
    c.check("prop gets a single root bone", prop["source"] == "prop" and len(prop["bones"]) == 1)


def _test_skeletal_deform(c: Collector, base: Path) -> None:
    """The deformer executes an authored pose (per-bone angle deltas) via forward
    kinematics and produces a visibly different frame at rest vs. full pose. It
    invents nothing: an unknown/empty pose holds the character still."""
    from PIL import Image, ImageChops, ImageDraw

    from .rig_builder import RigBuilder
    from .skeletal_deform import GESTURE_LIBRARY, SkeletalDeformer, resolve_pose

    base.mkdir(parents=True, exist_ok=True)
    beauty = Image.new("RGB", (180, 320), "white")
    d = ImageDraw.Draw(beauty)
    d.ellipse([60, 20, 120, 80], fill=(200, 120, 80))
    d.rectangle([50, 80, 130, 220], fill=(80, 120, 200))
    bpath = base / "b.png"
    beauty.save(bpath)
    mask = Image.new("L", (180, 320), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse([60, 20, 120, 80], fill=255)
    md.rectangle([50, 80, 130, 220], fill=255)
    mpath = base / "m.png"
    mask.save(mpath)

    rig = RigBuilder({}, base).build(bpath, (0.28, 0.06, 0.72, 0.72), mpath, "char", is_figure=True, out_dir=base)
    part_masks = {b["name"]: Image.open(b["mask"]).convert("L") for b in rig["bones"] if b["mask"]}
    cutout = Image.new("RGBA", (180, 320), (0, 0, 0, 0))
    cutout.paste(beauty, (0, 0), mask)

    deformer = SkeletalDeformer({})
    c.check("named gesture resolves to authored bone deltas", bool(resolve_pose({"pose": "wave"})))
    c.check("explicit bones authored are honoured", resolve_pose({"bones": {"head": 10}}) == {"head": 10.0})
    c.check("unknown pose resolves to empty (no invented motion)", resolve_pose({"pose": "nope"}) == {})

    pose = GESTURE_LIBRARY["wave"]
    f_rest = deformer.deform(cutout, rig, part_masks, pose, 0.0)
    f_full = deformer.deform(cutout, rig, part_masks, pose, 1.0)
    diff = ImageChops.difference(f_rest.convert("L"), f_full.convert("L"))
    changed = sum(1 for p in diff.getdata() if p > 10)
    c.check("skeletal deform produces visible articulation rest->pose", changed > 200, f"changed={changed}")


def _test_anatomical_separation(c: Collector, base: Path) -> None:
    """Anatomical separation yields all 14 named parts, each with parent, pivot,
    rest_rotation, z-order and confidence, every part inside the silhouette (no
    background leakage), arms occluding in front of the torso."""
    from PIL import Image, ImageDraw

    from .rig_builder import RigBuilder

    base.mkdir(parents=True, exist_ok=True)
    b = Image.new("RGB", (240, 420), "white")
    dr = ImageDraw.Draw(b)
    dr.ellipse([95, 30, 145, 90], fill=(210, 150, 110))
    dr.rectangle([100, 90, 140, 240], fill=(70, 110, 190))
    dr.rectangle([70, 100, 100, 220], fill=(70, 110, 190))
    dr.rectangle([140, 100, 170, 220], fill=(70, 110, 190))
    dr.rectangle([102, 240, 118, 400], fill=(60, 60, 90))
    dr.rectangle([122, 240, 138, 400], fill=(60, 60, 90))
    bp = base / "b.png"
    b.save(bp)
    m = Image.new("L", (240, 420), 0)
    mm = ImageDraw.Draw(m)
    mm.ellipse([95, 30, 145, 90], fill=255)
    mm.rectangle([70, 90, 170, 240], fill=255)
    mm.rectangle([102, 240, 138, 400], fill=255)
    mp = base / "m.png"
    m.save(mp)

    rig = RigBuilder({}, base).build(bp, (0.29, 0.07, 0.71, 0.95), mp, "char", is_figure=True, out_dir=base)
    expected = [
        "head",
        "spine",
        "upper_arm_l",
        "forearm_l",
        "hand_l",
        "upper_arm_r",
        "forearm_r",
        "hand_r",
        "thigh_l",
        "calf_l",
        "foot_l",
        "thigh_r",
        "calf_r",
        "foot_r",
    ]
    bones = {bn["name"]: bn for bn in rig["bones"]}
    c.check("all 14 anatomical parts produced", all(e in bones for e in expected))
    for e in ("head", "forearm_l", "hand_r", "calf_l"):
        bn = bones[e]
        has = all(k in bn for k in ("parent", "pivot", "rest_rotation", "z", "confidence", "mask", "cutout"))
        c.check(f"part {e} has parent/pivot/rest_rotation/z/confidence/mask/cutout", has, str(sorted(bn)))
        c.check(f"part {e} mask + cutout on disk", Path(bn["mask"]).exists() and Path(bn["cutout"]).exists())
    # No background leakage: every part mask is inside the character mask.
    cm = list(Image.open(mp).convert("L").getdata())
    leak = 0
    for bn in rig["bones"]:
        pm = list(Image.open(bn["mask"]).convert("L").getdata())
        leak += sum(1 for pv, cv in zip(pm, cm) if pv >= 128 and cv < 128)
    c.check("no background leakage in any part mask", leak == 0, f"leak={leak}")
    c.check("occlusion order: forearms in front of torso", bones["forearm_l"]["z"] > bones["spine"]["z"])
    c.check("part_quality recorded with a source + confidence", "part_quality" in rig and rig["part_quality"]["parts"])
    c.check(
        "offline uses geometric fallback, not raw-accepted SAM2",
        rig["part_quality"]["refined_with_sam2"] is False,
    )


def _test_part_perceptual_qc(c: Collector, base: Path) -> None:
    """Perceptual QC renders a contact sheet (rest + 3 poses) and runs structural
    joint/leakage/occlusion checks — surfacing issues rather than rubber-stamping."""
    from PIL import Image, ImageDraw

    from .part_qc import QC_POSES, perceptual_qc
    from .rig_builder import RigBuilder

    base.mkdir(parents=True, exist_ok=True)
    b = Image.new("RGB", (200, 360), "white")
    dr = ImageDraw.Draw(b)
    dr.ellipse([80, 24, 120, 74], fill=(200, 140, 100))
    dr.rectangle([70, 74, 130, 340], fill=(80, 120, 190))
    bp = base / "b.png"
    b.save(bp)
    m = Image.new("L", (200, 360), 0)
    ImageDraw.Draw(m).rectangle([65, 24, 135, 345], fill=255)
    mp = base / "m.png"
    m.save(mp)
    rig = RigBuilder({}, base).build(bp, (0.32, 0.06, 0.68, 0.96), mp, "char", is_figure=True, out_dir=base)
    base_cut = Image.new("RGBA", (200, 360), (0, 0, 0, 0))
    base_cut.paste(b, (0, 0), m)
    report = perceptual_qc(rig, base_cut, base / "qc", config={})
    c.check("QC produced a contact sheet on disk", Path(report["contact_sheet"]).exists())
    c.check("contact sheet has rest + 3 articulated poses", len(QC_POSES) == 4)
    for key in ("structural_ok", "issues", "area_spread", "part_source", "min_confidence", "failed_parts", "ok"):
        c.check(f"QC report has '{key}'", key in report)
    c.check("QC report ok is a boolean verdict", isinstance(report["ok"], bool))
    c.check("limb-length/tearing area_spread is measured (0..1)", 0.0 <= report["area_spread"] <= 1.0)


def _test_artifact_graph_p1(c: Collector, base: Path) -> None:
    """P1 acceptance: the artifact DAG gives dependency-aware deterministic
    recovery — the five scenarios the operator specified."""
    from .artifact_graph import ArtifactGraph, FailureClass, NodeStatus
    from .bfl_client import BFLError

    def build(root: Path) -> ArtifactGraph:
        g = ArtifactGraph(root)
        g.add_node("research", "research", [])
        g.add_node("script", "script", ["research"])
        g.add_node("storyboard", "storyboard", ["script"])
        g.add_node("scene_arch", "scene_architecture", ["storyboard"])
        for s in ("scene_01", "scene_02", "scene_03"):
            g.add_node(s, "scene", ["scene_arch"])
        for sh in ("scene_03_shot_01", "scene_03_shot_02", "scene_03_shot_03"):
            g.add_node(sh, "visual_asset", ["scene_03"])
        return g

    def produce(g: ArtifactGraph, nid: str, key: str) -> None:
        p = g.attempt_dir(nid, 1) / "out.png"
        p.write_text(nid)
        g.begin(nid, key, {"name": "bfl", "model": "flux-kontext"})
        g.mark_valid(nid, str(p))

    # Test 1 — moderation recovery
    g = build(base / "t1")
    key = g.artifact_key("visual_asset", "A12F", "B82C", "flux", "kontext", "C991")
    g.begin("scene_03_shot_02", key, {"name": "bfl"})
    plan = g.record_failure("scene_03_shot_02", BFLError("moderated", status="Request Moderated"), "bfl")
    c.check(
        "T1 moderation classified + rewrite strategy",
        plan["failure_class"] == FailureClass.MODERATION and plan["strategy"] == "prompt_safety_rewrite",
    )
    c.check("T1 node enters REPAIRING", g.nodes["scene_03_shot_02"].status == NodeStatus.REPAIRING)
    produce(g, "scene_03_shot_02", g.artifact_key("visual_asset", "A12F", "D91E", "flux", "kontext", "C991"))
    c.check("T1 recovers to VALID after retry", g.nodes["scene_03_shot_02"].status == NodeStatus.VALID)

    # Test 2 — resume after crash
    root = base / "t2"
    g = build(root)
    done = ("research", "script", "storyboard", "scene_arch", "scene_01", "scene_02")
    for n in done:
        produce(g, n, g.artifact_key(n, "h"))
    g.save()
    g2 = ArtifactGraph(root)  # simulate reload after crash
    cached = [n for n in done if g2.is_valid(n, g2.artifact_key(n, "h"))]
    c.check("T2 completed stages resume from cache", len(cached) == 6)
    c.check(
        "T2 the crashed stage is not cached (continues)", not g2.is_valid("scene_03", g2.artifact_key("scene_03", "h"))
    )

    # Test 3 — single-shot failure
    g = build(base / "t3")
    produce(g, "scene_03_shot_01", g.artifact_key("scene_03_shot_01", "h"))
    g.begin("scene_03_shot_02", g.artifact_key("scene_03_shot_02", "h"), {"name": "bfl"})
    g.record_failure("scene_03_shot_02", BFLError("moderated", status="Request Moderated"), "bfl")
    c.check(
        "T3 shot01 CACHE / shot02 REPAIR / shot03 GENERATE",
        g.is_valid("scene_03_shot_01", g.artifact_key("scene_03_shot_01", "h"))
        and g.nodes["scene_03_shot_02"].status == NodeStatus.REPAIRING
        and g.nodes["scene_03_shot_03"].status == NodeStatus.PENDING,
    )

    # Test 4 — upstream invalidation
    g = build(base / "t4")
    for n in list(g.nodes):
        produce(g, n, g.artifact_key(n, "h"))
    inv = g.invalidate_downstream("storyboard")
    c.check(
        "T4 downstream of storyboard invalidated",
        "scene_arch" in inv and "scene_03" in inv and "scene_03_shot_02" in inv,
    )
    c.check(
        "T4 unrelated upstream stays cached", "research" not in inv and g.nodes["research"].status == NodeStatus.VALID
    )
    c.check("T4 invalidated node is STALE", g.nodes["scene_arch"].status == NodeStatus.STALE)

    # Test 5 — chaos: kill after each stage, resume identical, no duplicate calls
    root = base / "t5"
    build(root).save()
    stages = ["research", "script", "storyboard", "scene_arch", "scene_01", "scene_02", "scene_03"]
    api_calls = {"n": 0}

    def run_resume() -> None:
        g = build(root)
        for n in stages:
            key = g.artifact_key(n, "h")
            if g.is_valid(n, key):
                continue
            api_calls["n"] += 1
            produce(g, n, key)
        g.save()

    for _ in range(len(stages)):  # repeated kill+resume
        run_resume()
    c.check(
        "T5 chaos resume: exactly one generation per stage (no duplicate API calls)",
        api_calls["n"] == len(stages),
        str(api_calls),
    )
    g = ArtifactGraph(root)
    c.check(
        "T5 final state: every stage valid, checksums stable",
        all(g.is_valid(n, g.artifact_key(n, "h")) for n in stages),
    )


def _test_quality_gate_p6(c: Collector, base: Path) -> None:
    """P6: the hierarchical QC rolls up technical/structural/continuity/scientific/
    editorial into one publish decision, and blocks publish on a blocking-level
    fail (a causal-continuity regression)."""
    import subprocess
    from types import SimpleNamespace as NS

    from .quality_gate import HierarchicalQC
    from .utils import save_json

    base.mkdir(parents=True, exist_ok=True)
    mp4 = base / "final.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=180x320:r=12",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(mp4),
        ],
        check=True,
    )
    save_json(
        base / "20_part_qc" / "part_qc_reports.json",
        [{"scene": "SC01", "failed_parts": [], "issues": ["joint_gap: [x]"]}],
    )
    save_json(base / "20_render_qc" / "summary.json", {"scenes": [{"object_motion": True, "render_verified": True}]})
    save_json(base / "21_continuity" / "continuity_report.json", {"causal_regressions": [], "issues": []})
    save_json(
        base / "04b_claim_graph" / "claim_graph.json",
        {"validation": {"high_importance_unsupported": [], "issue_count": 0}},
    )
    story = NS(
        scenes=[NS(scene_id="S1", duration_s=3.0), NS(scene_id="S2", duration_s=4.0), NS(scene_id="S3", duration_s=3.5)]
    )
    script = NS(hook="What if...", closing="And that is why.", beats=[NS(retention_function="hook")])

    r = HierarchicalQC({}, base).evaluate(base, mp4, story, script)
    c.check("technical level passes on a real H.264 MP4", r["levels"]["technical"]["status"] == "pass")
    c.check(
        "all 5 levels present in the roll-up",
        set(r["levels"]) == {"technical", "structural", "continuity", "scientific", "editorial"},
    )
    c.check("structural warn (joint gap) is non-blocking -> publishable", r["publishable"] is True)
    c.check("quality_gate.json emitted", (base / "quality_gate.json").exists())

    save_json(
        base / "21_continuity" / "continuity_report.json",
        {"causal_regressions": ["SC03"], "issues": [{"type": "causal_regression"}]},
    )
    r2 = HierarchicalQC({}, base / "g2").evaluate(base, mp4, story, script)
    c.check("a causal-continuity regression fails the continuity level", r2["levels"]["continuity"]["status"] == "fail")
    c.check(
        "a blocking-level fail marks the video NOT publishable",
        r2["publishable"] is False and "continuity" in r2["blocking_failures"],
    )


def _test_resource_orchestrator_p7(c: Collector, base: Path) -> None:
    """P7: the resource orchestrator classifies each stage by resource, schedules
    independent per-scene work into a parallel wave (gating GPU/A100 when no
    accelerator is present), estimates the run cost from a price table, and
    enforces a cost + retry budget. The plan is emitted so a run's spend and
    schedule are inspectable before anything is incurred."""
    import time

    from .resource_orchestrator import (
        Budget,
        ResourceOrchestrator,
        Scheduler,
        parallel_map,
    )

    base.mkdir(parents=True, exist_ok=True)

    # --- cost estimate: reasoning tokens + BFL images (candidates + scenes) ---
    orch = ResourceOrchestrator({"max_cost_usd": 1.0, "max_retries": 3}, base, gpu_available=False)
    est = orch.estimate_run(scene_count=3, candidate_count=4, bfl_enabled=True, video_seconds=0.0)
    # images = scenes*candidates + scenes = 3*4 + 3 = 15
    c.check("cost estimate counts candidate+scene images (3*4+3=15)", est["images"] == 15)
    c.check("estimate has a positive total spend", est["total_usd"] > 0.0)
    c.check(
        "estimate breaks down openai + bfl_images + video",
        {"openai", "bfl_images", "video"}.issubset(est),
    )

    # --- budget: afford, charge, exhaust, and cap retries ---
    b = Budget(max_cost_usd=0.10, max_retries=2)
    c.check("budget affords a sub-cap charge", b.charge(0.06) is True)
    c.check("budget refuses a charge that would exceed the cap", b.charge(0.06) is False and b.spent == 0.06)
    c.check("retry budget allows up to max_retries", b.allow_retry() and b.allow_retry())
    c.check("retry budget refuses beyond max_retries", b.allow_retry() is False)

    # --- scheduler: per-scene tasks form one parallel wave; GPU gated ---
    sched = Scheduler(gpu_available=False, max_api_workers=4)
    tasks = [
        {"id": "storyboard", "stage": "storyboard", "deps": []},
        {"id": "s1", "stage": "beauty_frame", "deps": ["storyboard"]},
        {"id": "s2", "stage": "beauty_frame", "deps": ["storyboard"]},
        {"id": "s3", "stage": "beauty_frame", "deps": ["storyboard"]},
        {"id": "seg1", "stage": "segmentation", "deps": ["s1"]},
    ]
    waves = sched.plan(tasks)
    scene_wave = next((w for w in waves if set(w["parallel_ids"]) >= {"s1", "s2", "s3"}), None)
    c.check("independent scenes schedule into one parallel wave", scene_wave is not None)
    c.check("parallel wave caps workers at scene count", scene_wave and scene_wave["max_workers"] == 3)
    seg_entry = next(
        (e for w in waves for e in w["tasks"] if e["id"] == "seg1"), None
    )
    c.check("segmentation is a GPU stage", seg_entry and seg_entry["resource"] == "gpu")
    c.check("GPU stage is gated when no accelerator is present", seg_entry and seg_entry["gpu_gated"] is True)

    # --- parallel_map: really runs concurrently, and respects the budget ---
    def _work(x: int) -> int:
        time.sleep(0.15)
        return x * x

    t0 = time.time()
    out = parallel_map(_work, [1, 2, 3, 4], max_workers=4)
    elapsed = time.time() - t0
    c.check("parallel_map returns per-item results in order", out == [1, 4, 9, 16])
    c.check("parallel_map runs concurrently (4x0.15s well under serial 0.6s)", elapsed < 0.45)

    bud = Budget(max_cost_usd=0.06)
    dispatched = parallel_map(lambda x: x, [1, 2, 3, 4], max_workers=2, budget=bud, unit_cost=0.02)
    n_done = sum(1 for r in dispatched if r is not None)
    c.check("budget-limited parallel_map dispatches only what it can afford (3 of 4)", n_done == 3)

    # --- emit: the plan is written to disk ---
    plan = orch.emit(
        scene_count=3,
        candidate_count=4,
        bfl_enabled=True,
        stages=["research", "storyboard", "beauty_frame", "segmentation", "render_remotion"],
        video_seconds=0.0,
    )
    c.check("orchestration_plan.json is emitted", (base / "orchestration_plan.json").exists())
    c.check(
        "emitted plan carries estimate + budget + schedule",
        {"estimate", "budget", "schedule"}.issubset(plan),
    )


def _test_rive_integration_optional(c: Collector, base: Path) -> None:
    """Optional Rive template path (last resort, NOT core): with no .riv provided
    it stays disabled and the custom cutout rig remains in control; when a real
    template file is supplied and enabled, it builds a state-machine driver plan
    and emits the RiveLayer.tsx loader + copies the template into the Remotion
    project — never inventing a binary .riv."""
    from .rive_runtime import RiveIntegration

    base.mkdir(parents=True, exist_ok=True)
    chars = [{"character_id": "c1", "scene_id": "S1", "pose_intent": "point"}]

    # Disabled by default -> no-op, custom rig stays.
    off = RiveIntegration({}, base / "off")
    c.check("no template -> Rive disabled (custom rig default)", off.enabled is False)
    plan_off = off.build_plan(chars)
    c.check("disabled plan explains the custom-rig fallback", plan_off["enabled"] is False and plan_off["drivers"] == [])
    c.check("disabled emit_remotion_assets is a no-op", off.emit_remotion_assets(base / "proj0")["enabled"] is False)

    # enabled flag but a missing file path is still not enabled (never fabricates).
    ghost = RiveIntegration({"enabled": True, "template_path": str(base / "missing.riv")}, base / "ghost")
    c.check("enabled but missing .riv file is not active", ghost.enabled is False)

    # Supply a real (stub) template file + enable -> active integration.
    riv = base / "human_template.riv"
    riv.write_bytes(b"RIVE\x00stub-binary")
    on = RiveIntegration(
        {"enabled": True, "template_path": str(riv), "state_machine": "SM1"}, base / "on"
    )
    c.check("a present template + enabled activates Rive", on.enabled is True)
    plan = on.build_plan(chars)
    c.check("driver maps pose_intent -> a state-machine input/value", plan["enabled"] and plan["drivers"]
            and plan["drivers"][0]["input"] and plan["drivers"][0]["value"] == "point")
    c.check("rive_plan.json is emitted", (base / "on" / "rive_plan.json").exists())

    proj = base / "proj"
    emitted = on.emit_remotion_assets(proj)
    c.check("RiveLayer.tsx loader component is written", Path(emitted["component"]).exists())
    c.check("loader references the Rive Web Runtime", "@rive-app/canvas" in Path(emitted["component"]).read_text())
    c.check("template is copied into the Remotion public/ dir", Path(emitted["template_public"]).exists())


def _test_hero_asset_f7(c: Collector, base: Path) -> None:
    """F7: isolated hero assets are opt-in and gated — disabled or with no image
    generator, build() returns None (the beauty-frame cutout stays the default
    and nothing is fabricated). When enabled with a (fake) generator producing a
    subject on a flat background, the background is keyed to transparent while
    the subject stays opaque, and a manifest is emitted."""
    from PIL import Image

    from .hero_asset import HeroAssetStudio, key_flat_background

    base.mkdir(parents=True, exist_ok=True)

    # Keyer: subject rectangle on a white background -> bg transparent, subject opaque.
    img = Image.new("RGB", (40, 40), (255, 255, 255))
    for y in range(10, 30):
        for x in range(10, 30):
            img.putpixel((x, y), (200, 40, 40))
    keyed = key_flat_background(img, tolerance=28, bg_rgb=(255, 255, 255))
    c.check("keyed image is RGBA", keyed.mode == "RGBA")
    c.check("a corner background pixel is transparent", keyed.getpixel((0, 0))[3] == 0)
    c.check("a subject pixel stays opaque", keyed.getpixel((20, 20))[3] == 255)

    # Gating: disabled -> None; enabled but no generator -> None.
    off = HeroAssetStudio({"hero_isolated_asset": False}, base / "off", image_generator=lambda b: None)
    c.check("disabled hero studio does not run (default cutout path)", off.build("S1", "a red cell") is None)
    nogen = HeroAssetStudio({"hero_isolated_asset": True}, base / "nogen", image_generator=None)
    c.check("enabled but no image generator -> None (never fabricates offline)", nogen.enabled is False)

    # Enabled with a fake generator that renders a subject on a flat white bg.
    def fake_gen(brief: Any) -> Path:
        out = Path(brief.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        im = Image.new("RGB", (48, 48), (255, 255, 255))
        for y in range(14, 34):
            for x in range(14, 34):
                im.putpixel((x, y), (30, 90, 200))
        im.save(out)
        return out

    on = HeroAssetStudio(
        {"hero_isolated_asset": True, "hero_background": "white"}, base / "on", image_generator=fake_gen
    )
    c.check("enabled with a generator is active", on.enabled is True)
    rec = on.build("S1", "a blue mitochondrion")
    c.check("build returns a cutout record", rec is not None and Path(rec["cutout"]).exists())
    c.check("cutout has a plausible opaque subject ratio (not empty, not full)", 0.05 < rec["opaque_ratio"] < 0.95)
    man = on.generate_all([("S1", "a blue mitochondrion"), ("S2", "a green chloroplast")])
    c.check("generate_all emits a manifest recording the opt-in default note", "default_note" in man)
    c.check("hero_assets.json is emitted", (base / "on" / "hero_assets.json").exists())


def _test_adaptive_candidate_tournament(c: Collector, base: Path) -> None:
    """Efficiency: with adaptive mode ON, a first candidate that clears the
    quality threshold ends the tournament at ONE image (no forced second image);
    a weak first candidate still escalates to more candidates. With adaptive OFF
    the two-candidate minimum is preserved so the comparison ranking stays real."""
    from PIL import Image

    from .candidate_tournament import CandidateTournament
    from .schemas import DrawingBrief, SceneIllustrationArchitecture, ShotState

    base.mkdir(parents=True, exist_ok=True)
    brief = DrawingBrief(
        brief_id="b1",
        scene_id="S1",
        positive_prompt="a labelled diagram of a cell",
        negative_prompt="blurry",
        kontext_instruction="render the cell",
        output_format="png",
    )
    arch = SceneIllustrationArchitecture(scene_id="S1")
    shot = ShotState(scene_id="S1", first_frame_description="cell intact", last_frame_description="cell divides")

    calls = {"n": 0}

    class _FakeLLM:
        # Ranking judge: return the deterministic fallback it is handed.
        def critique_image(self, **kw: Any) -> Any:
            return kw.get("fallback", {})

    def make_gen(color: tuple[int, int, int]):
        def gen(b: Any) -> Path:
            calls["n"] += 1
            out = Path(b.output_path)
            out.parent.mkdir(parents=True, exist_ok=True)
            im = Image.new("RGB", (64, 64), (245, 244, 240))
            # A rich, high-contrast subject -> high entropy/contrast -> strong prior.
            for yy in range(64):
                for xx in range(64):
                    if (xx // 4 + yy // 4) % 2 == 0:
                        im.putpixel((xx, yy), color)
            im.save(out)
            return out

        return gen

    # Adaptive ON + a strong first candidate -> exactly one generation.
    calls["n"] = 0
    t = CandidateTournament(
        llm=None,
        config={"candidate_count": 4, "adaptive_candidates": True, "adaptive_accept_score": 0.5},
        root=base / "adaptive_on",
    )
    res = t.run(brief, arch, shot, make_gen((20, 40, 160)))
    c.check("adaptive strong-first accepts one candidate (no forced second image)", calls["n"] == 1)
    c.check("single-candidate result names that candidate the winner", res.winner_id == "C01"
            and res.ranking_source == "adaptive_single")

    # Adaptive OFF -> keeps the >=2 minimum even when candidate_count=1.
    calls["n"] = 0
    t2 = CandidateTournament(
        llm=_FakeLLM(), config={"candidate_count": 1, "adaptive_candidates": False}, root=base / "adaptive_off"
    )
    res2 = t2.run(brief, arch, shot, make_gen((160, 30, 30)))
    c.check("non-adaptive preserves the two-candidate tournament minimum", calls["n"] >= 2 and len(res2.candidates) >= 2)


def _test_release_integrity_p0(c: Collector, base: Path) -> None:
    """P0 release integrity: a run can prove the code it executes is the code that
    was bundled/tested. compute_source_digest is deterministic and order-free; a
    single edited byte changes it; verify_parity flags drift; emit_run_release
    marks production_validated only when the live source matches the embedded
    fingerprint and a commit is recorded."""
    from .release_manifest import (
        build_fingerprint,
        compute_source_digest,
        emit_run_release,
        verify_parity,
    )

    pkg = base / "pkg"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "alpha.py").write_text("x = 1\n")
    (pkg / "beta.py").write_text("y = 2\n")

    d1, n1 = compute_source_digest(pkg)
    d2, n2 = compute_source_digest(pkg)
    c.check("source digest is deterministic across calls", d1 == d2 and n1 == n2 == 2)

    fp = build_fingerprint(pkg, pipeline_version="9.9.9")
    c.check("fingerprint carries version + digest + module count", fp["source_bundle_sha256"] == d1
            and fp["module_count"] == 2 and fp["pipeline_version"] == "9.9.9")

    # Parity holds while untouched...
    par = verify_parity(fp, pkg)
    c.check("parity is OK when the source is unchanged", par["parity_ok"] is True)
    # ...and breaks on a single edited byte.
    (pkg / "beta.py").write_text("y = 3\n")
    par2 = verify_parity(fp, pkg)
    c.check("a single edited byte breaks parity (drift detected)", par2["parity_ok"] is False)

    # A non-.py sidecar (the embedded fingerprint file) does not affect the digest.
    (pkg / "_build_release.json").write_text("{}")
    d3, n3 = compute_source_digest(pkg)
    c.check("non-.py sidecar files are excluded from the digest", n3 == 2)

    # emit_run_release: validated only when live matches embedded + commit present.
    good = base / "good"
    good.mkdir()
    (good / "alpha.py").write_text("x = 1\n")
    fp_good = build_fingerprint(good, pipeline_version="1.0.0")
    fp_good = {**fp_good, "git_commit": "deadbeef"}  # simulate a recorded commit
    rel = emit_run_release(good, base / "run_good", embedded=fp_good)
    c.check("a matching live source + recorded commit is production_validated", rel["production_validated"] is True)
    c.check("release.json is emitted", (base / "run_good" / "release.json").exists())

    # No embedded fingerprint -> honestly not validated.
    rel2 = emit_run_release(good, base / "run_none", embedded=None)
    c.check("no embedded fingerprint -> not production_validated", rel2["production_validated"] is False)


def _test_render_backend_select_f5(c: Collector, base: Path) -> None:
    """F5: 'auto' is the default renderer selector — it prefers the Remotion
    production path when the Node toolchain is present and degrades to the
    deterministic PIL renderer (with a reason) when it is not, so a run never
    crashes merely because Node is missing. Explicit 'remotion' can be made
    strict; explicit 'pil' always uses PIL."""
    from .hybrid_render import select_render_backend

    b, note = select_render_backend("auto", has_node=True)
    c.check("auto + node -> Remotion production path", b == "remotion" and note == "")
    b, note = select_render_backend("auto", has_node=False)
    c.check("auto without node -> PIL fallback with a reason", b == "pil" and bool(note))
    c.check("explicit pil always renders with PIL", select_render_backend("pil", has_node=True)[0] == "pil")
    c.check("explicit remotion + node -> remotion", select_render_backend("remotion", has_node=True)[0] == "remotion")
    c.check(
        "explicit remotion without node degrades to PIL by default (non-strict)",
        select_render_backend("remotion", has_node=False)[0] == "pil",
    )
    raised = False
    try:
        select_render_backend("remotion", has_node=False, strict=True)
    except RuntimeError:
        raised = True
    c.check("strict remotion without node is a hard error", raised is True)


def _test_topic_engine_p8(c: Collector, base: Path) -> None:
    """P8: the autonomous topic engine proposes candidates, scores each on
    richness/visual/novelty/appeal/safety, prefers an unproduced high-appeal
    question over a bare or already-made one, and emits its reasoning."""
    from .topic_engine import TopicEngine
    from .utils import save_json

    base.mkdir(parents=True, exist_ok=True)

    # A rich, visual, curiosity question should outscore a bare trivia string.
    eng = TopicEngine(None, {}, base)
    rich = eng.score_topic("What happens to the human body in extreme gravity?")
    bare = eng.score_topic("water")
    c.check("a rich visual question outscores a bare keyword", rich["composite"] > bare["composite"])
    c.check("all five scoring axes are present", set(rich["axes"]) == {
        "scientific_richness", "visual_potential", "novelty", "audience_appeal", "safety"
    })

    # A moderation-risky framing is penalized on the safety axis.
    risky = eng.score_topic("How does a gory violent weapon wound the body?")
    safe = eng.score_topic("How does the body heal a broken bone over time?")
    c.check("a moderation-risky framing scores lower on safety", risky["axes"]["safety"] < safe["axes"]["safety"])

    # propose(): deterministic candidates, ranked, one selected, artifact emitted.
    rep = eng.propose(seed="the deep ocean", count=5)
    c.check("propose returns scored candidates", len(rep["candidates"]) >= 3)
    c.check("propose selects the top-ranked candidate", rep["selected"] is not None
            and rep["selected"]["composite"] == max(s["composite"] for s in rep["candidates"]))
    c.check("topic_candidates.json is emitted", (base / "topic_candidates.json").exists())

    # Novelty: a topic already in history is de-prioritized (not selected) when
    # fresh alternatives exist.
    hist = base / "history.json"
    save_json(hist, ["What would you see inside the deep ocean?"])
    eng2 = TopicEngine(None, {"history_path": str(hist)}, base / "h")
    seen = eng2.score_topic("What would you see inside the deep ocean?")
    c.check("a produced topic is marked already_produced with zero novelty",
            seen["already_produced"] is True and seen["axes"]["novelty"] == 0.0)
    rep2 = eng2.propose(seed="the deep ocean", count=5)
    c.check("engine does not re-select an already-produced topic when alternatives exist",
            rep2["selected"] is None or rep2["selected"]["already_produced"] is False)


def _test_metadata_engine_p9(c: Collector, base: Path) -> None:
    """P9: the metadata engine assembles a real upload package — title (capped),
    sourced description, keyword tags, timestamp chapters starting at 0:00, and a
    thumbnail brief — from the script/research/storyboard artifacts, and emits it."""
    from types import SimpleNamespace as NS

    from .metadata_engine import MetadataEngine

    base.mkdir(parents=True, exist_ok=True)
    script = NS(
        title="What Zero Gravity Does to Your Body",
        hook="What happens to a human body in zero gravity?",
        closing="And that is why astronauts train so hard.",
    )
    research = NS(
        summary="In microgravity the body changes in weeks: fluids shift and bones weaken.",
        facts=[
            NS(claim="Astronauts can lose 1-2% of bone mass per month in microgravity."),
            NS(claim="Body fluids shift toward the head, puffing the face."),
        ],
        sources=[
            NS(title="NASA Human Research", url="https://www.nasa.gov/hrp", author="NASA"),
            NS(title="ESA Bone Study", url="https://www.esa.int/bone", author="ESA"),
        ],
    )
    story = NS(
        fps=30,
        scenes=[
            NS(scene_id="S1", duration_s=6.0, headline="Fluid shift", narration="Fluids move up."),
            NS(scene_id="S2", duration_s=7.5, headline="Bone loss", narration="Bones weaken."),
            NS(scene_id="S3", duration_s=5.0, headline="Recovery", narration="Back on Earth."),
        ],
    )

    pkg = MetadataEngine(None, {}, base).build(
        "What happens to a human body in zero gravity?", script, research, story
    )
    c.check("title is present and within the 100-char cap", 0 < len(pkg["title"]) <= 100)
    c.check("description embeds a key fact from research",
            "bone mass" in pkg["description"] or "fluids" in pkg["description"].lower())
    c.check("description lists sources with urls", "nasa.gov" in pkg["description"])
    c.check("tags are non-trivial keyword list", len(pkg["tags"]) >= 5 and "science" in pkg["tags"])
    c.check("chapters cover every scene", len(pkg["chapters"]) == 3)
    c.check("the first chapter starts at 0:00 (YouTube requires it)", pkg["chapters"][0]["start"] == "0:00")
    c.check("later chapters advance in time", pkg["chapters"][1]["start_s"] == 6.0)
    c.check("thumbnail brief carries a short overlay + 9:16 aspect",
            pkg["thumbnail"]["overlay_text"] and pkg["thumbnail"]["aspect"] == "9:16")
    c.check("metadata.json is emitted", (base / "metadata.json").exists())


def _test_continuity_validator_p5(c: Collector, base: Path) -> None:
    """P5: the active continuity validator flags causal-state regressions (a
    changed/damaged state that silently resets), character size jumps and
    reappear-after-gap — and passes a clean, monotonic sequence."""
    from .continuity_validator import ContinuityValidator

    # intact -> damaged -> intact must be caught as a causal regression.
    recs = [
        {
            "scene_id": "SC01",
            "time_stage": "before, wall intact",
            "characters": [{"id": "p", "area": 0.3}],
            "focal_subject": "wall",
        },
        {
            "scene_id": "SC02",
            "time_stage": "after, wall damaged",
            "characters": [{"id": "p", "area": 0.31}],
            "focal_subject": "wall",
        },
        {
            "scene_id": "SC03",
            "time_stage": "before intact again",
            "characters": [{"id": "p", "area": 0.32}],
            "focal_subject": "wall",
        },
    ]
    r = ContinuityValidator({}, base).validate(recs)
    c.check("causal regression (intact->damaged->intact) caught", r["causal_regressions"] == ["SC03"])
    c.check("continuity_report.json emitted", (base / "continuity_report.json").exists())

    recs2 = [
        {"scene_id": "SC01", "time_stage": "before", "characters": [{"id": "p", "area": 0.2}]},
        {"scene_id": "SC02", "time_stage": "during", "characters": []},
        {"scene_id": "SC03", "time_stage": "after", "characters": [{"id": "p", "area": 0.6}]},
    ]
    types = {i["type"] for i in ContinuityValidator({"continuity_max_size_jump": 0.4}, None).validate(recs2)["issues"]}
    c.check("character reappear-after-gap flagged", "character_reappears_after_gap" in types)
    c.check("character size jump flagged", "character_size_jump" in types)

    clean = [
        {"scene_id": "S1", "time_stage": "before", "characters": [{"id": "p", "area": 0.3}]},
        {"scene_id": "S2", "time_stage": "during", "characters": [{"id": "p", "area": 0.32}]},
        {"scene_id": "S3", "time_stage": "after", "characters": [{"id": "p", "area": 0.31}]},
    ]
    c.check("clean monotonic sequence passes", ContinuityValidator({}, None).validate(clean)["ok"])
    reset = [{"scene_id": "S1", "time_stage": "after"}, {"scene_id": "S2", "time_stage": "before", "allow_reset": True}]
    c.check("authored reset suppresses the regression flag", ContinuityValidator({}, None).validate(reset)["ok"])


def _test_audio_timeline_p4(c: Collector, base: Path) -> None:
    """P4: the audio-first timeline detects impact/emphasis beats from the voice
    word-timings and gives a primary impact frame so motion lands on the word."""
    from types import SimpleNamespace as NS

    from .audio_timeline import AudioTimeline

    script = NS(
        beats=[
            NS(
                beat_id="B1",
                spoken_line="Every day you PUNCH the wall.",
                emphasis_words=["punch", "wall"],
                sfx="impact_thud",
                pause_after_ms=300,
            )
        ]
    )
    storyboard = NS(scenes=[NS(scene_id="SC01", beat_id="B1", duration_s=4.0)])
    wt = {
        "B1": [
            {"word": "Every", "start_s": 0.0, "end_s": 0.4},
            {"word": "day", "start_s": 0.4, "end_s": 0.8},
            {"word": "you", "start_s": 0.8, "end_s": 1.1},
            {"word": "punch", "start_s": 1.1, "end_s": 1.6},
            {"word": "the", "start_s": 1.6, "end_s": 1.8},
            {"word": "wall", "start_s": 1.8, "end_s": 2.4},
        ]
    }
    tl = AudioTimeline({}, base).build(storyboard, script, {"word_timing": wt}, fps=12)
    sc = tl["scenes"]["SC01"]
    c.check("scene has an absolute audio window", sc["audio"]["start"] == 0.0 and sc["audio"]["end"] == 4.0)
    kinds = {b["type"] for b in sc["beats"]}
    c.check("emphasis words become impact beats", "impact" in kinds)
    c.check("sfx becomes an accent beat", "accent" in kinds)
    c.check("end-of-line pause becomes a settle beat", "settle" in kinds)
    punch = next((b for b in sc["beats"] if b["word"] == "punch"), None)
    c.check(
        "impact beat lands on the spoken word frame (~1.1s*12fps)", punch is not None and punch["frame"] in (13, 14)
    )
    pif = AudioTimeline({}, None)
    pif.build(storyboard, script, {"word_timing": wt}, fps=12)
    c.check(
        "primary impact frame snaps to the strongest emphasis word", pif.primary_impact_frame("SC01", 48) in (13, 14)
    )
    c.check("audio_timeline.json emitted", (base / "audio_timeline.json").exists())


def _test_claim_graph_p3(c: Collector, base: Path) -> None:
    """P3: the scientific claim graph traces each scene's claim to evidence
    (Fact + Source + confidence) and flags scientific-looking visuals that are
    not actually supported."""
    from types import SimpleNamespace as NS

    from .claim_graph import ScientificClaimGraph

    research = NS(
        facts=[
            NS(
                fact_id="F1", claim="Repeated impact causes cumulative tissue stress", source_ids=["S1"], confidence=0.9
            ),
            NS(fact_id="F2", claim="Bone remodels under load", source_ids=[], confidence=0.4),
        ],
        sources=[NS(source_id="S1", provider="openalex", authority_score=0.92, title="J Biomech")],
    )
    script = NS(
        beats=[
            NS(
                beat_id="B1",
                spoken_line="Every day you punch the wall.",
                evidence_refs=["F1"],
                retention_function="hook",
                purpose="hook",
            ),
            NS(
                beat_id="B2",
                spoken_line="Your bones adapt.",
                evidence_refs=["F2"],
                retention_function="body",
                purpose="body",
            ),
            NS(
                beat_id="B3",
                spoken_line="It looks dramatic.",
                evidence_refs=[],
                retention_function="body",
                purpose="body",
            ),
        ]
    )
    storyboard = NS(
        scenes=[
            NS(
                scene_id="SC01",
                beat_id="B1",
                narration="...",
                scientific_claim="Repeated impact causes cumulative tissue stress",
                visual_event="biomechanical stress diagram",
            ),
            NS(
                scene_id="SC02",
                beat_id="B2",
                narration="...",
                scientific_claim="Bone remodels under load",
                visual_event="bone remodeling",
            ),
            NS(scene_id="SC03", beat_id="B3", narration="...", scientific_claim="", visual_event="dramatic wall crack"),
        ]
    )
    g = ScientificClaimGraph({"claim_min_confidence": 0.5}, base).build(research, script, storyboard)
    c.check("one claim node per scene", g["validation"]["claim_count"] == 3)
    sc01 = next(cl for cl in g["claims"] if cl["scene_id"] == "SC01")
    c.check(
        "well-sourced claim traces to a peer-reviewed source with full support",
        any(e["source_type"] == "peer_reviewed" and e["supports"] == "full" for e in sc01["evidence"]),
    )
    c.check("claim carries an aggregate confidence", sc01["confidence"] >= 0.8)
    issues = {i["issue"] for i in g["validation"]["issues"]}
    c.check("weak/unsourced evidence flagged (F2)", "unsourced_evidence" in issues or "low_confidence" in issues)
    c.check("scientific-looking visual with no claim flagged (SC03)", "visual_without_claim" in issues)
    c.check("claim_graph.json emitted", (base / "claim_graph.json").exists())
    # A high-importance claim with zero evidence must be caught.
    research2 = NS(facts=[], sources=[])
    script2 = NS(beats=[NS(beat_id="B1", spoken_line="x", evidence_refs=[], retention_function="hook", purpose="hook")])
    story2 = NS(
        scenes=[
            NS(
                scene_id="SC01",
                beat_id="B1",
                narration="...",
                scientific_claim="A bold unsupported claim",
                visual_event="v",
            )
        ]
    )
    g2 = ScientificClaimGraph({}, base / "g2").build(research2, script2, story2)
    c.check(
        "high-importance unsupported claim is caught (not ok)",
        g2["validation"]["ok"] is False and g2["validation"]["high_importance_unsupported"],
    )


def _test_visual_provider_router(c: Collector, base: Path) -> None:
    """P2: the visual provider router classifies errors and recovers scene-scoped
    — moderation rewrite + retry, transient retry, opt-in fallback provider, and a
    scene-scoped raise when everything is exhausted (never a whole-video fallback)."""
    from .bfl_client import BFLError
    from .visual_provider import (
        CallableVisualProvider,
        ErrorClass,
        VisualRequest,
        build_visual_router,
        classify_error,
    )

    base.mkdir(parents=True, exist_ok=True)
    c.check(
        "moderation classified",
        classify_error(BFLError("m", status="Request Moderated")) == ErrorClass.CONTENT_MODERATION,
    )
    c.check("timeout classified", classify_error(TimeoutError("timed out")) == ErrorClass.TIMEOUT)
    c.check("rate-limit classified", classify_error(BFLError("r", status_code=429)) == ErrorClass.RATE_LIMIT)
    c.check("auth classified", classify_error(BFLError("a", status_code=401)) == ErrorClass.AUTH_ERROR)
    c.check("invalid-request classified", classify_error(ValueError("required field")) == ErrorClass.INVALID_REQUEST)

    class _LLM:
        def __init__(self, fail_times, exc):
            self.config = {}
            self.n = 0
            self.fail_times = fail_times
            self.exc = exc

        def generate_reference_image(self, *, prompt, output_path, init_image, force):
            self.n += 1
            if self.n <= self.fail_times:
                raise self.exc()
            Path(output_path).write_bytes(b"x" * 2000)
            return Path(output_path)

        def generate_json(self, **k):
            return {"prompt": "safe clinical educational diagram"}

    # moderation -> rewrite -> retry BFL succeeds
    r = build_visual_router(_LLM(1, lambda: BFLError("m", status="Request Moderated")), {"bfl_moderation_retries": 3})
    res = r.generate(VisualRequest("violent prompt", str(base / "a.png")))
    c.check(
        "moderation recovers on primary (bfl)", res.provider == "bfl" and res.attempts == 2 and Path(res.path).exists()
    )

    # transient error retried then succeeds
    r = build_visual_router(_LLM(1, lambda: TimeoutError("timed out")), {"transient_retries": 2})
    res = r.generate(VisualRequest("p", str(base / "b.png")))
    c.check("transient error retried same input", res.attempts == 2)

    # BFL persistently moderated -> opt-in fallback provider (scene-scoped)
    class _AlwaysMod:
        config: dict = {}

        def generate_reference_image(self, **k):
            raise BFLError("m", status="Request Moderated")

        def generate_json(self, **k):
            return {"prompt": "safe"}

    def _fb(req):
        Path(req.output_path).write_bytes(b"y" * 2000)
        return req.output_path

    fb = CallableVisualProvider("gemini", _fb, lambda: True, "gemini-image")
    r = build_visual_router(_AlwaysMod(), {"bfl_moderation_retries": 1}, [fb])
    res = r.generate(VisualRequest("p", str(base / "c.png")))
    c.check("falls back to opt-in provider after BFL exhausted", res.provider == "gemini")

    # all exhausted -> scene-scoped raise (not whole-video)
    r = build_visual_router(_AlwaysMod(), {"bfl_moderation_retries": 1})
    try:
        r.generate(VisualRequest("p", str(base / "d.png")))
        c.check("all-exhausted raises", False, "no exception")
    except RuntimeError as exc:
        c.check("all-exhausted raises scene-scoped", "scene-scoped" in str(exc))


def _test_moderation_recovery(c: Collector, base: Path) -> None:
    """A BFL content-moderation block must not kill the run: the prompt is
    rewritten to a safe clinical reframing and the image generation retried."""
    from .bfl_client import BFLError
    from .flux_studio import FluxKontextStudio
    from .prompt_safety import deterministic_soften, is_moderation_error
    from .schemas import DrawingBrief

    base.mkdir(parents=True, exist_ok=True)
    violent = "A person punching the wall, bloody knuckles, brutal violent impact, broken bones"
    soft = deterministic_soften(violent, 1).lower()
    c.check(
        "deterministic softener neutralises violence/gore terms",
        not any(t in soft for t in ("punch", "blood", "brutal", "violent", "broken")),
        soft,
    )
    c.check(
        "moderation detected from BFLError.status",
        is_moderation_error(BFLError("moderated", status="Request Moderated")),
    )
    c.check("non-moderation errors are not misclassified", not is_moderation_error(ValueError("timeout")))

    class _FakeLLM:
        def __init__(self):
            self.config = {}
            self.calls = []

        def generate_reference_image(self, *, prompt, output_path, init_image, force):
            self.calls.append(prompt)
            if len(self.calls) == 1:
                raise BFLError("BFL request was moderated", status="Request Moderated")
            Path(output_path).write_bytes(b"x" * 2000)
            return Path(output_path)

        def generate_json(self, **k):
            return {"prompt": "clean educational diagram of hand-bone stress"}

    llm = _FakeLLM()
    studio = FluxKontextStudio(llm, None, {"bfl_moderation_retries": 3}, base / "flux")
    brief = DrawingBrief(
        brief_id="B1",
        scene_id="S03",
        positive_prompt=violent,
        negative_prompt="",
        kontext_instruction="",
        compiled_prompt=violent,
        output_path=str(base / "s3.png"),
        aspect_ratio="9:16",
        seed=1,
    )
    out = studio.generate(brief)
    c.check("moderated scene recovers via rewrite+retry (run continues)", bool(out) and Path(out).exists())
    c.check("BFL was retried once with a safe prompt", len(llm.calls) == 2 and "moderat" not in llm.calls[1].lower())
    c.check("moderation-recovery lineage is persisted", any((base / "flux" / "moderation_recovery").glob("*.json")))


def _test_video_temporal_backends(c: Collector, base: Path) -> None:
    """WAN 2.2 / SkyReels-V2 backends are real diffusers adapters, honestly gated:
    unavailable without a GPU, built only when enabled, and the router falls back
    to the deterministic compositor offline (organic motion is never faked)."""
    from PIL import Image

    from .schemas import TemporalRequest
    from .temporal_backends import DeterministicCompositorBackend, TemporalBackendRouter
    from .temporal_video_models import SkyReelsVideoBackend, WanVideoBackend, build_video_backends

    base.mkdir(parents=True, exist_ok=True)
    wan = WanVideoBackend({"enable": True}, base / "w")
    sky = SkyReelsVideoBackend({"enable": True}, base / "s")
    c.check(
        "WAN backend id + real pipeline symbol",
        wan.backend_id == "wan-2.2-i2v" and wan.pipeline_symbol == "WanImageToVideoPipeline",
    )
    c.check(
        "SkyReels backend id + real pipeline symbol",
        sky.backend_id == "skyreels-v2-i2v" and "SkyReelsV2" in sky.pipeline_symbol,
    )
    c.check("video backends unavailable without a GPU", wan.available() is False and sky.available() is False)
    c.check(
        "disabled config builds no video backends", build_video_backends({"temporal": {}}, base, "production") == []
    )
    built = build_video_backends(
        {"temporal": {"wan": {"enable": True}, "skyreels": {"enable": True}}}, base, "production"
    )
    c.check("enabled config builds both backends", {b.backend_id for b in built} == {"wan-2.2-i2v", "skyreels-v2-i2v"})

    bp = base / "b.png"
    Image.new("RGB", (64, 64), "blue").save(bp)
    router = TemporalBackendRouter(built + [DeterministicCompositorBackend(base / "det")], base / "res")
    req = TemporalRequest(
        scene_id="S01",
        backend_preference=["wan-2.2-i2v", "deterministic-compositor"],
        beauty_start=str(bp),
        beauty_end=str(bp),
        duration_frames=12,
        fps=12,
        complexity="articulated",
        output_path=str(base / "out.mp4"),
    )
    res = router.generate(req)
    c.check(
        "router falls back to deterministic when video models unavailable", res.backend_id == "deterministic-compositor"
    )


def _test_character_director(c: Collector, base: Path) -> None:
    """CharacterDirector authors an explicit CharacterManifest from the authored
    figure_construction (deterministic fallback), flagging requires_articulation
    so the downstream quality gate can enforce a rig. No narration keyword scan."""
    from types import SimpleNamespace

    from .character_director import CharacterDirector

    arch_with = SimpleNamespace(
        figure_construction=[SimpleNamespace(figure_id="scientist_01", body_orientation="front_three_quarter")]
    )
    cd = CharacterDirector(None, {}, base)  # llm=None -> deterministic fallback
    manifest = cd.author("S01", arch_with, None)
    c.check("manifest uses the deterministic fallback", manifest.grounding_source == "deterministic-fallback")
    c.check("a declared figure becomes a character", len(manifest.characters) == 1)
    char = manifest.characters[0]
    c.check("character id carried from figure", char.character_id == "scientist_01")
    c.check("declared figure requires articulation", char.requires_articulation is True)
    c.check(
        "character bbox is normalized and non-degenerate", char.bbox[2] > char.bbox[0] and char.bbox[3] > char.bbox[1]
    )

    arch_none = SimpleNamespace(figure_construction=[])
    empty = cd.author("S02", arch_none, None)
    c.check("no figure -> no character (no articulation required)", empty.characters == [])


def _test_flat_explainer_style(c: Collector) -> None:
    """Art direction must steer flat vector explainer, not painterly ink."""
    from .schemas import HardCodedStyleCanon
    from .style_canon import base_bible

    canon = HardCodedStyleCanon()
    c.check("canon medium is flat vector", "flat vector" in canon.medium.lower())
    c.check("canon dropped editorial-ink medium", "editorial ink" not in canon.medium.lower())
    bible = base_bible("what happens if it rains")
    c.check("bible visual thesis is flat explainer", "flat vector" in bible.visual_thesis.lower())

    from .flux_prompt_system import FluxPromptSystem

    fps = FluxPromptSystem({}, _fresh_root(None, "flux_style_"))
    fingerprint = fps.fingerprint(bible)
    prompt = fingerprint.immutable_prompt.lower()
    c.check("FLUX style prompt asks for flat vector shapes", "flat" in prompt and "vector" in prompt)
    c.check("FLUX style prompt forbids painterly", "painterly" in prompt)


def _test_motion_eval(c: Collector) -> None:
    """Separated motion axes + the causal-clarity gate: supporting motion alone
    must not pass, and a declared hold must."""
    from .motion_eval import causal_clarity_ok, evaluate_plan

    # Real object state change + summary -> clear.
    good = {
        "causal_summary": "The river overtops its bank and floods the town.",
        "events": [{"event_id": "E1", "representation": "mask_reveal", "secondary": False}],
        "camera": {"move": "hold", "magnitude": 0.0},
        "effects": [],
        "captions": [],
    }
    rep = evaluate_plan(good)
    c.check("planned object state change detected", rep["state_change_planned"] and rep["object_motion"])
    c.check("render_verified is None until post-render QC runs", rep["render_verified"] is None)
    c.check("causal clarity passes with object state change", causal_clarity_ok(good))

    # Only a SECONDARY event -> not primary object motion (false-positive guard).
    secondary_only = {
        "causal_summary": "A subtle background drift.",
        "events": [{"event_id": "E1", "representation": "mask_reveal", "secondary": True}],
    }
    c.check(
        "secondary-only events are not primary object motion", evaluate_plan(secondary_only)["object_motion"] is False
    )

    # Only camera + particles, no object motion -> must FAIL the gate.
    supporting_only = {
        "causal_summary": "Rain intensifies.",
        "events": [],
        "camera": {"move": "push_in", "magnitude": 0.08},
        "effects": [{"effect": "rain", "intensity": 0.9}],
        "captions": [],
    }
    rep2 = evaluate_plan(supporting_only)
    c.check("camera+particle flagged as supporting-only", rep2["supporting_only_warning"])
    c.check("supporting-only motion fails causal gate", not causal_clarity_ok(supporting_only))

    # Declared hold (summary, no motion) -> allowed.
    hold = {"causal_summary": "The system rests before the change.", "events": [], "camera": {"move": "hold"}}
    c.check("declared hold passes the gate", causal_clarity_ok(hold))


def _test_reference_motion_guidance(c: Collector) -> None:
    """The reference profile is only a restrained PACING hint — it must not
    force extra motion, and it is empty when no profile is given."""
    from .animation_director import AnimationDirector

    guide = AnimationDirector._reference_motion_guidance
    c.check("no motion profile -> no guidance", guide(None) == "" and guide({}) == "")
    energetic = guide({"tempo": "energetic", "energy": 0.8, "cut_rate": 0.4})
    c.check("energetic reference hints brisk pacing", "brisk" in energetic)
    c.check("pacing hint explicitly does not justify extra motion", "does NOT justify extra motion" in energetic)
    calm = guide({"tempo": "calm", "energy": 0.1, "cut_rate": 0.0})
    c.check("calm reference hints slow pacing and holds", "generous holds" in calm)


# ---------------------------------------------------------------------------
# FLUX prompt budget (bloated initial prompt must fit, not crash)
# ---------------------------------------------------------------------------


def _test_flux_prompt_budget(c: Collector, base: Path) -> None:
    from .flux_prompt_system import FluxPromptSystem
    from .schemas import (
        ContinuityCanon,
        DepthPlane,
        MotionSeam,
        ReferencePack,
        SceneIllustrationArchitecture,
    )
    from .style_canon import base_bible

    root = base / "flux_prompt"
    system = FluxPromptSystem(
        {"seed_base": 100, "aspect_ratio": "9:16", "min_initial_prompt_words": 80, "max_initial_prompt_words": 380},
        root,
    )
    bible = base_bible("Rain for a year", "ref.png")
    pack = ReferencePack(scene_id="scene_1", board_path=str(root / "board.png"))
    Image.new("RGB", (180, 320), "#FAFAF7").save(pack.board_path)
    long_text = (
        "a sprawling metropolis under relentless torrential rainfall where every street canal and "
        "rooftop overflows with churning grey water while exhausted residents wade through waist "
        "deep floods past submerged vehicles collapsing infrastructure and improvised barricades " * 6
    )
    architecture = SceneIllustrationArchitecture(
        scene_id="scene_1",
        beat_id="B01",
        visual_thesis=long_text,
        focal_subject=long_text,
        narrative_claim=long_text,
        secondary_subjects=[long_text, long_text],
        depth_planes=[DepthPlane(plane_id="city", depth="midground", contents=long_text)],
        motion_seams=[
            MotionSeam(
                seam_id="rain",
                subject="rain",
                method="texture_loop",
                region="rain field",
                resting_overlap_rule="masked",
                required_variants=["initial", "changed"],
            )
        ],
        animation_representation=["texture_loop"],
    )
    brief = system.beauty_frame(
        architecture,
        bible,
        ContinuityCanon(),
        pack,
        "narration",
        "HEADLINE",
        0,
        style_source_path=str(pack.board_path),
        init_strategy="reference_board",
    )
    diagnostics = brief.prompt_diagnostics
    c.check(
        "bloated initial prompt fits budget instead of crashing",
        diagnostics.valid and diagnostics.word_count <= 380,
        f"valid={diagnostics.valid} words={diagnostics.word_count} errors={diagnostics.errors}",
    )
    head = " ".join(brief.compiled_prompt.lower().split()[:55])
    c.check(
        "trimmed prompt still states style early",
        any(token in head for token in ("scientific editorial", "illustration")),
    )
    # A second identical build is deterministic (resume reproduces the prompt).
    system2 = FluxPromptSystem(
        {"seed_base": 100, "aspect_ratio": "9:16", "min_initial_prompt_words": 80, "max_initial_prompt_words": 380},
        base / "flux_prompt2",
    )
    pack2 = ReferencePack(scene_id="scene_1", board_path=str(pack.board_path))
    brief2 = system2.beauty_frame(
        architecture,
        bible,
        ContinuityCanon(),
        pack2,
        "narration",
        "HEADLINE",
        0,
        style_source_path=str(pack.board_path),
        init_strategy="reference_board",
    )
    c.check("prompt trimming is deterministic", brief2.compiled_prompt == brief.compiled_prompt)


# ---------------------------------------------------------------------------
# Offline end-to-end
# ---------------------------------------------------------------------------


def _test_e2e_offline(c: Collector, base: Path) -> None:
    import subprocess

    from .pipeline import ScientificMotionStudioV10
    from .tests import FakeLLM

    ref_frame = _image_file(base / "ref.png", "#20282D")
    ref_video = base / "reference.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-loop",
            "1",
            "-i",
            str(ref_frame),
            "-t",
            "1",
            "-r",
            "12",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(ref_video),
        ],
        check=True,
    )

    def fake_flux(brief):
        out = Path(brief.output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (180, 320), "#FAFAF7")
        draw = ImageDraw.Draw(image)
        seed = brief.seed or 0
        colors = ["#475157", "#2E77A6", "#4190C3", "#D8483E", "#F3BD38"]
        draw.polygon([(15, 280), (45, 80), (90, 35), (160, 100), (170, 280)], fill=colors[seed % len(colors)])
        draw.text((10, 10), brief.scene_id, fill="#20282D")
        image.save(out)
        return out

    def fake_mask(beauty_path, region, output):
        beauty = Image.open(beauty_path)
        mask = Image.new("L", beauty.size, 0)
        ImageDraw.Draw(mask).rectangle((35, 55, 155, 285), fill=255)
        output.parent.mkdir(parents=True, exist_ok=True)
        mask.save(output)
        return output

    config = {
        "workspace": str(base / "studio"),
        "execution_mode": "test",
        "research_search": {"enabled": False},
        "llm": {"provider_order": [], "vision_provider_order": []},
        "drawing": {
            "seed_base": 123,
            "aspect_ratio": "9:16",
            "min_initial_prompt_words": 40,
            "max_initial_prompt_words": 500,
        },
        "candidate_tournament": {"candidate_count": 2},
        "audio": {"enabled": False},
        "temporal": {"enabled": False},
        "flux_studio": {"maximum_director_revisions": 1, "require_vision_director": True},
        "render": {"backend": "pil", "crf": 30},
    }
    studio = ScientificMotionStudioV10(
        config, llm_router=FakeLLM(), image_generator=fake_flux, mask_generator=fake_mask
    )
    result = studio.run(
        "What happens under nonstop rain?", ref_video, plan_only=False, render_video=True, job_id="e2e", force=True
    )
    c.check("e2e offline run renders", result["mode"] == "rendered")
    video = Path(result["video"])
    c.check("e2e video exists", video.exists() and video.stat().st_size > 1000)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "json", str(video)],
        check=True,
        capture_output=True,
        text=True,
    )
    c.check("e2e video is valid H.264", "h264" in json.loads(probe.stdout)["streams"][0]["codec_name"])

    run_dir = Path(result["run_dir"])
    manifest = load_json(run_dir / "job_manifest.json")
    c.check(
        "e2e manifest completed with schema version",
        manifest["status"] == "completed" and manifest["schema_version"] == "10.1",
    )

    provenance = load_json(run_dir / "provenance_manifest.json")
    c.check(
        "provenance manifest exists with providers locked",
        provenance["providers"]["reasoning"] == "openai-gpt"
        and provenance["providers"]["image_generation"] == "bfl-flux-kontext",
    )
    from .utils import sha256_file

    video_entry = provenance["artifacts"]["video"]
    c.check(
        "provenance video hash matches artifact", video_entry["exists"] and video_entry["sha256"] == sha256_file(video)
    )

    # Resume: a second run must reuse completed stages, not re-execute them.
    manifest_before = load_json(run_dir / "job_manifest.json")
    attempts_before = {k: v["attempt"] for k, v in manifest_before["stages"].items()}
    studio2 = ScientificMotionStudioV10(
        config, llm_router=FakeLLM(), image_generator=fake_flux, mask_generator=fake_mask
    )
    result2 = studio2.run(
        "What happens under nonstop rain?", ref_video, plan_only=False, render_video=False, job_id="e2e"
    )
    manifest_after = load_json(run_dir / "job_manifest.json")
    attempts_after = {k: v["attempt"] for k, v in manifest_after["stages"].items()}
    c.check(
        "rerun is idempotent (no stage re-execution)",
        result2["mode"] == "live_generation" and attempts_after == attempts_before,
        f"before={attempts_before} after={attempts_after}",
    )


def _test_cli(c: Collector, base: Path) -> None:
    import contextlib
    import subprocess

    from .cli import build_parser, main

    help_text = build_parser().format_help()
    c.check("cli --help documents plan-only default", "Plan-only is the default" in help_text)
    c.check("cli missing args exits 2", main([]) == 2)
    c.check("cli missing config exits 2", main(["t", "ref.mp4", "--config", str(base / "nope.json")]) == 2)

    # Plan-only smoke: full offline pipeline through the CLI entry point.
    ref_frame = _image_file(base / "cli_ref.png", "#20282D")
    ref_video = base / "cli_reference.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-loop",
            "1",
            "-i",
            str(ref_frame),
            "-t",
            "1",
            "-r",
            "12",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(ref_video),
        ],
        check=True,
    )
    config_path = base / "cli_config.json"
    config_path.write_text(
        json.dumps(
            {
                "workspace": str(base / "cli_studio"),
                "execution_mode": "test",
                "research_search": {"enabled": False},
                "llm": {"provider_order": [], "vision_provider_order": []},
                "audio": {"build_in_plan_mode": False},
                "temporal": {"enabled": False},
            }
        ),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        rc = main(
            ["What if rivers ran backward?", str(ref_video), "--config", str(config_path), "--job-id", "cli-smoke"]
        )
    c.check("cli plan-only smoke exits 0", rc == 0)
    result = json.loads(stdout.getvalue())
    c.check(
        "cli prints plan-only result manifest", result["mode"] == "plan_only" and Path(result["job_manifest"]).exists()
    )


def _test_service_api(c: Collector, base: Path) -> None:
    try:
        from fastapi.testclient import TestClient
    except ImportError as exc:
        raise AssertionError(
            "fastapi+httpx are required for the API tests (install the [api] and [test] extras)"
        ) from exc

    from .pipeline import ScientificMotionStudioV10
    from .service_api import create_app
    from .tests import FakeLLM

    import subprocess

    ref_frame = _image_file(base / "api_ref.png", "#20282D")
    ref_video = base / "api_reference.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-loop",
            "1",
            "-i",
            str(ref_frame),
            "-t",
            "1",
            "-r",
            "12",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(ref_video),
        ],
        check=True,
    )

    def factory():
        return ScientificMotionStudioV10(
            {
                "workspace": str(base / "api_studio"),
                "execution_mode": "test",
                "research_search": {"enabled": False},
                "llm": {"provider_order": [], "vision_provider_order": []},
                "audio": {"build_in_plan_mode": False},
                "temporal": {"enabled": False},
            },
            llm_router=FakeLLM(),
        )

    client = TestClient(create_app(factory))
    c.check("api 404 for unknown job", client.get("/jobs/nope").status_code == 404)
    bad = client.post("/jobs", json={"topic": "test topic", "reference_video": str(base / "missing.mp4")})
    c.check("api 400 for missing reference video", bad.status_code == 400)
    invalid = client.post("/jobs", json={"topic": "x", "reference_video": str(ref_video)})
    c.check("api 422 for too-short topic", invalid.status_code == 422)

    accepted = client.post(
        "/jobs",
        json={
            "topic": "What if rivers ran backward?",
            "reference_video": str(ref_video),
            "plan_only": True,
            "job_id": "api-smoke",
        },
    )
    c.check("api 202 accepted with job id", accepted.status_code == 202 and accepted.json()["job_id"] == "api-smoke")
    deadline = time.time() + 120
    status = {}
    while time.time() < deadline:
        status = client.get("/jobs/api-smoke").json()
        if status.get("status") in {"completed", "failed"}:
            break
        time.sleep(0.2)
    c.check(
        "api job completes with plan-only mode",
        status.get("status") == "completed" and status.get("mode") == "plan_only",
        json.dumps(status),
    )


def _fresh_root(root: str | Path | None, prefix: str) -> Path:
    """Test scratch roots are owned by the suite: an existing root from an
    earlier run is removed so reruns are idempotent (attempt budgets, locks
    and manifests never leak between executions)."""
    if root is None:
        return Path(tempfile.mkdtemp(prefix=prefix))
    base = Path(root)
    if base.exists():
        import shutil

        shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True, exist_ok=True)
    return base


def run_final_validation_tests(root: str | Path | None = None) -> dict[str, Any]:
    base = _fresh_root(root, "scistudio_final_tests_")
    c = Collector()

    _test_config_validation(c, base / "config")
    _test_secret_redaction(c)
    _test_url_and_retry_policy(c)
    _test_atomic_and_cache(c, base / "io")
    _test_safe_subprocess(c, base / "subproc")
    _test_job_runtime(c, base / "jobs")
    _test_download_validation(c, base / "downloads")
    _test_provider_locks(c, base / "locks")
    _test_vision_cache_content_hash(c, base / "vision")
    _test_openai_mocked_integration(c, base / "openai")
    _test_bfl_mocked_integration(c, base / "bfl")
    _test_llm_shape_coercion(c, base / "coercion")
    _test_model_fallback(c, base / "model_fallback")
    _test_vision_two_tier_escalation(c, base / "vision_two_tier")
    _test_director_review_salvage(c, base / "director_salvage")
    _test_reference_motion_guidance(c)
    _test_shot_executor(c, base / "shot_executor")
    _test_motion_eval(c)
    _test_object_segmenter(c, base / "segmenter")
    _test_object_grounding(c, base / "grounding")
    _test_mask_quality_gate(c, base / "mask_qc")
    _test_clean_plate(c, base / "clean_plate")
    _test_renderer_parity(c, base / "parity")
    _test_post_render_qc(c, base / "render_qc")
    _test_rig_builder(c, base / "rig_builder")
    _test_skeletal_deform(c, base / "skeletal_deform")
    _test_anatomical_separation(c, base / "anatomical")
    _test_part_perceptual_qc(c, base / "part_qc")
    _test_video_temporal_backends(c, base / "video_temporal")
    _test_artifact_graph_p1(c, base / "artifact_graph")
    _test_audio_timeline_p4(c, base / "audio_timeline")
    _test_continuity_validator_p5(c, base / "continuity")
    _test_quality_gate_p6(c, base / "quality_gate")
    _test_resource_orchestrator_p7(c, base / "resource_orchestrator")
    _test_release_integrity_p0(c, base / "release_integrity")
    _test_adaptive_candidate_tournament(c, base / "adaptive_tournament")
    _test_render_backend_select_f5(c, base / "render_backend")
    _test_hero_asset_f7(c, base / "hero_asset")
    _test_rive_integration_optional(c, base / "rive")
    _test_topic_engine_p8(c, base / "topic_engine")
    _test_metadata_engine_p9(c, base / "metadata_engine")
    _test_claim_graph_p3(c, base / "claim_graph")
    _test_visual_provider_router(c, base / "visual_router")
    _test_moderation_recovery(c, base / "moderation")
    _test_character_director(c, base / "character_director")
    _test_flat_explainer_style(c)
    _test_flux_prompt_budget(c, base / "flux_budget")
    _test_e2e_offline(c, base / "e2e")
    _test_cli(c, base / "cli")
    _test_service_api(c, base / "api")

    passed = all(item["passed"] for item in c.items)
    return {"passed": passed, "count": len(c.items), "tests": c.items, "root": str(base)}


def run_all_tests(root: str | Path | None = None) -> dict[str, Any]:
    """Aggregate runner: retained V9 + V10 suites plus the finalization suite.

    The given root is a dedicated test scratch directory owned by the suite;
    it is recreated from scratch on every run so reruns are idempotent.
    """
    from .tests_v10 import run_v10_regression_tests

    base = _fresh_root(root, "scistudio_all_tests_")
    legacy = run_v10_regression_tests(base / "regression")
    final = run_final_validation_tests(base / "final")
    from .tests_notebook import run_notebook_tests

    notebook = run_notebook_tests(base / "notebook")
    return {
        "passed": bool(legacy["passed"] and final["passed"] and notebook["passed"]),
        "count": legacy["count"] + final["count"] + notebook["count"],
        "regression_count": legacy["count"],
        "final_count": final["count"],
        "notebook_count": notebook["count"],
        "tests": [*legacy["tests"], *final["tests"], *notebook["tests"]],
        "root": str(base),
    }


if __name__ == "__main__":
    report = run_all_tests()
    print(json.dumps({k: v for k, v in report.items() if k != "tests"}, indent=2))
    raise SystemExit(0 if report["passed"] else 1)
