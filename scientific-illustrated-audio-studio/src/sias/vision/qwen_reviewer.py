"""Qwen structural reviewer: anatomy, identity, composition, asset integrity."""

from __future__ import annotations

from typing import Any

from ..exceptions import ProviderSchemaError
from ..schemas import VisionScore

HARD_FAIL_CODES = [
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

_SCORE_FIELDS = [
    "anatomy",
    "style_fidelity",
    "identity_consistency",
    "composition",
    "science_accuracy",
    "story_clarity",
    "novelty",
]


def structural_rubric(scene_summary: str, identity_name: str) -> str:
    return (
        "You are a STRUCTURAL reviewer for a scientific notebook cartoon still. "
        f"Identity: {identity_name}. Scene: {scene_summary}\n"
        "Score 0..1 on anatomy, style_fidelity, identity_consistency, composition, "
        "science_accuracy, story_clarity, novelty. Anatomy is strict: every human has "
        "one head, one torso, two arms, two legs, connected joints; duplicated or "
        "floating limbs are hard fails. Return STRICT JSON only:\n"
        '{"anatomy":0,"style_fidelity":0,"identity_consistency":0,"composition":0,'
        '"science_accuracy":0,"story_clarity":0,"novelty":0,'
        f'"hard_fail_reasons":[/* codes from {HARD_FAIL_CODES} */],'
        '"repair_instructions":[],"summary":""}'
    )


def parse_vision_score(raw: dict[str, Any], model_name: str) -> VisionScore:
    """Strict parse: every score field must be a number within [0,1]; hard-fail
    codes must be from the taxonomy. Malformed → ProviderSchemaError."""
    for field in _SCORE_FIELDS:
        value = raw.get(field)
        if not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
            raise ProviderSchemaError(
                f"reviewer field {field!r} missing or out of range: {value!r}",
                stage="vision.parse",
            )
    hard = raw.get("hard_fail_reasons", [])
    if not isinstance(hard, list):
        raise ProviderSchemaError("hard_fail_reasons must be a list", stage="vision.parse")
    unknown = [c for c in hard if c not in HARD_FAIL_CODES]
    if unknown:
        raise ProviderSchemaError(f"unknown hard-fail codes: {unknown}", stage="vision.parse")
    return VisionScore(
        model_name=model_name,
        **{f: float(raw[f]) for f in _SCORE_FIELDS},
        hard_fail_reasons=list(hard),
        repair_instructions=[str(x) for x in raw.get("repair_instructions", [])],
        summary=str(raw.get("summary", "")),
    )
