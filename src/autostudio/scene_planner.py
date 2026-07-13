from __future__ import annotations

from collections import OrderedDict

from .config import StudioConfig
from .hashing import hash_value
from .logging_utils import configure_logging
from .schemas import AssetRequirement, SceneObject, Storyboard
from .storyboard_generator import ALLOWED_ASSET_TYPES


class ScenePlanner:
    """Normalises the storyboard: clamps asset types, dedupes IDs, builds the global
    asset catalogue, and guarantees every requirement has a valid on-canvas placement."""

    def __init__(self, config: StudioConfig):
        self.config = config
        self.logger = configure_logging("autostudio.scene_planner")

    def _normalize_requirement(self, requirement: AssetRequirement) -> AssetRequirement:
        asset_type = requirement.asset_type.lower().strip().replace(" ", "_")
        if asset_type not in ALLOWED_ASSET_TYPES:
            asset_type = "generic_object"
        asset_id = requirement.asset_id.strip() or f"{asset_type}-{hash_value(requirement.source_prompt)[:8]}"
        return requirement.model_copy(update={"asset_type": asset_type, "asset_id": asset_id})

    def _default_object(self, asset_id: str, index: int, count: int) -> SceneObject:
        if count == 1:
            return SceneObject(asset_id=asset_id, x=0.18, y=0.28, width=0.64, height=0.42)
        columns = min(3, count)
        row, col = divmod(index, columns)
        return SceneObject(asset_id=asset_id, x=0.08 + col * (0.84 / columns), y=0.30 + row * 0.24, width=0.24, height=0.24, z_index=index)

    def plan(self, storyboard: Storyboard) -> Storyboard:
        catalog: OrderedDict[str, AssetRequirement] = OrderedDict()
        scenes = []
        for scene in storyboard.scenes:
            requirements = [self._normalize_requirement(item) for item in scene.asset_requirements]
            unique: OrderedDict[str, AssetRequirement] = OrderedDict()
            for requirement in requirements:
                unique.setdefault(requirement.asset_id, requirement)
                catalog.setdefault(requirement.asset_id, requirement)
            limited = list(unique.values())[:self.config.svg.max_scene_objects]
            valid_ids = {item.asset_id for item in limited}
            objects = [obj for obj in scene.objects if obj.asset_id in valid_ids]
            existing = {obj.asset_id for obj in objects}
            for index, requirement in enumerate(limited):
                if requirement.asset_id not in existing:
                    objects.append(self._default_object(requirement.asset_id, index, len(limited)))
            clamped = []
            for obj in objects[:self.config.svg.max_scene_objects]:
                clamped.append(obj.model_copy(update={
                    "width": max(0.04, min(obj.width, 1.0 - obj.x)),
                    "height": max(0.04, min(obj.height, 1.0 - obj.y)),
                }))
            scenes.append(scene.model_copy(update={"objects": sorted(clamped, key=lambda item: item.z_index), "asset_requirements": limited}))
        result = storyboard.model_copy(update={
            "scenes": scenes, "asset_catalog": list(catalog.values()),
            "estimated_duration_s": round(sum(scene.duration_s for scene in scenes), 3),
        })
        return result.model_copy(update={"storyboard_hash": hash_value(result.model_dump(exclude={"storyboard_hash"}))})
