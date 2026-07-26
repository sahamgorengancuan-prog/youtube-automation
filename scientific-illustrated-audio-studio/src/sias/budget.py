"""BudgetLedger — every paid call is estimated, compared with the hard cap,
and refused before it is made when it would exceed the budget. No hidden calls."""

from __future__ import annotations

from typing import Any

from .exceptions import BudgetExceededError
from .schemas import RunBudget


class BudgetLedger:
    def __init__(self, budget: RunBudget, hard_stop: bool = True):
        self.budget = budget
        self.hard_stop = hard_stop
        self.decisions: list[dict[str, Any]] = []

    # ---- estimation ------------------------------------------------------
    def can_afford(self, kind: str, amount: float = 1) -> bool:
        b = self.budget
        if kind == "image":
            return b.image_calls + amount <= b.max_image_calls
        if kind == "vision":
            return b.vision_calls + amount <= b.max_vision_calls
        if kind == "tts_chars":
            return b.tts_characters + amount <= b.max_tts_characters
        return True  # transcription/repair are tracked but uncapped by default

    def charge(self, kind: str, amount: float = 1, note: str = "") -> None:
        """Estimate → compare → record → abort if the cap would be exceeded."""
        allowed = self.can_afford(kind, amount)
        self.decisions.append(
            {"kind": kind, "amount": amount, "allowed": allowed, "note": note}
        )
        if not allowed:
            if self.hard_stop:
                raise BudgetExceededError(
                    f"budget cap for {kind!r} would be exceeded (+{amount})",
                    stage="budget",
                )
            return
        b = self.budget
        if kind == "image":
            b.image_calls += int(amount)
        elif kind == "vision":
            b.vision_calls += int(amount)
        elif kind == "tts_chars":
            b.tts_characters += int(amount)
        elif kind == "transcription_s":
            b.transcription_seconds += float(amount)
        elif kind == "repair":
            b.repair_requests += int(amount)

    def snapshot(self) -> dict[str, Any]:
        return {**self.budget.model_dump(), "decisions": len(self.decisions)}
