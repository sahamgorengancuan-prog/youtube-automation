"""Visual provider router — classified errors + intelligent, scene-scoped recovery.

A single image failure must recover the *right* way and only on the failed asset,
never the whole video. This wraps image generation behind a uniform
``VisualProvider`` interface and routes:

    BFL (primary)
        -> CONTENT_MODERATION -> prompt safety rewrite -> retry BFL
        -> TIMEOUT/SERVER      -> retry same input
        -> RATE_LIMIT          -> backoff + retry
        -> QUALITY_FAILURE     -> regenerate with a new seed
        -> AUTH/INVALID        -> stop this provider
        -> still failing       -> next provider (opt-in Gemini / local HF)
        -> all exhausted       -> raise scene-scoped (resume/repair that node)

BFL stays the primary and only default provider; alternate providers are opt-in
(config ``image_fallbacks``), so the default production path is unchanged. The
router never falls back for the whole video — each call is one asset/shot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from .prompt_safety import PromptSafetyRewriter, is_moderation_error


class ErrorClass(str, Enum):
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    AUTH_ERROR = "auth_error"
    CONTENT_MODERATION = "content_moderation"
    SERVER_ERROR = "server_error"
    INVALID_REQUEST = "invalid_request"
    QUALITY_FAILURE = "quality_failure"
    UNKNOWN = "unknown"


def classify_error(exc: Exception) -> ErrorClass:
    if is_moderation_error(exc):
        return ErrorClass.CONTENT_MODERATION
    name = type(exc).__name__
    msg = str(exc).lower()
    code = getattr(exc, "status_code", None)
    if "timeout" in msg or "timed out" in msg or name.endswith("TimeoutError"):
        return ErrorClass.TIMEOUT
    if code == 429 or "rate limit" in msg or "429" in msg:
        return ErrorClass.RATE_LIMIT
    if code in (401, 403) or "unauthor" in msg or "forbidden" in msg or "api key" in msg:
        return ErrorClass.AUTH_ERROR
    if (code is not None and 500 <= int(code) < 600) or "server error" in msg or "503" in msg or "502" in msg:
        return ErrorClass.SERVER_ERROR
    if "quality" in msg or "qc failed" in name.lower():
        return ErrorClass.QUALITY_FAILURE
    if code == 400 or name in {"ValueError", "ValidationError"} or "invalid" in msg or "required" in msg:
        return ErrorClass.INVALID_REQUEST
    return ErrorClass.UNKNOWN


@dataclass
class VisualRequest:
    prompt: str
    output_path: str
    init_image: str | None = None
    aspect_ratio: str = "9:16"
    seed: int = 0
    force: bool = False


@dataclass
class VisualResult:
    path: str
    provider: str
    model: str = ""
    attempts: int = 1
    error_history: list[dict[str, Any]] = field(default_factory=list)


class VisualProvider(Protocol):
    name: str

    def available(self) -> bool: ...
    def generate(self, request: VisualRequest) -> Path: ...


class BFLVisualProvider:
    """Primary provider — BFL FLUX Kontext via the LLM router."""

    name = "bfl"

    def __init__(self, llm: Any, model: str = "flux-kontext-pro"):
        self.llm = llm
        self.model = model

    def available(self) -> bool:
        return self.llm is not None

    def generate(self, request: VisualRequest) -> Path:
        result = self.llm.generate_reference_image(
            prompt=request.prompt,
            output_path=Path(request.output_path),
            init_image=request.init_image,
            force=request.force,
        )
        if result is None or not Path(result).exists():
            raise RuntimeError("BFL returned no image")
        return Path(result)


class CallableVisualProvider:
    """Opt-in fallback provider wrapping any ``generate(request)->path`` callable
    (e.g. a Gemini image call or a local diffusers pipeline). Availability is
    decided by the supplied ``available`` callable, so real alternates stay
    honestly gated (key/GPU present)."""

    def __init__(self, name: str, fn: Any, available_fn: Any = None, model: str = ""):
        self.name = name
        self._fn = fn
        self._available = available_fn or (lambda: True)
        self.model = model

    def available(self) -> bool:
        try:
            return bool(self._available())
        except Exception:
            return False

    def generate(self, request: VisualRequest) -> Path:
        return Path(self._fn(request))


class VisualProviderRouter:
    def __init__(
        self, providers: list[Any], config: dict[str, Any] | None = None, rewriter: PromptSafetyRewriter | None = None
    ):
        self.providers = providers
        self.config = config or {}
        self.rewriter = rewriter or PromptSafetyRewriter(None, {})

    def generate(self, request: VisualRequest) -> VisualResult:
        moderation_retries = int(self.config.get("bfl_moderation_retries", 3))
        transient_retries = int(self.config.get("transient_retries", 2))
        quality_retries = int(self.config.get("quality_retries", 1))
        history: list[dict[str, Any]] = []
        total_attempts = 0

        for provider in self.providers:
            if not provider.available():
                continue
            prompt = request.prompt
            seed = request.seed
            mod_used = trans_used = qual_used = 0
            while True:
                total_attempts += 1
                try:
                    req = VisualRequest(
                        prompt,
                        request.output_path,
                        request.init_image,
                        request.aspect_ratio,
                        seed,
                        request.force or total_attempts > 1,
                    )
                    path = provider.generate(req)
                    return VisualResult(
                        str(path), provider.name, getattr(provider, "model", ""), total_attempts, history
                    )
                except Exception as exc:
                    ec = classify_error(exc)
                    history.append({"provider": provider.name, "error_class": ec.value, "message": str(exc)[:160]})
                    if ec == ErrorClass.CONTENT_MODERATION and mod_used < moderation_retries:
                        mod_used += 1
                        prompt = self.rewriter.rewrite(prompt, str(getattr(exc, "status", "") or exc), mod_used)
                        continue
                    if (
                        ec in (ErrorClass.TIMEOUT, ErrorClass.SERVER_ERROR, ErrorClass.RATE_LIMIT)
                        and trans_used < transient_retries
                    ):
                        trans_used += 1
                        continue  # retry same input (backoff handled by the client)
                    if ec == ErrorClass.QUALITY_FAILURE and qual_used < quality_retries:
                        qual_used += 1
                        seed = seed + 9973  # regenerate with variation
                        continue
                    # AUTH_ERROR / INVALID_REQUEST / exhausted -> stop this provider,
                    # try the next opt-in fallback provider (scene-scoped).
                    break

        raise RuntimeError(
            "All visual providers exhausted for this asset "
            f"(attempts={total_attempts}, history={history[-4:]}). This is scene-scoped: "
            "the completed stages resume from cache; only this node is repaired/regenerated."
        )


def build_visual_router(llm: Any, config: dict[str, Any], fallbacks: list[Any] | None = None) -> VisualProviderRouter:
    """BFL primary + any opt-in fallback providers (default: none)."""
    providers: list[Any] = [BFLVisualProvider(llm, str(config.get("bfl_model", "flux-kontext-pro")))]
    for fb in fallbacks or []:
        providers.append(fb)
    return VisualProviderRouter(providers, config, PromptSafetyRewriter(llm, config.get("prompt_safety", {})))
