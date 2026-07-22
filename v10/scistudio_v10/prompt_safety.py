"""Prompt safety rewriter — recover from image-provider content moderation.

A single moderated image must not kill a 15-minute production run. When BFL
returns "Request Moderated", this rewrites the offending FLUX prompt into a
safe-for-work, clinically-framed educational illustration prompt that preserves
the scientific intent and the flat-vector explainer style, then the caller
retries. Two backends:

* **LLM rewrite** (OpenAI reasoning): asks the model to reframe the prompt to pass
  moderation while keeping it a non-graphic educational diagram.
* **Deterministic softener** (always available): a rule-based pass that replaces
  violence/gore/injury terms with clinical equivalents, strips intensifiers, and
  prepends a safe-illustration frame. Used as a fallback and, on later attempts,
  stacked on top of the LLM rewrite.

The rewrite is escalating: each attempt softens more. Nothing about the science
is invented — only the *wording* is made moderation-safe; if every attempt still
moderates, the caller raises a scene-scoped error (the run resumes from cache).
"""

from __future__ import annotations

import re
from typing import Any

# Clinical / educational reframings for terms that commonly trip image moderation.
_SOFTEN = {
    r"\bpunch(?:ing|es|ed)?\b": "impact",
    r"\bpunches\b": "impacts",
    r"\bhit(?:ting|s)?\b": "contact",
    r"\bstrik(?:e|ing|es)\b": "contact",
    r"\bsmash(?:ing|es|ed)?\b": "press",
    r"\bblood(?:y|ied)?\b": "tissue",
    r"\bgore\b": "tissue detail",
    r"\bgory\b": "detailed",
    r"\bwound(?:s|ed)?\b": "affected region",
    r"\binjur(?:y|ies|ed)\b": "tissue stress",
    r"\bbroken\b": "stressed",
    r"\bfractur(?:e|es|ed|ing)\b": "stress line",
    r"\bbruis(?:e|es|ed|ing)\b": "mark",
    r"\bviolen(?:t|ce)\b": "mechanical force",
    r"\bfist(?:s)?\b": "hand",
    r"\bweapon(?:s)?\b": "tool",
    r"\bpain(?:ful)?\b": "strain",
    r"\bdestroy(?:ing|ed|s)?\b": "deform",
    r"\battack(?:ing|ed|s)?\b": "act on",
    r"\bbeat(?:ing|s)?\b": "repeated contact",
    r"\bkill(?:ing|s|ed)?\b": "stop",
    r"\bwar\b": "conflict of forces",
    r"\bknuckle(?:s)?\b": "hand bone",
}
_INTENSIFIERS = re.compile(
    r"\b(brutal|savage|extreme|graphic|violent|aggressive|intense|shocking|gruesome|horrifying)\b",
    re.IGNORECASE,
)
_SAFE_FRAME = (
    "Clean educational scientific illustration, flat vector explainer style, "
    "non-graphic, safe-for-work, diagrammatic and clinical: "
)


def deterministic_soften(prompt: str, attempt: int = 1) -> str:
    """Rule-based moderation softener. Escalates with ``attempt``."""
    text = prompt
    for pattern, repl in _SOFTEN.items():
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    text = _INTENSIFIERS.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    if attempt >= 2:
        # Second pass: keep only the neutral illustrative core, drop any residual
        # action clause after a clear separator, and enforce the safe frame.
        text = re.sub(r"[!]+", ".", text)
        text = _SAFE_FRAME + text
    if attempt >= 3:
        text = (
            _SAFE_FRAME + "labeled cross-section diagram of the biological/physical system involved, "
            "neutral clinical framing, no people in action, no impact depicted."
        )
    return text[:1800]


class PromptSafetyRewriter:
    def __init__(self, llm: Any = None, config: dict[str, Any] | None = None):
        self.llm = llm
        self.config = config or {}

    def rewrite(self, prompt: str, reason: str = "", attempt: int = 1) -> str:
        """Return a moderation-safe rewrite of ``prompt``. Tries the LLM first,
        then always applies the deterministic softener so the result is safe even
        if the LLM declines or is unavailable."""
        base = prompt
        if self.llm is not None and self.config.get("use_llm_rewrite", True):
            try:
                out = self.llm.generate_json(
                    system=(
                        "You rewrite image-generation prompts that were blocked by a content-safety "
                        "filter. Keep the scientific/educational meaning and the flat-vector explainer "
                        "style, but make it safe-for-work: remove or clinically reframe graphic violence, "
                        "gore, injury, blood, weapons and aggression. It must read as a neutral educational "
                        'diagram. Return strict JSON {"prompt": "..."}.'
                    ),
                    prompt=(
                        f"Blocked prompt:\n{prompt}\n\nModeration reason (may be vague): {reason or 'content moderated'}.\n"
                        "Rewrite it to pass moderation while preserving the scientific illustration intent."
                    ),
                    namespace="prompt_safety_rewrite",
                    fallback=None,
                    force=True,
                )
                if isinstance(out, dict) and str(out.get("prompt", "")).strip():
                    base = str(out["prompt"]).strip()
            except Exception:
                base = prompt
        return deterministic_soften(base, attempt)


def is_moderation_error(exc: Exception) -> bool:
    """True if an exception represents an image-provider content-moderation block."""
    status = str(getattr(exc, "status", "") or "").lower()
    message = str(exc).lower()
    return "moderat" in status or "moderat" in message
