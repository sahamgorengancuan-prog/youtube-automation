"""Hook tournament v2: adds the scale-first variant and an explicit
clickbait-risk score on top of the engine's scorer."""

from __future__ import annotations

from typing import Any

from sias.schemas import HookVariant
from sias.story.hooks import generate_hooks, score_hook


class HookVariantV2(HookVariant):
    kind: str  # widen: consequence_first | contradiction_first | scale_first
    clickbait_risk: float = 0.0


def generate_hooks_v2(topic: str, llm: Any = None) -> list[HookVariantV2]:
    base = [HookVariantV2(**h.model_dump()) for h in generate_hooks(topic, llm)]
    stem = topic.rstrip("?").strip().lower()
    scale = HookVariantV2(
        hook_id="H3",
        kind="scale_first",
        text=f"Take {stem} and multiply it across a whole year — the numbers stop being weather.",
    )
    score_hook(scale)
    base.append(scale)
    for h in base:
        h.clickbait_risk = round(1.0 - h.scores.get("overclaim_avoidance", 0.5), 4)
    return base


def select_hook_v2(hooks: list[HookVariantV2]) -> HookVariantV2:
    if not hooks:
        raise ValueError("no hooks")
    return sorted(hooks, key=lambda h: (-h.total, h.clickbait_risk, h.hook_id))[0]
