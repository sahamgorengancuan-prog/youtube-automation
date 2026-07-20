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
    SYSTEM = """You are the Animation Director of a scientific editorial studio.
Return JSON only. Animate the approved illustration without making it feel like a presentation.
Default is hold. Every event must express the scene's stated causal change and must target an available semantic layer.
Prefer replacement drawings, local masks, material loops and restrained transforms. Never add automatic fade,
zoom, entrance animation, random parallax, camera shake or decorative motion."""

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
Return an AnimationPlan. Rules:
- camera_locked=true unless an indispensable camera action is explicitly justified; no camera events otherwise.
- Only target these exact layer IDs: {available}.
- Every MotionEvent needs event_id, reason_id, target_layer, representation, start_frame, end_frame, easing and parameters.
- Maximum one primary event and two secondary events at the same time.
- If no local movement is needed, return events=[] and preserve a deliberate hold.
- Use replacement_pose only when pose_variant_paths exist; use masks for local changes; keep beauty-base locked.
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
            set([*plan.hold_regions, *[layer_item.layer_id for layer_item in contract.layers if layer_item.locked]])
        )
        save_json(self.root / f"{scene.scene_id}.json", plan)
        return plan

    @staticmethod
    def _reference_motion_guidance(reference_motion: dict[str, Any] | None) -> str:
        """Turn the reference video's measured motion dynamics into director
        guidance so the animation matches its energy and rhythm (never its
        content). Returns an empty string when no profile is available."""
        if not isinstance(reference_motion, dict) or not reference_motion:
            return ""
        tempo = str(reference_motion.get("tempo", "moderate"))
        energy = reference_motion.get("energy", "")
        cut_rate = reference_motion.get("cut_rate", "")
        pace = {
            "energetic": (
                "The reference moves with HIGH energy: give the scene fluid, continuous motion — "
                "2-3 well-timed events that overlap and chain so the frame never feels frozen, "
                "larger (but still causal) transforms, and smooth ease-in-out. Keep it dynamic, not jittery."
            ),
            "moderate": (
                "The reference has MODERATE energy: one clear primary motion plus a supporting "
                "secondary event, with gentle continuous easing so the scene feels alive."
            ),
            "calm": (
                "The reference is CALM: keep motion restrained and deliberate — a single subtle "
                "primary event and long holds."
            ),
        }.get(tempo, "")
        return (
            "\nReference motion profile (MATCH its energy and rhythm, NEVER its content): "
            f"tempo={tempo}, energy={energy}, cut_rate={cut_rate}.\n{pace}\n"
        )

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
        return AnimationPlan(
            scene_id=scene.scene_id,
            fps=fps,
            duration_frames=duration_frames,
            camera_locked=True,
            events=events,
            audio_sync=audio_timing,
            hold_regions=holds,
        )
