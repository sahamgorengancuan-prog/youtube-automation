"""Spoken script assembly + constraint validation (§5.3): spoken language,
45–75 s, 145–165 wpm, 8–18-word sentences, no lecture phrases."""

from __future__ import annotations

import re

from ..schemas import SpokenScript, StoryBeat
from .catchphrase import count_occurrences

FORBIDDEN_PHRASES = [
    "in this video",
    "today we will learn",
    "as an ai",
    "welcome back to the channel",
]

_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def assemble_script(
    beats: list[StoryBeat],
    catchphrase: str,
    language: str = "en",
    wpm: float = 155.0,
    catchphrase_beat_id: str = "B05",
) -> SpokenScript:
    """Join beat narrations; inject the catchphrase once at the start of the
    intuition→mechanism beat (default B05, the explanation)."""
    beat_sentences: dict[str, list[str]] = {}
    parts: list[str] = []
    for beat in beats:
        text = beat.narration.strip()
        if beat.beat_id == catchphrase_beat_id and catchphrase and count_occurrences(text, catchphrase) == 0:
            text = f"{catchphrase} {text}"
        sentences = [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]
        beat_sentences[beat.beat_id] = sentences
        parts.append(text)
    full = " ".join(parts)
    words = full.split()
    return SpokenScript(
        language=language,
        full_text=full,
        sentences=[s for group in beat_sentences.values() for s in group],
        beat_sentences=beat_sentences,
        word_count=len(words),
        est_duration_s=round(len(words) / (wpm / 60.0), 2),
        catchphrase_count=count_occurrences(full, catchphrase),
    )


def validate_script(
    script: SpokenScript,
    min_duration_s: float = 45.0,
    max_duration_s: float = 75.0,
    wpm_min: float = 145.0,
    wpm_max: float = 165.0,
    sentence_min_words: int = 8,
    sentence_max_words: int = 18,
) -> list[str]:
    issues: list[str] = []
    if not (min_duration_s <= script.est_duration_s <= max_duration_s):
        issues.append(
            f"estimated duration {script.est_duration_s}s outside {min_duration_s}..{max_duration_s}s"
        )
    if script.est_duration_s > 0:
        wpm = script.word_count / (script.est_duration_s / 60.0)
        if not (wpm_min - 1 <= wpm <= wpm_max + 1):
            issues.append(f"wpm {wpm:.0f} outside {wpm_min}..{wpm_max}")
    lower = script.full_text.lower()
    for phrase in FORBIDDEN_PHRASES:
        if phrase in lower:
            issues.append(f"forbidden phrase present: {phrase!r}")
    long_or_short = [
        s for s in script.sentences if not (sentence_min_words <= len(s.split()) <= sentence_max_words)
    ]
    if len(long_or_short) > max(2, len(script.sentences) // 3):
        issues.append(
            f"{len(long_or_short)} of {len(script.sentences)} sentences outside "
            f"{sentence_min_words}..{sentence_max_words} words"
        )
    return issues
