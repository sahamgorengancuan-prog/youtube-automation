"""Transition policy: hard cut for surprise, short dissolve for explanation
continuity, page turn only between narrative acts. Never random."""

from __future__ import annotations

SURPRISE_ROLES = {"cold_open", "gasp_reveal"}
EXPLANATION_ROLES = {"explanation", "scale_example"}
ACT_BOUNDARIES = {("fact_3", "explanation"), ("scale_example", "gasp_reveal")}


def transition_for(prev_role: str | None, role: str) -> str:
    if prev_role is None:
        return "cut"
    if (prev_role, role) in ACT_BOUNDARIES:
        return "page_turn"
    if role in SURPRISE_ROLES:
        return "cut"
    if role in EXPLANATION_ROLES:
        return "dissolve"
    return "cut"
