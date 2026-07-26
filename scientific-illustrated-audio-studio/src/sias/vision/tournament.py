"""Candidate tournament: stable seeds, per-role candidate counts, full review
sequence. Generation and review functions are injected, so the tournament logic
is fully testable offline."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable

from ..exceptions import AssetIntegrityError
from ..schemas import CandidateRecord, SceneSpec, VisionScore
from .consensus import combine

HERO_ROLES = {"cold_open", "gasp_reveal", "payoff"}
MIN_IMAGE_BYTES = 1024


def candidate_count(beat_role: str, normal: int = 2, hero: int = 3) -> int:
    return hero if beat_role in HERO_ROLES else normal


def stable_seed(seed_base: int, scene_id: str, candidate_index: int) -> int:
    digest = hashlib.sha256(f"{seed_base}|{scene_id}|{candidate_index}".encode("utf-8")).hexdigest()
    return int(digest[:12], 16)


def verify_image(path: str | Path, min_bytes: int = MIN_IMAGE_BYTES) -> None:
    p = Path(path)
    if not p.exists():
        raise AssetIntegrityError(f"candidate image missing: {p}", stage="tournament")
    if p.stat().st_size < min_bytes:
        raise AssetIntegrityError(f"candidate image suspiciously small: {p}", stage="tournament")
    try:
        from PIL import Image

        with Image.open(p) as im:
            im.verify()
    except Exception as exc:
        raise AssetIntegrityError(f"candidate image corrupt: {p} ({exc})", stage="tournament") from exc


def run_tournament(
    scene: SceneSpec,
    prompt_hash: str,
    generate_fn: Callable[[SceneSpec, int, int], str],
    qwen_fn: Callable[[str], VisionScore],
    gemini_fn: Callable[[str], VisionScore],
    seed_base: int = 270726,
    candidates_normal: int = 2,
    candidates_hero: int = 3,
    approval_threshold: float = 0.82,
    disagreement_threshold: float = 0.18,
) -> dict[str, Any]:
    """Returns {candidates: [CandidateRecord], winner: id|None, status}."""
    n = candidate_count(scene.beat_role, candidates_normal, candidates_hero)
    records: list[CandidateRecord] = []
    for i in range(n):
        seed = stable_seed(seed_base, scene.scene_id, i)
        image_path = generate_fn(scene, i, seed)
        verify_image(image_path)
        qwen = qwen_fn(image_path)
        gemini = gemini_fn(image_path)
        verdict = combine(qwen, gemini, approval_threshold, disagreement_threshold)
        records.append(
            CandidateRecord(
                candidate_id=f"{scene.scene_id}_C{i + 1:02d}",
                scene_id=scene.scene_id,
                image_path=str(image_path),
                seed=seed,
                prompt_hash=prompt_hash,
                qwen=qwen,
                gemini=gemini,
                consensus_score=verdict["consensus_score"],
                status=verdict["status"],
            )
        )

    ranked = sorted(records, key=lambda r: (-r.consensus_score, r.candidate_id))
    approved = [r for r in ranked if r.status == "APPROVED"]
    review_required = [r for r in ranked if r.status == "REVIEW_REQUIRED"]
    if approved:
        return {"candidates": records, "winner": approved[0].candidate_id, "status": "APPROVED"}
    if review_required:
        return {"candidates": records, "winner": None, "status": "REVIEW_REQUIRED"}
    return {"candidates": records, "winner": None, "status": "REPAIR"}
