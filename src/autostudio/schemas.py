from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StudioModel(BaseModel):
    """Base model. `extra="ignore"` keeps LLM output tolerant; assignment is validated."""

    model_config = ConfigDict(extra="ignore", validate_assignment=True)


# --------------------------------------------------------------------------- #
# Search + research
# --------------------------------------------------------------------------- #
class SourceRecord(StudioModel):
    source_id: str
    provider: str
    title: str
    url: str | None = None
    snippet: str = ""
    author: str | None = None
    published_at: str | None = None
    score: float = 0.0


class ResearchFact(StudioModel):
    fact_id: str
    claim: str
    source_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    numeric_values: list[str] = Field(default_factory=list)
    visual_hint: str = ""


class ResearchBundle(StudioModel):
    topic: str
    summary: str
    facts: list[ResearchFact]
    numbers: list[ResearchFact] = Field(default_factory=list)
    references: list[SourceRecord]
    comparisons: list[str] = Field(default_factory=list)
    visual_ideas: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    research_hash: str = ""


class ValidationIssue(StudioModel):
    severity: str
    code: str
    message: str
    claim: str | None = None
    source_ids: list[str] = Field(default_factory=list)


class ValidationReport(StudioModel):
    passed: bool
    coverage_score: float = Field(ge=0.0, le=1.0)
    source_diversity: int = 0
    issues: list[ValidationIssue] = Field(default_factory=list)
    validated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# --------------------------------------------------------------------------- #
# Script
# --------------------------------------------------------------------------- #
class ScriptBeat(StudioModel):
    beat_id: str
    purpose: str
    narration: str
    evidence_refs: list[str] = Field(default_factory=list)
    words: int = 0
    start_s: float = 0.0
    end_s: float = 0.0
    estimated_duration_s: float = 0.0


class ScriptPackage(StudioModel):
    topic: str
    hook: str
    curiosity: str
    scientific_explanation: str
    escalation: str
    ending: str
    beats: list[ScriptBeat]
    estimated_duration_s: float = 0.0
    total_words: int = 0
    tone: str = "curious, precise, escalating"
    script_hash: str = ""


# --------------------------------------------------------------------------- #
# Storyboard + scene graph
# --------------------------------------------------------------------------- #
class AssetRequirement(StudioModel):
    asset_id: str
    asset_type: str
    label: str = ""
    variant: str = "default"
    palette_role: str = "primary"
    source_prompt: str = ""
    tags: list[str] = Field(default_factory=list)


class SceneObject(StudioModel):
    """Placement of one asset on the canvas. Coordinates are normalised 0..1."""

    asset_id: str
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(gt=0.0, le=1.0)
    height: float = Field(gt=0.0, le=1.0)
    rotation: float = 0.0
    z_index: int = 0
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)


class CameraPlan(StudioModel):
    shot: str = "wide"
    zoom_start: float = 1.0
    zoom_end: float = 1.04
    pan_x: float = 0.0
    pan_y: float = 0.0
    framing: str = "center"


class AnimationPlaceholder(StudioModel):
    """Phase-2 animation intent. Carried through the storyboard, not rendered here."""

    target_asset_id: str
    kind: str
    start_s: float = 0.0
    duration_s: float = 1.0
    easing: str = "easeInOut"


class DashboardSpec(StudioModel):
    experiment_id: str = "EXPERIMENT #001"
    status_label: str = "Simulation Status"
    status_value: str = "Running"
    metric_label: str = "VALUE"
    metric_value: str = "—"
    severity: str = "normal"


class Scene(StudioModel):
    scene_id: str
    duration_s: float = Field(gt=0.0)
    narration: str
    title: str = ""
    objects: list[SceneObject] = Field(default_factory=list)
    camera: CameraPlan = Field(default_factory=CameraPlan)
    animations: list[AnimationPlaceholder] = Field(default_factory=list)
    dashboard: DashboardSpec = Field(default_factory=DashboardSpec)
    text: list[str] = Field(default_factory=list)
    icons: list[str] = Field(default_factory=list)
    background: str = "paper"
    motion: str = "hold"
    transition: str = "cut"
    asset_requirements: list[AssetRequirement] = Field(default_factory=list)


class Storyboard(StudioModel):
    topic: str
    canvas_width: int = 1080
    canvas_height: int = 1920
    style_name: str = "scientific-simulation-flat"
    scenes: list[Scene]
    asset_catalog: list[AssetRequirement] = Field(default_factory=list)
    estimated_duration_s: float = 0.0
    storyboard_hash: str = ""


# --------------------------------------------------------------------------- #
# Cache + run bookkeeping
# --------------------------------------------------------------------------- #
class AssetMetadata(StudioModel):
    asset_hash: str
    prompt_hash: str
    svg_hash: str
    asset_id: str
    asset_type: str
    source_prompt: str
    topic: str
    created_at: str
    updated_at: str
    reuse_counter: int = 0
    embedding: list[float] | None = None
    generator_version: str = "svg-template-v1"


class RunManifest(StudioModel):
    run_id: str
    topic: str
    mode: str
    created_at: str
    project_root: str
    run_directory: str
    hardware: dict[str, Any]
    research_hash: str
    script_hash: str
    storyboard_hash: str
    asset_hashes: dict[str, str]
    files: dict[str, str]
    validation_passed: bool = False
    estimated_duration_s: float = 0.0
    warnings: list[str] = Field(default_factory=list)
