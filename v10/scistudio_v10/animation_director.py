from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .llm import LLMRouter
from .schemas import (
    AnimationPlan,
    MotionEvent,
    SceneIllustrationArchitecture,
    SceneRequest,
    SemanticLayerContract,
)
from .utils import ensure_dir, save_json


class AnimationDirector:
    SYSTEM = """You are the Animation Director of a scientific explainer studio. Return JSON only.
Your ONE job is CAUSAL CLARITY: the viewer must clearly see WHAT changes, WHY it changes, and its CONSEQUENCE.
You author every visual decision as an explicit directive; the renderer only executes what you author — it invents nothing.
PRIMARY motion is object state change: the specific object that carries the causal action moves from its first-frame
state to its last-frame state (via replacement_pose, mask_reveal, local_deformation or a restrained transform on that
object's layer). Camera, particle effects and captions are SECONDARY, supporting elements — include them ONLY when they
sharpen the causal read, each with a narrative reason. Default everything to OFF/HOLD: no camera move, no particles and
no captions unless you explicitly author them. Never add decorative motion, entrance animation, random parallax, camera
shake, or an always-moving camera. Captions are usually unnecessary because English audio narrates the shot — author a
caption only for a label or measured value the narration cannot carry."""

    def __init__(self, llm: LLMRouter, config: dict[str, Any], root: str | Path):
        self.llm = llm
        self.config = config
        self.root = ensure_dir(root)

    def plan(
        self,
        scene: SceneRequest,
        architecture: SceneIllustrationArchitecture,
        contract: SemanticLayerContract,
        *,
        fps: int,
        audio_timing: dict[str, Any] | None = None,
        reference_motion: dict[str, Any] | None = None,
        force: bool = False,
    ) -> AnimationPlan:
        duration_frames = max(1, round(scene.duration_s * fps))
        fallback = self._fallback(scene, architecture, contract, duration_frames, fps, audio_timing or {})
        available = [layer.layer_id for layer in contract.layers]
        raw = self.llm.generate_json(
            system=self.SYSTEM,
            prompt=f"""Scene: {json.dumps(scene.model_dump(mode="json"), ensure_ascii=False)}
Illustration architecture: {json.dumps(architecture.model_dump(mode="json"), ensure_ascii=False)}
Available layers: {json.dumps([layer_item.model_dump(mode="json") for layer_item in contract.layers], ensure_ascii=False)}
Duration: {duration_frames} frames at {fps} fps.
Audio timing: {json.dumps(audio_timing or {}, ensure_ascii=False)}
{self._reference_motion_guidance(reference_motion)}
Return an AnimationPlan (the authored animation DSL). Fields:
- causal_summary: one sentence — WHAT changes, WHY, and the CONSEQUENCE. This is the shot's contract; author motion that makes it visible.
- events: PRIMARY object state changes. Each MotionEvent needs event_id, reason_id (the narrative reason), target_layer
  (one of {available}), representation, start_frame, end_frame, easing, parameters. Prefer replacement_pose (only when
  that layer has pose_variant_paths), mask_reveal, local_deformation, or a restrained transform on the object that carries
  the change. Keep the beauty-base and locked layers still. Max one primary + two secondary simultaneous events.
- camera: a CameraDirective. Use move="hold" (default) unless a move genuinely aids the causal read; then set move, a small
  magnitude (0.03-0.08), start_frame, end_frame, easing and reason. No always-on camera.
- effects: list of EffectDirective — ONLY if a physical effect (e.g. rain, wind, spark) is part of the causal action.
  Each needs effect, region (normalized x0,y0,x1,y1 of where it happens), intensity (0..1), direction_deg, start/end_frame, reason.
  Leave [] when no effect is needed. Do not add ambient weather that is not part of the science.
- captions: list of CaptionDirective — usually []. Author one only for a label/value the audio cannot convey (kind, text,
  position, start/end_frame, reason).
If the shot needs no motion, return events=[], camera hold, effects=[], captions=[] — a deliberate, honest hold.
""",
            namespace=f"v10_animation_{scene.scene_id}",
            fallback=fallback.model_dump(mode="json"),
            force=force,
        )
        try:
            plan = AnimationPlan.model_validate(raw)
        except Exception:
            plan = fallback
        plan.scene_id = scene.scene_id
        plan.fps = fps
        plan.duration_frames = duration_frames
        plan.camera_locked = True
        plan.events = self._sanitize(plan.events, contract, duration_frames)
        plan.hold_regions = sorted(
            set(
                [
                    *plan.hold_regions,
                    *[layer_item.layer_id for layer_item in contract.layers if layer_item.locked],
                ]
            )
        )
        save_json(self.root / f"{scene.scene_id}.json", plan)
        # Non-blocking causal-clarity evaluation (separated motion axes). Saved
        # as an observability artifact so shots that move only camera/particles/
        # text without an object state change are visible for review.
        try:
            from .motion_eval import evaluate_plan

            report = evaluate_plan(plan)
            save_json(self.root / f"{scene.scene_id}_motion_eval.json", report)
            if report.get("supporting_only_warning"):
                print(
                    f"⚠ [{scene.scene_id}] supporting motion (camera/particles/text) without an object "
                    "state change — causal clarity is weak for this shot."
                )
        except Exception:
            pass
        return plan

    @staticmethod
    def _reference_motion_guidance(reference_motion: dict[str, Any] | None) -> str:
        """A restrained PACING hint from the reference video's measured dynamics.

        It informs how brisk the editing/easing should feel — it must NOT force
        extra or decorative motion. Causal clarity always wins over matching a
        tempo. Returns an empty string when no profile is available."""
        if not isinstance(reference_motion, dict) or not reference_motion:
            return ""
        tempo = str(reference_motion.get("tempo", "moderate"))
        pace = {
            "energetic": "Reference pacing is brisk: keep easing snappy, but only animate what the causal action needs.",
            "moderate": "Reference pacing is moderate: steady, clear easing.",
            "calm": "Reference pacing is calm: slow, deliberate easing and generous holds.",
        }.get(tempo, "")
        return f"\nPacing hint (does NOT justify extra motion): tempo={tempo}. {pace}\n"

    @staticmethod
    def _sanitize(events: list[MotionEvent], contract: SemanticLayerContract, duration: int) -> list[MotionEvent]:
        available = {layer.layer_id: layer for layer in contract.layers}
        clean: list[MotionEvent] = []
        for event in events:
            if event.target_layer not in available:
                continue
            if event.target_layer == "beauty-base":
                continue
            if event.representation == "replacement_pose" and not available[event.target_layer].pose_variant_paths:
                continue
            event.start_frame = max(0, min(duration - 1, event.start_frame))
            event.end_frame = max(event.start_frame + 1, min(duration, event.end_frame))
            clean.append(event)
        return clean

    def _fallback(
        self,
        scene: SceneRequest,
        architecture: SceneIllustrationArchitecture,
        contract: SemanticLayerContract,
        duration_frames: int,
        fps: int,
        audio_timing: dict[str, Any],
    ) -> AnimationPlan:
        events: list[MotionEvent] = []
        start = max(1, round(duration_frames * 0.20))
        end = max(start + 2, round(duration_frames * 0.82))
        layer_map = {layer.layer_id: layer for layer in contract.layers}
        for index, seam in enumerate(architecture.motion_seams):
            if seam.seam_id not in layer_map:
                continue
            layer = layer_map[seam.seam_id]
            secondary = index > 0
            # Replacement drawings preserve authored art better than synthetic
            # deformation. Whenever approved variants exist, prefer them even if
            # the conceptual seam was described as a local deformation.
            if layer.pose_variant_paths and seam.method != "texture_loop":
                representation = "replacement_pose"
                params = {"pose_paths": layer.pose_variant_paths, "hold_last": True}
            elif seam.method == "texture_loop":
                representation = "texture_loop"
                params = {"axis": "y", "speed_px_per_second": 55, "masked": True}
            elif seam.method == "layer_transform":
                representation = "translate"
                params = {"from": [0, 0], "to": [18, 0], "masked": True}
            elif seam.method == "overlay_only":
                representation = "overlay_draw"
                params = {"progress": [0, 1]}
            else:
                representation = "local_deformation" if seam.method == "local_deformation" else "mask_reveal"
                params = {"progress": [0, 1], "masked": True}
            events.append(
                MotionEvent(
                    event_id=f"EV{index + 1:02d}",
                    reason_id=scene.beat_id or scene.scene_id,
                    target_layer=seam.seam_id,
                    representation=representation,  # type: ignore[arg-type]
                    start_frame=start + index * 2,
                    end_frame=end,
                    easing="ease-in-out" if representation != "texture_loop" else "linear",
                    parameters=params,
                    secondary=secondary,
                )
            )
        holds = [layer.layer_id for layer in contract.layers if layer.locked]
        causal = getattr(scene, "desired_change", "") or getattr(scene, "narration", "") or scene.scene_id
        return AnimationPlan(
            scene_id=scene.scene_id,
            fps=fps,
            duration_frames=duration_frames,
            camera_locked=True,
            events=events,
            causal_summary=str(causal)[:300],
            audio_sync=audio_timing,
            hold_regions=holds,
        )
