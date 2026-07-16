"""Public research search (Wikipedia, Crossref, OpenAlex, arXiv) and the
LLM-backed research pack builder."""

from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path
from typing import Any

import requests

from .llm import LLMRouter
from .schemas import Fact, ResearchPack, SourceDoc
from .security import redacted_exception_text
from .utils import ensure_dir, hash_value, load_json, save_json

logger = logging.getLogger(__name__)

# Public metadata APIs return small JSON/XML documents; cap defensively.
_MAX_RESEARCH_RESPONSE_BYTES = 4 * 1024 * 1024


class PublicResearchSearch:
    """Federated public-source search with per-provider failure isolation."""

    def __init__(self, config: dict[str, Any], cache_root: str | Path):
        self.config = config
        self.cache_root = ensure_dir(cache_root)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "ScientificMotionStudio/10.1 research@example.invalid"})
        self.timeout = int(config.get("timeout", 25))

    def _get_json(self, url: str, params: dict[str, Any]) -> Any:
        response = self.session.get(url, params=params, timeout=self.timeout)
        response.raise_for_status()
        if len(response.content) > _MAX_RESEARCH_RESPONSE_BYTES:
            raise ValueError(f"research response from {url} exceeds size cap")
        return response.json()

    def search(self, topic: str, force: bool = False) -> list[SourceDoc]:
        if not self.config.get("enabled", True):
            return []
        cache_path = self.cache_root / f"search-{hash_value(topic)}.json"
        if cache_path.exists() and not force:
            return [SourceDoc.model_validate(x) for x in load_json(cache_path, [])]
        sources: list[SourceDoc] = []
        for fn in (self._wikipedia, self._crossref, self._openalex, self._arxiv):
            try:
                sources.extend(fn(topic))
            except (requests.RequestException, ValueError, KeyError) as exc:
                logger.warning("[research] %s skipped: %s", fn.__name__, redacted_exception_text(exc, 200))
        dedup: dict[str, SourceDoc] = {}
        for item in sources:
            key = (item.url or item.title).strip().lower()
            if key and key not in dedup:
                dedup[key] = item
        ranked = list(dedup.values())
        words = set(re.findall(r"[a-z0-9]+", topic.lower()))
        for item in ranked:
            text_words = set(re.findall(r"[a-z0-9]+", f"{item.title} {item.snippet}".lower()))
            overlap = len(words & text_words) / max(1, len(words))
            item.relevance_score = min(1.0, overlap)
            item.final_score = 0.6 * item.authority_score + 0.4 * item.relevance_score
        ranked.sort(key=lambda x: x.final_score, reverse=True)
        ranked = ranked[: int(self.config.get("max_sources", 24))]
        for index, item in enumerate(ranked, 1):
            item.source_id = f"S{index:02d}"
        save_json(cache_path, ranked)
        return ranked

    def _wikipedia(self, topic: str) -> list[SourceDoc]:
        endpoint = "https://en.wikipedia.org/w/api.php"
        payload = self._get_json(
            endpoint,
            {
                "action": "query",
                "generator": "search",
                "gsrsearch": topic,
                "gsrlimit": 5,
                "prop": "extracts|info",
                "exintro": 1,
                "explaintext": 1,
                "inprop": "url",
                "format": "json",
            },
        )
        output = []
        for page in payload.get("query", {}).get("pages", {}).values():
            output.append(
                SourceDoc(
                    provider="wikipedia",
                    title=page.get("title", "Wikipedia"),
                    url=page.get("fullurl", ""),
                    snippet=page.get("extract", "")[:1600],
                    authority_score=0.72,
                )
            )
        return output

    def _crossref(self, topic: str) -> list[SourceDoc]:
        payload = self._get_json(
            "https://api.crossref.org/works",
            {"query": topic, "rows": 6, "select": "DOI,title,author,published,URL,abstract,publisher,type"},
        )
        output = []
        for item in payload.get("message", {}).get("items", []):
            title = " ".join(item.get("title") or ["Untitled"])
            authors = item.get("author") or []
            author = ", ".join(" ".join(filter(None, [a.get("given", ""), a.get("family", "")])) for a in authors[:3])
            abstract = re.sub(r"<[^>]+>", " ", item.get("abstract", ""))
            output.append(
                SourceDoc(
                    provider="crossref",
                    title=title,
                    url=item.get("URL", ""),
                    author=author,
                    snippet=html.unescape(abstract)[:1800],
                    authority_score=0.86,
                    metadata={"doi": item.get("DOI", ""), "publisher": item.get("publisher", "")},
                )
            )
        return output

    def _openalex(self, topic: str) -> list[SourceDoc]:
        payload = self._get_json(
            "https://api.openalex.org/works", {"search": topic, "per-page": 6, "mailto": "research@example.invalid"}
        )
        output = []
        for item in payload.get("results", []):
            inverted = item.get("abstract_inverted_index") or {}
            words = sorted(
                ((pos, word) for word, positions in inverted.items() for pos in positions), key=lambda x: x[0]
            )
            abstract = " ".join(word for _, word in words)
            source = ((item.get("primary_location") or {}).get("source") or {}).get("display_name", "")
            output.append(
                SourceDoc(
                    provider="openalex",
                    title=item.get("display_name", "Untitled"),
                    url=item.get("doi") or item.get("id", ""),
                    snippet=abstract[:1800],
                    authority_score=0.88,
                    published_at=str(item.get("publication_year", "")),
                    metadata={"cited_by_count": item.get("cited_by_count", 0), "venue": source},
                )
            )
        return output

    def _arxiv(self, topic: str) -> list[SourceDoc]:
        import xml.etree.ElementTree as ET

        response = self.session.get(
            "https://export.arxiv.org/api/query",
            params={
                "search_query": f"all:{topic}",
                "start": 0,
                "max_results": 5,
                "sortBy": "relevance",
                "sortOrder": "descending",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        if len(response.content) > _MAX_RESEARCH_RESPONSE_BYTES:
            raise ValueError("arxiv response exceeds size cap")
        root = ET.fromstring(response.content)
        ns = {"a": "http://www.w3.org/2005/Atom"}
        output = []
        for entry in root.findall("a:entry", ns):
            output.append(
                SourceDoc(
                    provider="arxiv",
                    title=" ".join((entry.findtext("a:title", default="", namespaces=ns)).split()),
                    url=entry.findtext("a:id", default="", namespaces=ns),
                    snippet=" ".join((entry.findtext("a:summary", default="", namespaces=ns)).split())[:1800],
                    author=", ".join(
                        a.findtext("a:name", default="", namespaces=ns) for a in entry.findall("a:author", ns)[:3]
                    ),
                    published_at=entry.findtext("a:published", default="", namespaces=ns),
                    authority_score=0.82,
                )
            )
        return output


class ResearchEngine:
    SYSTEM = """You are a scientific research editor for a short-form motion graphics studio.
Preserve uncertainty. Do not invent citations. Return rich JSON, but you may use nested objects when useful.
The downstream engine is permissive: prioritize factual quality over matching a brittle schema."""

    def __init__(self, llm: LLMRouter, cache_root: str | Path):
        self.llm = llm
        self.cache_root = ensure_dir(cache_root)

    def build(self, topic: str, sources: list[SourceDoc], force: bool = False) -> ResearchPack:
        cache_path = self.cache_root / f"research-{hash_value([topic, [s.url for s in sources]])}.json"
        if cache_path.exists() and not force:
            return ResearchPack.model_validate(load_json(cache_path))
        source_payload = [s.model_dump(mode="json") for s in sources]
        fallback = self._fallback(topic, sources)
        raw = self.llm.generate_json(
            system=self.SYSTEM,
            prompt=f"""Build an evidence pack for this topic: {topic}

Sources:
{json.dumps(source_payload, ensure_ascii=False, indent=2)}

Return an object with topic, summary, hooks, facts, comparisons, visual_ideas, limitations.
Each fact should include claim, source_ids, confidence, numeric_values, comparison, visual_hint.
Structured numeric_values are allowed. Never cite a source ID that is not in the supplied list.""",
            namespace="research",
            fallback=fallback.model_dump(mode="json"),
            force=force,
        )
        if not isinstance(raw, dict):
            raw = fallback.model_dump(mode="json")
        raw["topic"] = topic
        raw["sources"] = source_payload
        raw["raw_llm_output"] = raw.copy()
        pack = ResearchPack.model_validate(raw)
        valid_ids = {s.source_id for s in sources}
        for index, fact in enumerate(pack.facts, 1):
            fact.fact_id = fact.fact_id or f"F{index:02d}"
            fact.source_ids = [sid for sid in fact.source_ids if sid in valid_ids]
            fact.confidence = max(0.0, min(1.0, float(fact.confidence or 0.0)))
        supported = sum(1 for f in pack.facts if f.source_ids)
        pack.validation_score = round(supported / max(1, len(pack.facts)), 3)
        pack.research_hash = hash_value(pack.model_dump(exclude={"research_hash", "raw_llm_output"}))
        save_json(cache_path, pack)
        return pack

    def _fallback(self, topic: str, sources: list[SourceDoc]) -> ResearchPack:
        facts = []
        for index, source in enumerate(sources[:8], 1):
            claim = source.snippet.strip().split(". ")[0].strip()
            if claim:
                facts.append(
                    Fact(
                        fact_id=f"F{index:02d}",
                        claim=claim[:500],
                        source_ids=[source.source_id],
                        confidence=0.55,
                        visual_hint=source.title,
                    )
                )
        return ResearchPack(
            topic=topic,
            summary=f"Evidence pack assembled from {len(sources)} public sources for {topic}.",
            hooks=[f"What changes first if {topic.lower()}?"],
            facts=facts,
            comparisons=[],
            visual_ideas=[topic],
            limitations=["Fallback extraction used; review claims before publication."],
            sources=sources,
        )
