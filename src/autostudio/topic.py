from __future__ import annotations

import json
from enum import Enum

from .llm import LLMClient
from .search import SearchService


class TopicMode(str, Enum):
    MANUAL = "manual"
    KEYWORD = "keyword"
    AUTO = "auto"


class TopicResolver:
    """Three input modes:

    * ``manual``  — use the given topic verbatim.
    * ``keyword`` — expand a keyword into ranked "What if" candidates, pick the best.
    * ``auto``    — discover trending science headlines, convert, rank, and select.
    """

    def __init__(self, llm: LLMClient, search: SearchService):
        self.llm = llm
        self.search = search

    def manual(self, topic: str) -> str:
        if not topic.strip():
            raise ValueError("Manual topic cannot be empty.")
        return topic.strip()

    def keyword(self, keyword: str) -> tuple[str, list[dict]]:
        prompt = (
            f'Expand the scientific keyword "{keyword}" into 8 advertiser-friendly What If topics. '
            "Rank by visual clarity, scientific depth, curiosity, and flat-SVG feasibility. "
            "Avoid medical advice, animal harm, victim-focused disasters, and unsupported claims. "
            'Return JSON: {"candidates":[{"topic":"What if ...?","score":0.0,"reason":"..."}],"selected":"What if ...?"}'
        )
        result = self.llm.generate_json(
            "You are a scientific topic editor. Return JSON only.",
            prompt, cache_namespace="topic-keyword", max_new_tokens=1000,
        )
        candidates = result.get("candidates") or []
        selected = str(result.get("selected") or "")
        if not selected and candidates:
            selected = str(max(candidates, key=lambda item: float(item.get("score", 0))).get("topic"))
        return selected or f"What if {keyword} changed the world?", candidates

    def automatic(self) -> tuple[str, list[dict]]:
        titles = self.search.discover_trending_titles(24)
        prompt = (
            "Turn these recent science and technology headlines into safe, timeless What If animation topics. "
            "Rank by scientific value, visual clarity, curiosity, and reusable SVG feasibility.\n\n"
            f"{json.dumps(titles, ensure_ascii=False)}\n\n"
            'Return JSON: {"candidates":[{"topic":"What if ...?","score":0.0,"source_headline":"...","reason":"..."}],"selected":"What if ...?"}'
        )
        result = self.llm.generate_json(
            "You are a scientific trend editor. Avoid sensationalism. Return JSON only.",
            prompt, cache_namespace="topic-auto", max_new_tokens=1300,
        )
        candidates = result.get("candidates") or []
        selected = str(result.get("selected") or "")
        if not selected and candidates:
            selected = str(max(candidates, key=lambda item: float(item.get("score", 0))).get("topic"))
        return selected or "What if Earth stopped rotating for one second?", candidates

    def resolve(self, mode: str, value: str = "") -> tuple[str, list[dict]]:
        parsed = TopicMode(mode)
        if parsed is TopicMode.MANUAL:
            return self.manual(value), []
        if parsed is TopicMode.KEYWORD:
            return self.keyword(value)
        return self.automatic()
