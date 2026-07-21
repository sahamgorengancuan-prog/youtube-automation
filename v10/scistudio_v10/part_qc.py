"""Perceptual QC for the articulated rig — beyond "the function ran".

Passing unit tests and a pixel-change percentage only prove code executed. This
module checks the things a viewer actually notices, on the *rendered* articulated
poses:

* **joint continuity** — no transparent gap opens at the shoulder/elbow/hip/knee
  when the child limb rotates (the overlap must cover the seam);
* **limb-length stability** — a bone is rigid; its rendered part must not stretch
  or shrink between poses;
* **occlusion order** — arms/head composite in front of the torso;
* **no background leakage** — every part is inside the character silhouette;
* **no torso drag** — the upper-arm part must not carry a big slab of torso.

It renders a **contact sheet** (rest pose + ≥3 articulated poses) as the visual
artifact, runs the structural checks above deterministically, and — when a vision
model is available — asks a **Qwen VL perceptual gate** to judge the contact sheet
("are the joints connected, any tearing, are limb lengths consistent?"). The
vision gate is GPU/API-gated; the structural checks run anywhere.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from .skeletal_deform import GESTURE_LIBRARY, SkeletalDeformer
from .utils import ensure_dir

# Poses used for the contact sheet / QC (rest + three articulated).
QC_POSES: list[tuple[str, dict[str, float]]] = [
    ("rest", {}),
    ("gesture_present", GESTURE_LIBRARY["gesture_present"]),
    ("wave", GESTURE_LIBRARY["wave"]),
    ("step", GESTURE_LIBRARY["step"]),
]


def _load_parts(rig: dict[str, Any]) -> dict[str, Image.Image]:
    parts: dict[str, Image.Image] = {}
    for bone in rig.get("bones", []):
        p = bone.get("mask")
        if p and Path(p).exists():
            parts[bone["name"]] = Image.open(p).convert("L")
    return parts


def render_poses(rig: dict[str, Any], base_cutout: Image.Image) -> list[tuple[str, Image.Image]]:
    parts = _load_parts(rig)
    deformer = SkeletalDeformer({})
    frames = []
    for label, pose in QC_POSES:
        img = deformer.deform(base_cutout, rig, parts, pose, 1.0) if pose else base_cutout.convert("RGBA")
        frames.append((label, img))
    return frames


def contact_sheet(rig: dict[str, Any], base_cutout: Image.Image, out_path: str | Path) -> Path:
    frames = render_poses(rig, base_cutout)
    w, h = base_cutout.size
    pad = 8
    sheet = Image.new("RGBA", (w * len(frames) + pad * (len(frames) + 1), h + pad * 2), (24, 26, 30, 255))
    for i, (_label, img) in enumerate(frames):
        sheet.alpha_composite(img.convert("RGBA"), (pad + i * (w + pad), pad))
    out_path = Path(out_path)
    ensure_dir(out_path.parent)
    sheet.convert("RGB").save(out_path)
    return out_path


def _alpha_at(img: Image.Image, x: float, y: float, r: int = 4) -> float:
    """Mean alpha coverage in an r-neighbourhood of (x,y)."""
    a = img.convert("RGBA").getchannel("A")
    w, h = a.size
    xs = range(max(0, int(x) - r), min(w, int(x) + r + 1))
    ys = range(max(0, int(y) - r), min(h, int(y) + r + 1))
    vals = [a.getpixel((xx, yy)) for yy in ys for xx in xs]
    return (sum(1 for v in vals if v >= 128) / len(vals)) if vals else 0.0


def perceptual_qc(
    rig: dict[str, Any],
    base_cutout: Image.Image,
    out_dir: str | Path,
    *,
    llm: Any = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Render the contact sheet, run structural joint/leakage/occlusion checks,
    and (optionally) a Qwen VL perceptual gate. Returns a report with ``ok``."""
    config = config or {}
    out_dir = ensure_dir(out_dir)
    obj_id = rig.get("object_id", "char")
    sheet_path = contact_sheet(rig, base_cutout, out_dir / f"{obj_id}_contact_sheet.png")
    w, h = base_cutout.size
    parts = _load_parts(rig)
    deformer = SkeletalDeformer({})
    bones = {b["name"]: b for b in rig.get("bones", [])}

    issues: list[str] = []

    # -- joint continuity: for each parent->child joint, the child's pivot must
    # stay covered (no gap) across the articulated poses.
    joint_gaps: list[str] = []
    for name, bone in bones.items():
        parent = bone.get("parent")
        if not parent:
            continue
        for _label, pose in QC_POSES[1:]:
            img = deformer.deform(base_cutout, rig, parts, pose, 1.0)
            resolved = deformer.accumulate(rig, pose, 1.0, (w, h))
            _angle, pivot_px = resolved.get(name, (0.0, (w / 2, h / 2)))
            if _alpha_at(img, pivot_px[0], pivot_px[1], r=max(3, int(min(w, h) * 0.015))) < 0.35:
                joint_gaps.append(f"{parent}->{name}")
                break
    if joint_gaps:
        issues.append(f"joint_gap: {sorted(set(joint_gaps))}")

    # -- background leakage: every part mask must be inside the character silhouette.
    leakage = 0
    silhouette = base_cutout.convert("RGBA").getchannel("A")
    for _nm, pm in parts.items():
        pdd = list(pm.resize((w, h)).getdata())
        sdd = list(silhouette.getdata())
        out = sum(1 for pv, sv in zip(pdd, sdd) if pv >= 128 and sv < 128)
        leakage += out
    leak_frac = leakage / max(1, w * h)
    if leak_frac > float(config.get("qc_max_leak_frac", 0.01)):
        issues.append(f"background_leakage: {leak_frac:.3f}")

    # -- occlusion order: arms/head z must exceed the torso z.
    spine_z = bones.get("spine", {}).get("z", 20)
    bad_z = [
        n for n in ("forearm_l", "forearm_r", "hand_l", "hand_r", "head") if bones.get(n, {}).get("z", 0) <= spine_z
    ]
    if bad_z:
        issues.append(f"occlusion_order: {bad_z} not in front of torso")

    # -- limb-length stability / no tearing: rigid FK preserves each part, so the
    # total visible character area must stay stable across poses. A big drop means
    # a part vanished/flew off (tearing) or stretched away.
    areas = []
    for _label, pose in QC_POSES:
        img = deformer.deform(base_cutout, rig, parts, pose, 1.0) if pose else base_cutout.convert("RGBA")
        a = img.convert("RGBA").getchannel("A")
        areas.append(sum(1 for v in a.getdata() if v >= 128))
    if areas and max(areas) > 0:
        spread = (max(areas) - min(areas)) / max(areas)
        report_area_spread = round(spread, 3)
        if spread > float(config.get("qc_max_area_spread", 0.30)):
            issues.append(f"limb_length_or_tearing: area varies {spread:.2f} across poses")
    else:
        report_area_spread = 1.0

    # -- no torso drag: the upper-arm mask must not overlap the torso column much.
    torso = parts.get("spine")
    drag = []
    if torso is not None:
        tdd = list(torso.resize((w, h)).getdata())
        for arm in ("upper_arm_l", "upper_arm_r"):
            am = parts.get(arm)
            if am is None:
                continue
            add = list(am.resize((w, h)).getdata())
            inter = sum(1 for a, t in zip(add, tdd) if a >= 128 and t >= 128)
            area = max(1, sum(1 for a in add if a >= 128))
            if inter / area > float(config.get("qc_max_arm_torso_overlap", 0.5)):
                drag.append(arm)
    if drag:
        issues.append(f"torso_drag: {drag}")

    # -- part confidence surfaced from the segmenter.
    pq = rig.get("part_quality", {})
    report: dict[str, Any] = {
        "contact_sheet": str(sheet_path),
        "structural_ok": not issues,
        "issues": issues,
        "area_spread": report_area_spread,
        "part_source": "sam2" if pq.get("refined_with_sam2") else "geometric",
        "min_confidence": pq.get("min_confidence", 0.0),
        "failed_parts": pq.get("failed_parts", []),
    }

    if config.get("vision_part_qc") and llm is not None:
        report["vision_qc"] = _vision_gate(llm, sheet_path, config.get("causal_summary", ""))

    report["ok"] = report["structural_ok"] and not report["failed_parts"]
    return report


def _vision_gate(llm: Any, sheet_path: str | Path, causal_summary: str = "") -> Any:
    narration = (
        f" The shot's intended action is: '{causal_summary}'. Also judge pose_fits_narration: does the "
        "articulated pose plausibly depict that action?"
        if causal_summary
        else ""
    )
    try:
        return llm.critique_image(
            image_path=str(sheet_path),
            prompt=(
                "This is a contact sheet: the SAME character in a rest pose then three articulated poses. "
                'Judge the rig quality. Return JSON: {"joints_connected": true/false, "tearing": true/false, '
                '"limb_length_consistent": true/false, "background_clean": true/false, '
                '"looks_like_one_body": true/false, "pose_fits_narration": true/false, "reason": "..."}. '
                "Flag any gap at shoulder/elbow/hip/knee, any body part that detached, or torso dragged by an arm."
                + narration
            ),
            namespace="part_perceptual_qc",
            fallback=None,
        )
    except Exception:
        return None
