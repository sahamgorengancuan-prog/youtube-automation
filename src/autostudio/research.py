from __future__ import annotations

import json
import re
from pathlib import Path

from .config import StudioConfig
from .hashing import atomic_write_json, hash_value, read_json
from .llm import LLMClient
from .logging_utils import configure_logging
from .schemas import ResearchBundle, ResearchFact, SourceRecord, ValidationIssue, ValidationReport


class ScientificValidator:
    """Deterministic checks. No LLM: every rule is inspectable and reproducible."""

    def __init__(self, config: StudioConfig):
        self.config = config

    def validate(self, bundle: ResearchBundle) -> ValidationReport:
        issues: list[ValidationIssue] = []
        valid_ids = {source.source_id for source in bundle.references}
        supported = 0
        for fact in bundle.facts + bundle.numbers:
            missing = [source_id for source_id in fact.source_ids if source_id not in valid_ids]
            if missing:
                issues.append(ValidationIssue(
                    severity="error", code="missing_source_reference",
                    message=f"Unknown source IDs: {missing}", claim=fact.claim, source_ids=fact.source_ids,
                ))
            if fact.source_ids and not missing:
                supported += 1
            else:
                issues.append(ValidationIssue(
                    severity="warning", code="unsupported_claim",
                    message="Claim has no valid source reference.", claim=fact.claim, source_ids=fact.source_ids,
                ))
            contains_number = bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:%|km|m/s|kg|years?|hours?|°C|K|Hz|W|J)?\b", fact.claim))
            if contains_number and self.config.research.strict_numeric_validation and not fact.source_ids:
                issues.append(ValidationIssue(
                    severity="error", code="uncited_number",
                    message="Numeric claim requires a source.", claim=fact.claim,
                ))
        total = max(1, len(bundle.facts) + len(bundle.numbers))
        coverage = supported / total
        non_wikipedia = sum(1 for source in bundle.references if source.provider.lower() != "wikipedia")
        if non_wikipedia < self.config.research.minimum_non_wikipedia_sources:
            issues.append(ValidationIssue(
                severity="error", code="source_diversity_low",
                message=f"Only {non_wikipedia} non-Wikipedia sources are available.",
            ))
        if len(bundle.facts) < self.config.research.minimum_supported_facts:
            issues.append(ValidationIssue(
                severity="warning", code="too_few_facts",
                message=f"Research contains only {len(bundle.facts)} facts.",
            ))
        return ValidationReport(
            passed=not any(issue.severity == "error" for issue in issues) and coverage >= 0.65,
            coverage_score=round(coverage, 3),
            source_diversity=len({source.provider for source in bundle.references}),
            issues=issues,
        )


class ResearchService:
    """LLM synthesis grounded in supplied sources, then deterministic validation."""

    SYSTEM_PROMPT = (
        "You are a scientific research editor. Use only supplied source records. "
        "Never invent citations. Separate established facts from uncertainty. "
        "Numeric statements must point to source IDs. Return valid JSON only."
    )

    def __init__(self, config: StudioConfig, llm: LLMClient, cache_dir: Path):
        self.config = config
        self.llm = llm
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.validator = ScientificValidator(config)
        self.logger = configure_logging("autostudio.research")

    def _fallback(self, topic: str, sources: list[SourceRecord]) -> ResearchBundle:
        facts = []
        for index, source in enumerate(sources[:8], start=1):
            claim = source.snippet.strip() or source.title
            if claim:
                facts.append(ResearchFact(
                    fact_id=f"F{index:02d}", claim=claim[:500], source_ids=[source.source_id],
                    confidence=0.55, visual_hint=source.title,
                ))
        return ResearchBundle(
            topic=topic,
            summary=" ".join(fact.claim for fact in facts[:3])[:1200],
            facts=facts,
            references=sources,
            visual_ideas=[source.title for source in sources[:5]],
            limitations=["Fallback synthesis was used."],
        )

    def collect(self, topic: str, sources: list[SourceRecord], force_refresh: bool = False) -> tuple[ResearchBundle, ValidationReport]:
        source_payload = [source.model_dump(mode="json") for source in sources]
        key = hash_value({"topic": topic, "sources": source_payload})
        bundle_path = self.cache_dir / f"{key}.json"
        validation_path = self.cache_dir / f"{key}.validation.json"
        if bundle_path.exists() and validation_path.exists() and not force_refresh:
            return ResearchBundle.model_validate(read_json(bundle_path)), ValidationReport.model_validate(read_json(validation_path))
        prompt = f"""
Topic: {topic}

Source records:
{json.dumps(source_payload, ensure_ascii=False, indent=2)}

Create a concise summary, 6-10 source-backed facts, important numbers, intuitive comparisons,
flat-vector visual ideas, and limitations. Return:
{{"topic":"...","summary":"...","facts":[{{"fact_id":"F01","claim":"...","source_ids":["source-id"],"confidence":0.0,"numeric_values":[],"visual_hint":"..."}}],"numbers":[],"comparisons":[],"visual_ideas":[],"limitations":[]}}
Do not return references; the application attaches them.
""".strip()
        try:
            raw = self.llm.generate_json(self.SYSTEM_PROMPT, prompt, cache_namespace="research-synthesis", max_new_tokens=2200, force_refresh=force_refresh)
            raw.update({"topic": topic, "references": source_payload})
            bundle = ResearchBundle.model_validate(raw)
        except Exception as exc:
            self.logger.warning("Research fallback: %s", exc)
            bundle = self._fallback(topic, sources)
        bundle = bundle.model_copy(update={"research_hash": hash_value(bundle.model_dump(exclude={"research_hash"}))})
        validation = self.validator.validate(bundle)
        atomic_write_json(bundle_path, bundle)
        atomic_write_json(validation_path, validation)
        return bundle, validation
