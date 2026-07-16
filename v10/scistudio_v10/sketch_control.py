from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from .schemas import ControlSketchSpec, ShotState
from .utils import ensure_dir, save_json


class ControlSketchBuilder:
    """Creates invisible motion-control sketches from approved beauty frames.

    These sketches are controls for a temporal model, never visible final art.
    """

    def __init__(self, root: str | Path):
        self.root = ensure_dir(root)

    def build_pair(
        self, scene_id: str, beauty_start: str, beauty_end: str, state: ShotState
    ) -> tuple[ControlSketchSpec, ControlSketchSpec]:
        start = self._edge_sketch(beauty_start, self.root / f"{scene_id}_first.png")
        end = self._edge_sketch(beauty_end or beauty_start, self.root / f"{scene_id}_last.png")
        a = ControlSketchSpec(
            sketch_id=f"{scene_id}-first",
            scene_id=scene_id,
            frame_role="first",
            output_path=start,
            regions=state.control_sketch_regions,
            instructions=state.motion_bridge,
        )
        b = ControlSketchSpec(
            sketch_id=f"{scene_id}-last",
            scene_id=scene_id,
            frame_role="last",
            output_path=end,
            regions=state.control_sketch_regions,
            instructions=state.motion_bridge,
        )
        save_json(
            self.root / f"{scene_id}.json", {"first": a.model_dump(mode="json"), "last": b.model_dump(mode="json")}
        )
        return a, b

    @staticmethod
    def _edge_sketch(source: str, output: str | Path) -> str:
        image = Image.open(source).convert("L").filter(ImageFilter.FIND_EDGES)
        image = image.point(lambda x: 255 if x > 28 else 0)
        image = ImageOps.invert(image)
        output = Path(output)
        ensure_dir(output.parent)
        image.save(output)
        return str(output)
