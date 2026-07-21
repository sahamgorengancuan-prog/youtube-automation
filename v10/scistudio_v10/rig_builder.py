"""Rig Builder — articulated anatomical skeleton for a character object.

Cutout animation (translate/rotate/scale the whole object) makes a figure slide
like paper. To make a character *move*, its body must be split into parts that
rotate about joints: head, torso/spine, upper-arm, forearm, hand, thigh, calf,
foot — each a bone with a parent, a pivot (the joint it rotates about) and a
part-mask carved out of the object's own mask. The renderer then deforms each
part along its bone (bend an elbow, swing a leg) instead of moving one flat
sprite.

Two backends, both guarded:

* **Pose backend** (preferred, GPU/model): Ultralytics YOLO-pose returns COCO-17
  keypoints; bones are derived from the joint chain and part-masks are the object
  mask intersected with a capsule around each bone segment.
* **Deterministic fallback** (always available, no GPU/model): a canonical
  humanoid laid out by proportion inside the object's bounding box. This keeps
  articulated rigging working — and testable — without any model, and is used
  automatically whenever pose estimation is unavailable or finds no person.

A non-figure object (a prop, a planet) gets a trivial single-bone root rig, so
the same deform path degrades gracefully to whole-object motion.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from .utils import ensure_dir

# Canonical bone chain (parent -> child). Order matters: parents before children
# so a transform stack can be composed root-first.
BONE_CHAIN: list[tuple[str, str | None]] = [
    ("spine", None),
    ("head", "spine"),
    ("upper_arm_l", "spine"),
    ("forearm_l", "upper_arm_l"),
    ("hand_l", "forearm_l"),
    ("upper_arm_r", "spine"),
    ("forearm_r", "upper_arm_r"),
    ("hand_r", "forearm_r"),
    ("thigh_l", "spine"),
    ("calf_l", "thigh_l"),
    ("foot_l", "calf_l"),
    ("thigh_r", "spine"),
    ("calf_r", "thigh_r"),
    ("foot_r", "calf_r"),
]

# COCO-17 keypoint indices (Ultralytics pose order).
_KP = {
    "nose": 0,
    "shoulder_l": 5,
    "shoulder_r": 6,
    "elbow_l": 7,
    "elbow_r": 8,
    "wrist_l": 9,
    "wrist_r": 10,
    "hip_l": 11,
    "hip_r": 12,
    "knee_l": 13,
    "knee_r": 14,
    "ankle_l": 15,
    "ankle_r": 16,
}


class RigBuilder:
    """Builds an articulated :class:`Rig` for a character object."""

    def __init__(self, config: dict[str, Any] | None = None, root: str | Path | None = None):
        self.config = config or {}
        self.root = ensure_dir(root) if root is not None else None
        self.use_pose = bool(self.config.get("use_pose", True))
        self._model = None
        self._pose_failed = False

    # -- capability ---------------------------------------------------------
    def available_pose(self) -> bool:
        if not self.use_pose or self._pose_failed:
            return False
        try:
            return importlib.util.find_spec("ultralytics") is not None
        except Exception:
            return False

    # -- public API ---------------------------------------------------------
    def build(
        self,
        beauty_path: str | Path,
        object_bbox: tuple[float, float, float, float],
        object_mask_path: str | Path | None,
        object_id: str,
        *,
        is_figure: bool = True,
        out_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        """Return a rig dict: ``{object_id, source, is_figure, bbox, bones:[...]}``.

        Each bone: ``{name, parent, pivot:[x,y], tip:[x,y], rest_angle, length,
        z, mask}`` with normalized (0..1) coordinates in frame space.
        """
        out_dir = Path(out_dir) if out_dir is not None else (self.root or Path("."))
        ensure_dir(out_dir)
        if not is_figure:
            return self._prop_rig(object_bbox, object_id)
        joints: dict[str, tuple[float, float]] | None = None
        source = "fallback"
        if self.available_pose():
            try:
                joints = self._pose_joints(beauty_path, object_bbox)
                if joints:
                    source = "pose"
            except Exception:
                self._pose_failed = True
                joints = None
        if not joints:
            joints = self._fallback_joints(object_bbox)
        bones = self._bones_from_joints(joints)
        rig = {
            "object_id": object_id,
            "source": source,
            "is_figure": True,
            "bbox": [round(v, 4) for v in object_bbox],
            "bones": bones,
        }
        # Carve per-part masks from the object mask (best effort).
        try:
            self._carve_part_masks(rig, object_mask_path, beauty_path, out_dir)
        except Exception:
            pass
        return rig

    # -- pose backend -------------------------------------------------------
    def _load_model(self):
        if self._model is not None:
            return self._model
        from ultralytics import YOLO  # type: ignore[import-not-found]

        self._model = YOLO(self.config.get("pose_model", "yolov8n-pose.pt"))
        return self._model

    def _pose_joints(
        self, beauty_path: str | Path, bbox: tuple[float, float, float, float]
    ) -> dict[str, tuple[float, float]] | None:
        model = self._load_model()
        image = Image.open(beauty_path).convert("RGB")
        width, height = image.size
        results = model(str(beauty_path), verbose=False)
        if not results:
            return None
        kpts = getattr(results[0], "keypoints", None)
        if kpts is None or getattr(kpts, "xy", None) is None or len(kpts.xy) == 0:
            return None
        # Pick the person whose box best overlaps the grounded object bbox.
        best = kpts.xy[0].cpu().numpy()
        pts = {name: (float(best[idx][0]) / width, float(best[idx][1]) / height) for name, idx in _KP.items()}
        # Reject degenerate (all-zero) detections.
        if all(x == 0 and y == 0 for x, y in pts.values()):
            return None
        return self._joints_from_keypoints(pts, bbox)

    @staticmethod
    def _mid(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
        return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)

    def _joints_from_keypoints(
        self, kp: dict[str, tuple[float, float]], bbox: tuple[float, float, float, float]
    ) -> dict[str, tuple[float, float]]:
        neck = self._mid(kp["shoulder_l"], kp["shoulder_r"])
        pelvis = self._mid(kp["hip_l"], kp["hip_r"])
        return {
            "head": kp["nose"],
            "neck": neck,
            "pelvis": pelvis,
            "shoulder_l": kp["shoulder_l"],
            "elbow_l": kp["elbow_l"],
            "wrist_l": kp["wrist_l"],
            "shoulder_r": kp["shoulder_r"],
            "elbow_r": kp["elbow_r"],
            "wrist_r": kp["wrist_r"],
            "hip_l": kp["hip_l"],
            "knee_l": kp["knee_l"],
            "ankle_l": kp["ankle_l"],
            "hip_r": kp["hip_r"],
            "knee_r": kp["knee_r"],
            "ankle_r": kp["ankle_r"],
        }

    # -- deterministic fallback --------------------------------------------
    def _fallback_joints(self, bbox: tuple[float, float, float, float]) -> dict[str, tuple[float, float]]:
        """Canonical standing humanoid laid out by proportion inside ``bbox``."""
        x0, y0, x1, y1 = bbox
        w = max(1e-3, x1 - x0)
        h = max(1e-3, y1 - y0)

        def pt(fx: float, fy: float) -> tuple[float, float]:
            return (x0 + fx * w, y0 + fy * h)

        return {
            "head": pt(0.50, 0.08),
            "neck": pt(0.50, 0.20),
            "pelvis": pt(0.50, 0.55),
            "shoulder_l": pt(0.34, 0.22),
            "elbow_l": pt(0.28, 0.38),
            "wrist_l": pt(0.24, 0.52),
            "shoulder_r": pt(0.66, 0.22),
            "elbow_r": pt(0.72, 0.38),
            "wrist_r": pt(0.76, 0.52),
            "hip_l": pt(0.42, 0.56),
            "knee_l": pt(0.40, 0.78),
            "ankle_l": pt(0.39, 0.98),
            "hip_r": pt(0.58, 0.56),
            "knee_r": pt(0.60, 0.78),
            "ankle_r": pt(0.61, 0.98),
        }

    # -- bone assembly ------------------------------------------------------
    _BONE_ENDS = {
        "spine": ("pelvis", "neck"),
        "head": ("neck", "head"),
        "upper_arm_l": ("shoulder_l", "elbow_l"),
        "forearm_l": ("elbow_l", "wrist_l"),
        "hand_l": ("wrist_l", "wrist_l"),
        "upper_arm_r": ("shoulder_r", "elbow_r"),
        "forearm_r": ("elbow_r", "wrist_r"),
        "hand_r": ("wrist_r", "wrist_r"),
        "thigh_l": ("hip_l", "knee_l"),
        "calf_l": ("knee_l", "ankle_l"),
        "foot_l": ("ankle_l", "ankle_l"),
        "thigh_r": ("hip_r", "knee_r"),
        "calf_r": ("knee_r", "ankle_r"),
        "foot_r": ("ankle_r", "ankle_r"),
    }
    _BONE_Z = {"spine": 10, "head": 12}

    def _bones_from_joints(self, joints: dict[str, tuple[float, float]]) -> list[dict[str, Any]]:
        bones: list[dict[str, Any]] = []
        for name, parent in BONE_CHAIN:
            a_key, b_key = self._BONE_ENDS[name]
            pivot = joints.get(a_key)
            tip = joints.get(b_key)
            if pivot is None or tip is None:
                continue
            dx, dy = tip[0] - pivot[0], tip[1] - pivot[1]
            length = math.hypot(dx, dy)
            angle = math.degrees(math.atan2(dy, dx))
            # Extremities (hand/foot) have zero-length ends; give them a small stub.
            if length < 1e-4:
                length = 0.04
            bones.append(
                {
                    "name": name,
                    "parent": parent,
                    "pivot": [round(pivot[0], 4), round(pivot[1], 4)],
                    "tip": [round(tip[0], 4), round(tip[1], 4)],
                    "rest_angle": round(angle, 2),
                    "length": round(length, 4),
                    "z": self._BONE_Z.get(name, 11),
                    "mask": None,
                }
            )
        return bones

    def _prop_rig(self, bbox: tuple[float, float, float, float], object_id: str) -> dict[str, Any]:
        x0, y0, x1, y1 = bbox
        pivot = [round((x0 + x1) / 2.0, 4), round((y0 + y1) / 2.0, 4)]
        return {
            "object_id": object_id,
            "source": "prop",
            "is_figure": False,
            "bbox": [round(v, 4) for v in bbox],
            "bones": [
                {
                    "name": "root",
                    "parent": None,
                    "pivot": pivot,
                    "tip": [pivot[0], round(y0, 4)],
                    "rest_angle": -90.0,
                    "length": round(max(1e-3, (y1 - y0)) / 2.0, 4),
                    "z": 11,
                    "mask": None,
                }
            ],
        }

    # -- part mask carving --------------------------------------------------
    def _carve_part_masks(
        self,
        rig: dict[str, Any],
        object_mask_path: str | Path | None,
        beauty_path: str | Path,
        out_dir: Path,
    ) -> None:
        """Split the object mask into per-bone masks: object_mask ∩ capsule(bone)."""
        if object_mask_path and Path(object_mask_path).exists():
            base = Image.open(object_mask_path).convert("L")
        else:
            # No object mask: use the whole bbox as the silhouette.
            beauty = Image.open(beauty_path).convert("RGB")
            base = Image.new("L", beauty.size, 0)
            d = ImageDraw.Draw(base)
            x0, y0, x1, y1 = rig["bbox"]
            d.rectangle(
                [x0 * base.width, y0 * base.height, x1 * base.width, y1 * base.height],
                fill=255,
            )
        width, height = base.size
        # Radius of the limb capsule as a fraction of the bbox width.
        bx0, by0, bx1, by1 = rig["bbox"]
        span = max(1e-3, (bx1 - bx0)) * width
        radius = max(4.0, span * float(self.config.get("rig_limb_radius", 0.16)))
        head_radius = max(6.0, span * float(self.config.get("rig_head_radius", 0.28)))
        for bone in rig["bones"]:
            cap = Image.new("L", (width, height), 0)
            d = ImageDraw.Draw(cap)
            px0, py0 = bone["pivot"][0] * width, bone["pivot"][1] * height
            px1, py1 = bone["tip"][0] * width, bone["tip"][1] * height
            r = head_radius if bone["name"] == "head" else radius
            _draw_capsule(d, px0, py0, px1, py1, r)
            part = Image.new("L", (width, height), 0)
            part.paste(base, (0, 0), cap.point(lambda p: 255 if p else 0))
            part = part.filter(ImageFilter.MaxFilter(3))
            mask_path = out_dir / f"{rig['object_id']}_{bone['name']}.png"
            part.point(lambda p: 255 if p >= 128 else 0).save(mask_path)
            bone["mask"] = str(mask_path)


def _draw_capsule(draw: ImageDraw.ImageDraw, x0: float, y0: float, x1: float, y1: float, r: float) -> None:
    """Fill a capsule (stadium) between two points with radius ``r``."""
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
