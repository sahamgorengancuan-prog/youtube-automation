from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schemas import SceneIllustrationArchitecture, SceneRequest, ShotState
from .utils import ensure_dir, load_json, save_json


class ShotStatePlanner:
    SYSTEM = """You are a shot-state director. Return JSON only. Define a precise first frame, last frame,
invariants, changed elements and the motion bridge. The last frame must be reachable from the first without
restyling or recomposing unaffected regions. Prefer locked camera and local causal change."""

    def __init__(self, llm: Any, config: dict[str, Any], root: str | Path):
        self.llm = llm
        self.config = config
        self.root = ensure_dir(root)

    def plan(
        self, scene: SceneRequest, architecture: SceneIllustrationArchitecture, *, force: bool = False
    ) -> ShotState:
        path = self.root / f"{scene.scene_id}.json"
        if path.exists() and not force:
            return ShotState.model_validate(load_json(path))
        fallback = self._fallback(scene, architecture)
        raw = self.llm.generate_json(
            system=self.SYSTEM,
            prompt=f"Scene: {json.dumps(scene.model_dump(mode='json'), ensure_ascii=False)}\nArchitecture: {json.dumps(architecture.model_dump(mode='json'), ensure_ascii=False)}",
            namespace=f"v10_shot_state_{scene.scene_id}",
            fallback=fallback.model_dump(mode="json"),
            force=force,
        )
        try:
            state = ShotState.model_validate(raw)
        except Exception:
            state = fallback
        state.scene_id = scene.scene_id
        save_json(path, state)
        return state

    @staticmethod
    def _fallback(scene: SceneRequest, architecture: SceneIllustrationArchitecture) -> ShotState:
        changed = [s.region for s in architecture.motion_seams]
        complexity = (
            "hold"
            if not changed
            else (
                "organic"
                if any(s.method in {"local_deformation", "texture_loop"} for s in architecture.motion_seams)
                else "articulated"
            )
        )
        return ShotState(
            scene_id=scene.scene_id,
            first_frame_description=f"Established approved composition before {scene.desired_change or scene.visual_event}.",
            last_frame_description=f"The same composition after the causal change: {scene.desired_change or scene.visual_event}.",
            invariant_elements=["camera", "palette", "line language", "unaffected environment", "subject identity"],
            changed_elements=changed,
            motion_bridge=[f"locally transform {x}" for x in changed] or ["deliberate hold"],
            preserve_regions=[p.contents for p in architecture.depth_planes if p.movement_role == "hold"],
            control_sketch_required=complexity in {"articulated", "deformation", "organic"},
            control_sketch_regions=changed,
            temporal_complexity=complexity,
        )
