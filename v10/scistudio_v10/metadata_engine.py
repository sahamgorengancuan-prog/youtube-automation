"""Automated publish metadata — title, description, tags, chapters, thumbnail.

A finished MP4 is not a finished upload. This assembles the publish package from
artifacts the pipeline already produced — the script (title/hook/closing), the
research (facts + sources), and the storyboard (scene timings) — so the video
ships with a real title, a sourced description, keyword tags, timestamp
chapters, and a thumbnail brief, instead of just `{title, topic, job_id}`.

Everything is deterministic assembly over existing artifacts (no network, fully
testable offline); production can optionally polish the title/description with
the reasoning LLM, but the deterministic package is always valid on its own. The
package is emitted as a lineage artifact and merged into the metadata handed to
the publisher, so what gets uploaded is auditable before it is sent.
"""

from __future__ import annotations

import re
from typing import Any

from .utils import ensure_dir, save_json, slugify

_STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "what",
    "why", "how", "does", "do", "is", "are", "you", "your", "it", "its", "that",
    "this", "if", "would", "happens", "happen", "when", "into", "at", "by", "as",
}

_TITLE_MAX = 100  # YouTube hard limit
_DESC_MAX = 4900  # under YouTube's 5000 cap, leaving room


def _keywords(text: str, limit: int = 12) -> list[str]:
    words = re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", (text or "").lower())
    out: list[str] = []
    for w in words:
        if w in _STOPWORDS or w in out:
            continue
        out.append(w)
        if len(out) >= limit:
            break
    return out


def _timestamp(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


class MetadataEngine:
    def __init__(self, llm: Any = None, config: dict[str, Any] | None = None, root: Any = None):
        self.llm = llm
        self.config = config or {}
        self.root = ensure_dir(root) if root is not None else None

    def _title(self, topic: str, script: Any) -> str:
        base = (getattr(script, "title", "") or "").strip()
        if not base:
            hook = (getattr(script, "hook", "") or "").strip()
            base = hook or topic
        base = re.sub(r"\s+", " ", base).strip().rstrip(".")
        if len(base) > _TITLE_MAX:
            base = base[: _TITLE_MAX - 1].rstrip() + "…"
        return base

    def _description(self, topic: str, script: Any, research: Any, sources: list[Any]) -> str:
        lines: list[str] = []
        summary = (getattr(research, "summary", "") or "").strip()
        hook = (getattr(script, "hook", "") or "").strip()
        lead = summary or hook or f"A short science explainer on {topic}."
        lines.append(lead)
        lines.append("")
        facts = getattr(research, "facts", []) or []
        key = [f for f in facts if (getattr(f, "claim", "") or "").strip()][:4]
        if key:
            lines.append("In this video:")
            for f in key:
                claim = re.sub(r"\s+", " ", str(getattr(f, "claim", ""))).strip()
                if claim:
                    lines.append(f"• {claim}")
            lines.append("")
        srcs = [s for s in (sources or []) if (getattr(s, "url", "") or "").strip()][:5]
        if srcs:
            lines.append("Sources:")
            for s in srcs:
                title = (getattr(s, "title", "") or "source").strip()
                lines.append(f"- {title}: {getattr(s, 'url', '')}")
            lines.append("")
        tags = self._tags(topic, script, research)
        if tags:
            lines.append(" ".join(f"#{t.replace('-', '')}" for t in tags[:5]))
        desc = "\n".join(lines).strip()
        return desc[:_DESC_MAX]

    def _tags(self, topic: str, script: Any, research: Any) -> list[str]:
        pool = topic + " " + (getattr(script, "title", "") or "")
        for f in (getattr(research, "facts", []) or [])[:6]:
            pool += " " + str(getattr(f, "claim", ""))
        base = ["science", "education", "explainer", "animation"]
        kws = _keywords(pool, 12)
        out: list[str] = []
        for t in kws + base:
            if t not in out:
                out.append(t)
        return out[:15]

    def _chapters(self, storyboard: Any) -> list[dict[str, Any]]:
        scenes = getattr(storyboard, "scenes", []) or []
        chapters: list[dict[str, Any]] = []
        t = 0.0
        for i, sc in enumerate(scenes):
            label = (
                (getattr(sc, "headline", "") or "").strip()
                or (getattr(sc, "narration", "") or "").strip()[:40]
                or f"Part {i + 1}"
            )
            chapters.append(
                {"start": _timestamp(t), "start_s": round(t, 2), "title": label[:60]}
            )
            t += float(getattr(sc, "duration_s", 5.0) or 5.0)
        # YouTube requires the first chapter at 0:00; guaranteed above.
        return chapters

    def _thumbnail_spec(self, topic: str, script: Any, hero_image: str | None) -> dict[str, Any]:
        overlay = (getattr(script, "hook", "") or topic).strip()
        overlay = re.sub(r"\s+", " ", overlay)
        # A punchy 2-4 word overlay reads on a phone thumbnail.
        short = " ".join(overlay.split()[:4]) if overlay else topic
        return {
            "hero_image": hero_image or "",
            "overlay_text": short.upper(),
            "style": "bold flat vector, high contrast, single focal subject, "
            "large legible text, safe margins",
            "aspect": "9:16",
        }

    def build(
        self,
        topic: str,
        script: Any,
        research: Any,
        storyboard: Any,
        sources: list[Any] | None = None,
        hero_image: str | None = None,
    ) -> dict[str, Any]:
        sources = sources if sources is not None else (getattr(research, "sources", []) or [])
        package = {
            "title": self._title(topic, script),
            "description": self._description(topic, script, research, sources),
            "tags": self._tags(topic, script, research),
            "chapters": self._chapters(storyboard),
            "thumbnail": self._thumbnail_spec(topic, script, hero_image),
            "category": str(self.config.get("category", "Education")),
            "language": str(self.config.get("language", "en")),
            "privacy": str(self.config.get("privacy", "private")),
            "slug": slugify(topic, 60),
            "source": "deterministic",
        }
        package = self._maybe_polish(package, topic)
        if self.root is not None:
            save_json(self.root / "metadata.json", package)
        return package

    def _maybe_polish(self, package: dict[str, Any], topic: str) -> dict[str, Any]:
        """Optionally sharpen title/description with the LLM; deterministic
        package is kept if the LLM is unavailable or returns nothing usable."""
        if self.llm is None or not self.config.get("llm_polish", False):
            return package
        try:
            raw = self.llm.generate_json(
                system=(
                    "You are a YouTube growth editor. Improve the title (<=100 "
                    "chars, curiosity-driven, no clickbait lies) and the first "
                    "line of the description. Keep it accurate and safe-for-work."
                ),
                prompt=(
                    f"Topic: {topic}\nCurrent title: {package['title']}\n"
                    f"Description lead: {package['description'].splitlines()[0]}\n"
                    'Return JSON {"title": "...", "description_lead": "..."}.'
                ),
                namespace="metadata_engine",
                fallback={},
            )
            if isinstance(raw, dict):
                new_title = str(raw.get("title", "")).strip()
                if new_title:
                    package["title"] = new_title[:_TITLE_MAX]
                lead = str(raw.get("description_lead", "")).strip()
                if lead:
                    rest = package["description"].split("\n", 1)
                    package["description"] = (
                        lead + ("\n" + rest[1] if len(rest) > 1 else "")
                    )[:_DESC_MAX]
                package["source"] = "llm_polished"
        except Exception:
            pass
        return package
