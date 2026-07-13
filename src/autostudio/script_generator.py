from __future__ import annotations

import json
from pathlib import Path

from .config import StudioConfig
from .hashing import atomic_write_json, hash_value, read_json
from .llm import LLMClient
from .logging_utils import configure_logging
from .schemas import ResearchBundle, ScriptBeat, ScriptPackage, ValidationReport


class ScriptGenerator:
    """Retention-first narration. LLM writes beats; Python enforces timing and word budget."""

    SYSTEM_PROMPT = (
        "You write high-retention scientific animation scripts. Do not sound like Wikipedia. "
        "Start inside a concrete event. Use short spoken sentences, contrast, consequences, "
        "escalation, and payoff. Every factual beat must cite supplied fact IDs. Return JSON only."
    )

    def __init__(self, config: StudioConfig, llm: LLMClient, cache_dir: Path):
        self.config = config
        self.llm = llm
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.logger = configure_logging("autostudio.script")

    def _timing(self, package: ScriptPackage) -> ScriptPackage:
        beats, cursor, total = [], 0.0, 0
        for beat in package.beats:
            words = max(1, len(beat.narration.split()))
            duration = words / self.config.script.words_per_second
            beats.append(beat.model_copy(update={
                "words": words, "start_s": round(cursor, 3),
                "end_s": round(cursor + duration, 3), "estimated_duration_s": round(duration, 3),
            }))
            cursor += duration
            total += words
        return package.model_copy(update={"beats": beats, "estimated_duration_s": round(cursor, 3), "total_words": total})

    def _repair(self, package: ScriptPackage, research: ResearchBundle) -> ScriptPackage:
        package = self._timing(package)
        minimum, maximum = self.config.script.target_words_min, self.config.script.target_words_max
        beats = list(package.beats)
        if package.total_words < minimum:
            existing = {ref for beat in beats for ref in beat.evidence_refs}
            for fact in [fact for fact in research.facts if fact.fact_id not in existing]:
                if sum(len(beat.narration.split()) for beat in beats) >= minimum:
                    break
                beats.insert(max(1, len(beats) - 1), ScriptBeat(
                    beat_id=f"B{len(beats) + 1:02d}", purpose="evidence-backed escalation",
                    narration=f"Here is the strange part: {fact.claim}", evidence_refs=[fact.fact_id],
                ))
        elif package.total_words > maximum:
            repaired, running = [], 0
            for beat in beats:
                remaining = maximum - running
                if remaining <= 0:
                    break
                narration = " ".join(beat.narration.split()[:remaining])
                repaired.append(beat.model_copy(update={"narration": narration}))
                running += len(narration.split())
            beats = repaired
        return self._timing(package.model_copy(update={"beats": beats}))

    def generate(self, research: ResearchBundle, validation: ValidationReport, force_refresh: bool = False) -> ScriptPackage:
        key = hash_value({
            "research": research.research_hash,
            "validation": validation.model_dump(mode="json"),
            "config": self.config.script.model_dump(mode="json"),
        })
        path = self.cache_dir / f"{key}.json"
        if path.exists() and not force_refresh:
            return ScriptPackage.model_validate(read_json(path))
        evidence = {
            "summary": research.summary,
            "facts": [fact.model_dump(mode="json") for fact in research.facts],
            "numbers": [fact.model_dump(mode="json") for fact in research.numbers],
            "comparisons": research.comparisons,
            "limitations": research.limitations,
            "validation": validation.model_dump(mode="json"),
        }
        prompt = f"""
Write an English scientific short about: {research.topic}
Target {self.config.script.target_words_min}-{self.config.script.target_words_max} spoken words,
{self.config.script.scene_count_min}-{self.config.script.scene_count_max} beats, approximately {self.config.script.target_duration_seconds:.0f} seconds.
Use cold open -> curiosity -> rule -> consequences -> escalation -> ending. No greeting or generic definition.

Evidence:
{json.dumps(evidence, ensure_ascii=False, indent=2)}

Return:
{{"topic":"...","hook":"...","curiosity":"...","scientific_explanation":"...","escalation":"...","ending":"...","tone":"curious, precise, escalating","beats":[{{"beat_id":"B01","purpose":"cold open","narration":"...","evidence_refs":["F01"]}}],"estimated_duration_s":0,"total_words":0}}
""".strip()
        raw = self.llm.generate_json(self.SYSTEM_PROMPT, prompt, cache_namespace="script", max_new_tokens=2200, force_refresh=force_refresh)
        raw["topic"] = research.topic
        package = self._repair(ScriptPackage.model_validate(raw), research)
        if not (self.config.script.target_words_min <= package.total_words <= self.config.script.target_words_max):
            repair_prompt = (
                f"Rewrite this ScriptPackage to {self.config.script.target_words_min}-{self.config.script.target_words_max} words, "
                f"preserving evidence references and beat order. Return JSON only.\n\n{json.dumps(package.model_dump(mode='json'), ensure_ascii=False)}"
            )
            try:
                repaired = self.llm.generate_json(self.SYSTEM_PROMPT, repair_prompt, cache_namespace="script-repair", max_new_tokens=1900, force_refresh=force_refresh)
                repaired["topic"] = research.topic
                package = self._repair(ScriptPackage.model_validate(repaired), research)
            except Exception as exc:
                self.logger.warning("Script rewrite failed: %s", exc)
        package = package.model_copy(update={"script_hash": hash_value(package.model_dump(exclude={"script_hash"}))})
        atomic_write_json(path, package)
        return package
