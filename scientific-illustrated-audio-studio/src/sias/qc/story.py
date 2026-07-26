"""Story QC wrapper (beats/script/catchphrase/payoff timing)."""

from __future__ import annotations

from ..config import SIASConfig
from ..schemas import QCCheck, SpokenScript, StoryBeat
from ..story.validators import validate_story


def check_story(script: SpokenScript, beats: list[StoryBeat], cfg: SIASConfig) -> list[QCCheck]:
    return validate_story(script, beats, cfg)
