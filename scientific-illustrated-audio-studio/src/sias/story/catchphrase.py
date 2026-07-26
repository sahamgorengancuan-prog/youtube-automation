"""Catchphrase rules: exactly once, near the intuition→mechanism transition,
never the first sentence, never inside the payoff."""

from __future__ import annotations

from ..schemas import SpokenScript


def count_occurrences(text: str, phrase: str) -> int:
    return text.lower().count(phrase.lower()) if phrase else 0


def placement_issues(script: SpokenScript, phrase: str, payoff_beat_id: str = "B08") -> list[str]:
    issues: list[str] = []
    n = count_occurrences(script.full_text, phrase)
    if n == 0:
        issues.append("catchphrase missing (must appear exactly once)")
    elif n > 1:
        issues.append(f"catchphrase appears {n} times (max 1)")
    if script.sentences and phrase.lower() in script.sentences[0].lower():
        issues.append("catchphrase must not be the first sentence")
    payoff = " ".join(script.beat_sentences.get(payoff_beat_id, []))
    if phrase and phrase.lower() in payoff.lower():
        issues.append("catchphrase must not be repeated in the payoff")
    return issues
