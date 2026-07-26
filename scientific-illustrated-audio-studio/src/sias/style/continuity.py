"""Continuity strategy: a previous approved scene is attached as reference only
when the scene declares continuity_refs — never by default."""

from __future__ import annotations

from ..schemas import ReferenceAsset, SceneSpec


def continuity_reference(
    scene: SceneSpec, approved_scenes: dict[str, str]
) -> ReferenceAsset | None:
    for ref_scene_id in scene.continuity_refs:
        path = approved_scenes.get(ref_scene_id)
        if path:
            return ReferenceAsset(
                ref_id=f"prev_{ref_scene_id}",
                kind="previous_scene",
                path=path,
                reason=f"declared continuity with {ref_scene_id}",
            )
    return None
