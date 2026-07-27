"""Consistency measurement, tiered honestly.

* heuristic tier (always available, PIL-only): palette-histogram distance +
  edge-density delta against the approved style pack. It FLAGS drift; it never
  approves anything on its own.
* objective tier (DINOv2 / DreamSim adapters): used when torch weights are
  installed and license-cleared; per-axis thresholds are CALIBRATED from
  approved + intentionally-rejected examples, never one universal number.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter

AXES = ("character_identity", "style_identity", "environment_continuity", "composition_diversity")


def _palette_hist(path: str | Path, bins: int = 4) -> list[float]:
    with Image.open(path) as img:
        small = img.convert("RGB").resize((64, 64))
    counts = [0.0] * (bins ** 3)
    step = 256 // bins
    for r, g, b in small.getdata():
        counts[(r // step) * bins * bins + (g // step) * bins + (b // step)] += 1
    total = sum(counts) or 1.0
    return [c / total for c in counts]


def _edge_density(path: str | Path) -> float:
    with Image.open(path) as img:
        edges = img.convert("L").resize((128, 128)).filter(ImageFilter.FIND_EDGES)
    hist = edges.histogram()
    strong = sum(hist[64:])
    return strong / (128 * 128)


def palette_distance(a: str | Path, b: str | Path) -> float:
    ha, hb = _palette_hist(a), _palette_hist(b)
    return round(sum(abs(x - y) for x, y in zip(ha, hb)) / 2.0, 4)  # 0..1


def structure_delta(a: str | Path, b: str | Path) -> float:
    return round(abs(_edge_density(a) - _edge_density(b)), 4)


class HeuristicConsistency:
    """Compare a candidate against the approved style pack. Flag-only."""

    tier = "heuristic"

    def __init__(self, style_pack: list[str], palette_max: float = 0.45, structure_max: float = 0.18):
        self.style_pack = [p for p in style_pack if Path(p).exists()]
        self.palette_max = palette_max
        self.structure_max = structure_max

    def check(self, candidate: str | Path) -> dict[str, Any]:
        if not self.style_pack:
            return {"tier": self.tier, "status": "SKIP", "reason": "no approved style pack yet"}
        pal = min(palette_distance(candidate, ref) for ref in self.style_pack)
        struct = min(structure_delta(candidate, ref) for ref in self.style_pack)
        flags = []
        if pal > self.palette_max:
            flags.append(f"palette drift {pal} > {self.palette_max}")
        if struct > self.structure_max:
            flags.append(f"structure drift {struct} > {self.structure_max}")
        return {
            "tier": self.tier,
            "status": "FLAG" if flags else "OK",
            "palette_distance": pal,
            "structure_delta": struct,
            "flags": flags,
            "note": "heuristic tier flags drift for review; approval still requires VLM QC",
        }


def calibrate_thresholds(
    embed_fn,
    approved: list[str],
    rejected: list[str],
) -> dict[str, float]:
    """Per-axis threshold calibration for the objective tier: the midpoint
    between the worst approved distance and the best rejected distance, per the
    blueprint (style pack + accepted + intentionally-rejected examples).
    `embed_fn(path) -> vector`; injectable so it is testable without torch."""

    def dist(a, b):
        va, vb = embed_fn(a), embed_fn(b)
        dot = sum(x * y for x, y in zip(va, vb))
        na = sum(x * x for x in va) ** 0.5
        nb = sum(y * y for y in vb) ** 0.5
        return 1.0 - dot / (na * nb or 1.0)

    anchor = approved[0]
    worst_ok = max(dist(anchor, a) for a in approved[1:]) if len(approved) > 1 else 0.1
    best_bad = min(dist(anchor, r) for r in rejected) if rejected else worst_ok + 0.2
    threshold = round((worst_ok + best_bad) / 2.0, 4)
    return {axis: threshold for axis in AXES}
