"""Strongly-validated configuration models and execution modes.

``StudioConfig`` is the single entry point: it validates the loose nested
dictionary that earlier prototypes passed around, enforces the production
provider boundary, and can be converted back into the runtime dictionary
shape (`as_runtime_dict`) that the pipeline modules consume, preserving any
extra keys the user supplied.

Execution modes
---------------
* ``development`` — permissive: alternative providers and deterministic
  fallbacks are allowed (fallbacks are still marked and cached separately).
* ``test`` — like development, intended for injected fake providers and
  offline suites. Never performs paid live calls by itself.
* ``production`` — the provider boundary is locked and validated:
  OpenAI GPT is the exclusive reasoning/vision provider and BFL FLUX
  Kontext is the exclusive image engine. Configurations that enable any
  unauthorized fallback are rejected, and runtime failures raise
  ``ProviderUnavailableError`` instead of degrading silently.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .errors import ProviderLockViolationError

BFL_ALLOWED_MODELS = frozenset(
    {
        "flux-kontext-pro",
        "flux-kontext-max",
    }
)
BFL_MIN_ASPECT = 3 / 7
BFL_MAX_ASPECT = 7 / 3

_ASPECT_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*:\s*(\d+(?:\.\d+)?)\s*$")


class ExecutionMode(str, Enum):
    development = "development"
    test = "test"
    production = "production"


class RetryPolicy(BaseModel):
    """Shared retry policy for provider HTTP calls."""

    model_config = ConfigDict(extra="allow")

    max_attempts: int = Field(default=4, ge=1, le=10)
    base_delay_s: float = Field(default=1.0, ge=0.05, le=30.0)
    max_delay_s: float = Field(default=30.0, ge=0.1, le=300.0)
    jitter: float = Field(default=0.25, ge=0.0, le=1.0)


class LLMConfig(BaseModel):
    """Text/vision/image provider routing configuration."""

    model_config = ConfigDict(extra="allow")

    provider_order: list[str] | str = Field(default_factory=lambda: ["openai"])
    vision_provider_order: list[str] | str | None = None
    execution_mode: ExecutionMode = ExecutionMode.development

    openai_model: str = "gpt-5-mini"
    openai_vision_model: str = ""
    openai_reasoning_effort: str = "low"
    openai_max_output_tokens: int = Field(default=8000, ge=256, le=200_000)
    openai_connect_timeout_s: float = Field(default=15.0, ge=1.0, le=120.0)
    openai_read_timeout_s: float = Field(default=180.0, ge=5.0, le=1200.0)
    openai_json_repair_attempts: int = Field(default=1, ge=0, le=3)

    image_provider: str = "bfl"
    bfl_model: str = "flux-kontext-pro"
    bfl_base_url: str = "https://api.bfl.ai/v1"
    bfl_aspect_ratio: str = "9:16"
    bfl_timeout: float = Field(default=240.0, ge=10.0, le=3600.0)
    bfl_poll_interval: float = Field(default=1.5, ge=0.1, le=30.0)
    bfl_prompt_upsampling: bool = False
    bfl_safety_tolerance: int = Field(default=2, ge=0, le=6)
    bfl_output_format: str = "png"
    bfl_seed: int | None = None
    bfl_allowed_url_hosts: list[str] = Field(default_factory=lambda: ["api.bfl.ai", "*.bfl.ai"])
    bfl_max_download_bytes: int = Field(default=32 * 1024 * 1024, ge=1024, le=512 * 1024 * 1024)

    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    fallback_cache_ttl_s: float = Field(default=3600.0, ge=0.0)

    enable_local_fallback: bool = False
    enable_local_image_generation: bool = False

    @field_validator("openai_reasoning_effort")
    @classmethod
    def _effort(cls, value: str) -> str:
        allowed = {"minimal", "low", "medium", "high"}
        if value not in allowed:
            raise ValueError(f"openai_reasoning_effort must be one of {sorted(allowed)}")
        return value

    @field_validator("openai_model")
    @classmethod
    def _openai_model(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("openai_model must be a non-empty model identifier")
        return value.strip()

    @field_validator("bfl_model")
    @classmethod
    def _bfl_model(cls, value: str) -> str:
        if value not in BFL_ALLOWED_MODELS:
            raise ValueError(
                f"bfl_model {value!r} is not a supported FLUX Kontext model; allowed: {sorted(BFL_ALLOWED_MODELS)}"
            )
        return value

    @field_validator("bfl_base_url")
    @classmethod
    def _bfl_base_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("bfl_base_url must be https")
        return value.rstrip("/")

    @field_validator("bfl_aspect_ratio")
    @classmethod
    def _aspect(cls, value: str) -> str:
        match = _ASPECT_PATTERN.match(str(value))
        if not match:
            raise ValueError(f"bfl_aspect_ratio {value!r} must look like '9:16'")
        left, right = float(match.group(1)), float(match.group(2))
        if right <= 0 or left <= 0:
            raise ValueError("bfl_aspect_ratio terms must be positive")
        ratio = left / right
        if not (BFL_MIN_ASPECT <= ratio <= BFL_MAX_ASPECT):
            raise ValueError(f"bfl_aspect_ratio {value!r} outside the FLUX Kontext supported range 3:7..7:3")
        return f"{match.group(1)}:{match.group(2)}"

    @field_validator("bfl_output_format")
    @classmethod
    def _fmt(cls, value: str) -> str:
        if str(value).lower() not in {"png", "jpeg"}:
            raise ValueError("bfl_output_format must be png or jpeg")
        return str(value).lower()

    def ordered_providers(self) -> list[str]:
        order = self.provider_order
        if isinstance(order, str):
            order = [item.strip() for item in order.split(",") if item.strip()]
        return [str(item).lower() for item in order]

    def ordered_vision_providers(self) -> list[str]:
        order = self.vision_provider_order
        if order is None:
            return self.ordered_providers()
        if isinstance(order, str):
            order = [item.strip() for item in order.split(",") if item.strip()]
        return [str(item).lower() for item in order]


class TemporalCommandConfig(BaseModel):
    """External temporal-backend command configuration.

    The safe form is ``argv``: a list of tokens where ``{placeholders}`` are
    substituted per token (never joined through a shell). The legacy string
    ``command`` is parsed with ``shlex`` into the same argv form.
    Shell execution is disabled by default, marked unsafe, and rejected in
    production mode unless ``security_override_unsafe_shell`` is set.
    """

    model_config = ConfigDict(extra="allow")

    argv: list[str] = Field(default_factory=list)
    command: str = ""  # legacy single-string form; shlex-split, never shell-run
    timeout_s: float = Field(default=1800.0, ge=1.0, le=24 * 3600.0)
    allow_shell: bool = False
    unsafe_shell_command: str = ""
    security_override_unsafe_shell: bool = False


class TemporalConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    use_for_articulated: bool = False
    backend_preference: list[str] = Field(
        default_factory=lambda: ["sketch-controlled-video", "deterministic-compositor"]
    )
    sketch_backend: TemporalCommandConfig = Field(default_factory=TemporalCommandConfig)


class RenderConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    backend: str = "remotion"
    install_dependencies: bool = True
    crf: int = Field(default=18, ge=0, le=51)

    @field_validator("backend")
    @classmethod
    def _backend(cls, value: str) -> str:
        allowed = {"remotion", "pil", "deterministic", "preview"}
        low = str(value).lower()
        if low not in allowed:
            raise ValueError(f"render backend must be one of {sorted(allowed)}")
        return low


class LimitsConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    max_file_bytes: int = Field(default=512 * 1024 * 1024, ge=1024)
    max_concurrent_jobs: int = Field(default=1, ge=1, le=64)
    max_stage_attempts: int = Field(default=5, ge=1, le=50)


class PublishingConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    enabled: bool = False
    provider: str = "local-archive"
    archive_dir: str = ""
    endpoint: str = ""
    allowed_hosts: list[str] = Field(default_factory=list)


class CandidateTournamentConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    candidate_count: int = Field(default=4, ge=1, le=12)
    seed_stride: int = Field(default=9973, ge=1)


class FluxStudioConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    maximum_director_revisions: int = Field(default=3, ge=1, le=10)
    require_vision_director: bool = True
    min_valid_image_bytes: int = Field(default=1024, ge=1)


class StudioConfig(BaseModel):
    """Top-level validated configuration for the studio pipeline."""

    model_config = ConfigDict(extra="allow")

    workspace: str = "./scientific_motion_studio_v10"
    execution_mode: ExecutionMode = ExecutionMode.development
    job_id: str | None = None

    llm: LLMConfig = Field(default_factory=LLMConfig)
    temporal: TemporalConfig = Field(default_factory=TemporalConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    publishing: PublishingConfig = Field(default_factory=PublishingConfig)
    candidate_tournament: CandidateTournamentConfig = Field(default_factory=CandidateTournamentConfig)
    flux_studio: FluxStudioConfig = Field(default_factory=FluxStudioConfig)

    @field_validator("workspace")
    @classmethod
    def _workspace(cls, value: str) -> str:
        if not str(value).strip():
            raise ValueError("workspace must be a non-empty path")
        return str(value)

    @model_validator(mode="after")
    def _propagate_and_lock(self) -> StudioConfig:
        # The LLM router reads its own execution mode; keep it in sync.
        self.llm.execution_mode = self.execution_mode
        if self.execution_mode == ExecutionMode.production:
            self._enforce_production_locks()
        return self

    def _enforce_production_locks(self) -> None:
        problems: list[str] = []
        order = self.llm.ordered_providers()
        if order != ["openai"]:
            problems.append(
                f"llm.provider_order must be ['openai'] in production (got {order}); "
                "Gemini/OpenRouter/local fallbacks are not authorized"
            )
        vision = self.llm.ordered_vision_providers()
        if vision != ["openai"]:
            problems.append(f"llm.vision_provider_order must be ['openai'] in production (got {vision})")
        if self.llm.image_provider != "bfl":
            problems.append(
                f"llm.image_provider must be 'bfl' in production (got {self.llm.image_provider!r}); "
                "BFL FLUX Kontext is the exclusive production image engine"
            )
        if self.llm.enable_local_fallback:
            problems.append("llm.enable_local_fallback must be false in production")
        if self.llm.enable_local_image_generation:
            problems.append("llm.enable_local_image_generation must be false in production")
        extras = self.llm.model_extra or {}
        for banned in ("gemini_image_model", "openai_image_model", "local_image_model"):
            if extras.get(banned):
                problems.append(
                    f"llm.{banned} must not be set in production; only BFL FLUX Kontext may generate images"
                )
        sketch = self.temporal.sketch_backend
        if sketch.allow_shell and not sketch.security_override_unsafe_shell:
            problems.append(
                "temporal.sketch_backend.allow_shell requires security_override_unsafe_shell=true in production"
            )
        if problems:
            raise ProviderLockViolationError("Production provider-lock validation failed:\n- " + "\n- ".join(problems))

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | StudioConfig) -> StudioConfig:
        if isinstance(raw, StudioConfig):
            return raw
        return cls.model_validate(raw or {})

    def as_runtime_dict(self) -> dict[str, Any]:
        """The nested-dict shape the pipeline modules consume.

        Extra keys supplied by the user are preserved verbatim.
        """
        data = self.model_dump(mode="json")
        data["execution_mode"] = self.execution_mode.value
        data["llm"]["execution_mode"] = self.execution_mode.value
        return data
