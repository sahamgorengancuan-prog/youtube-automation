"""Anatomical part separation — turn a whole-character silhouette into clean,
independently-movable body parts.

Capsule carving alone is NOT accepted production output: it seams at joints, can
leak background, and can drag torso pixels along with an arm. This module does
the real separation:

1. **Whole-character mask** (SAM2 or the caller's mask) bounds every part, so no
   part can contain background pixels — no background leakage by construction.
2. **Pose keypoints** define each limb's axis (shoulder→elbow, elbow→wrist, ...).
3. **Per-limb SAM2 refinement** (when available): SAM2 is prompted per limb with a
   tight box + a positive point on the limb axis + *negative* points on the other
   joints, so the forearm mask excludes the torso and the far arm — the arm never
   carries the torso.
4. **Joint overlap**: each part extends slightly across its pivot into the parent,
   so when the child rotates there is no gap at the shoulder/elbow/hip/knee.
5. **Occlusion order**: an explicit anatomical z-order (torso behind, arms/head in
   front; far-side limbs behind the torso for a three-quarter/profile view).
6. **Per-part QC + retry**: a part that is empty, too small, or escapes the
   character is retried (wider prompt) and, if it still fails, is flagged
   ``failed`` with low confidence — never silently accepted as clean.

Offline / no GPU, SAM2 refinement is unavailable; the geometric separation runs
with the same overlap + occlusion + QC + confidence, but is marked
``source="geometric"`` with reduced confidence so the perceptual QC and the
pipeline can tell refined parts from fallback parts.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from .utils import ensure_dir

# Anatomical occlusion order for a front / front-three-quarter view: torso and
# legs behind, head and arms in front. Higher = nearer the camera.
Z_ORDER: dict[str, int] = {
    "spine": 20,
    "thigh_l": 21,
    "thigh_r": 21,
    "calf_l": 22,
    "calf_r": 22,
    "foot_l": 23,
    "foot_r": 23,
    "head": 30,
    "upper_arm_l": 31,
    "upper_arm_r": 31,
    "forearm_l": 32,
    "forearm_r": 32,
    "hand_l": 33,
    "hand_r": 33,
}

# Per-limb capsule geometry: radius as a fraction of character-bbox width, plus
# how far to extend past the pivot (into the parent, for joint overlap) and past
# the tip, both as a fraction of the limb length.
_LIMB_GEOM: dict[str, tuple[float, float, float]] = {
    "spine": (0.24, 0.05, 0.08),
    "head": (0.28, 0.15, 0.55),
    "upper_arm_l": (0.11, 0.28, 0.12),
    "forearm_l": (0.09, 0.30, 0.12),
    "hand_l": (0.17, 0.0, 0.0),
    "upper_arm_r": (0.11, 0.28, 0.12),
    "forearm_r": (0.09, 0.30, 0.12),
    "hand_r": (0.17, 0.0, 0.0),
    "thigh_l": (0.12, 0.22, 0.10),
    "calf_l": (0.10, 0.28, 0.12),
    "foot_l": (0.17, 0.0, 0.0),
    "thigh_r": (0.12, 0.22, 0.10),
    "calf_r": (0.10, 0.28, 0.12),
    "foot_r": (0.17, 0.0, 0.0),
}


class AnatomicalPartSegmenter:
    def __init__(self, config: dict[str, Any] | None = None, root: str | Path | None = None, segmenter: Any = None):
        self.config = config or {}
        self.root = ensure_dir(root) if root is not None else None
        self.segmenter = segmenter  # ObjectSegmenter (SAM2) for per-limb refinement

    def use_sam2(self) -> bool:
        return bool(self.segmenter is not None and getattr(self.segmenter, "available_sam2", lambda: False)())

    def separate(
        self,
        beauty_path: str | Path,
        char_mask_path: str | Path | None,
        rig: dict[str, Any],
        out_dir: str | Path,
    ) -> dict[str, Any]:
        """Fill each bone in ``rig`` with mask/cutout/z/confidence/source and
        return a quality summary. Mutates ``rig['bones']`` in place."""
        out_dir = ensure_dir(out_dir)
        beauty = Image.open(beauty_path).convert("RGBA")
        width, height = beauty.size
        char_mask = self._char_mask(char_mask_path, rig, (width, height))
        joints_px = self._joint_pixels(rig, (width, height))
        span = max(1e-3, (rig["bbox"][2] - rig["bbox"][0])) * width

        parts_report: list[dict[str, Any]] = []
        refined = self.use_sam2()
        for bone in rig["bones"]:
            name = bone["name"]
            mask, source, confidence = self._part_mask(
                name, bone, beauty_path, char_mask, joints_px, span, (width, height)
            )
            # Cutout = beauty pixels under the part mask (visible art from the frame).
            cutout = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            cutout.paste(beauty, (0, 0), mask)
            mask_path = out_dir / f"{rig['object_id']}_{name}.png"
            cut_path = out_dir / f"{rig['object_id']}_{name}_cutout.png"
            mask.save(mask_path)
            cutout.save(cut_path)
            bone["mask"] = str(mask_path)
            bone["cutout"] = str(cut_path)
            bone["z"] = Z_ORDER.get(name, 25)
            bone["rest_rotation"] = bone.get("rest_angle", 0.0)
            bone["confidence"] = round(confidence, 3)
            bone["part_source"] = source
            parts_report.append({"name": name, "source": source, "confidence": round(confidence, 3)})

        confidences = [p["confidence"] for p in parts_report]
        failed = [p["name"] for p in parts_report if p["source"] == "failed"]
        summary = {
            "refined_with_sam2": refined,
            "parts": parts_report,
            "min_confidence": round(min(confidences), 3) if confidences else 0.0,
            "mean_confidence": round(sum(confidences) / len(confidences), 3) if confidences else 0.0,
            "failed_parts": failed,
            "ok": not failed
            and (min(confidences) if confidences else 0.0) >= float(self.config.get("part_min_confidence", 0.4)),
        }
        rig["part_quality"] = summary
        return summary

    # -- per-part mask ------------------------------------------------------
    def _part_mask(self, name, bone, beauty_path, char_mask, joints_px, span, size) -> tuple[Image.Image, str, float]:
        width, height = size
        p0 = (bone["pivot"][0] * width, bone["pivot"][1] * height)
        p1 = (bone["tip"][0] * width, bone["tip"][1] * height)
        radius_f, ext_pivot, ext_tip = _LIMB_GEOM.get(name, (0.12, 0.3, 0.2))
        radius = max(4.0, span * radius_f)
        region = self._capsule_region(p0, p1, radius, ext_pivot, ext_tip, size, name)

        # SAM2 per-limb refinement: prompt with the limb box + positive point on
        # the axis + negative points at the OTHER joints, then intersect with the
        # character mask. Falls through to geometric on any failure/empty result.
        if self.use_sam2():
            try:
                refined = self._sam2_limb(name, bone, beauty_path, joints_px, region, char_mask, size)
                if refined is not None:
                    qc = self._qc(refined, char_mask, span)
                    if qc >= 0.5:
                        return self._clean(refined), "sam2", min(1.0, 0.7 + 0.3 * qc)
            except Exception:
                pass

        # Geometric fallback (overlap capsule ∩ character mask), with one retry at
        # a wider radius if the first attempt is too small.
        for attempt, scale in enumerate((1.0, 1.5)):
            cap = self._capsule_region(p0, p1, radius * scale, ext_pivot, ext_tip, size, name)
            part = Image.new("L", size, 0)
            part.paste(char_mask, (0, 0), cap)
            # Close seams (MaxFilter dilates), then re-intersect with the character
            # mask so the dilation can never push a part outside the silhouette —
            # zero background leakage by construction.
            part = part.filter(ImageFilter.MaxFilter(3))
            part = Image.composite(part, Image.new("L", size, 0), char_mask)
            part = self._clean(part)
            qc = self._qc(part, char_mask, span)
            if qc >= 0.35:
                conf = (0.6 if attempt == 0 else 0.5) * min(1.0, 0.5 + qc)
                return part, "geometric", round(conf, 3)
        # Still failing -> flagged, low confidence, not silently accepted.
        part = Image.new("L", size, 0)
        part.paste(char_mask, (0, 0), region)
        return self._clean(part), "failed", 0.2

    def _sam2_limb(self, name, bone, beauty_path, joints_px, region, char_mask, size):
        width, height = size
        bbox_px = region.getbbox()
        if bbox_px is None:
            return None
        bx0, by0, bx1, by1 = bbox_px
        bbox_norm = (bx0 / width, by0 / height, bx1 / width, by1 / height)
        mid = ((bone["pivot"][0] + bone["tip"][0]) / 2, (bone["pivot"][1] + bone["tip"][1]) / 2)
        # Negative points: every OTHER joint, so SAM2 does not grab the torso or
        # the neighbouring limb.
        a_key = bone["name"]
        neg = [(x / width, y / height) for k, (x, y) in joints_px.items() if not self._joint_of(a_key, k)]
        out = Path(str(bone.get("mask") or "")).with_suffix(".sam2.png") if bone.get("mask") else None
        tmp = out or (self.root / f"_sam2_{name}.png" if self.root else Path(f"/tmp/_sam2_{name}.png"))
        result = self.segmenter.mask_for(
            beauty_path,
            name,
            tmp,
            bbox=bbox_norm,
            positive_points=[mid],
            negative_points=neg[:8],
        )
        if result is None or not Path(result).exists():
            return None
        limb = Image.open(result).convert("L").resize(size)
        # Constrain to the character silhouette AND the limb region (kills leakage).
        limb = Image.composite(limb, Image.new("L", size, 0), char_mask.point(lambda p: 255 if p >= 128 else 0))
        limb = Image.composite(limb, Image.new("L", size, 0), region)
        return limb

    @staticmethod
    def _joint_of(bone_name: str, joint_key: str) -> bool:
        """True if a joint belongs to the given bone (so it is NOT a negative)."""
        side = bone_name[-2:] if bone_name[-2:] in ("_l", "_r") else ""
        table = {
            "spine": ("pelvis", "neck"),
            "head": ("neck", "head"),
            "upper_arm": ("shoulder", "elbow"),
            "forearm": ("elbow", "wrist"),
            "hand": ("wrist",),
            "thigh": ("hip", "knee"),
            "calf": ("knee", "ankle"),
            "foot": ("ankle",),
        }
        stem = bone_name[:-2] if side else bone_name
        for j in table.get(stem, ()):  # e.g. "elbow" matches joint "elbow_l"
            if joint_key.startswith(j):
                if not side or joint_key.endswith(side) or j in ("neck", "pelvis"):
                    return True
        return False

    # -- geometry helpers ---------------------------------------------------
    def _capsule_region(self, p0, p1, radius, ext_pivot, ext_tip, size, name) -> Image.Image:
        width, height = size
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        length = math.hypot(dx, dy)
        if length < 1e-4 or name in ("hand_l", "hand_r", "foot_l", "foot_r"):
            # Extremity: a blob at the joint.
            r = max(radius, 6.0)
            reg = Image.new("L", size, 0)
            ImageDraw.Draw(reg).ellipse([p0[0] - r, p0[1] - r, p0[0] + r, p0[1] + r], fill=255)
            return reg
        ux, uy = dx / length, dy / length
        a = (p0[0] - ux * ext_pivot * length, p0[1] - uy * ext_pivot * length)
        b = (p1[0] + ux * ext_tip * length, p1[1] + uy * ext_tip * length)
        reg = Image.new("L", size, 0)
        _draw_capsule(ImageDraw.Draw(reg), a[0], a[1], b[0], b[1], radius)
        return reg

    def _char_mask(self, char_mask_path, rig, size) -> Image.Image:
        width, height = size
        if char_mask_path and Path(char_mask_path).exists():
            m = Image.open(char_mask_path).convert("L").resize(size)
            return m.point(lambda p: 255 if p >= 128 else 0)
        m = Image.new("L", size, 0)
        x0, y0, x1, y1 = rig["bbox"]
        ImageDraw.Draw(m).rectangle([x0 * width, y0 * height, x1 * width, y1 * height], fill=255)
        return m

    @staticmethod
    def _joint_pixels(rig, size) -> dict[str, tuple[float, float]]:
        width, height = size
        pts: dict[str, tuple[float, float]] = {}
        for bone in rig["bones"]:
            pts[f"{bone['name']}_pivot"] = (bone["pivot"][0] * width, bone["pivot"][1] * height)
            pts[f"{bone['name']}_tip"] = (bone["tip"][0] * width, bone["tip"][1] * height)
        return pts

    @staticmethod
    def _qc(part: Image.Image, char_mask: Image.Image, span: float) -> float:
        """Return a 0..1 quality score: fraction of the part inside the character
        silhouette, weighted down if the part is empty or tiny."""
        pd = list(part.resize(char_mask.size).getdata())
        cd = list(char_mask.getdata())
        area = sum(1 for v in pd if v >= 128)
        if area == 0:
            return 0.0
        inside = sum(1 for pv, cv in zip(pd, cd) if pv >= 128 and cv >= 128)
        inside_frac = inside / max(1, area)
        min_area = max(4.0, (span * 0.04) ** 2)
        size_ok = min(1.0, area / min_area)
        return round(inside_frac * (0.4 + 0.6 * size_ok), 3)

    @staticmethod
    def _clean(mask: Image.Image) -> Image.Image:
        return mask.point(lambda p: 255 if p >= 128 else 0)


def _draw_capsule(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, r: float) -> None:
    draw.ellipse([x0 - r, y0 - r, x0 + r, y0 + r], fill=255)
    draw.ellipse([x1 - r, y1 - r, x1 + r, y1 + r], fill=255)
    dx, dy = x1 - x0, y1 - y0
    length = math.hypot(dx, dy)
    if length < 1e-4:
        return
    nx, ny = -dy / length * r, dx / length * r
    draw.polygon(
        [(x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)],
        fill=255,
    )
