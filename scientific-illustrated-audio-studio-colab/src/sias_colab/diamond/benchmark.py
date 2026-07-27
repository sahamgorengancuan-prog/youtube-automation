"""SIAS model benchmark harness — models are pinned per TASK by measured
results, not by README reputation. Offline scaffold: the prompt sets, runner
and report schema are real and tested; live scoring runs when generators and
reviewers are wired."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from sias.filesystem import atomic_write_json

CATEGORIES = {
    "style_board": 10,
    "recurring_character": 10,
    "hand_limb_challenge": 10,
    "multi_reference": 10,
    "scientific_diagram": 10,
    "repair_instruction": 10,
    "typography_scene": 10,
}

_SEED_PROMPTS = {
    "style_board": "Six-panel Scientific Notebook Cartoon style board: reaction, mechanism, environment, close-up, causal diagram, gasp frame — variant {i}",
    "recurring_character": "The guide scientist (same face, hair, outfit as the character sheet) {action} — variant {i}",
    "hand_limb_challenge": "Guide scientist with BOTH hands clearly visible {action}, correct five-finger simplification — variant {i}",
    "multi_reference": "Compose guide scientist + rain gauge + flooded street from three references, one focal idea — variant {i}",
    "scientific_diagram": "Clean causal diagram: {mechanism}, three labelled arrows, short noun labels only — variant {i}",
    "repair_instruction": "REPAIR: fix only {defect}; preserve identity, palette, paper texture, layout — variant {i}",
    "typography_scene": "Notebook page with a large hand-lettered number '{number}' and one short label — variant {i}",
}

_FILLS = {
    "action": ["pointing up", "holding an umbrella", "taking notes", "gesturing at a chart"],
    "mechanism": ["soil saturation", "runoff cascade", "evaporation loop", "pressure gradient"],
    "defect": ["a duplicated left hand", "a floating arrow", "hallucinated text", "a missing prop"],
    "number": ["365", "10x", "2.4", "90%"],
}

METRICS = ["prompt_compliance", "consistency", "anatomy", "edit_preservation",
           "science_clarity", "text_rendering", "speed_s", "peak_vram_gb", "failure_rate"]


class BenchmarkResult(BaseModel):
    model: str
    category: str
    metrics: dict[str, float] = Field(default_factory=dict)
    license_status: str = "UNKNOWN_BLOCKED"
    failures: int = 0


def build_benchmark_set() -> list[dict[str, Any]]:
    cases = []
    for category, count in CATEGORIES.items():
        template = _SEED_PROMPTS[category]
        for i in range(1, count + 1):
            fills = {k: v[i % len(v)] for k, v in _FILLS.items()}
            cases.append({"case_id": f"{category}_{i:02d}", "category": category,
                          "prompt": template.format(i=i, **fills)})
    return cases


def run_benchmark(
    models: dict[str, Callable[[str], dict[str, float]]],
    cases: list[dict[str, Any]] | None = None,
    license_statuses: dict[str, str] | None = None,
) -> list[BenchmarkResult]:
    """models: name -> fn(prompt) returning metric dict (injectable; live fns
    wrap real generators + reviewers). Aggregates per model×category."""
    cases = cases or build_benchmark_set()
    statuses = license_statuses or {}
    results: dict[tuple[str, str], list[dict[str, float]]] = {}
    for name, fn in models.items():
        for case in cases:
            try:
                metrics = fn(case["prompt"])
            except Exception:
                metrics = {"failure": 1.0}
            results.setdefault((name, case["category"]), []).append(metrics)
    out: list[BenchmarkResult] = []
    for (name, category), rows in results.items():
        agg: dict[str, float] = {}
        failures = sum(1 for r in rows if r.get("failure"))
        for metric in METRICS:
            vals = [r[metric] for r in rows if metric in r]
            if vals:
                agg[metric] = round(sum(vals) / len(vals), 4)
        agg["failure_rate"] = round(failures / len(rows), 4)
        out.append(BenchmarkResult(model=name, category=category, metrics=agg,
                                   license_status=statuses.get(name, "UNKNOWN_BLOCKED"),
                                   failures=failures))
    return out


def selection_report(results: list[BenchmarkResult], out_path: str | Path) -> dict[str, Any]:
    """Pin winners PER TASK; a model with blocked license never wins."""
    winners: dict[str, dict[str, Any]] = {}
    for result in results:
        if result.license_status in ("NONCOMMERCIAL_ONLY", "RESEARCH_ONLY", "UNKNOWN_BLOCKED"):
            continue
        score = (result.metrics.get("prompt_compliance", 0) + result.metrics.get("consistency", 0)
                 + result.metrics.get("anatomy", 0) - result.metrics.get("failure_rate", 0))
        current = winners.get(result.category)
        if current is None or score > current["score"]:
            winners[result.category] = {"model": result.model, "score": round(score, 4),
                                        "metrics": result.metrics}
    report = {"winners_by_task": winners,
              "note": "no universal winner required; blocked-license models cannot win",
              "results": [r.model_dump() for r in results]}
    atomic_write_json(out_path, report)
    return report
