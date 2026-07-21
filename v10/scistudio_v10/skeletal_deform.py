"""Skeletal deformation — a **custom 2D cutout skeletal rig**, NOT Rive.

Be precise about what this is and is not: it is a custom articulated 2D rig —
PNG body-part cutouts rotated about pivots by forward kinematics. It is **not**
Rive: there is no artboard, no bone/mesh skin deformation, no constraints and no
state machine, and it does not read or write ``.riv``. It does not claim to
satisfy a "Rive" requirement.

On Rive specifically: ``.riv`` is a binary artboard/animation/state-machine
exported from the Rive Editor. There is no practical external Python/Colab API to
synthesize a whole rig ``.riv`` from a notebook, but Colab *can* run the Rive Web
Runtime, load a pre-made ``human_template.riv``, drive its state machine and swap
image assets at runtime. So a real-Rive path is possible via a template — it is
tracked separately and is not what this module provides.

What this module does: the :class:`~scistudio_v10.rig_builder.RigBuilder` builds a
bone hierarchy with per-part masks, the character/animation director *authors* a
pose (per-bone angle deltas), and this executes it by forward kinematics — each
part rotates about its joint, children following parents. The renderer never
invents the pose; it plays the authored one. Deterministic and GPU-free.
"""

from __future__ import annotations

import math
from typing import Any

from PIL import Image


def _rotate_point(point: tuple[float, float], pivot: tuple[float, float], degrees: float) -> tuple[float, float]:
    """Rotate ``point`` about ``pivot`` by ``degrees`` (screen space, y-down)."""
    rad = math.radians(degrees)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    dx, dy = point[0] - pivot[0], point[1] - pivot[1]
    return (
        pivot[0] + dx * cos_a - dy * sin_a,
        pivot[1] + dx * sin_a + dy * cos_a,
    )


class SkeletalDeformer:
    """Executes an authored per-bone pose over a rigged cutout via forward
    kinematics. Pure executor — no motion is invented here."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    def accumulate(
        self, rig: dict[str, Any], pose_deltas: dict[str, float], progress: float, size: tuple[int, int]
    ) -> dict[str, tuple[float, tuple[float, float]]]:
        """Forward kinematics: per bone, the accumulated angle (own + ancestors)
        and the pivot in pixels after the parent chain has rotated it."""
        width, height = size
        resolved: dict[str, tuple[float, tuple[float, float]]] = {}
        for bone in rig.get("bones", []):  # BONE_CHAIN order = parents first
            name = bone["name"]
            parent = bone.get("parent")
            own = float(pose_deltas.get(name, 0.0)) * progress
            pivot_px = (bone["pivot"][0] * width, bone["pivot"][1] * height)
            if parent and parent in resolved:
                parent_angle, parent_pivot = resolved[parent]
                total = parent_angle + own
                # This bone's joint is dragged along by the parent's rotation.
                pivot_px = _rotate_point(pivot_px, parent_pivot, parent_angle)
            else:
                total = own
            resolved[name] = (total, pivot_px)
        return resolved

    def deform(
        self,
        base_cutout: Image.Image,
        rig: dict[str, Any],
        part_masks: dict[str, Image.Image],
        pose_deltas: dict[str, float],
        progress: float,
    ) -> Image.Image:
        """Composite the deformed body parts for a single frame."""
        base = base_cutout.convert("RGBA")
        width, height = base.size
        resolved = self.accumulate(rig, pose_deltas, progress, (width, height))
        # Draw parts back-to-front by bone z (spine/legs behind, head/arms front).
        bones = sorted(rig.get("bones", []), key=lambda b: b.get("z", 11))
        canvas = Image.new("RGBA", base.size, (0, 0, 0, 0))
        drew_any = False
        for bone in bones:
            name = bone["name"]
            mask = part_masks.get(name)
            if mask is None:
                continue
            angle, pivot_px = resolved.get(name, (0.0, (width / 2, height / 2)))
            part = Image.new("RGBA", base.size, (0, 0, 0, 0))
            part.paste(base, (0, 0), mask.convert("L").resize(base.size))
            if abs(angle) > 1e-3:
                part = part.rotate(
                    -angle,  # PIL rotates counter-clockwise; screen angle is CW
                    resample=Image.Resampling.BICUBIC,
                    center=pivot_px,
                    expand=False,
                )
            canvas.alpha_composite(part)
            drew_any = True
        if not drew_any:
            return base
        return canvas


# A small library of authored gestures: per-bone angle deltas (degrees) reached
# at full progress. The director selects a gesture by name (or authors explicit
# bone deltas); the renderer only executes it. These are *authored data*, not
# renderer-side keyword inference.
GESTURE_LIBRARY: dict[str, dict[str, float]] = {
    "idle_breath": {"spine": 2.0, "head": 3.0, "upper_arm_l": 4.0, "upper_arm_r": -4.0},
    "gesture_present": {"upper_arm_r": -38.0, "forearm_r": -30.0, "head": 6.0, "spine": 3.0},
    "gesture_both": {
        "upper_arm_l": 34.0,
        "forearm_l": 26.0,
        "upper_arm_r": -34.0,
        "forearm_r": -26.0,
        "head": 4.0,
    },
    "wave": {"upper_arm_r": -46.0, "forearm_r": -40.0, "hand_r": -18.0, "head": 5.0},
    "nod": {"head": 16.0, "spine": 2.0},
    "point_left": {"upper_arm_l": 44.0, "forearm_l": 20.0, "head": -6.0},
    "step": {
        "thigh_l": -22.0,
        "calf_l": 18.0,
        "thigh_r": 20.0,
        "calf_r": -12.0,
        "spine": -2.0,
    },
    "reach_up": {
        "upper_arm_l": 120.0,
        "forearm_l": 20.0,
        "upper_arm_r": -120.0,
        "forearm_r": -20.0,
        "head": -8.0,
    },
}


def resolve_pose(parameters: dict[str, Any]) -> dict[str, float]:
    """Return per-bone angle deltas from an authored motion-event's parameters.

    Accepts either explicit ``{"bones": {name: degrees}}`` (fully authored) or a
    named ``{"pose": "wave"}`` from :data:`GESTURE_LIBRARY`. Unknown names resolve
    to an empty pose (the character holds — no invented motion)."""
    bones = parameters.get("bones")
    if isinstance(bones, dict) and bones:
        return {str(k): float(v) for k, v in bones.items()}
    name = str(parameters.get("pose", "")).strip()
    return dict(GESTURE_LIBRARY.get(name, {}))
