"""Post-render QC — verify causal clarity from the RENDERED frames.

Motion evaluated from the plan is only *planned* clarity. This module reads the
actual rendered frames and the causal object's mask and checks whether the
object region truly changed between the start and the end of the shot — the
rendered evidence that ``local_deformation``/``mask_reveal``/etc. actually did
something, not just that the word appears in the JSON. It also checks that the
background stayed stable for a declared hold.

An optional vision gate (Qwen VL via the LLM router) can answer the specific
causal questions ("did the waterline rise, did the house stay still, is it timed
to the audio"). That path is GPU/API-gated; the structural check runs anywhere.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image, ImageChops

from .motion_eval import evaluate_plan


def _region_change(initial: Image.Image, final: Image.Image, mask: Image.Image | None) -> tuple[float, float]:
    """Return (mean change inside mask, mean change outside mask), normalized."""
    a = initial.convert("L")
    b = final.convert("L").resize(a.size)
    diff = ImageChops.difference(a, b)
    dd = list(diff.getdata())
    if mask is None:
        avg = sum(dd) / (len(dd) * 255.0)
        return avg, avg
    m = list(mask.convert("L").resize(a.size).getdata())
    inside = [d for d, mm in zip(dd, m) if mm >= 128]
    outside = [d for d, mm in zip(dd, m) if mm < 128]
    inside_avg = (sum(inside) / (len(inside) * 255.0)) if inside else 0.0
    outside_avg = (sum(outside) / (len(outside) * 255.0)) if outside else 0.0
    return inside_avg, outside_avg


def verify(
    initial: Image.Image,
    final: Image.Image,
    object_mask: Image.Image | None,
    plan: Any,
    *,
    change_threshold: float = 0.015,
) -> dict[str, Any]:
    """Verify a shot from its rendered first/last frames and the object mask."""
    report = dict(evaluate_plan(plan))  # planned axes
    inside, outside = _region_change(initial, final, object_mask)
    report["object_region_change"] = round(inside, 4)
    report["background_change"] = round(outside, 4)
    object_moved = inside > change_threshold
    report["object_moved_in_render"] = object_moved
    if report.get("declared_hold"):
        # A declared hold should NOT move the object.
        verified = not object_moved
        report["render_verified"] = verified
        report["render_qc_reason"] = "held as declared" if verified else "declared hold but object moved"
    elif report.get("object_motion"):
        # An action shot MUST show the object region change in the render.
        verified = object_moved
        report["render_verified"] = verified
        report["render_qc_reason"] = (
            "object state change is visible in the render"
            if verified
            else "planned object motion did not produce a visible change (static render)"
        )
    else:
        report["render_verified"] = False
        report["render_qc_reason"] = "no object motion planned"
    return report


class PostRenderQC:
    """Runs :func:`verify` (and an optional vision gate) over rendered scenes."""

    def __init__(self, llm: Any = None, config: dict[str, Any] | None = None):
        self.llm = llm
        self.config = config or {}

    def verify_scene(
        self,
        initial_path: str | Path,
        final_path: str | Path,
        object_mask_path: str | Path | None,
        plan: Any,
    ) -> dict[str, Any]:
        try:
            initial = Image.open(initial_path)
            final = Image.open(final_path)
            mask = Image.open(object_mask_path) if object_mask_path and Path(object_mask_path).exists() else None
        except Exception as exc:
            return {"render_verified": None, "render_qc_reason": f"unreadable frames: {exc}"}
        report = verify(
            initial, final, mask, plan, change_threshold=float(self.config.get("render_change_threshold", 0.015))
        )
        if self.config.get("vision_render_qc") and self.llm is not None:
            report["vision_qc"] = self._vision_gate(initial_path, final_path, plan)
        return report

    def _vision_gate(self, initial_path, final_path, plan) -> Any:
        """Optional Qwen VL gate on a before/after board (GPU/API-gated)."""
        try:
            summary = getattr(plan, "causal_summary", "") or ""
            board = self._board(initial_path, final_path)
            return self.llm.critique_image(
                image_path=board,
                prompt=(
                    "Left is the FIRST frame, right is the LAST frame of one shot. The intended causal change is: "
                    f"'{summary}'. Answer JSON: did the intended change actually happen (change_visible: true/false), "
                    "did unrelated parts stay still (background_stable: true/false), and a one-line reason."
                ),
                namespace="render_qc",
                fallback=None,
            )
        except Exception:
            return None

    @staticmethod
    def _board(initial_path, final_path) -> str:
        a = Image.open(initial_path).convert("RGB")
        b = Image.open(final_path).convert("RGB").resize(a.size)
        board = Image.new("RGB", (a.width * 2 + 12, a.height), "#101010")
        board.paste(a, (0, 0))
        board.paste(b, (a.width + 12, 0))
        out = Path(final_path).with_name("render_qc_board.png")
        board.save(out)
        return str(out)
