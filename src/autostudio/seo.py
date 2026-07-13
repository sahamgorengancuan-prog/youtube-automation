from __future__ import annotations

from pathlib import Path

from .config import StudioConfig
from .hashing import atomic_write_json, hash_value, read_json, slugify
from .llm import LLMClient
from .schemas import ResearchBundle, ScriptPackage, SEOPackage


class SEOGenerator:
    """Upload-ready YouTube metadata. LLM-authored with a deterministic fallback,
    then hard-clamped to the configured title/hashtag/tag limits."""

    def __init__(self, config: StudioConfig, llm: LLMClient, cache_root: Path):
        self.config = config
        self.llm = llm
        self.cache_dir = cache_root / "metadata"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _fallback(self, script: ScriptPackage, research: ResearchBundle) -> SEOPackage:
        title = (script.topic.strip().rstrip("?") + "?")[: self.config.seo.max_title_chars]
        hashtags = ["#WhatIf", "#Science", "#Animation", "#Explained", "#Shorts"]
        tags = [
            "what if", "science explained", "scientific animation", "educational shorts",
            "science simulation", script.topic.lower(), "flat vector animation", "science facts",
        ]
        description = (
            f"{script.hook}\n\nThis scientific animation explores {script.topic.lower()} "
            f"using source-backed research and a hypothetical simulation. "
            f"The outcome is educational, not a prediction.\n\n" + " ".join(hashtags)
        )
        return SEOPackage(
            title=title, description=description, hashtags=hashtags,
            tags=tags[: self.config.seo.tag_count], filename=slugify(title) + ".mp4", seo_score=78,
        )

    def generate(self, script: ScriptPackage, research: ResearchBundle, force_refresh: bool = False) -> SEOPackage:
        key = hash_value({"script": script.script_hash, "research": research.research_hash})
        cache_path = self.cache_dir / f"{key}.json"
        if cache_path.exists() and not force_refresh:
            return SEOPackage.model_validate(read_json(cache_path))
        prompt = f"""
Create upload metadata for an English scientific YouTube Short.
Topic: {script.topic}
Hook: {script.hook}
Ending: {script.ending}
Research summary: {research.summary}

Return JSON:
{{
  "title": "maximum {self.config.seo.max_title_chars} characters",
  "description": "clear, intriguing, non-misleading description",
  "hashtags": ["exactly {self.config.seo.hashtag_count} focused hashtags"],
  "tags": ["up to {self.config.seo.tag_count} focused tags"],
  "filename": "safe-file-name.mp4",
  "seo_score": 0
}}
Avoid miracle, guaranteed, shocking, or 100% safe.
"""
        try:
            raw = self.llm.generate_json(
                "You write accurate YouTube metadata for science animations. Return JSON only.",
                prompt, cache_namespace="seo", max_new_tokens=900, force_refresh=force_refresh,
            )
            package = SEOPackage.model_validate(raw)
        except Exception:
            package = self._fallback(script, research)
        package = package.model_copy(update={
            "title": package.title[: self.config.seo.max_title_chars],
            "hashtags": package.hashtags[: self.config.seo.hashtag_count],
            "tags": package.tags[: self.config.seo.tag_count],
            "filename": slugify(package.filename.removesuffix(".mp4")) + ".mp4",
        })
        atomic_write_json(cache_path, package)
        return package
