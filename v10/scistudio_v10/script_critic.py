"""Retention-first script quality gate."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .llm import LLMRouter
from .schemas import ResearchPack, ScriptPackage
from .utils import ensure_dir, save_json


STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "if",
    "then",
    "of",
    "to",
    "in",
    "on",
    "at",
    "for",
    "is",
    "are",
    "was",
    "were",
    "it",
    "its",
    "this",
    "that",
    "these",
    "those",
    "as",
    "by",
    "with",
    "from",
    "into",
    "would",
    "will",
    "can",
    "could",
    "so",
    "you",
    "your",
    "we",
    "our",
    "they",
    "their",
    "he",
    "she",
    "his",
    "her",
    "not",
    "no",
    "yes",
    "here",
    "there",
    "what",
}


class ScriptCritic:
    """Retention-first script quality gate — the narration counterpart to the
    visual critic. It is deterministic first (hook, information gain, pacing,
    curiosity, payoff), then optionally sharpened by an LLM. Weak scripts are
    rewritten instead of silently narrated.
    """

    SYSTEM = """You are a ruthless retention editor for science shorts (Kurzgesagt-level).
You judge whether a script hooks in the first 3 seconds, never stalls, escalates stakes,
and pays off. You return strict JSON only and propose concrete beat-level rewrites."""

    def __init__(self, llm: LLMRouter, config: dict[str, Any], root: str | Path):
        self.llm = llm
        self.config = config
        self.root = ensure_dir(root)

    # -- deterministic metrics ------------------------------------------------
    @staticmethod
    def _content_words(text: str) -> list[str]:
        return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS and len(w) > 2]

    def _metrics(self, script: ScriptPackage) -> dict[str, float]:
        beats = script.beats or []
        lines = [b.spoken_line for b in beats]
        first = lines[0] if lines else ""
        # Hook: question, number, or tension word in the first beat, and not too long.
        hook_signals = bool(re.search(r"\?|\d", first)) or bool(
            re.search(r"\b(imagine|what|suddenly|never|stop|vanish|collapse|instant|impossible)\b", first.lower())
        )
        hook_len_ok = 3 <= len(first.split()) <= 22
        hook = float(0.6 * hook_signals + 0.4 * hook_len_ok)
        # Information gain: fraction of beats that introduce new content words.
        seen: set[str] = set()
        novel = 0
        for line in lines:
            words = set(self._content_words(line))
            if len(words - seen) >= max(2, int(0.4 * max(1, len(words)))):
                novel += 1
            seen |= words
        info_gain = novel / max(1, len(lines))
        # Pacing: total duration within target window and no over-long single beat.
        lo = float(self.config.get("target_duration_min", 40.0))
        hi = float(self.config.get("target_duration_max", 60.0))
        dur = script.estimated_duration_s or sum(b.duration_s for b in beats)
        pacing = 1.0 if lo <= dur <= hi else max(0.0, 1.0 - abs(dur - (lo + hi) / 2) / (hi))
        longest = max((len(line.split()) for line in lines), default=0)
        no_stall = 1.0 if longest <= int(self.config.get("max_beat_words", 26)) else 0.6
        # Escalation: later beats raise stakes via consequence language OR a
        # time-stage progression (instant -> seconds -> minutes -> hours -> days),
        # which is itself escalation for a what-if chain.
        esc_terms = (
            "then",
            "next",
            "worse",
            "more",
            "every",
            "entire",
            "cascade",
            "chain",
            "finally",
            "result",
            "faster",
            "spread",
        )
        time_terms = ("instant", "moment", "second", "minute", "hour", "day", "week", "month", "year")
        second_half = lines[len(lines) // 2 :]
        esc_hits = sum(
            any(t in line.lower() for t in esc_terms) or any(t in line.lower() for t in time_terms)
            for line in second_half
        )
        escalation = min(1.0, esc_hits / max(1, len(second_half)) + 0.2)
        # Payoff: closing resolves / states the lesson.
        closing = (script.closing or (lines[-1] if lines else "")).lower()
        payoff = float(
            bool(re.search(r"\b(lesson|because|connected|why|matters|truth|reason|result|is that)\b", closing))
        )
        beat_count_ok = 1.0 if 6 <= len(beats) <= 11 else 0.6

        # Relevance: the opening must actually establish the topic's subject (so
        # the video is about something), without forcing every line to repeat the
        # subject noun. Drift is measured separately by `coherence` below.
        subject_words = set(self._content_words(script.topic))
        head_words = set(self._content_words(" ".join([script.hook or ""] + lines[:2])))
        relevance = 1.0 if (not subject_words or (subject_words & head_words)) else 0.4

        # Coherence: penalise beats that read like pasted paper abstracts or drift
        # to unrelated astronomy. This is what catches the "narasi ke planet lain".
        OFF_TOPIC = (
            "we present",
            "simulations of",
            "orbital period",
            "spin axis",
            "exoplanet",
            "super-earth",
            "super earth",
            "quasi-satellite",
            "quasi satellite",
            "aquaplanet",
            "tidally locked",
            "near-earth",
            "solar system bodies",
            "have found a special place",
            "in this paper",
            "we investigate",
            "we study",
            "this study",
            "et al",
        )
        offtopic_beats = sum(1 for line in lines if any(marker in line.lower() for marker in OFF_TOPIC))
        coherence = 1.0 - offtopic_beats / max(1, len(lines))

        return {
            "hook_strength": round(hook, 3),
            "relevance": round(relevance, 3),
            "coherence": round(coherence, 3),
            "information_gain": round(info_gain, 3),
            "pacing": round(pacing, 3),
            "no_stall": round(no_stall, 3),
            "escalation": round(escalation, 3),
            "payoff": round(payoff, 3),
            "structure": round(beat_count_ok, 3),
            "estimated_duration_s": round(dur, 2),
            "beat_count": len(beats),
        }

    # -- review ---------------------------------------------------------------
    def review(self, script: ScriptPackage, research: ResearchPack, force: bool = False) -> dict[str, Any]:
        metrics = self._metrics(script)
        keys = [
            "hook_strength",
            "relevance",
            "coherence",
            "information_gain",
            "pacing",
            "no_stall",
            "escalation",
            "payoff",
            "structure",
        ]
        structural_min = min(metrics[k] for k in keys)
        threshold = float(self.config.get("acceptance_threshold", 0.6))
        vision_or_text_llm = self.llm.available("gemini") or self.llm.available("openai") or self.llm.available("local")

        report: dict[str, Any] = {
            "metrics": metrics,
            "scores": {k: metrics[k] for k in keys},
            "failures": [],
            "repair_plan": [],
        }
        for k in keys:
            if metrics[k] < threshold:
                report["failures"].append(f"{k}={metrics[k]:.2f} < {threshold:.2f}")

        if not vision_or_text_llm:
            report["passed"] = structural_min >= threshold
            report["mode"] = "structural-only (no LLM)"
            save_json(self.root / f"{script.script_hash or 'script'}-quality.json", report)
            return report

        prompt = f"""Score this science-short script for retention and propose rewrites.
Deterministic metrics already computed: {json.dumps(metrics, ensure_ascii=False)}
Script: {json.dumps(script.model_dump(mode="json", exclude={"raw_llm_output"}), ensure_ascii=False)}

Return JSON: {{"passed": bool, "scores": {{"hook_strength":0-1,"information_gain":0-1,"curiosity":0-1,
"escalation":0-1,"payoff":0-1,"clarity":0-1,"naturalness":0-1}}, "failures": [..],
"repair_plan": [{{"beat_id":"B0x","rewrite":"stronger spoken line"}}]}}
Be strict: a first line that does not create an open loop in 3 seconds fails hook_strength."""
        raw = self.llm.generate_json(
            system=self.SYSTEM,
            prompt=prompt,
            namespace="script_critic",
            fallback={
                "passed": structural_min >= threshold,
                "scores": report["scores"],
                "failures": report["failures"],
                "repair_plan": [],
            },
            force=force,
        )
        if not isinstance(raw, dict):
            raw = {
                "passed": structural_min >= threshold,
                "scores": report["scores"],
                "failures": report["failures"],
                "repair_plan": [],
            }
        raw.setdefault("metrics", metrics)
        scores = {**{k: metrics[k] for k in keys}, **(raw.get("scores") or {})}
        llm_min = min([float(v) for v in scores.values()] or [0.0])
        raw["passed"] = bool(raw.get("passed", True) and structural_min >= threshold and llm_min >= threshold)
        raw["scores"] = scores
        save_json(self.root / f"{script.script_hash or 'script'}-quality.json", raw)
        return raw

    # -- refine ---------------------------------------------------------------
    def refine(
        self, script: ScriptPackage, research: ResearchPack, director, force: bool = False
    ) -> tuple[ScriptPackage, dict[str, Any]]:
        max_rounds = int(self.config.get("max_rounds", 2))
        strict = bool(self.config.get("strict", False))
        report = self.review(script, research, force=force)
        for _ in range(max_rounds):
            if report.get("passed"):
                break
            repairs = report.get("repair_plan") or report.get("failures")
            if self.llm.available("gemini") or self.llm.available("openai") or self.llm.available("local"):
                prompt = f"""Rewrite this script to fix these problems while keeping the facts and evidence refs.
Problems: {json.dumps(repairs, ensure_ascii=False)}
Current script: {json.dumps(script.model_dump(mode="json", exclude={"raw_llm_output"}), ensure_ascii=False)}
Return the same JSON shape (topic, title, hook, beats[], closing). Keep 7-10 beats, {self.config.get("target_duration_min", 40)}-{self.config.get("target_duration_max", 60)}s."""
                raw = self.llm.generate_json(
                    system=director.SCRIPT_SYSTEM,
                    prompt=prompt,
                    namespace="script_refine",
                    fallback=script.model_dump(mode="json"),
                    force=True,
                )
                script = director._normalize_script(raw, script, script.topic)
            else:
                break
            report = self.review(script, research, force=True)
        if strict and not report.get("passed"):
            report["note"] = "script did not reach retention threshold after refinement"
        return script, report
