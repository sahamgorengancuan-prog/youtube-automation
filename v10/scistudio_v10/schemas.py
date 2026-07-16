from __future__ import annotations

import json
from datetime import datetime, UTC
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class OpenModel(BaseModel):
    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True, validate_assignment=False)


class SourceDoc(OpenModel):
    source_id: str = ""
    provider: str = "unknown"
    title: str = "Untitled source"
    url: str = ""
    snippet: str = ""
    author: str = ""
    published_at: str = ""
    authority_score: float = 0.5
    relevance_score: float = 0.0
    final_score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class Fact(OpenModel):
    fact_id: str = ""
    claim: str = ""
    source_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.6
    numeric_values: list[Any] = Field(default_factory=list)
    comparison: Any = ""
    visual_hint: Any = ""

    @field_validator("source_ids", mode="before")
    @classmethod
    def normalize_source_ids(cls, value: Any) -> list[str]:
        if value is None:
            return []
        values = value if isinstance(value, (list, tuple, set)) else [value]
        result: list[str] = []
        for item in values:
            if isinstance(item, dict):
                item = item.get("source_id", item.get("id", item.get("value", "")))
            text = str(item).strip()
            if text and text not in result:
                result.append(text)
        return result

    @field_validator("numeric_values", mode="before")
    @classmethod
    def normalize_numeric_values(cls, value: Any) -> list[Any]:
        if value is None:
            return []
        return list(value) if isinstance(value, (list, tuple, set)) else [value]


class ResearchPack(OpenModel):
    topic: str
    summary: str = ""
    hooks: list[Any] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    comparisons: list[Any] = Field(default_factory=list)
    visual_ideas: list[Any] = Field(default_factory=list)
    limitations: list[Any] = Field(default_factory=list)
    sources: list[SourceDoc] = Field(default_factory=list)
    validation_score: float = 0.0
    research_hash: str = ""
    raw_llm_output: Any = None


class Beat(OpenModel):
    beat_id: str = ""
    purpose: str = "information_gain"
    duration_s: float = 5.0
    spoken_line: str = ""
    visual_event: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    emotion: str = "curiosity"
    retention_function: str = "information_gain"
    emphasis_words: list[str] = Field(default_factory=list)
    pause_after_ms: int = 80
    sfx: str = "none"
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("duration_s", mode="before")
    @classmethod
    def duration_to_float(cls, value: Any) -> float:
        if isinstance(value, dict):
            value = value.get("seconds", value.get("value", 5.0))
        try:
            return max(0.25, float(value))
        except Exception:
            return 5.0


class ScriptPackage(OpenModel):
    topic: str
    title: str = ""
    hook: str = ""
    beats: list[Beat] = Field(default_factory=list)
    closing: str = ""
    total_words: int = 0
    estimated_duration_s: float = 0.0
    script_hash: str = ""
    raw_llm_output: Any = None


class SceneRequest(OpenModel):
    scene_id: str = ""
    beat_id: str = ""
    duration_s: float = 5.0
    narration: str = ""
    headline: str = ""
    visual_event: str = ""
    scientific_claim: str = ""
    time_stage: str = ""
    attention_goal: str = ""
    desired_change: str = ""
    transition: str = "cut"
    raw_llm_output: Any = None

    @field_validator("duration_s", mode="before")
    @classmethod
    def duration_to_float(cls, value: Any) -> float:
        if isinstance(value, dict):
            value = value.get("seconds", value.get("value", 5.0))
        try:
            return max(0.25, float(value))
        except Exception:
            return 5.0


class Storyboard(OpenModel):
    topic: str
    width: int = 1080
    height: int = 1920
    fps: int = 30
    scenes: list[SceneRequest] = Field(default_factory=list)
    estimated_duration_s: float = 0.0
    storyboard_hash: str = ""
    raw_llm_output: Any = None


class LineLanguage(OpenModel):
    silhouette_px: tuple[float, float] = (3.2, 4.4)
    structural_px: tuple[float, float] = (1.4, 2.7)
    texture_px: tuple[float, float] = (0.8, 1.5)
    behavior: list[str] = Field(default_factory=list)
    endings: str = "selectively tapered and occasionally broken"
    contour_rule: str = "contours follow observed form and overlap, never primitive geometry"


class ColorLanguage(OpenModel):
    paper: str = "#FAFAF7"
    ink: str = "#20282D"
    slate: str = "#475157"
    mist: str = "#EBEFF0"
    steel: str = "#A8BAC2"
    blue_primary: str = "#2E77A6"
    blue_secondary: str = "#4190C3"
    warning_red: str = "#D8483E"
    sun_yellow: str = "#F3BD38"
    maximum_dominant_hues: int = 4
    value_bands: int = 4
    rule: str = "restrained scientific palette; accents encode causal meaning"


class TypographyLanguage(OpenModel):
    family: str = "condensed grotesk sans serif"
    fallback_stack: list[str] = Field(
        default_factory=lambda: ["Arial Narrow", "Roboto Condensed", "Arial", "sans-serif"]
    )
    headline_case: str = "uppercase"
    headline_weight: int = 900
    label_weight: int = 700
    alignment: str = "left or optically centered according to composition"
    rule: str = "typography behaves like an experimental instrument panel, not a presentation template"


class CompositionLanguage(OpenModel):
    scene_first: bool = True
    depth_planes: tuple[int, int] = (3, 5)
    visual_mass_fraction: tuple[float, float] = (0.38, 0.62)
    asymmetry_required: bool = True
    integrated_environment_required: bool = True
    negative_space_rule: str = "breathing room is authored around the focal route, never leftover blank space"
    perspective_rule: str = "one coherent perspective system per shot"


class MotionLanguage(OpenModel):
    default_state: Literal["hold"] = "hold"
    camera_locked_by_default: bool = True
    maximum_primary_motion_groups: int = 1
    maximum_secondary_motion_groups: int = 2
    default_fade_allowed: bool = False
    default_zoom_allowed: bool = False
    decorative_motion_allowed: bool = False
    preferred_representations: list[str] = Field(
        default_factory=lambda: [
            "replacement_pose_sequence",
            "layered_texture_loop",
            "local_deformation",
            "semantic_layer_transform",
            "scientific_overlay",
        ]
    )


class HardCodedStyleCanon(OpenModel):
    canon_id: Literal["experiment-ledger-editorial-ink-v1"] = "experiment-ledger-editorial-ink-v1"
    display_name: str = "Experiment Ledger Editorial Ink"
    reference_origin: str = "user-supplied experiment-style scientific motion reference"
    medium: str = "authored digital editorial ink illustration with restrained flat material planes"
    audience_age: str = "adult general science audience"
    line: LineLanguage = Field(default_factory=LineLanguage)
    color: ColorLanguage = Field(default_factory=ColorLanguage)
    typography: TypographyLanguage = Field(default_factory=TypographyLanguage)
    composition: CompositionLanguage = Field(default_factory=CompositionLanguage)
    motion: MotionLanguage = Field(default_factory=MotionLanguage)
    recurring_ui: list[str] = Field(
        default_factory=lambda: [
            "experiment identifier panel",
            "simulation status panel",
            "single metric panel",
            "stage label",
        ]
    )
    visual_traits: list[str] = Field(
        default_factory=lambda: [
            "mature scientific editorial illustration",
            "fluid authored contours",
            "observational anatomy and material construction",
            "integrated causal environments",
            "paper-like light background",
            "charcoal and blue-gray structural palette",
            "sparse red warning and yellow energy accents",
            "condensed uppercase scientific labels",
            "controlled texture and line imperfections",
        ]
    )
    forbidden: list[str] = Field(
        default_factory=lambda: [
            "child mascot anatomy",
            "oversized round head",
            "capsule limbs",
            "mitten hands",
            "rounded-rectangle buildings as hero art",
            "Microsoft Word shape assembly",
            "Paint-like polygon collage",
            "isolated icon sticker collection",
            "uniform black sticker outline",
            "random micro-details",
            "generic AI glossy rendering",
            "different art style per shot",
            "procedural SVG hero illustration",
            "prompt-only style reset",
            "PowerPoint entrance motion",
        ]
    )
    style_hash: str = ""

    @model_validator(mode="after")
    def lock_identity(self):
        # The LLM may enrich topic-specific direction elsewhere, but it cannot
        # rename or mutate the studio's hard-coded visual identity.
        self.canon_id = "experiment-ledger-editorial-ink-v1"
        self.display_name = "Experiment Ledger Editorial Ink"
        return self


class ArtDirectionBible(OpenModel):
    schema_version: str = "10.0"
    topic: str
    canon_id: str = "experiment-ledger-editorial-ink-v1"
    locked_canon: HardCodedStyleCanon = Field(default_factory=HardCodedStyleCanon)
    visual_thesis: str = ""
    topic_specific_motifs: list[str] = Field(default_factory=list)
    recurring_symbols: list[str] = Field(default_factory=list)
    scientific_readability_rules: list[str] = Field(default_factory=list)
    anti_ai_rules: list[str] = Field(default_factory=list)
    continuity_priorities: list[str] = Field(default_factory=list)
    reference_board_path: str = ""
    raw_llm_output: Any = None

    @model_validator(mode="after")
    def enforce_locked_canon(self):
        self.canon_id = "experiment-ledger-editorial-ink-v1"
        self.locked_canon = HardCodedStyleCanon()
        return self


class CanonAnchor(OpenModel):
    anchor_id: str
    role: Literal[
        "reference_video_board", "master_style_anchor", "approved_scene", "subject_sheet", "environment_sheet"
    ]
    path: str
    scene_id: str = ""
    notes: str = ""
    approved: bool = True


class RecurringSubjectCanon(OpenModel):
    subject_id: str
    description: str
    immutable_traits: list[str] = Field(default_factory=list)
    allowed_variations: list[str] = Field(default_factory=list)
    reference_anchor_ids: list[str] = Field(default_factory=list)


class ContinuityCanon(OpenModel):
    schema_version: str = "10.0"
    canon_id: str = "experiment-ledger-editorial-ink-v1"
    palette_lock: dict[str, Any] = Field(default_factory=dict)
    line_lock: dict[str, Any] = Field(default_factory=dict)
    typography_lock: dict[str, Any] = Field(default_factory=dict)
    ui_lock: list[str] = Field(default_factory=list)
    recurring_subjects: list[RecurringSubjectCanon] = Field(default_factory=list)
    anchors: list[CanonAnchor] = Field(default_factory=list)
    continuity_rules: list[str] = Field(default_factory=list)
    prohibited_drift: list[str] = Field(default_factory=list)


class DepthPlane(OpenModel):
    plane_id: str
    depth: Literal["foreground", "midground", "background", "atmosphere", "overlay"]
    contents: str
    value_range: str = ""
    line_weight_role: str = ""
    movement_role: str = "hold"


class PerspectivePlan(OpenModel):
    camera_height: str = "eye level"
    view: str = "three-quarter editorial view"
    lens_language: str = "moderate perspective without photographic distortion"
    horizon_y: float = 0.50
    vanishing_points: list[tuple[float, float]] = Field(default_factory=list)
    scale_logic: str = "consistent within scene"


class FigureConstruction(OpenModel):
    figure_id: str
    role: str = "human subject"
    proportion_heads: float = 7.2
    body_orientation: str = "three-quarter"
    weight_distribution: str = "credible weight-bearing stance"
    gesture_line: str = ""
    joint_logic: list[str] = Field(default_factory=list)
    hand_construction: str = "palm mass, knuckle plane, finger direction and thumb lock"
    clothing_logic: str = "folds follow tension, gravity and joint compression"
    forbidden_shortcuts: list[str] = Field(
        default_factory=lambda: [
            "circle-joint puppet",
            "tube limbs",
            "oval fist",
            "front-facing torso with side-facing action",
        ]
    )


class MaterialMarkPlan(OpenModel):
    material: str
    visual_cues: list[str] = Field(default_factory=list)
    line_marks: list[str] = Field(default_factory=list)
    value_behavior: str = ""
    motion_behavior: str = ""


class MotionSeam(OpenModel):
    seam_id: str
    subject: str
    method: Literal[
        "replacement_pose", "semantic_mask", "layer_transform", "local_deformation", "texture_loop", "overlay_only"
    ]
    region: str
    resting_overlap_rule: str
    required_variants: list[str] = Field(default_factory=list)


class SceneIllustrationArchitecture(OpenModel):
    schema_version: str = "10.0"
    scene_id: str
    beat_id: str = ""
    narrative_claim: str = ""
    visual_thesis: str = ""
    canvas: tuple[int, int] = (1080, 1920)
    composition_route: list[str] = Field(default_factory=list)
    perspective: PerspectivePlan = Field(default_factory=PerspectivePlan)
    depth_planes: list[DepthPlane] = Field(default_factory=list)
    focal_subject: str = ""
    secondary_subjects: list[str] = Field(default_factory=list)
    figure_construction: list[FigureConstruction] = Field(default_factory=list)
    material_marks: list[MaterialMarkPlan] = Field(default_factory=list)
    negative_space: str = ""
    contour_architecture: list[str] = Field(default_factory=list)
    color_script: list[str] = Field(default_factory=list)
    scientific_annotations: list[str] = Field(default_factory=list)
    motion_seams: list[MotionSeam] = Field(default_factory=list)
    animation_representation: list[str] = Field(default_factory=list)
    required_pose_variants: list[str] = Field(default_factory=list)
    director_notes: list[str] = Field(default_factory=list)
    prohibited_visual_shortcuts: list[str] = Field(default_factory=list)
    beauty_frame_first: Literal[True] = True
    semantic_split_after_approval: Literal[True] = True
    procedural_hero_allowed: Literal[False] = False
    raw_llm_output: Any = None


class ReferenceRequirement(OpenModel):
    reference_id: str
    purpose: Literal["style", "pose", "anatomy", "environment", "material", "lighting", "continuity"]
    query: str
    priority: int = 1
    use_rule: str = "observe structure only; do not copy composition"
    source_preference: list[str] = Field(
        default_factory=lambda: ["user reference", "licensed stock", "generated reference"]
    )


class ReferencePack(OpenModel):
    scene_id: str
    style_anchor_ids: list[str] = Field(default_factory=list)
    requirements: list[ReferenceRequirement] = Field(default_factory=list)
    board_path: str = ""
    previous_approved_scene: str = ""
    subject_anchor_paths: list[str] = Field(default_factory=list)
    environment_anchor_paths: list[str] = Field(default_factory=list)


class FluxStyleFingerprint(OpenModel):
    schema_version: str = "10.0"
    canon_id: str = "experiment-ledger-editorial-ink-v1"
    medium_sentence: str = ""
    contour_sentence: str = ""
    anatomy_sentence: str = ""
    palette_sentence: str = ""
    composition_sentence: str = ""
    texture_sentence: str = ""
    anti_ai_sentence: str = ""
    immutable_prompt: str = ""
    fingerprint_hash: str = ""


class FluxPromptBlock(OpenModel):
    block_id: str
    role: Literal[
        "image_type",
        "subject_action",
        "environment",
        "composition",
        "style",
        "anatomy_material",
        "lighting_color",
        "continuity",
        "edit_scope",
        "animation_preparation",
        "negative_constraints",
    ]
    text: str
    immutable: bool = False
    priority: int = 1


class FluxPromptStack(OpenModel):
    schema_version: str = "10.0"
    purpose: Literal["master_anchor", "beauty_frame", "revision", "pose_variant", "layer_isolation"]
    blocks: list[FluxPromptBlock] = Field(default_factory=list)
    compiled_prompt: str = ""
    immutable_style_hash: str = ""
    scene_delta_hash: str = ""
    word_count: int = 0
    init_strategy: Literal[
        "reference_board", "master_style_anchor", "previous_approved_scene", "approved_beauty_frame", "current_revision"
    ] = "master_style_anchor"


class FluxPromptDiagnostics(OpenModel):
    valid: bool = True
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    word_count: int = 0
    immutable_style_hash: str = ""
    has_explicit_subject: bool = False
    has_explicit_preservation: bool = False
    has_local_edit_scope: bool = False
    vague_language_found: list[str] = Field(default_factory=list)
    contradictory_language_found: list[str] = Field(default_factory=list)


class FluxGenerationPolicy(OpenModel):
    schema_version: str = "10.0"
    model: str = "flux-kontext-pro"
    aspect_ratio: str = "9:16"
    prompt_upsampling: bool = False
    safety_tolerance: int = 2
    output_format: Literal["png", "jpeg"] = "png"
    seed_base: int = 240921
    max_initial_prompt_words: int = 380
    min_initial_prompt_words: int = 80
    max_edit_prompt_words: int = 180
    max_adjustments_per_pass: int = 2
    one_reference_only: Literal[True] = True


class FluxRevisionPass(OpenModel):
    pass_id: str
    target_regions: list[str] = Field(default_factory=list)
    adjustments: list[ConcreteAdjustment] = Field(default_factory=list)
    instruction: str = ""
    preserve: list[str] = Field(default_factory=list)
    priority: Literal["critical", "high", "medium", "low"] = "high"


class FluxConsistencyState(OpenModel):
    schema_version: str = "10.0"
    canon_id: str = "experiment-ledger-editorial-ink-v1"
    immutable_style_hash: str = ""
    master_anchor_path: str = ""
    previous_approved_scene: str = ""
    seed_family_base: int = 240921
    approved_scene_paths: list[str] = Field(default_factory=list)
    recurring_subject_descriptors: list[str] = Field(default_factory=list)
    drift_watchlist: list[str] = Field(default_factory=list)


class DrawingBrief(OpenModel):
    schema_version: str = "10.0"
    brief_id: str
    scene_id: str
    canon_id: str = "experiment-ledger-editorial-ink-v1"
    purpose: Literal["master_anchor", "beauty_frame", "revision", "pose_variant", "layer_isolation"] = "beauty_frame"
    positive_prompt: str
    negative_prompt: str
    kontext_instruction: str
    anchor_board_path: str = ""
    init_image_path: str = ""
    output_path: str = ""
    aspect_ratio: str = "9:16"
    seed: int | None = None
    preserve: list[str] = Field(default_factory=list)
    change: list[str] = Field(default_factory=list)
    semantic_requirements: list[str] = Field(default_factory=list)
    motion_requirements: list[str] = Field(default_factory=list)
    prompt_stack: FluxPromptStack | None = None
    compiled_prompt: str = ""
    prompt_diagnostics: FluxPromptDiagnostics | None = None
    style_fingerprint_hash: str = ""
    init_strategy: str = "master_style_anchor"
    prompt_upsampling: bool = False
    safety_tolerance: int = 2
    output_format: Literal["png", "jpeg"] = "png"
    request_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def enforce_non_procedural(self):
        joined = (self.positive_prompt + " " + self.kontext_instruction).lower()
        if "procedural svg hero" in joined or "assemble from primitive" in joined:
            raise ValueError("DrawingBrief may not request procedural hero art")
        return self


class ConcreteAdjustment(OpenModel):
    adjustment_id: str
    target_region: str
    problem: str
    instruction: str
    preserve: list[str] = Field(default_factory=list)
    priority: Literal["critical", "high", "medium", "low"] = "high"


class DirectorChangeOrder(OpenModel):
    schema_version: str = "10.0"
    scene_id: str
    revision_number: int = 1
    status: Literal["approve", "revise", "requires_human_or_vision_director"] = "revise"
    diagnosis: str = ""
    adjustments: list[ConcreteAdjustment] = Field(default_factory=list)
    continuity_corrections: list[str] = Field(default_factory=list)
    animation_readiness_corrections: list[str] = Field(default_factory=list)
    immutable_preserve_list: list[str] = Field(default_factory=list)
    revised_kontext_instruction: str = ""

    @model_validator(mode="after")
    def concrete_when_revising(self):
        if self.status == "revise" and not self.adjustments:
            raise ValueError("A revision order must contain concrete adjustments")
        return self


class RevisionRecord(OpenModel):
    scene_id: str
    revision_number: int
    draft_path: str
    change_order: DirectorChangeOrder
    output_path: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class BeautyFrame(OpenModel):
    scene_id: str
    image_path: str
    approved: bool = False
    approval_source: str = ""
    style_anchor_ids: list[str] = Field(default_factory=list)
    revision_records: list[RevisionRecord] = Field(default_factory=list)
    prompt_hash: str = ""
    provider: str = "flux-kontext-pro"
    seed: int | None = None


class SemanticLayer(OpenModel):
    layer_id: str
    description: str
    source_region: str
    extraction_method: Literal["external_mask", "kontext_isolation", "artist_layer", "full_frame", "scientific_overlay"]
    z_index: int = 0
    locked: bool = True
    mask_path: str = ""
    image_path: str = ""
    pose_variant_paths: list[str] = Field(default_factory=list)


class SemanticLayerContract(OpenModel):
    scene_id: str
    beauty_frame_path: str
    layers: list[SemanticLayer] = Field(default_factory=list)
    separation_occurs_after_approval: Literal[True] = True
    preserve_original_beauty: Literal[True] = True
    extraction_notes: list[str] = Field(default_factory=list)


class MotionEvent(OpenModel):
    event_id: str
    reason_id: str
    target_layer: str
    representation: Literal[
        "hold",
        "translate",
        "rotate",
        "scale",
        "opacity",
        "replacement_pose",
        "texture_loop",
        "local_deformation",
        "mask_reveal",
        "overlay_draw",
    ]
    start_frame: int
    end_frame: int
    easing: str = "linear"
    parameters: dict[str, Any] = Field(default_factory=dict)
    secondary: bool = False

    @model_validator(mode="after")
    def validate_event(self):
        if not self.reason_id.strip():
            raise ValueError("MotionEvent requires reason_id")
        if self.end_frame <= self.start_frame:
            raise ValueError("MotionEvent end_frame must be after start_frame")
        return self


class AnimationPlan(OpenModel):
    scene_id: str
    fps: int = 30
    duration_frames: int
    camera_locked: bool = True
    events: list[MotionEvent] = Field(default_factory=list)
    audio_sync: dict[str, Any] = Field(default_factory=dict)
    hold_regions: list[str] = Field(default_factory=list)
    maximum_simultaneous_groups: int = 3


class HybridLayer(OpenModel):
    layer_id: str
    kind: Literal["raster", "svg_overlay", "pose_sequence", "mask", "video_clip"]
    path: str
    z_index: int = 0
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0)
    opacity: float = 1.0
    blend_mode: str = "normal"
    mask_path: str = ""
    pose_paths: list[str] = Field(default_factory=list)


class HybridScenePackage(OpenModel):
    scene_id: str
    duration_frames: int
    fps: int = 30
    canvas: tuple[int, int] = (1080, 1920)
    narration: str = ""
    headline: str = ""
    background: str = "#FAFAF7"
    layers: list[HybridLayer] = Field(default_factory=list)
    animation: AnimationPlan
    voice_path: str = ""
    transition: str = "cut"
    temporal_clip_path: str = ""
    temporal_backend: str = ""


class PipelineResult(OpenModel):
    schema_version: str = "10.0"
    topic: str
    mode: Literal["plan_only", "live_generation", "rendered"]
    run_dir: str
    reference_board: str = ""
    art_direction_bible: str = ""
    continuity_canon: str = ""
    architectures: str = ""
    drawing_briefs: str = ""
    beauty_frames: str = ""
    semantic_contracts: str = ""
    animation_plans: str = ""
    video: str = ""
    warnings: list[str] = Field(default_factory=list)


def format_numeric_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:g}" if isinstance(value, float) else str(value)
    if isinstance(value, dict):
        amount = value.get("value", value.get("amount", value.get("number", "")))
        unit = value.get("unit", value.get("units", ""))
        context = value.get("context", value.get("label", value.get("description", "")))
        main = " ".join(str(x).strip() for x in (amount, unit) if str(x).strip())
        return f"{main} ({context})" if main and str(context).strip() else main or json.dumps(value, ensure_ascii=False)
    return str(value)


# ---------------------------------------------------------------------------
# V10 production runtime, continuity retrieval, candidate tournament and
# temporal-control contracts.
# ---------------------------------------------------------------------------


class ProviderCapability(OpenModel):
    name: str
    available: bool = True
    supports_streaming: bool = False
    supports_images: bool = False
    supports_video: bool = False
    supports_audio: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderSpec(OpenModel):
    provider_id: str
    provider_type: Literal[
        "llm",
        "vision",
        "image",
        "tts",
        "transcription",
        "segmentation",
        "temporal_video",
        "stock",
        "publisher",
        "renderer",
    ]
    implementation: str
    priority: int = 100
    enabled: bool = True
    capabilities: list[ProviderCapability] = Field(default_factory=list)
    config: dict[str, Any] = Field(default_factory=dict)


class StageRecord(OpenModel):
    stage_id: str
    status: Literal["pending", "running", "completed", "failed", "skipped", "interrupted", "stale"] = "pending"
    attempt: int = 0
    input_hash: str = ""
    output_path: str = ""
    output_sha256: str = ""
    started_at: str = ""
    completed_at: str = ""
    error_type: str = ""
    error_message: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class JobManifest(OpenModel):
    schema_version: str = "10.1"
    job_id: str
    topic: str
    run_dir: str
    status: Literal["created", "running", "completed", "failed", "cancelled"] = "created"
    config_hash: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    stages: dict[str, StageRecord] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class AssetRecord(OpenModel):
    asset_id: str
    path: str
    asset_type: Literal[
        "style_anchor",
        "approved_scene",
        "subject_view",
        "environment",
        "material",
        "pose",
        "beauty_frame",
        "control_sketch",
        "mask",
        "video_clip",
    ]
    approved: bool = True
    scene_id: str = ""
    subject_ids: list[str] = Field(default_factory=list)
    environment_id: str = ""
    camera_view: str = ""
    perspective: str = ""
    chronology_index: int = -1
    role_tags: list[str] = Field(default_factory=list)
    style_hash: str = ""
    identity_hash: str = ""
    composition_hash: str = ""
    quality_score: float = 0.0
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict[str, Any] = Field(default_factory=dict)


class AssetQuery(OpenModel):
    scene_id: str
    subject_ids: list[str] = Field(default_factory=list)
    environment_id: str = ""
    camera_view: str = ""
    perspective: str = ""
    desired_types: list[str] = Field(default_factory=list)
    required_roles: list[str] = Field(default_factory=list)
    style_hash: str = ""
    chronology_index: int = -1
    maximum_results: int = 8


class RankedAsset(OpenModel):
    asset: AssetRecord
    score: float
    reasons: list[str] = Field(default_factory=list)


class ReferenceSelection(OpenModel):
    scene_id: str
    query: AssetQuery
    selected: list[RankedAsset] = Field(default_factory=list)
    board_path: str = ""
    selection_notes: list[str] = Field(default_factory=list)


class CharacterView(OpenModel):
    view_id: str
    angle: Literal["front", "three_quarter", "side", "rear", "expression", "action", "detail"]
    path: str
    approved: bool = True
    notes: str = ""


class CharacterProfile(OpenModel):
    subject_id: str
    display_name: str
    immutable_traits: list[str] = Field(default_factory=list)
    proportion_rules: list[str] = Field(default_factory=list)
    wardrobe_rules: list[str] = Field(default_factory=list)
    palette_roles: dict[str, str] = Field(default_factory=dict)
    views: list[CharacterView] = Field(default_factory=list)
    style_hash: str = ""


class ShotState(OpenModel):
    scene_id: str
    first_frame_description: str
    last_frame_description: str
    invariant_elements: list[str] = Field(default_factory=list)
    changed_elements: list[str] = Field(default_factory=list)
    motion_bridge: list[str] = Field(default_factory=list)
    camera_motion: str = "locked"
    preserve_regions: list[str] = Field(default_factory=list)
    control_sketch_required: bool = False
    control_sketch_regions: list[str] = Field(default_factory=list)
    temporal_complexity: Literal["hold", "simple", "articulated", "deformation", "organic"] = "simple"


class CandidateFrame(OpenModel):
    candidate_id: str
    scene_id: str
    image_path: str
    seed: int | None = None
    prompt_hash: str = ""
    provider: str = "flux-kontext-pro"
    metrics: dict[str, float] = Field(default_factory=dict)


class CandidateScore(OpenModel):
    candidate_id: str
    total_score: float
    style_consistency: float = 0.0
    subject_consistency: float = 0.0
    composition_fitness: float = 0.0
    motion_readiness: float = 0.0
    causal_clarity: float = 0.0
    penalties: list[str] = Field(default_factory=list)
    rationale: str = ""


class CandidateTournamentResult(OpenModel):
    scene_id: str
    candidates: list[CandidateFrame] = Field(default_factory=list)
    scores: list[CandidateScore] = Field(default_factory=list)
    winner_id: str = ""
    winner_path: str = ""
    comparison_board: str = ""
    ranking_source: str = ""


class ControlSketchSpec(OpenModel):
    sketch_id: str
    scene_id: str
    frame_role: Literal["first", "last", "intermediate"]
    output_path: str
    regions: list[str] = Field(default_factory=list)
    line_color: str = "#FFFFFF"
    background_color: str = "#000000"
    instructions: list[str] = Field(default_factory=list)


class TemporalRequest(OpenModel):
    scene_id: str
    backend_preference: list[str] = Field(default_factory=list)
    beauty_start: str
    beauty_end: str = ""
    control_sketch_start: str = ""
    control_sketch_end: str = ""
    preserve_masks: list[str] = Field(default_factory=list)
    motion_prompt: str = ""
    duration_frames: int = 1
    fps: int = 30
    complexity: Literal["hold", "simple", "articulated", "deformation", "organic"] = "simple"
    output_path: str = ""


class TemporalResult(OpenModel):
    scene_id: str
    backend_id: str
    output_path: str
    success: bool = True
    deterministic: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
