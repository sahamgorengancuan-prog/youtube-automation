"""Voice bible defaults — a clever friend, not an announcer."""

from __future__ import annotations

from ..schemas import VoiceBible

DELIVERY = [
    "curious",
    "warm",
    "lightly amused",
    "natural contractions",
    "varied sentence rhythm",
    "brisk hook",
    "relaxed explanation",
    "short anticipatory pause before reveals",
    "clean reveal landing",
]

FORBIDDEN = [
    "announcer voice",
    "movie-trailer cadence",
    "robotic equal pauses",
    "constant excitement",
    "bullet-list reading",
]


def build_voice_bible(voice: str = "cedar", model: str = "gpt-4o-mini-tts") -> VoiceBible:
    return VoiceBible(delivery=list(DELIVERY), forbidden=list(FORBIDDEN), voice=voice, model=model)
