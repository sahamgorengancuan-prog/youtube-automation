"""Colab configuration = engine config + Colab layer (source mode, UI language,
arm switch, canary mode, Higgsfield opt-in)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from sias.config import SIASConfig, load_config as _engine_load  # noqa: F401

COLAB_RUN_MODES = ("plan", "canary", "style_lock", "pilot", "production", "repair", "render_only")


class HiggsfieldCfg(BaseModel):
    enabled: bool = False
    virality_predictor_enabled: bool = False
    model: str = ""
    max_calls: int = 5


class ColabCfg(BaseModel):
    source_mode: str = "github_repository"  # standalone | github_repository | uploaded_zip
    ui_language: str = "id"  # id | en
    use_drive: bool = True
    drive_root: str = "/content/drive/MyDrive/SIAS"
    arm_paid_calls: bool = False
    run_mode: str = "plan"
    higgsfield: HiggsfieldCfg = Field(default_factory=HiggsfieldCfg)

    @field_validator("run_mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in COLAB_RUN_MODES:
            raise ValueError(f"run_mode must be one of {COLAB_RUN_MODES}")
        return v

    @field_validator("ui_language")
    @classmethod
    def _lang(cls, v: str) -> str:
        if v not in ("id", "en"):
            raise ValueError("ui_language must be 'id' or 'en'")
        return v

    @field_validator("source_mode")
    @classmethod
    def _source(cls, v: str) -> str:
        if v not in ("standalone", "github_repository", "uploaded_zip"):
            raise ValueError("source_mode must be standalone|github_repository|uploaded_zip")
        return v


class StudioConfig(BaseModel):
    engine: SIASConfig = Field(default_factory=SIASConfig)
    colab: ColabCfg = Field(default_factory=ColabCfg)

    @property
    def run_mode(self) -> str:
        return self.colab.run_mode


def load_studio_config(
    engine_yaml: str | Path,
    overrides: dict[str, Any] | None = None,
    colab: dict[str, Any] | None = None,
) -> StudioConfig:
    engine = _engine_load(engine_yaml, overrides=overrides or {})
    colab_cfg = ColabCfg.model_validate(colab or {})
    # Keep the engine's run_mode in lock-step where the mode exists there too.
    if colab_cfg.run_mode in ("plan", "style_lock", "pilot", "production", "repair", "render_only"):
        engine = engine.model_copy(update={"run_mode": colab_cfg.run_mode})
    return StudioConfig(engine=engine, colab=colab_cfg)
