"""Reference hierarchy (§13). Never send all references indiscriminately; the
selection (and what was omitted, and why) is recorded."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..filesystem import sha256_file
from ..schemas import ReferenceAsset, SceneSpec

ORDER = [
    "master_style_board",
    "character_sheet",
    "prop_sheet",
    "environment_anchor",
    "previous_scene",
    "pose_sketch",
    "special_object",
    "special_object_secondary",
]


def select_references(
    scene: SceneSpec,
    available: list[ReferenceAsset],
    max_references: int = 8,
) -> dict[str, Any]:
    by_kind: dict[str, list[ReferenceAsset]] = {}
    for ref in available:
        by_kind.setdefault(ref.kind, []).append(ref)

    selected: list[ReferenceAsset] = []
    reasons: dict[str, str] = {}
    for kind in ORDER:
        if len(selected) >= max_references:
            break
        pool = by_kind.get(kind, [])
        if not pool:
            continue
        if kind == "previous_scene" and not scene.continuity_refs:
            reasons[kind] = "omitted: scene declares no continuity refs"
            continue
        if kind == "character_sheet" and not scene.characters:
            reasons[kind] = "omitted: no characters in scene"
            continue
        ref = pool[0]
        if ref.path and Path(ref.path).exists() and not ref.sha256:
            ref.sha256 = sha256_file(ref.path)
        selected.append(ref)
        reasons[kind] = f"selected: {ref.ref_id}"

    omitted = [r.ref_id for r in available if r not in selected]
    return {
        "scene_id": scene.scene_id,
        "selected": [r.model_dump() for r in selected],
        "omitted": omitted,
        "reasons": reasons,
        "count": len(selected),
        "hashes": [r.sha256 for r in selected],
    }
