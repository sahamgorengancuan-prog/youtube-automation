"""Consensus selector: weighted score, hard-fail veto, disagreement handling.

A candidate is never approved while any reviewer reports a hard fail. Large
reviewer disagreement does not auto-reject — it marks REVIEW_REQUIRED for a
tie-break/human look."""

from __future__ import annotations

from typing import Any

from ..schemas import VISION_WEIGHTS, VisionScore


def weighted_score(score: VisionScore) -> float:
    total = 0.0
    for field, weight in VISION_WEIGHTS.items():
        total += weight * float(getattr(score, field))
    return round(total, 4)


def combine(
    qwen: VisionScore,
    gemini: VisionScore,
    approval_threshold: float = 0.82,
    disagreement_threshold: float = 0.18,
) -> dict[str, Any]:
    q, g = weighted_score(qwen), weighted_score(gemini)
    consensus = round((q + g) / 2.0, 4)
    hard = list(qwen.hard_fail_reasons) + list(gemini.hard_fail_reasons)
    result: dict[str, Any] = {
        "qwen_score": q,
        "gemini_score": g,
        "consensus_score": consensus,
        "hard_fail_reasons": hard,
        "repair_instructions": list(dict.fromkeys(qwen.repair_instructions + gemini.repair_instructions)),
    }
    if hard:
        result["status"] = "REPAIR"
        result["reason"] = f"hard fail(s): {sorted(set(hard))}"
        return result
    if abs(q - g) > disagreement_threshold:
        result["status"] = "REVIEW_REQUIRED"
        result["reason"] = (
            f"reviewer disagreement {abs(q - g):.2f} > {disagreement_threshold}; "
            "tie-break analysis / human inspection required"
        )
        return result
    if consensus >= approval_threshold:
        result["status"] = "APPROVED"
        result["reason"] = f"consensus {consensus} >= {approval_threshold}"
    else:
        result["status"] = "REPAIR"
        result["reason"] = f"consensus {consensus} < {approval_threshold}"
    return result
