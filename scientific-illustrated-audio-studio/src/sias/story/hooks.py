"""Hook generation: two variants (consequence-first, contradiction-first),
scored on curiosity/visualizability/truthfulness/naturalness/payoff-consistency/
overclaim-avoidance. Production does not proceed until one hook is selected."""

from __future__ import annotations

import re
from typing import Any

from ..schemas import HookVariant

_CRITERIA = [
    "immediate_curiosity",
    "visualizability",
    "truthfulness",
    "spoken_naturalness",
    "promise_payoff_consistency",
    "overclaim_avoidance",
]

_CLICKBAIT = re.compile(
    r"\b(you won't believe|shocking|doctors hate|number \d+ will|destroyed|insane)\b",
    re.IGNORECASE,
)


def generate_hooks(topic: str, llm: Any = None) -> list[HookVariant]:
    base = topic.rstrip("?").strip()
    if llm is not None:
        data = llm.structured_json(
            system="Write two spoken-style hooks for a science short. No clickbait overclaim.",
            prompt=(
                f"Topic: {topic}. Return JSON {{\"hooks\":[{{\"kind\":\"consequence_first\",\"text\":...}},"
                f"{{\"kind\":\"contradiction_first\",\"text\":...}}]}}"
            ),
            namespace="hooks",
        )
        hooks = [
            HookVariant(hook_id=f"H{i + 1}", kind=h["kind"], text=h["text"])
            for i, h in enumerate(data.get("hooks", []))
        ]
    else:
        hooks = [
            HookVariant(
                hook_id="H1",
                kind="consequence_first",
                text=f"If {base.lower()}, the first thing to break isn't what you'd guess.",
            ),
            HookVariant(
                hook_id="H2",
                kind="contradiction_first",
                text=f"Everyone assumes {base.lower()} is about water. It isn't — not mainly.",
            ),
        ]
    return [score_hook(h) for h in hooks]


def score_hook(hook: HookVariant) -> HookVariant:
    text = hook.text
    words = text.split()
    scores: dict[str, float] = {}
    scores["immediate_curiosity"] = min(1.0, 0.5 + (0.3 if "?" in text or "isn't" in text or "break" in text else 0.1))
    scores["visualizability"] = 0.7 if any(w in text.lower() for w in ("water", "rain", "ground", "break", "fall", "grow")) else 0.5
    scores["truthfulness"] = 0.8 if not _CLICKBAIT.search(text) else 0.3
    scores["spoken_naturalness"] = 0.8 if 8 <= len(words) <= 22 else 0.5
    scores["promise_payoff_consistency"] = 0.75
    scores["overclaim_avoidance"] = 0.2 if _CLICKBAIT.search(text) else 0.9
    hook.scores = scores
    hook.total = round(sum(scores.values()) / len(_CRITERIA), 4)
    return hook


def select_hook(hooks: list[HookVariant]) -> HookVariant:
    if not hooks:
        raise ValueError("no hooks to select from")
    return sorted(hooks, key=lambda h: (-h.total, h.hook_id))[0]
