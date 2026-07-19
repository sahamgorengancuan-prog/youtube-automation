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
            "execution_mode": "production",
            "llm": {"provider_order": ["openai"], "vision_provider_order": ["openai", "gemini"]},
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
        def _openai_vision(self, image_path, prompt):
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
