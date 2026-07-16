from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageOps

from .schemas import (
    AnimationPlan,
    HybridLayer,
    HybridScenePackage,
    SceneRequest,
    SemanticLayerContract,
)
from .utils import ensure_dir, save_json


class HybridPackageBuilder:
    """Converts an approved beauty frame and masks into renderable layers.

    Visible art is always sampled from the approved beauty/pose frames. Masks
    only control alpha. This avoids vector restyling and preserves the studio's
    authored line quality.
    """

    def __init__(self, config: dict[str, Any], root: str | Path):
        self.config = config
        self.root = ensure_dir(root)

    def build(
        self,
        scene: SceneRequest,
        contract: SemanticLayerContract,
        animation: AnimationPlan,
        overlay_path: str,
        voice_path: str = "",
        temporal_clip_path: str = "",
        temporal_backend: str = "",
    ) -> HybridScenePackage:
        out_dir = ensure_dir(self.root / scene.scene_id)
        beauty = Image.open(contract.beauty_frame_path).convert("RGBA")
        moving_masks: list[Image.Image] = []
        layers: list[HybridLayer] = []

        if temporal_clip_path and Path(temporal_clip_path).exists():
            layers.append(
                HybridLayer(layer_id="temporal-beauty", kind="video_clip", path=temporal_clip_path, z_index=0)
            )
            if overlay_path:
                layers.append(
                    HybridLayer(layer_id="scientific-overlay", kind="svg_overlay", path=overlay_path, z_index=100)
                )
            package = HybridScenePackage(
                scene_id=scene.scene_id,
                duration_frames=animation.duration_frames,
                fps=animation.fps,
                canvas=(int(self.config.get("width", 1080)), int(self.config.get("height", 1920))),
                narration=scene.narration,
                headline=scene.headline,
                layers=layers,
                animation=animation,
                voice_path=voice_path,
                transition=scene.transition,
                temporal_clip_path=temporal_clip_path,
                temporal_backend=temporal_backend,
            )
            save_json(out_dir / "hybrid_scene.json", package)
            return package

        semantic = {layer.layer_id: layer for layer in contract.layers}
        animated_targets = {event.target_layer for event in animation.events}
        for target in sorted(animated_targets):
            layer = semantic.get(target)
            if layer is None or not layer.mask_path:
                continue
            mask = Image.open(layer.mask_path).convert("L").resize(beauty.size)
            moving_masks.append(mask)
            cutout = Image.new("RGBA", beauty.size, (0, 0, 0, 0))
            cutout.paste(beauty, (0, 0), mask)
            cutout_path = out_dir / f"{target}_cutout.png"
            cutout.save(cutout_path)
            pose_cutouts: list[str] = []
            for i, pose_path in enumerate(layer.pose_variant_paths):
                pose = Image.open(pose_path).convert("RGBA").resize(beauty.size)
                pose_cutout = Image.new("RGBA", beauty.size, (0, 0, 0, 0))
                pose_cutout.paste(pose, (0, 0), mask)
                pose_out = out_dir / f"{target}_pose_{i:02d}.png"
                pose_cutout.save(pose_out)
                pose_cutouts.append(str(pose_out))
            layers.append(
                HybridLayer(
                    layer_id=target,
                    kind="pose_sequence" if pose_cutouts else "raster",
                    path=str(cutout_path),
                    z_index=20,
                    pose_paths=pose_cutouts,
                )
            )

        if moving_masks:
            union = moving_masks[0]
            for mask in moving_masks[1:]:
                union = ImageChops.lighter(union, mask)
            inverse = ImageOps.invert(union)
            base = Image.new("RGBA", beauty.size, (0, 0, 0, 0))
            base.paste(beauty, (0, 0), inverse)
        else:
            base = beauty
        base_path = out_dir / "beauty_base.png"
        base.save(base_path)
        layers.insert(0, HybridLayer(layer_id="beauty-base", kind="raster", path=str(base_path), z_index=0))
        if overlay_path:
            layers.append(
                HybridLayer(layer_id="scientific-overlay", kind="svg_overlay", path=overlay_path, z_index=100)
            )

        package = HybridScenePackage(
            scene_id=scene.scene_id,
            duration_frames=animation.duration_frames,
            fps=animation.fps,
            canvas=(int(self.config.get("width", 1080)), int(self.config.get("height", 1920))),
            narration=scene.narration,
            headline=scene.headline,
            layers=layers,
            animation=animation,
            voice_path=voice_path,
            transition=scene.transition,
        )
        save_json(out_dir / "hybrid_scene.json", package)
        return package
