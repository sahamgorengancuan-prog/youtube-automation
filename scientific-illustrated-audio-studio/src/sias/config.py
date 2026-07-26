"""Validated configuration. Invalid values are rejected before any provider
call; run modes and budgets live here, not scattered through modules."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from .exceptions import ConfigurationError

RUN_MODES = ("plan", "style_lock", "pilot", "production", "repair", "render_only")

DEFAULT_PALETTE = {
    "ink": "#252525",
    "paper": "#F4EEDC",
    "primary": "#2F6F8F",
    "attention": "#E48A3A",
    "danger": "#C94C4C",
    "nature": "#5F8A55",
    "secondary": "#8A73A8",
}

ALLOWED_MOTION = [
    "hold",
    "slow_push_in",
    "slow_pull_out",
    "pan_left",
    "pan_right",
    "label_reveal",
    "page_turn",
]


class ProjectCfg(BaseModel):
    title: str = "Untitled episode"
    topic: str = ""
    audience: str = "curious general audience, age 15+"
    language: str = "en"
    aspect_ratio: str = "9:16"
    width: int = 1080
    height: int = 1920
    target_duration_min_s: int = 45
    target_duration_max_s: int = 75


class StoryCfg(BaseModel):
    min_scenes: int = 7
    max_scenes: int = 9
    target_wpm_min: int = 145
    target_wpm_max: int = 165
    facts_count: int = 3
    catchphrase: str = "Your intuition skipped a page—let's put the science back."
    catchphrase_max_uses: int = 1
    first_payoff_deadline_s: int = 12

    @field_validator("max_scenes")
    @classmethod
    def _scene_bounds(cls, v: int, info: Any) -> int:
        if v < info.data.get("min_scenes", 7):
            raise ValueError("max_scenes must be >= min_scenes")
        return v


class VisualCfg(BaseModel):
    identity_name: str = "Scientific Notebook Cartoon"
    draft_model: str = "flux-2-klein-9b"
    production_model: str = "flux-2-pro"
    premium_model: str = "flux-2-max"
    typography_model: str = "flux-2-flex"
    candidates_normal: int = 2
    candidates_hero: int = 3
    max_repairs_per_scene: int = 2
    max_reference_images: int = 8
    output_format: str = "png"
    seed_base: int = 270726
    palette: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_PALETTE))


class VisionCfg(BaseModel):
    approval_threshold: float = 0.82
    disagreement_threshold: float = 0.18
    qwen_family_prefix: str = "qwen/"
    gemini_family_prefix: str = "google/"
    deny_training_or_data_collection: bool = True

    @field_validator("approval_threshold", "disagreement_threshold")
    @classmethod
    def _unit(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("thresholds must be within [0,1]")
        return v


class AudioCfg(BaseModel):
    tts_model: str = "gpt-4o-mini-tts"
    voice: str = "cedar"
    output_format: str = "wav"
    target_lufs: float = -16.0
    true_peak_db: float = -1.5
    silent_peak_threshold_db: float = -55.0
    generate_per_scene: bool = False
    transcription_model: str = "whisper-1"


class RenderCfg(BaseModel):
    backend: str = "ffmpeg"
    fps: int = 30
    duration_tolerance_s: float = 0.08
    captions: bool = True
    caption_font_path: str = ""
    min_scene_duration_s: float = 2.4
    max_zoom_pct: float = 4.0
    max_pan_pct: float = 3.0
    allowed_motion: list[str] = Field(default_factory=lambda: list(ALLOWED_MOTION))

    @field_validator("max_zoom_pct")
    @classmethod
    def _zoom_cap(cls, v: float) -> float:
        if v > 4.0:
            raise ValueError("max_zoom_pct is capped at 4% by product policy")
        return v

    @field_validator("max_pan_pct")
    @classmethod
    def _pan_cap(cls, v: float) -> float:
        if v > 3.0:
            raise ValueError("max_pan_pct is capped at 3% by product policy")
        return v


class BudgetsCfg(BaseModel):
    max_image_calls: int = 30
    max_vision_calls: int = 80
    max_tts_characters: int = 9000
    hard_stop_on_budget_exceeded: bool = True


class PrivacyCfg(BaseModel):
    write_api_keys_to_logs: bool = False
    write_full_auth_headers: bool = False
    openrouter_data_collection: str = "deny"


class RetriesCfg(BaseModel):
    network_max_attempts: int = 4
    generation_max_attempts: int = 2
    repair_max_attempts: int = 2


class CacheCfg(BaseModel):
    enabled: bool = True
    validation: str = "input_hash_and_output_sha256"


class LoggingCfg(BaseModel):
    level: str = "INFO"
    structured_json: bool = True


class SIASConfig(BaseModel):
    run_mode: str = "plan"
    allow_production_without_pilot: bool = False
    project: ProjectCfg = Field(default_factory=ProjectCfg)
    story: StoryCfg = Field(default_factory=StoryCfg)
    visual: VisualCfg = Field(default_factory=VisualCfg)
    style: dict[str, Any] = Field(default_factory=dict)
    vision: VisionCfg = Field(default_factory=VisionCfg)
    audio: AudioCfg = Field(default_factory=AudioCfg)
    render: RenderCfg = Field(default_factory=RenderCfg)
    budgets: BudgetsCfg = Field(default_factory=BudgetsCfg)
    privacy: PrivacyCfg = Field(default_factory=PrivacyCfg)
    retries: RetriesCfg = Field(default_factory=RetriesCfg)
    cache: CacheCfg = Field(default_factory=CacheCfg)
    logging: LoggingCfg = Field(default_factory=LoggingCfg)

    @field_validator("run_mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in RUN_MODES:
            raise ValueError(f"run_mode must be one of {RUN_MODES}, got {v!r}")
        return v


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(*paths: str | Path, overrides: dict | None = None) -> SIASConfig:
    """Merge YAML files in order, apply overrides, validate. Raises
    ConfigurationError with the pydantic detail on any invalid value."""
    merged: dict[str, Any] = {}
    for p in paths:
        p = Path(p)
        if not p.exists():
            raise ConfigurationError(f"config file not found: {p}", stage="config")
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ConfigurationError(f"config root must be a mapping: {p}", stage="config")
        merged = _deep_merge(merged, data)
    if overrides:
        merged = _deep_merge(merged, overrides)
    try:
        return SIASConfig.model_validate(merged)
    except Exception as exc:  # pydantic ValidationError → typed config error
        raise ConfigurationError(f"invalid configuration: {exc}", stage="config") from exc
