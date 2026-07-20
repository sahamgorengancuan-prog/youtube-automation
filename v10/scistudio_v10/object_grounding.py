"""Object grounding — locate causal objects in the approved beauty frame.

Runs *before* segmentation. The vision director (Qwen VL via the LLM router)
looks at the actual beauty frame and returns, for each causal object, a real
bounding box, SAM2 prompt points, depth order, a pivot, and the before→after
states. SAM2 then executes those prompts instead of guessing the image centre.

A deterministic fallback grounds one object per motion seam from the scene
architecture so the pipeline still produces a usable manifest offline (no vision
call), but the grounding is then only approximate — the mask QC gate downstream
is what decides whether a mask is trustworthy.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schemas import ObjectManifest, SceneObjectManifest, SceneIllustrationArchitecture
from .utils import ensure_dir, save_json

_GROUND_SYSTEM = (
    "You are a vision grounding director. Look at the supplied approved illustration and locate the specific "
    "objects that carry the scene's causal change. Return JSON only."
)


class ObjectGrounder:
    def __init__(self, llm: Any, config: dict[str, Any], root: str | Path):
        self.llm = llm
        self.config = config
        self.root = ensure_dir(root)

    def ground(
        self,
        beauty_path: str | Path,
        architecture: SceneIllustrationArchitecture,
        *,
        force: bool = False,
    ) -> SceneObjectManifest:
        beauty_path = str(beauty_path)
        cached = self.root / f"{architecture.scene_id}_objects.json"
        if cached.exists() and not force:
            try:
                return SceneObjectManifest.model_validate(json.loads(cached.read_text()))
            except Exception:
                pass
        manifest = self._vision_ground(beauty_path, architecture, force=force)
        if manifest is None or not manifest.objects:
            manifest = self._fallback(beauty_path, architecture)
        # Always clamp coordinates and guarantee a causal subject exists.
        manifest.objects = [self._sanitize(o) for o in manifest.objects]
        if manifest.objects and not any(o.is_causal_subject for o in manifest.objects):
            manifest.objects[0].is_causal_subject = True
        save_json(cached, manifest)
        return manifest

    # -- vision-director grounding -----------------------------------------
    def _vision_ground(
        self, beauty_path: str, architecture: SceneIllustrationArchitecture, *, force: bool
    ) -> SceneObjectManifest | None:
        if self.llm is None or not hasattr(self.llm, "critique_image"):
            return None
        seams = [{"seam_id": s.seam_id, "subject": s.subject, "region": s.region} for s in architecture.motion_seams]
        prompt = f"""The scene's causal change is carried by these objects (seam_id -> subject):
{json.dumps(seams, ensure_ascii=False)}

For EACH object return an entry in an "objects" JSON array with:
- object_id, seam_id (matching above), label
- bbox as [x0,y0,x1,y1] normalized 0..1 tightly around the object in THIS image
- positive_points: 1-3 [x,y] points (0..1) clearly INSIDE the object
- negative_points: 0-2 [x,y] points on nearby things that are NOT the object
- depth: one of background|midground|foreground|overlay
- pivot: [x,y] the natural anchor the object moves around
- initial_state and target_state: the before and after of the causal change
- is_causal_subject: true for the main object that carries the change
- grounding_confidence: 0..1
Return {{"objects": [...]}} only."""
        try:
            raw = self.llm.critique_image(
                image_path=beauty_path,
                prompt=prompt,
                namespace=f"grounding_{architecture.scene_id}",
                fallback=None,
                force=force,
            )
        except Exception:
            return None
        if not isinstance(raw, dict):
            return None
        objects = raw.get("objects", raw if isinstance(raw, list) else [])
        try:
            manifest = SceneObjectManifest(
                scene_id=architecture.scene_id,
                beauty_frame_path=beauty_path,
                objects=[ObjectManifest.model_validate(o) for o in objects if isinstance(o, dict)],
                grounding_source="vision-director",
            )
        except Exception:
            return None
        return manifest

    # -- deterministic fallback --------------------------------------------
    def _fallback(self, beauty_path: str, architecture: SceneIllustrationArchitecture) -> SceneObjectManifest:
        objects: list[ObjectManifest] = []
        seams = architecture.motion_seams or []
        for index, seam in enumerate(seams):
            # Spread fallback boxes across the frame instead of stacking them all
            # at the centre, and place a positive point at each box centre.
            band = 1.0 / max(1, len(seams))
            y0 = min(0.62, 0.12 + index * band * 0.5)
            box = (0.18, y0, 0.82, min(0.94, y0 + 0.5))
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            objects.append(
                ObjectManifest(
                    object_id=f"obj-{seam.seam_id}",
                    label=seam.subject[:60],
                    seam_id=seam.seam_id,
                    bbox=box,
                    positive_points=[(cx, cy)],
                    depth="foreground" if index == 0 else "midground",
                    pivot=(cx, cy),
                    initial_state="initial",
                    target_state=seam.subject[:80],
                    is_causal_subject=index == 0,
                    grounding_confidence=0.25,
                )
            )
        return SceneObjectManifest(
            scene_id=architecture.scene_id,
            beauty_frame_path=beauty_path,
            objects=objects,
            grounding_source="deterministic-fallback",
        )

    @staticmethod
    def _sanitize(obj: ObjectManifest) -> ObjectManifest:
        def clamp01(v: float) -> float:
            return max(0.0, min(1.0, float(v)))

        x0, y0, x1, y1 = obj.bbox
        x0, x1 = sorted((clamp01(x0), clamp01(x1)))
        y0, y1 = sorted((clamp01(y0), clamp01(y1)))
        if x1 - x0 < 0.02:
            x0, x1 = max(0.0, x0 - 0.05), min(1.0, x1 + 0.05)
        if y1 - y0 < 0.02:
            y0, y1 = max(0.0, y0 - 0.05), min(1.0, y1 + 0.05)
        obj.bbox = (x0, y0, x1, y1)
        obj.pivot = (clamp01(obj.pivot[0]), clamp01(obj.pivot[1]))
        obj.positive_points = [(clamp01(p[0]), clamp01(p[1])) for p in obj.positive_points]
        obj.negative_points = [(clamp01(p[0]), clamp01(p[1])) for p in obj.negative_points]
        if not obj.positive_points:
            obj.positive_points = [((x0 + x1) / 2, (y0 + y1) / 2)]
        return obj
