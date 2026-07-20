"""Object segmentation for real per-object motion.

The renderer already builds a per-object *cutout* (beauty frame x mask) for each
animated layer, so moving that layer moves only that object — but only if the
mask is accurate. Previously masks were "drawn" by a FLUX Kontext round-trip,
which is expensive and imprecise. This module produces accurate masks locally:

* **SAM2 backend** (preferred, GPU): Segment-Anything-2 / Ultralytics-SAM, box-
  or point-prompted from the layer's region, returns a tight binary mask.
* **Heuristic fallback** (always available, no GPU/model): separates the drawn
  subject from the flat paper background by colour distance from the frame's
  corner background, keeps the dominant region, and (optionally) restricts to a
  region bbox. This keeps the object-separation pipeline working — and testable
  — even without SAM2.

Everything is guarded: on any failure the caller falls back to the next method,
and ultimately the pipeline can still composite whole-frame layers.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter

from .utils import ensure_dir


class ObjectSegmenter:
    """Produces binary object masks for beauty-frame layers (SAM2 or heuristic)."""

    def __init__(self, config: dict[str, Any] | None = None, root: str | Path | None = None):
        self.config = config or {}
        self.root = ensure_dir(root) if root is not None else None
        self.use_sam2 = bool(self.config.get("use_sam2", True))
        self._sam2 = None
        self._sam2_failed = False

    # -- capability ---------------------------------------------------------
    def available_sam2(self) -> bool:
        if not self.use_sam2 or self._sam2_failed:
            return False
        try:  # cheap import probe only — model is lazily loaded on first use
            import importlib.util

            has_ultralytics = importlib.util.find_spec("ultralytics") is not None
            has_sam2 = importlib.util.find_spec("sam2") is not None
            return has_ultralytics or has_sam2
        except Exception:
            return False

    # -- public API ---------------------------------------------------------
    def mask_for(
        self,
        beauty_path: str | Path,
        region: str,
        output: str | Path,
        *,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> Path | None:
        """Return a binary mask for the object in ``region``. Tries SAM2, then a
        deterministic heuristic. ``bbox`` is a normalized (x0,y0,x1,y1) hint."""
        output = Path(output)
        ensure_dir(output.parent)
        if self.available_sam2():
            try:
                result = self._sam2_mask(beauty_path, region, output, bbox)
                if result is not None:
                    return result
            except Exception:
                self._sam2_failed = True  # don't keep retrying a broken backend
        try:
            return self._heuristic_mask(beauty_path, output, bbox)
        except Exception:
            return None

    # -- SAM2 backend (best effort; runs in Colab with a GPU) ---------------
    def _load_sam2(self):
        if self._sam2 is not None:
            return self._sam2
        from ultralytics import SAM  # type: ignore[import-not-found]

        model_id = self.config.get("sam2_model", "sam2_b.pt")
        self._sam2 = SAM(model_id)
        return self._sam2

    def _sam2_mask(
        self,
        beauty_path: str | Path,
        region: str,
        output: Path,
        bbox: tuple[float, float, float, float] | None,
    ) -> Path | None:
        model = self._load_sam2()
        image = Image.open(beauty_path).convert("RGB")
        width, height = image.size
        # Prompt with the region bbox when known, else a centred point.
        predict_kwargs: dict[str, Any] = {"verbose": False}
        if bbox is not None:
            x0, y0, x1, y1 = bbox
            predict_kwargs["bboxes"] = [[x0 * width, y0 * height, x1 * width, y1 * height]]
        else:
            predict_kwargs["points"] = [[width * 0.5, height * 0.5]]
            predict_kwargs["labels"] = [1]
        results = model(str(beauty_path), **predict_kwargs)
        if not results:
            return None
        masks = getattr(results[0], "masks", None)
        if masks is None or getattr(masks, "data", None) is None or len(masks.data) == 0:
            return None
        arr = masks.data[0].cpu().numpy()
        binary = (arr > 0.5).astype("uint8") * 255
        mask_img = Image.fromarray(binary, mode="L").resize((width, height))
        mask_img.point(lambda p: 255 if p >= 128 else 0).save(output)
        return output

    # -- heuristic fallback (deterministic, no GPU) -------------------------
    def _heuristic_mask(
        self,
        beauty_path: str | Path,
        output: Path,
        bbox: tuple[float, float, float, float] | None,
    ) -> Path:
        """Foreground mask = pixels that differ from the flat paper background.

        Works well for flat editorial/explainer art on a light field: samples
        the four corners as background, marks pixels whose colour distance
        exceeds a tolerance, smooths, and optionally clips to a region bbox."""
        image = Image.open(beauty_path).convert("RGB")
        width, height = image.size
        px = image.load()
        corners = [
            px[0, 0],
            px[width - 1, 0],
            px[0, height - 1],
            px[width - 1, height - 1],
        ]
        bg = tuple(sum(c[i] for c in corners) // 4 for i in range(3))
        tol = float(self.config.get("segment_bg_tolerance", 42))
        mask = Image.new("L", (width, height), 0)
        mpx = mask.load()
        # Subsample for speed on large frames, then the mask is smoothed/scaled.
        step = max(1, int(max(width, height) / 512))
        for y in range(0, height, step):
            for x in range(0, width, step):
                r, g, b = px[x, y]
                dist = abs(r - bg[0]) + abs(g - bg[1]) + abs(b - bg[2])
                if dist > tol:
                    for yy in range(y, min(y + step, height)):
                        for xx in range(x, min(x + step, width)):
                            mpx[xx, yy] = 255
        mask = mask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
        if bbox is not None:
            clip = Image.new("L", (width, height), 0)
            cp = clip.load()
            x0, y0, x1, y1 = bbox
            for y in range(int(y0 * height), int(y1 * height)):
                for x in range(int(x0 * width), int(x1 * width)):
                    cp[x, y] = 255
            mask = Image.composite(mask, Image.new("L", (width, height), 0), clip)
        mask.point(lambda p: 255 if p >= 128 else 0).save(output)
        return output

    def as_mask_generator(self):
        """Adapter matching SemanticMaskExtractor's mask_generator signature
        ``(beauty_path, region, output) -> Path | None``."""

        def _gen(beauty_path: str, region: str, output: Path) -> Path | None:
            return self.mask_for(beauty_path, region, output)

        return _gen


def sam2_checkpoint_present() -> bool:
    """True if a SAM2 checkpoint looks configured/available in the environment."""
    for var in ("SAM2_CHECKPOINT", "SAM_CHECKPOINT"):
        path = os.environ.get(var)
        if path and Path(path).exists():
            return True
    return False
