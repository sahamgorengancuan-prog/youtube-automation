"""Character Director — author an explicit CharacterManifest per scene.

The rig must not depend on guessing "is there a person?" from narration keywords.
This director produces an explicit contract: which characters are present, where
(bbox), and whether they must be articulated. Two backends:

* **Vision backend** (production): a VL model (Qwen via the LLM router) looks at
  the approved beauty frame and reports the characters it actually sees, with a
  bbox and a ``requires_articulation`` flag and a ``pose_intent`` for the shot.
* **Deterministic fallback** (offline/no vision): derives characters from the
  architecture's authored ``figure_construction`` (structured data the scene
  architect already produced) — not a narration keyword scan.

Downstream, ``requires_articulation=True`` with no rig built is a hard failure
(see the pipeline quality gate), so a declared character is always animated or
the run stops.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schemas import Character, CharacterManifest, SceneIllustrationArchitecture
from .utils import ensure_dir, save_json


class CharacterDirector:
    def __init__(self, llm: Any, config: dict[str, Any] | None = None, root: str | Path | None = None):
        self.llm = llm
        self.config = config or {}
        self.root = ensure_dir(root) if root is not None else None

    def author(
        self,
        scene_id: str,
        architecture: SceneIllustrationArchitecture,
        beauty_path: str | Path | None = None,
        *,
        force: bool = False,
    ) -> CharacterManifest:
        manifest = self._fallback(scene_id, architecture)
        if self.llm is not None and beauty_path and Path(beauty_path).exists():
            try:
                vision = self._vision(scene_id, beauty_path, architecture)
                if vision is not None and vision.characters:
                    manifest = vision
            except Exception:
                pass
        if self.root is not None:
            save_json(self.root / f"{scene_id}_characters.json", manifest)
        return manifest

    # -- vision backend -----------------------------------------------------
    def _vision(
        self, scene_id: str, beauty_path: str | Path, architecture: SceneIllustrationArchitecture
    ) -> CharacterManifest | None:
        prompt = (
            "You are a character director for an animated science short. Look at this frame and report every "
            "human/creature CHARACTER that should be animated (not props, not background). Return strict JSON: "
            '{"characters":[{"character_id":"...","present":true,"bbox":[x0,y0,x1,y1],'
            '"body_orientation":"front|front_three_quarter|profile|back","requires_articulation":true,'
            '"pose_intent":"one of wave|nod|gesture_present|gesture_both|point_left|step|reach_up|idle_breath"}]}. '
            'bbox is normalized 0..1. If there is no character, return {"characters":[]}.'
        )
        raw = self.llm.critique_image(
            image_path=str(beauty_path),
            prompt=prompt,
            namespace=f"character_director_{scene_id}",
            fallback=None,
        )
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception:
                return None
        if not isinstance(raw, dict):
            return None
        chars: list[Character] = []
        for i, c in enumerate(raw.get("characters", []) or []):
            if not isinstance(c, dict) or not c.get("present", True):
                continue
            try:
                chars.append(
                    Character(
                        character_id=str(c.get("character_id") or f"char_{i + 1:02d}"),
                        present=True,
                        bbox=self._clamp_bbox(c.get("bbox")),
                        body_orientation=str(c.get("body_orientation") or "front_three_quarter"),
                        requires_articulation=bool(c.get("requires_articulation", True)),
                        pose_intent=str(c.get("pose_intent") or ""),
                    )
                )
            except Exception:
                continue
        if not chars:
            return None
        return CharacterManifest(scene_id=scene_id, grounding_source="vision-director", characters=chars)

    # -- deterministic fallback --------------------------------------------
    def _fallback(self, scene_id: str, architecture: SceneIllustrationArchitecture) -> CharacterManifest:
        chars: list[Character] = []
        figures = getattr(architecture, "figure_construction", []) or []
        # Spread character bboxes across the focal band when there are several.
        for i, figure in enumerate(figures):
            fid = getattr(figure, "figure_id", f"char_{i + 1:02d}")
            orient = getattr(figure, "body_orientation", "front_three_quarter")
            bbox = self._focal_bbox(architecture, i, len(figures))
            chars.append(
                Character(
                    character_id=str(fid),
                    present=True,
                    bbox=bbox,
                    body_orientation=str(orient),
                    requires_articulation=True,
                    pose_intent="",
                )
            )
        return CharacterManifest(scene_id=scene_id, grounding_source="deterministic-fallback", characters=chars)

    @staticmethod
    def _focal_bbox(
        architecture: SceneIllustrationArchitecture, index: int, total: int
    ) -> tuple[float, float, float, float]:
        total = max(1, total)
        width = min(0.5, 0.8 / total)
        cx = (index + 0.5) / total
        x0 = max(0.0, min(1.0 - width, cx - width / 2))
        return (round(x0, 3), 0.12, round(x0 + width, 3), 0.96)

    @staticmethod
    def _clamp_bbox(raw: Any) -> tuple[float, float, float, float]:
        try:
            x0, y0, x1, y1 = (float(v) for v in raw)
        except Exception:
            return (0.3, 0.12, 0.7, 0.96)
        x0, x1 = sorted((max(0.0, min(1.0, x0)), max(0.0, min(1.0, x1))))
        y0, y1 = sorted((max(0.0, min(1.0, y0)), max(0.0, min(1.0, y1))))
        if x1 - x0 < 0.02:
            x0, x1 = max(0.0, x0 - 0.1), min(1.0, x1 + 0.1)
        if y1 - y0 < 0.02:
            y0, y1 = max(0.0, y0 - 0.1), min(1.0, y1 + 0.1)
        return (round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3))
