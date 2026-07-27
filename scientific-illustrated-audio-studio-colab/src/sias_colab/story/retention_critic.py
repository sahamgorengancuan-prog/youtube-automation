"""Retention Critic: detects retention killers and applies AT MOST ONE
automatic revision. Never an endless critique loop."""

from __future__ import annotations

import difflib
import re
from typing import Any

from sias.config import SIASConfig
from sias.schemas import SpokenScript, StoryBeat
from sias.story.spoken_script import assemble_script

_GENERIC_ENDINGS = re.compile(r"\b(in conclusion|to summarize|as explained above)\b", re.IGNORECASE)


def critique(script: SpokenScript, beats: list[StoryBeat]) -> list[str]:
    issues: list[str] = []
    if script.sentences:
        first = script.sentences[0]
        if len(first.split()) > 18:
            issues.append("slow_opening: first sentence exceeds 18 words")
        if not any(ch in first for ch in "?—") and "imagine" not in first.lower() and "stops" not in first.lower():
            issues.append("slow_opening: cold open lacks a curiosity trigger")
    roles = [b.role for b in beats]
    if "explanation" in roles and "fact_1" in roles and roles.index("explanation") < roles.index("fact_1"):
        issues.append("explanation_before_curiosity: mechanism precedes the examples")
    narrations = [b.narration.strip().lower() for b in beats if b.narration.strip()]
    for i, a in enumerate(narrations):
        for b in narrations[i + 1 :]:
            if difflib.SequenceMatcher(None, a, b).ratio() > 0.9:
                issues.append("repeated_fact: two beats narrate nearly the same thing")
    overlong = [s for s in script.sentences if len(s.split()) > 22]
    if overlong:
        issues.append(f"overlong_sentences: {len(overlong)} sentence(s) exceed 22 words")
    if script.sentences and _GENERIC_ENDINGS.search(script.sentences[-1]):
        issues.append("generic_conclusion: ending uses summary boilerplate")
    if len(beats) >= 8 and not beats[6].narration:
        issues.append("delayed_payoff: gasp reveal beat is empty")
    return issues


def _revise_beats(beats: list[StoryBeat], issues: list[str]) -> list[StoryBeat]:
    revised = [b.model_copy(deep=True) for b in beats]
    for issue in issues:
        if issue.startswith("slow_opening") and revised:
            words = revised[0].narration.split()
            if len(words) > 18:
                revised[0].narration = " ".join(words[:16]).rstrip(",;") + "."
        if issue.startswith("generic_conclusion") and revised:
            revised[-1].narration = re.sub(
                _GENERIC_ENDINGS, "here's the part worth remembering:", revised[-1].narration
            )
        if issue.startswith("overlong_sentences"):
            for beat in revised:
                sentences = re.split(r"(?<=[.!?])\s+", beat.narration)
                fixed = []
                for s in sentences:
                    w = s.split()
                    fixed.append(" ".join(w[:20]).rstrip(",;") + ("." if len(w) > 20 else "") if len(w) > 22 else s)
                beat.narration = " ".join(x for x in fixed if x)
    return revised


def critique_and_revise(script: SpokenScript, beats: list[StoryBeat], cfg: SIASConfig) -> dict[str, Any]:
    """One critique pass; if issues found, ONE automatic revision; re-critique
    for the record and stop (bounded by design)."""
    issues = critique(script, beats)
    if not issues:
        return {"issues": [], "revised": False, "script": script.model_dump(),
                "beats": [b.model_dump() for b in beats]}
    revised_beats = _revise_beats(beats, issues)
    revised_script = assemble_script(revised_beats, cfg.story.catchphrase, cfg.project.language)
    return {
        "issues": issues,
        "revised": True,
        "remaining_issues": critique(revised_script, revised_beats),
        "script": revised_script.model_dump(),
        "beats": [b.model_dump() for b in revised_beats],
    }
