"""Core Pydantic schemas — the structured data every stage exchanges.

Structured data over free-form agent prose: prompts, reviews, timings and
manifests are typed models, validated at the boundary."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

HardFailCode = Literal[
    "HF_ANATOMY_MISSING_LIMB",
    "HF_ANATOMY_DUPLICATED_LIMB",
    "HF_ANATOMY_DISCONNECTED_PART",
    "HF_IDENTITY_WRONG_CHARACTER",
    "HF_STYLE_ABSTRACT",
    "HF_STYLE_SURREAL",
    "HF_SCIENCE_CONTRADICTION",
    "HF_COMPOSITION_UNREADABLE",
    "HF_TEXT_HALLUCINATION",
    "HF_ASSET_CORRUPT",
]

BEAT_ROLES = [
    "cold_open",
    "fact_1",
    "fact_2",
    "fact_3",
    "explanation",
    "scale_example",
    "gasp_reveal",
    "payoff",
]

VISION_WEIGHTS = {
    "anatomy": 0.15,
    "style_fidelity": 0.18,
    "identity_consistency": 0.17,
    "composition": 0.12,
    "science_accuracy": 0.15,
    "story_clarity": 0.15,
    "novelty": 0.08,
}


class ResearchClaim(BaseModel):
    claim_id: str
    statement: str
    sources: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    caveat: str = ""


class ResearchPack(BaseModel):
    topic: str
    audience: str = "curious general audience, age 15+"
    claims: list[ResearchClaim] = Field(default_factory=list)
    summary: str = ""


class HookVariant(BaseModel):
    hook_id: str
    kind: Literal["consequence_first", "contradiction_first"]
    text: str
    scores: dict[str, float] = Field(default_factory=dict)
    total: float = 0.0


class StoryBeat(BaseModel):
    beat_id: str
    role: str
    summary: str = ""
    narration: str = ""
    emotional_from: str = ""
    emotional_to: str = ""
    claim_refs: list[str] = Field(default_factory=list)


class SpokenScript(BaseModel):
    language: str = "en"
    full_text: str = ""
    sentences: list[str] = Field(default_factory=list)
    beat_sentences: dict[str, list[str]] = Field(default_factory=dict)
    word_count: int = 0
    est_duration_s: float = 0.0
    catchphrase_count: int = 0


class SceneSpec(BaseModel):
    scene_id: str
    beat_role: str = ""
    narration: str = ""
    visual_objective: str = ""
    composition: str = ""
    characters: list[str] = Field(default_factory=list)
    props: list[str] = Field(default_factory=list)
    scientific_labels: list[str] = Field(default_factory=list)
    visual_joke: str = ""
    reveal_word: str = ""
    continuity_refs: list[str] = Field(default_factory=list)
    actual_start_s: float | None = None
    actual_end_s: float | None = None
    # Institutional panel contract. `panel_metric` carries a real figure only
    # when an evidence-backed claim supplies one — {label, value, unit, alert};
    # left empty the compositor shows a simulation status instead of inventing
    # a measurement. `panel_background` is light | night | split_right.
    panel_title: str = ""
    panel_metric: dict[str, str] = Field(default_factory=dict)
    panel_background: str = ""
    # Layout grammar (see sias.style.composition). Left empty it is derived from
    # the beat role, which generalises across topics the way a subject cannot.
    composition_archetype: str = ""


class StyleBible(BaseModel):
    identity_name: str = "Scientific Notebook Cartoon"
    palette: dict[str, str] = Field(default_factory=dict)
    line: str = "dark graphite/ink contour, mostly uniform width, tiny handmade wobble"
    paper: str = "warm off-white notebook paper, faint square grid, generous margins"
    motifs: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)
    version: str = "1"


class VoiceBible(BaseModel):
    persona: str = "A clever friend who genuinely enjoys explaining strange science."
    delivery: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)
    voice: str = "cedar"
    model: str = "gpt-4o-mini-tts"


class ReferenceAsset(BaseModel):
    ref_id: str
    kind: str
    path: str
    sha256: str = ""
    reason: str = ""


class VisionScore(BaseModel):
    model_name: str = ""
    anatomy: float = 0.0
    style_fidelity: float = 0.0
    identity_consistency: float = 0.0
    composition: float = 0.0
    science_accuracy: float = 0.0
    story_clarity: float = 0.0
    novelty: float = 0.0
    hard_fail_reasons: list[str] = Field(default_factory=list)
    repair_instructions: list[str] = Field(default_factory=list)
    summary: str = ""


class CandidateRecord(BaseModel):
    candidate_id: str
    scene_id: str
    image_path: str = ""
    seed: int = 0
    prompt_hash: str = ""
    qwen: VisionScore | None = None
    gemini: VisionScore | None = None
    consensus_score: float = 0.0
    status: str = "PENDING"


class RepairRequest(BaseModel):
    scene_id: str
    source_candidate_id: str
    hard_fail_codes: list[str] = Field(default_factory=list)
    visible_failures: list[str] = Field(default_factory=list)
    required_corrections: list[str] = Field(default_factory=list)
    preserve_regions: list[str] = Field(default_factory=list)
    forbidden_changes: list[str] = Field(default_factory=list)
    repair_attempt: int = 1


class AlignmentWord(BaseModel):
    word: str
    start_s: float
    end_s: float


class AlignmentSegment(BaseModel):
    text: str
    start_s: float
    end_s: float
    words: list[AlignmentWord] = Field(default_factory=list)


class SceneTiming(BaseModel):
    scene_id: str
    start_s: float
    end_s: float
    reveal_word_time_s: float | None = None


class RenderScene(BaseModel):
    scene_id: str
    image_path: str
    start_s: float
    end_s: float
    motion: str = "hold"
    transition_in: str = "cut"


class RunBudget(BaseModel):
    max_image_calls: int = 30
    max_vision_calls: int = 80
    max_tts_characters: int = 9000
    image_calls: int = 0
    vision_calls: int = 0
    tts_characters: int = 0
    transcription_seconds: float = 0.0
    repair_requests: int = 0


class StageManifest(BaseModel):
    stage_id: str
    status: str = "PENDING"
    input_hash: str = ""
    artifact_path: str = ""
    artifact_sha256: str = ""
    provider: str = ""
    model: str = ""
    references: list[str] = Field(default_factory=list)
    request_count: int = 0
    estimated_cost: float | None = None
    actual_cost: float | None = None
    started_at: str = ""
    finished_at: str = ""
    parent_stage_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class EpisodeManifest(BaseModel):
    episode_id: str
    topic: str = ""
    language: str = "en"
    scenes: list[SceneSpec] = Field(default_factory=list)
    scene_timings: list[SceneTiming] = Field(default_factory=list)
    narration_duration_s: float = 0.0
    video_path: str = ""
    srt_path: str = ""
    qc_status: str = "PENDING"
    warnings: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class QCCheck(BaseModel):
    check_id: str
    level: str = "final"
    status: Literal["PASS", "WARN", "FAIL"] = "PASS"
    detail: str = ""


class QCReport(BaseModel):
    status: Literal["PASS", "PASS_WITH_WARNINGS", "FAIL", "HUMAN_DECISION_REQUIRED"] = "PASS"
    checks: list[QCCheck] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
