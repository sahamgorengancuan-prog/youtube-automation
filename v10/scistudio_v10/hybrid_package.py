from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageFilter

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
                HybridLayer(
                    layer_id="temporal-beauty",
                    kind="video_clip",
                    path=temporal_clip_path,
                    z_index=0,
                )
            )
            if overlay_path:
                layers.append(
                    HybridLayer(
                        layer_id="scientific-overlay",
                        kind="svg_overlay",
                        path=overlay_path,
                        z_index=100,
                    )
                )
            package = HybridScenePackage(
                scene_id=scene.scene_id,
                duration_frames=animation.duration_frames,
                fps=animation.fps,
                canvas=(
                    int(self.config.get("width", 1080)),
                    int(self.config.get("height", 1920)),
                ),
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

        # Depth order is preserved from the grounded object depth, not flattened
        # to a single z. Moving objects sit above the clean background plate and
        # occlude each other by depth.
        depth_z = {"background": 6, "midground": 15, "foreground": 25, "overlay": 90}
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
            layer_depth = getattr(layer, "depth", "midground") or "midground"
            layers.append(
                HybridLayer(
                    layer_id=target,
                    kind="pose_sequence" if pose_cutouts else "raster",
                    path=str(cutout_path),
                    z_index=depth_z.get(str(layer_depth), 15) + list(sorted(animated_targets)).index(target),
                    bbox=getattr(layer, "bbox", (0.0, 0.0, 1.0, 1.0)),
                    pivot=getattr(layer, "pivot", (0.5, 0.5)),
                    pose_paths=pose_cutouts,
                )
            )

        # Clean background plate: remove the moving objects AND reconstruct the
        # background behind them (inpaint), so a moving object never reveals a
        # transparent hole or flat fill. Without moving objects, the beauty frame
        # is already a clean plate.
        if moving_masks:
            union = moving_masks[0]
            for mask in moving_masks[1:]:
                union = ImageChops.lighter(union, mask)
            base = self._clean_plate(beauty.convert("RGB"), union).convert("RGBA")
        else:
            base = beauty
        base_path = out_dir / "beauty_base.png"
        base.save(base_path)
        layers.insert(
            0,
            HybridLayer(layer_id="beauty-base", kind="raster", path=str(base_path), z_index=0),
        )
        if overlay_path:
            layers.append(
                HybridLayer(
                    layer_id="scientific-overlay",
                    kind="svg_overlay",
                    path=overlay_path,
                    z_index=100,
                )
            )

        package = HybridScenePackage(
            scene_id=scene.scene_id,
            duration_frames=animation.duration_frames,
            fps=animation.fps,
            canvas=(
                int(self.config.get("width", 1080)),
                int(self.config.get("height", 1920)),
            ),
            narration=scene.narration,
            headline=scene.headline,
            layers=layers,
            animation=animation,
            voice_path=voice_path,
            transition=scene.transition,
        )
        save_json(out_dir / "hybrid_scene.json", package)
        return package

    def _clean_plate(self, beauty_rgb: Image.Image, hole_mask: Image.Image) -> Image.Image:
        """Reconstruct the background behind the moving objects.

        Uses OpenCV Telea inpainting when available (best quality); otherwise a
        deterministic PIL fallback that bleeds surrounding colour into the holes
        (dilate + heavy blur composited under the hole), which is good enough for
        a plate that is mostly re-covered by the object at rest and only revealed
        as the object moves."""
        hole = hole_mask.convert("L").resize(beauty_rgb.size)
        # Dilate the hole slightly so object edges/halos are also replaced.
        hole = hole.filter(ImageFilter.MaxFilter(7))
        try:
            import cv2
            import numpy as np

            arr = np.array(beauty_rgb)[:, :, ::-1].copy()  # RGB->BGR
            m = (np.array(hole) > 128).astype("uint8") * 255
            radius = int(self.config.get("inpaint_radius", 6))
            filled = cv2.inpaint(arr, m, radius, cv2.INPAINT_TELEA)
            return Image.fromarray(filled[:, :, ::-1])  # BGR->RGB
        except Exception:
            # PIL fallback: iteratively bleed neighbouring colour into the hole.
            result = beauty_rgb.copy()
            for _ in range(int(self.config.get("inpaint_passes", 6))):
                spread = result.filter(ImageFilter.GaussianBlur(18))
                result.paste(spread, (0, 0), hole)
            return result
