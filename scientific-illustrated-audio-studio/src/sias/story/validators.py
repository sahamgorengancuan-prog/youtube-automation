"""Aggregated story QC: beats + script + catchphrase + first payoff timing."""

from __future__ import annotations

from ..config import SIASConfig
from ..schemas import QCCheck, SpokenScript, StoryBeat
from .beats import validate_beats
from .catchphrase import placement_issues
from .spoken_script import validate_script


def validate_story(script: SpokenScript, beats: list[StoryBeat], cfg: SIASConfig) -> list[QCCheck]:
    checks: list[QCCheck] = []
    for issue in validate_beats(beats, cfg.story.min_scenes, cfg.story.max_scenes):
        checks.append(QCCheck(check_id="beats", level="story", status="FAIL", detail=issue))
    for issue in validate_script(
        script,
        cfg.project.target_duration_min_s,
        cfg.project.target_duration_max_s,
        cfg.story.target_wpm_min,
        cfg.story.target_wpm_max,
    ):
        checks.append(QCCheck(check_id="script", level="story", status="WARN", detail=issue))
    for issue in placement_issues(script, cfg.story.catchphrase):
        checks.append(QCCheck(check_id="catchphrase", level="story", status="FAIL", detail=issue))

    # First payoff deadline: the second beat (first interesting fact) must land
    # within the configured window at the estimated speaking rate.
    if beats and script.word_count:
        first_two = beats[:2]
        words_before_payoff = sum(len(" ".join(script.beat_sentences.get(b.beat_id, [])).split()) for b in first_two[:1])
        seconds = words_before_payoff / (155.0 / 60.0)
        if seconds > cfg.story.first_payoff_deadline_s:
            checks.append(
                QCCheck(
                    check_id="first_payoff",
                    level="story",
                    status="WARN",
                    detail=f"first payoff estimated at {seconds:.1f}s (> {cfg.story.first_payoff_deadline_s}s)",
                )
            )
    if not any(c.status == "FAIL" for c in checks):
        checks.append(QCCheck(check_id="story_overall", level="story", status="PASS"))
    return checks
