from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Iterable

import feedparser
import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import StudioConfig
from .hashing import atomic_write_json, hash_value, read_json
from .logging_utils import configure_logging
from .schemas import SourceRecord


class SearchService:
    """Free-only web search. No paid APIs (no Tavily/SerpAPI/Google). Cached per topic."""

    def __init__(self, config: StudioConfig, cache_dir: Path):
        self.config = config
        self.cache_dir = cache_dir / "search"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "AutonomousScientificAnimationStudio/0.2"})
        self.logger = configure_logging("autostudio.search")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=5), reraise=True)
    def _get_json(self, url: str, params: dict | None = None) -> dict:
        response = self.session.get(url, params=params, timeout=self.config.search.timeout_seconds)
        response.raise_for_status()
        return response.json()

    def _source_id(self, provider: str, title: str, url: str | None) -> str:
        return f"{provider[:3].lower()}-{hash_value({'title': title, 'url': url})[:10]}"

    def _deduplicate(self, sources: Iterable[SourceRecord]) -> list[SourceRecord]:
        output: list[SourceRecord] = []
        seen: set[str] = set()
        for source in sources:
            key = re.sub(r"\W+", "", source.title.lower())[:120]
            if not key or key in seen:
                continue
            seen.add(key)
            output.append(source)
        return output

    def search_ddgs(self, query: str, limit: int) -> list[SourceRecord]:
        try:
            from ddgs import DDGS

            output = []
            for item in DDGS().text(query, max_results=limit):
                title = str(item.get("title") or "").strip()
                url = item.get("href") or item.get("url")
                if title:
                    output.append(SourceRecord(
                        source_id=self._source_id("ddgs", title, url), provider="DDGS",
                        title=title, url=url, snippet=str(item.get("body") or ""), score=0.72,
                    ))
            return output
        except Exception as exc:
            self.logger.warning("DDGS failed: %s", exc)
            return []

    def search_wikipedia(self, query: str, limit: int) -> list[SourceRecord]:
        try:
            data = self._get_json("https://en.wikipedia.org/w/api.php", {
                "action": "query", "list": "search", "srsearch": query,
                "format": "json", "utf8": 1, "srlimit": limit,
            })
            output = []
            for item in data.get("query", {}).get("search", []):
                title = item.get("title", "")
                url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
                snippet = re.sub("<[^>]+>", "", html.unescape(item.get("snippet", "")))
                output.append(SourceRecord(
                    source_id=self._source_id("wikipedia", title, url), provider="Wikipedia",
                    title=title, url=url, snippet=snippet, score=0.58,
                ))
            return output
        except Exception as exc:
            self.logger.warning("Wikipedia failed: %s", exc)
            return []

    def search_crossref(self, query: str, limit: int) -> list[SourceRecord]:
        try:
            data = self._get_json("https://api.crossref.org/works", {
                "query.title": query, "rows": limit,
                "select": "DOI,title,URL,author,published-online,published-print,abstract",
            })
            output = []
            for item in data.get("message", {}).get("items", []):
                title = " ".join(item.get("title") or []).strip()
                if not title:
                    continue
                url = item.get("URL") or (f"https://doi.org/{item['DOI']}" if item.get("DOI") else None)
                authors = item.get("author") or []
                author = ", ".join(" ".join(filter(None, [a.get("given"), a.get("family")])) for a in authors[:3]) or None
                published = None
                for key in ("published-print", "published-online"):
                    parts = ((item.get(key) or {}).get("date-parts") or [])
                    if parts:
                        published = "-".join(str(value) for value in parts[0])
                        break
                output.append(SourceRecord(
                    source_id=self._source_id("crossref", title, url), provider="Crossref",
                    title=title, url=url, snippet=re.sub("<[^>]+>", "", item.get("abstract") or "")[:1000],
                    author=author, published_at=published, score=0.90,
                ))
            return output
        except Exception as exc:
            self.logger.warning("Crossref failed: %s", exc)
            return []

    def search_openalex(self, query: str, limit: int) -> list[SourceRecord]:
        try:
            data = self._get_json("https://api.openalex.org/works", {
                "search": query, "per-page": limit,
                "select": "id,title,doi,publication_year,primary_location,authorships",
            })
            output = []
            for item in data.get("results", []):
                title = item.get("title") or ""
                location = item.get("primary_location") or {}
                url = location.get("landing_page_url") or item.get("doi") or item.get("id")
                authors = ", ".join(str((a.get("author") or {}).get("display_name") or "") for a in (item.get("authorships") or [])[:3]).strip(", ") or None
                output.append(SourceRecord(
                    source_id=self._source_id("openalex", title, url), provider="OpenAlex",
                    title=title, url=url, author=authors,
                    published_at=str(item.get("publication_year") or "") or None, score=0.88,
                ))
            return output
        except Exception as exc:
            self.logger.warning("OpenAlex failed: %s", exc)
            return []

    def search_arxiv(self, query: str, limit: int) -> list[SourceRecord]:
        try:
            response = self.session.get("https://export.arxiv.org/api/query", params={
                "search_query": f"all:{query}", "start": 0, "max_results": limit,
            }, timeout=self.config.search.timeout_seconds)
            response.raise_for_status()
            feed = feedparser.loads(response.text)
            output = []
            for entry in feed.entries:
                title = re.sub(r"\s+", " ", entry.get("title", "")).strip()
                url = entry.get("link")
                output.append(SourceRecord(
                    source_id=self._source_id("arxiv", title, url), provider="arXiv",
                    title=title, url=url,
                    snippet=re.sub(r"\s+", " ", entry.get("summary", "")).strip()[:1200],
                    author=", ".join(a.get("name", "") for a in entry.get("authors", [])[:3]) or None,
                    published_at=entry.get("published"), score=0.92,
                ))
            return output
        except Exception as exc:
            self.logger.warning("arXiv failed: %s", exc)
            return []

    def search_topic(self, topic: str, force_refresh: bool = False) -> list[SourceRecord]:
        key = hash_value({
            "topic": topic, "providers": self.config.search.providers,
            "limit": self.config.search.max_results_per_provider,
        })
        path = self.cache_dir / f"{key}.json"
        if path.exists() and not force_refresh:
            return [SourceRecord.model_validate(item) for item in read_json(path, [])]
        results: list[SourceRecord] = []
        for provider in self.config.search.providers:
            method = getattr(self, f"search_{provider}", None)
            if callable(method):
                results.extend(method(topic, self.config.search.max_results_per_provider))
        results = sorted(self._deduplicate(results), key=lambda item: item.score, reverse=True)
        atomic_write_json(path, [item.model_dump(mode="json") for item in results])
        return results

    def discover_trending_titles(self, limit: int = 24) -> list[str]:
        """Free trend discovery via science RSS feeds + DDGS. Used by auto topic mode."""
        titles: list[str] = []
        for feed_url in self.config.search.rss_feeds:
            try:
                feed = feedparser.parse(feed_url)
                titles.extend(str(entry.get("title") or "").strip() for entry in feed.entries[:10])
            except Exception as exc:
                self.logger.warning("RSS failed %s: %s", feed_url, exc)
        titles.extend(item.title for item in self.search_ddgs("latest science technology discovery research", 10))
        output, seen = [], set()
        for title in titles:
            key = re.sub(r"\W+", "", title.lower())
            if title and key not in seen:
                seen.add(key)
                output.append(title)
        return output[:limit]
