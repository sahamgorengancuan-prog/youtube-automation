"""Autonomous topic engine — propose, score, and select what to make next.

The studio should not always wait for a hand-fed topic: given a science domain
(or nothing), it should surface candidate questions, score each on the axes that
actually decide whether a Short is worth making, avoid repeating what it already
covered, and select the strongest — with the reasoning recorded so the choice is
auditable, not a black box.

Scoring axes (each 0..1, combined by a configurable weight):

* **scientific_richness** — is there real mechanism/number/cause to explain (not
  a one-line trivia answer)?
* **visual_potential** — can it be *shown* (a process, a transformation, a scale)
  rather than only narrated?
* **novelty** — how far is it from topics already produced (history) and from
  generic phrasing?
* **audience_appeal** — curiosity pull of the framing ("what happens if…",
  "why does…").
* **safety** — penalize framings likely to trip content moderation, so the run
  does not crash downstream on a rejected image prompt.

Production brainstorms candidates with the reasoning LLM; offline derives them
deterministically from seed domains × question frames, so selection is testable
with no network. Providers are unchanged: reasoning stays OpenAI-only.
"""

from __future__ import annotations

import re
from typing import Any

from .utils import ensure_dir, hash_value, load_json, save_json, slugify

# Default science domains to draw candidates from when no seed is given.
_DEFAULT_DOMAINS = [
    "the human body",
    "space and gravity",
    "the deep ocean",
    "cells and microbiology",
    "climate and weather",
    "energy and thermodynamics",
    "the brain and perception",
    "materials and chemistry",
]

# Question frames with a strong curiosity pull and a clear visual payload.
_FRAMES = [
    "What happens to {d} in extreme conditions?",
    "Why does {d} behave the way it does?",
    "What if {d} suddenly stopped working?",
    "How does {d} actually change over time?",
    "What would you see inside {d}?",
]

# Signals that a topic has real mechanism to explain (visual + scientific).
_RICH_SIGNALS = re.compile(
    r"\b(how|why|what happens|mechanism|process|inside|change|transform|cause|"
    r"energy|force|pressure|cell|molecul|gravit|reaction|flow|scale)\w*",
    re.IGNORECASE,
)
_VISUAL_SIGNALS = re.compile(
    r"\b(inside|see|watch|transform|grow|collapse|flow|move|expand|shrink|"
    r"happens?|change|build|break)\w*",
    re.IGNORECASE,
)
_APPEAL_SIGNALS = re.compile(
    r"\b(what if|why|what happens|would you|suddenly|extreme|actually)\b",
    re.IGNORECASE,
)
# Framings that commonly trip image-provider moderation — penalize, don't ban
# (the safety rewriter can still recover; we just prefer safer candidates).
_RISKY_SIGNALS = re.compile(
    r"\b(gore|gory|graphic|blood|kill|murder|weapon|violent|violence|nsfw|"
    r"explicit|torture|suicide|self-harm)\w*",
    re.IGNORECASE,
)

_DEFAULT_WEIGHTS = {
    "scientific_richness": 0.28,
    "visual_potential": 0.24,
    "novelty": 0.18,
    "audience_appeal": 0.18,
    "safety": 0.12,
}


def _clip01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else round(float(x), 4)


class TopicEngine:
    def __init__(self, llm: Any = None, config: dict[str, Any] | None = None, root: Any = None):
        self.llm = llm
        self.config = config or {}
        self.root = ensure_dir(root) if root is not None else None
        self.weights = {**_DEFAULT_WEIGHTS, **(self.config.get("weights") or {})}
        # History of already-produced topic slugs, for novelty.
        self.history_path = self.config.get("history_path")
        self._history: set[str] = set()
        if self.history_path:
            hist = load_json(self.history_path, []) or []
            self._history = {slugify(str(t), 60) for t in hist}

    # ---- scoring ---------------------------------------------------------
    def score_topic(self, topic: str) -> dict[str, Any]:
        text = (topic or "").strip()
        words = [w for w in re.split(r"\s+", text) if w]
        n = len(words)
        richness = _clip01(
            0.25
            + 0.5 * min(1.0, len(_RICH_SIGNALS.findall(text)) / 3.0)
            + (0.15 if 5 <= n <= 14 else 0.0)
        )
        visual = _clip01(0.2 + 0.6 * min(1.0, len(_VISUAL_SIGNALS.findall(text)) / 2.0))
        appeal = _clip01(0.2 + 0.7 * min(1.0, len(_APPEAL_SIGNALS.findall(text)) / 2.0))
        risky = len(_RISKY_SIGNALS.findall(text))
        safety = _clip01(1.0 - 0.5 * risky)
        # Novelty: unseen in history + not a bare 1-2 word query. An
        # already-produced topic is fully penalized (novelty 0), regardless of
        # phrasing, so it is not re-selected while fresh options remain.
        slug = slugify(text, 60)
        seen = slug in self._history
        novelty = 0.0 if seen else _clip01(0.85 + (0.15 if n >= 5 else 0.0))
        axes = {
            "scientific_richness": richness,
            "visual_potential": visual,
            "novelty": novelty,
            "audience_appeal": appeal,
            "safety": safety,
        }
        composite = round(sum(self.weights[k] * axes[k] for k in axes), 4)
        return {
            "topic": text,
            "slug": slug,
            "axes": axes,
            "composite": composite,
            "already_produced": seen,
        }

    # ---- candidate generation -------------------------------------------
    def _deterministic_candidates(self, seed: str | None, count: int) -> list[str]:
        domains = (
            [seed] if seed else list(self.config.get("domains") or _DEFAULT_DOMAINS)
        )
        out: list[str] = []
        for d in domains:
            for frame in _FRAMES:
                out.append(frame.format(d=d))
                if len(out) >= max(count * 3, count):
                    break
            if len(out) >= max(count * 3, count):
                break
        # Stable de-dupe preserving order.
        seen: set[str] = set()
        uniq = [t for t in out if not (t in seen or seen.add(t))]
        return uniq

    def _llm_candidates(self, seed: str | None, count: int) -> list[str]:
        if self.llm is None:
            return []
        domain = seed or ", ".join(self.config.get("domains") or _DEFAULT_DOMAINS[:4])
        try:
            raw = self.llm.generate_json(
                system=(
                    "You are a science-communication editor picking topics for a "
                    "60-second animated explainer. Propose specific, visual, "
                    "mechanism-rich questions — not trivia. Safe-for-work."
                ),
                prompt=(
                    f"Domain(s): {domain}. Propose {max(count * 2, count)} candidate "
                    "topics as questions a curious person would click. Return JSON "
                    '{"topics": ["...", "..."]}. Each must be showable as animation.'
                ),
                namespace="topic_engine",
                fallback={"topics": []},
            )
            items = (raw or {}).get("topics") if isinstance(raw, dict) else None
            return [str(t).strip() for t in (items or []) if str(t).strip()]
        except Exception:
            return []

    # ---- public API ------------------------------------------------------
    def propose(self, seed: str | None = None, count: int = 6) -> dict[str, Any]:
        candidates = self._llm_candidates(seed, count) or self._deterministic_candidates(
            seed, count
        )
        scored = [self.score_topic(t) for t in candidates]
        # Prefer higher composite; break ties deterministically by slug hash.
        scored.sort(key=lambda s: (-s["composite"], hash_value(s["slug"], 8)))
        # Do not select something already produced unless nothing else remains.
        fresh = [s for s in scored if not s["already_produced"]]
        ranked = fresh or scored
        selected = ranked[0] if ranked else None
        report = {
            "seed": seed,
            "source": "llm" if self.llm is not None and candidates else "deterministic",
            "count": len(scored),
            "weights": self.weights,
            "selected": selected,
            "candidates": scored[: max(count, 1) * 3],
        }
        if self.root is not None:
            save_json(self.root / "topic_candidates.json", report)
        return report

    def record_produced(self, topic: str) -> None:
        """Append a produced topic to the history file so it is not re-selected."""
        if not self.history_path:
            return
        hist = load_json(self.history_path, []) or []
        if topic not in hist:
            hist.append(topic)
            save_json(self.history_path, hist)
        self._history.add(slugify(topic, 60))
