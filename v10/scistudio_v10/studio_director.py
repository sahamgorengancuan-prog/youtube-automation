from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .llm import LLMRouter
from .schemas import (
    ArtDirectionBible,
    CanonAnchor,
    ContinuityCanon,
    RecurringSubjectCanon,
    ResearchPack,
    ScriptPackage,
    Storyboard,
)
from .style_canon import base_bible, build_hard_coded_canon
from .utils import ensure_dir, load_json, save_json


class ExecutiveArtDirector:
    """The LLM is the head of the visual studio, not a post-hoc scorekeeper.

    The studio's house style is hard-coded. The LLM may direct how that style
    expresses a topic, but it cannot replace the canon with a generic or childish
    vocabulary. This keeps style consistent without forcing beauty art into SVG.
    """

    SYSTEM = """You are the Executive Art Director of an institutional scientific animation studio.
The studio's locked visual canon is supplied below. You do not invent a new style for each scene.
Your job is to translate the topic into one coherent visual thesis, recurring motifs, scientific
readability rules, anti-AI drawing instructions and continuity priorities. Return JSON only.
Never recommend procedural SVG hero art, icon collage, mascot anatomy, primitive-shape construction,
or prompt-only generation without visual anchors."""

    CONTINUITY_SYSTEM = """You are the Continuity Director for one scientific animation.
Return JSON only. Build a production canon that makes every shot look authored by one studio:
fixed palette, line behavior, typography, experiment UI, recurring subject construction, environment
vocabulary and explicit drift prohibitions. Use approved frames as visual anchors. Do not redesign the style."""

    def __init__(self, llm: LLMRouter, config: dict[str, Any], root: str | Path):
        self.llm = llm
        self.config = config
        self.root = ensure_dir(root)

    def art_bible(
        self,
        topic: str,
        research: ResearchPack,
        script: ScriptPackage,
        storyboard: Storyboard,
        reference_board_path: str,
        *,
        force: bool = False,
    ) -> ArtDirectionBible:
        path = self.root / "art_direction_bible.json"
        if path.exists() and not force:
            return ArtDirectionBible.model_validate(load_json(path))
        fallback = base_bible(topic, reference_board_path)
        canon = build_hard_coded_canon()
        raw = self.llm.generate_json(
            system=self.SYSTEM,
            prompt=f"""LOCKED HOUSE CANON — DO NOT CHANGE:
{json.dumps(canon.model_dump(mode="json"), ensure_ascii=False, indent=2)}

Topic: {topic}
Research summary: {research.summary[:1800]}
Script: {json.dumps(script.model_dump(mode="json"), ensure_ascii=False)}
Storyboard causal events: {json.dumps([s.model_dump(mode="json") for s in storyboard.scenes], ensure_ascii=False)}
Reference style board: {reference_board_path}

Return only topic-specific additions:
visual_thesis, topic_specific_motifs, recurring_symbols, scientific_readability_rules,
anti_ai_rules and continuity_priorities. Do not return a replacement canon.""",
            namespace="v10_art_bible",
            fallback={
                "visual_thesis": fallback.visual_thesis,
                "topic_specific_motifs": fallback.topic_specific_motifs,
                "recurring_symbols": fallback.recurring_symbols,
                "scientific_readability_rules": fallback.scientific_readability_rules,
                "anti_ai_rules": fallback.anti_ai_rules,
                "continuity_priorities": fallback.continuity_priorities,
            },
            force=force,
        )
        data = raw if isinstance(raw, dict) else {}
        bible = ArtDirectionBible(
            topic=topic,
            locked_canon=canon,
            visual_thesis=str(data.get("visual_thesis", fallback.visual_thesis)),
            topic_specific_motifs=self._list(data.get("topic_specific_motifs", [])),
            recurring_symbols=self._merge(fallback.recurring_symbols, self._list(data.get("recurring_symbols", []))),
            scientific_readability_rules=self._merge(
                fallback.scientific_readability_rules, self._list(data.get("scientific_readability_rules", []))
            ),
            anti_ai_rules=self._merge(fallback.anti_ai_rules, self._list(data.get("anti_ai_rules", []))),
            continuity_priorities=self._merge(
                fallback.continuity_priorities, self._list(data.get("continuity_priorities", []))
            ),
            reference_board_path=reference_board_path,
            raw_llm_output=raw,
        )
        save_json(path, bible)
        return bible

    def continuity_canon(
        self,
        bible: ArtDirectionBible,
        storyboard: Storyboard,
        reference_board_path: str,
        *,
        force: bool = False,
    ) -> ContinuityCanon:
        path = self.root / "continuity_canon.json"
        if path.exists() and not force:
            return ContinuityCanon.model_validate(load_json(path))
        locked = bible.locked_canon
        fallback_subjects = self._fallback_subjects(storyboard)
        raw = self.llm.generate_json(
            system=self.CONTINUITY_SYSTEM,
            prompt=f"""Locked canon:
{json.dumps(locked.model_dump(mode="json"), ensure_ascii=False)}
Visual thesis: {bible.visual_thesis}
Topic motifs: {json.dumps(bible.topic_specific_motifs, ensure_ascii=False)}
Scenes: {json.dumps([s.model_dump(mode="json") for s in storyboard.scenes], ensure_ascii=False)}

Return recurring_subjects, continuity_rules and prohibited_drift.
Recurring subjects must define immutable_traits and allowed_variations.
Do not alter palette, line system, typography or UI.""",
            namespace="v10_continuity",
            fallback={
                "recurring_subjects": [s.model_dump(mode="json") for s in fallback_subjects],
                "continuity_rules": [
                    "Every new scene uses the master style anchor and at least one approved prior scene when available.",
                    "Repeated subjects inherit silhouette, proportions, palette role and material marks from their canon sheet.",
                    "The experiment UI remains optically identical in position, spacing, line weight and typography.",
                    "The current scene may change content but may not reset the rendering language.",
                ],
                "prohibited_drift": [
                    "head-to-body ratio changes",
                    "line weight reset",
                    "new unrelated palette",
                    "different background treatment",
                    "mascot facial simplification",
                    "random outline thickness",
                    "scene-by-scene art-style improvisation",
                    "generic AI cinematic lighting",
                ],
            },
            force=force,
        )
        data = raw if isinstance(raw, dict) else {}
        subjects: list[RecurringSubjectCanon] = []
        for i, item in enumerate(data.get("recurring_subjects", [])):
            try:
                subjects.append(RecurringSubjectCanon.model_validate(item))
            except Exception:
                if i < len(fallback_subjects):
                    subjects.append(fallback_subjects[i])
        if not subjects:
            subjects = fallback_subjects
        canon = ContinuityCanon(
            palette_lock=locked.color.model_dump(mode="json"),
            line_lock=locked.line.model_dump(mode="json"),
            typography_lock=locked.typography.model_dump(mode="json"),
            ui_lock=list(locked.recurring_ui),
            recurring_subjects=subjects,
            anchors=[
                CanonAnchor(
                    anchor_id="reference-video-board",
                    role="reference_video_board",
                    path=reference_board_path,
                    notes="Visual-language reference only; never copy content or composition.",
                )
            ],
            continuity_rules=self._list(data.get("continuity_rules", [])),
            prohibited_drift=self._list(data.get("prohibited_drift", [])),
        )
        save_json(path, canon)
        return canon

    @staticmethod
    def _fallback_subjects(storyboard: Storyboard) -> list[RecurringSubjectCanon]:
        text = " ".join((s.visual_event + " " + s.scientific_claim).lower() for s in storyboard.scenes)
        subjects: list[RecurringSubjectCanon] = []
        if any(word in text for word in ("earth", "planet", "globe", "world")):
            subjects.append(
                RecurringSubjectCanon(
                    subject_id="earth-system",
                    description="Recurring scientific Earth/world system",
                    immutable_traits=[
                        "same continent abstraction",
                        "same charcoal-blue palette",
                        "same contour hierarchy",
                    ],
                    allowed_variations=["rotation", "damage state", "day-night state", "scale within composition"],
                )
            )
        if any(word in text for word in ("city", "building", "infrastructure")):
            subjects.append(
                RecurringSubjectCanon(
                    subject_id="city-system",
                    description="Recurring urban environment family",
                    immutable_traits=[
                        "same window rhythm",
                        "same structural line language",
                        "same material value bands",
                    ],
                    allowed_variations=["damage", "water level", "perspective crop", "density"],
                )
            )
        if any(word in text for word in ("human", "person", "people", "body", "hand")):
            subjects.append(
                RecurringSubjectCanon(
                    subject_id="human-figure-family",
                    description="Adult human figure construction used throughout the video",
                    immutable_traits=[
                        "adult 7-7.5 head proportion",
                        "same facial construction",
                        "same hand anatomy",
                        "same line pressure",
                    ],
                    allowed_variations=["pose", "expression", "wardrobe within fixed palette", "view angle"],
                )
            )
        if not subjects:
            subjects.append(
                RecurringSubjectCanon(
                    subject_id="primary-system",
                    description="The recurring physical system of the hypothetical",
                    immutable_traits=["same construction logic", "same material marks", "same palette role"],
                    allowed_variations=["causal state", "scale", "view angle"],
                )
            )
        return subjects

    @staticmethod
    def _list(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]
        return [str(value).strip()] if str(value).strip() else []

    @staticmethod
    def _merge(a: list[str], b: list[str]) -> list[str]:
        out: list[str] = []
        for item in [*a, *b]:
            if item not in out:
                out.append(item)
        return out
