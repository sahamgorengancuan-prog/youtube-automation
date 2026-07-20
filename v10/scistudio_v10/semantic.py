from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PIL import Image

from .llm import LLMRouter
from .schemas import (
    BeautyFrame,
    SceneIllustrationArchitecture,
    SemanticLayer,
    SemanticLayerContract,
)
from .utils import ensure_dir, save_json


class SemanticSeparationPlanner:
    """Plans the minimum semantic separation after beauty approval.

    The approved beauty image remains the source of visible pixels. Generated or
    external segmentation is used primarily to make masks, so animation control
    does not redraw the entire artwork or degrade its style.
    """

    def __init__(self, config: dict[str, Any], root: str | Path):
        self.config = config
        self.root = ensure_dir(root)

    def plan(
        self,
        architecture: SceneIllustrationArchitecture,
        beauty: BeautyFrame,
        pose_variants: dict[str, str] | None = None,
    ) -> SemanticLayerContract:
        if not beauty.approved and self.config.get("require_approved_beauty", True):
            raise RuntimeError("Semantic separation must occur after explicit beauty-frame approval")
        pose_variants = pose_variants or {}
        layers = [
            SemanticLayer(
                layer_id="beauty-base",
                description="Approved full-scene beauty frame",
                source_region="entire canvas",
                extraction_method="full_frame",
                z_index=0,
                locked=True,
                image_path=beauty.image_path,
            )
        ]
        for index, seam in enumerate(architecture.motion_seams, 1):
            related_poses = [path for key, path in pose_variants.items() if key.startswith(seam.seam_id + "-")]
            layers.append(
                SemanticLayer(
                    layer_id=seam.seam_id,
                    description=seam.subject,
                    source_region=seam.region,
                    extraction_method="external_mask" if self.config.get("mask_provider") else "kontext_isolation",
                    z_index=10 + index,
                    locked=False,
                    pose_variant_paths=related_poses,
                )
            )
        # Scientific overlay / HUD is OPT-IN, not forced. Shorts are narrated by
        # audio, so on-screen UI appears only when explicitly enabled (config)
        # or when the director actually authored annotations for this scene.
        overlay_requested = bool(self.config.get("enable_scientific_overlay", False)) or bool(
            getattr(architecture, "scientific_annotations", None)
        )
        if overlay_requested:
            layers.append(
                SemanticLayer(
                    layer_id="scientific-overlay",
                    description="Experiment UI, labels, arrows, metrics and scientific annotations",
                    source_region="overlay safe zones",
                    extraction_method="scientific_overlay",
                    z_index=100,
                    locked=True,
                )
            )
        contract = SemanticLayerContract(
            scene_id=architecture.scene_id,
            beauty_frame_path=beauty.image_path,
            layers=layers,
            extraction_notes=[
                "The approved beauty frame remains the visible pixel source.",
                "Masks isolate only the smallest moving regions.",
                "No whole-scene vectorization or primitive reconstruction is permitted.",
                "Replacement poses inherit the approved scene as their Kontext init image.",
            ],
        )
        save_json(self.root / "contracts" / f"{architecture.scene_id}.json", contract)
        return contract


class SemanticMaskExtractor:
    def __init__(
        self,
        llm: LLMRouter,
        config: dict[str, Any],
        root: str | Path,
        mask_generator: Callable[[str, str, Path], Path | None] | None = None,
    ):
        self.llm = llm
        self.config = config
        self.root = ensure_dir(root)
        self.mask_generator = mask_generator
        # Object segmentation (SAM2 with a local heuristic fallback) is the
        # preferred mask source: accurate, cheap and offline-capable. It only
        # yields alpha masks — visible pixels always come from the beauty frame,
        # so authored line quality is preserved and no image is vectorized.
        self._segmenter = None
        if mask_generator is None and bool(self.config.get("use_object_segmentation", True)):
            try:
                from .sam2_segment import ObjectSegmenter

                self._segmenter = ObjectSegmenter(self.config, self.root / "segments")
            except Exception:
                self._segmenter = None

    def extract_all(
        self,
        contract: SemanticLayerContract,
        architecture: SceneIllustrationArchitecture,
        *,
        force: bool = False,
    ) -> SemanticLayerContract:
        seam_map = {m.seam_id: m for m in architecture.motion_seams}
        for layer in contract.layers:
            if layer.extraction_method not in {"kontext_isolation", "external_mask"}:
                continue
            target = seam_map.get(layer.layer_id)
            if target is None:
                continue
            output = self.root / architecture.scene_id / f"{layer.layer_id}_mask.png"
            ensure_dir(output.parent)
            if output.exists() and output.stat().st_size > 256 and not force:
                layer.mask_path = str(output)
                continue
            if self.mask_generator is not None:
                result = self.mask_generator(contract.beauty_frame_path, target.region, output)
            elif self._segmenter is not None:
                # SAM2 (or heuristic) local segmentation — no paid image call.
                result = self._segmenter.mask_for(contract.beauty_frame_path, target.region, output)
                if result is None:  # segmentation failed -> last-resort drawn mask
                    result = self._kontext_mask(contract.beauty_frame_path, target.region, output, force=force)
            else:
                result = self._kontext_mask(contract.beauty_frame_path, target.region, output, force=force)
            if result is None:
                raise RuntimeError(f"Could not create semantic mask for {architecture.scene_id}/{layer.layer_id}")
            self._normalize_mask(result, output)
            layer.mask_path = str(output)
        save_json(
            self.root / "contracts" / f"{architecture.scene_id}_extracted.json",
            contract,
        )
        return contract

    def _kontext_mask(self, beauty_path: str, region: str, output: Path, *, force: bool) -> Path | None:
        raw = output.with_name(output.stem + "_raw.png")
        prompt = f"""Using the supplied approved scientific illustration, create an exact aligned binary segmentation mask.
The target region is: {region}.
Output the target region as pure white (#FFFFFF), every other pixel as pure black (#000000).
Keep exactly the same canvas, camera, scale, silhouette and position as the input. No text, shading, gray edges,
new objects, style transfer or composition change. This is a machine mask, not visible artwork."""
        result = self.llm.generate_reference_image(
            prompt=prompt,
            output_path=raw,
            init_image=beauty_path,
            force=force,
        )
        return result

    @staticmethod
    def _normalize_mask(source: str | Path, output: Path) -> None:
        image = Image.open(source).convert("L")
        # Hard binary mask. Because visible pixels come from the original beauty
        # frame, this normalization cannot degrade the authored line style.
        image = image.point(lambda p: 255 if p >= 128 else 0, mode="1").convert("L")
        image.save(output)
