"""Gemini editorial reviewer: story clarity, humor, label economy, emotion."""

from __future__ import annotations

from .qwen_reviewer import HARD_FAIL_CODES, parse_vision_score  # noqa: F401 (shared strict parser)


def editorial_rubric(scene_summary: str, identity_name: str) -> str:
    return (
        "You are an EDITORIAL reviewer for a scientific notebook cartoon still. "
        f"Identity: {identity_name}. Scene: {scene_summary}\n"
        "Score 0..1 on anatomy, style_fidelity, identity_consistency, composition, "
        "science_accuracy, story_clarity, novelty. Editorial focus: one hero idea, "
        "curiosity pull, readable labels (short noun phrases only), light humor that "
        "serves the story, safe caption zone. Dense infographic posters and large "
        "hallucinated text blocks are hard fails. Return STRICT JSON only with keys "
        "anatomy, style_fidelity, identity_consistency, composition, science_accuracy, "
        f"story_clarity, novelty, hard_fail_reasons (codes from {HARD_FAIL_CODES}), "
        "repair_instructions, summary."
    )
