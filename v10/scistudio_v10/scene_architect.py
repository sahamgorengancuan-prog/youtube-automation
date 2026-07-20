from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .llm import LLMRouter
from .schemas import (
    ArtDirectionBible,
    ContinuityCanon,
    DepthPlane,
    FigureConstruction,
    MaterialMarkPlan,
    MotionSeam,
    PerspectivePlan,
    SceneIllustrationArchitecture,
    SceneRequest,
    Storyboard,
)
from .utils import ensure_dir, load_json, save_json


class SceneIllustrationArchitect:
    SYSTEM = """You are the Lead Scene Illustration Architect for an institutional science-animation studio.
Return JSON only. The hard-coded house style and continuity canon are immutable.
Design one whole authored illustration before any semantic separation. Think like an illustrator:
composition, visual route, perspective, gesture, anatomy, overlap, material marks, value structure,
negative space, contour architecture and motion seams. Never design by assembling isolated icons.
Never request procedural SVG hero art, primitive shapes, mascot anatomy or generic AI cinematic art.
Semantic separation occurs only after the beauty frame is approved."""

    def __init__(self, llm: LLMRouter, config: dict[str, Any], root: str | Path):
        self.llm = llm
        self.config = config
        self.root = ensure_dir(root)

    def plan_all(
        self,
        storyboard: Storyboard,
        bible: ArtDirectionBible,
        continuity: ContinuityCanon,
        *,
        force: bool = False,
    ) -> list[SceneIllustrationArchitecture]:
        results = []
        previous_summary = ""
        for index, scene in enumerate(storyboard.scenes):
            architecture = self.plan(
                scene,
                storyboard,
                bible,
                continuity,
                previous_summary,
                index=index,
                force=force,
            )
            results.append(architecture)
            previous_summary = architecture.visual_thesis + " | " + "; ".join(architecture.color_script)
        save_json(self.root / "scene_architectures.json", results)
        return results

    def plan(
        self,
        scene: SceneRequest,
        storyboard: Storyboard,
        bible: ArtDirectionBible,
        continuity: ContinuityCanon,
        previous_scene_summary: str,
        *,
        index: int,
        force: bool = False,
    ) -> SceneIllustrationArchitecture:
        path = self.root / f"{scene.scene_id}.json"
        if path.exists() and not force:
            return SceneIllustrationArchitecture.model_validate(load_json(path))
        fallback = self._fallback(scene, storyboard, bible, index)
        raw = self.llm.generate_json(
            system=self.SYSTEM,
            prompt=f"""LOCKED ART DIRECTION:
{json.dumps(bible.model_dump(mode="json"), ensure_ascii=False)}

LOCKED CONTINUITY:
{json.dumps(continuity.model_dump(mode="json"), ensure_ascii=False)}

CURRENT SCENE:
{json.dumps(scene.model_dump(mode="json"), ensure_ascii=False)}
Previous scene visual summary: {previous_scene_summary or "none"}
Canvas: {storyboard.width}x{storyboard.height}, vertical.

Return a SceneIllustrationArchitecture with:
- visual_thesis and a 3-6 step composition_route describing the eye path;
- coherent perspective and 3-5 depth planes;
- focal_subject, secondary_subjects, negative_space and contour_architecture;
- figure_construction for every human, including adult proportion, body orientation, weight distribution,
  gesture line, joint logic, hand construction and clothing logic;
- material_marks for water, soil, concrete, metal, vegetation, atmosphere or other visible materials;
- color_script and scientific_annotations;
- motion_seams chosen after imagining a finished beauty frame;
- animation_representation and required_pose_variants;
- director_notes and prohibited_visual_shortcuts.

Hard rules:
1. beauty_frame_first=true; semantic_split_after_approval=true; procedural_hero_allowed=false.
2. The image must read as one authored environment, never a collage of isolated objects.
3. Interior lines explain form, material, force or perspective; no decorative pseudo-detail.
4. For human action, prefer replacement poses/local redraws over circle-joint puppet rigs.
5. Preserve the house line, palette, experiment UI and adult scientific tone.
6. Motion seams must be minimal and selected only for the stated desired_change.
""",
            namespace=f"v10_scene_architecture_{scene.scene_id}",
            fallback=fallback.model_dump(mode="json"),
            force=force,
        )
        try:
            architecture = SceneIllustrationArchitecture.model_validate(raw)
        except Exception:
            architecture = fallback
        architecture.scene_id = scene.scene_id
        architecture.beat_id = scene.beat_id
        architecture.narrative_claim = scene.scientific_claim or scene.narration
        architecture.canvas = (storyboard.width, storyboard.height)
        architecture.beauty_frame_first = True
        architecture.semantic_split_after_approval = True
        architecture.procedural_hero_allowed = False
        architecture.prohibited_visual_shortcuts = self._merge(
            architecture.prohibited_visual_shortcuts,
            bible.locked_canon.forbidden,
        )
        save_json(path, architecture)
        return architecture

    def _fallback(
        self,
        scene: SceneRequest,
        storyboard: Storyboard,
        bible: ArtDirectionBible,
        index: int,
    ) -> SceneIllustrationArchitecture:
        event = scene.visual_event or scene.desired_change or scene.narration
        lower = event.lower()
        figures: list[FigureConstruction] = []
        if any(
            token in lower
            for token in (
                "person",
                "human",
                "people",
                "hand",
                "body",
                "worker",
                "scientist",
            )
        ):
            figures.append(
                FigureConstruction(
                    figure_id="primary-human",
                    body_orientation="three-quarter profile aligned with the action",
                    weight_distribution="weight follows gravity and the physical action; feet and pelvis support the gesture",
                    gesture_line="one continuous directional sweep from head through rib cage, pelvis and action limb",
                    joint_logic=[
                        "shoulder mass connects arm to rib cage",
                        "elbow direction follows the forearm plane",
                        "wrist remains aligned with load",
                        "overlap hides seams in the resting pose",
                    ],
                )
            )
        materials: list[MaterialMarkPlan] = []
        candidates = [
            (
                "water",
                ("water", "rain", "ocean", "flood"),
                [
                    "layered contour rhythm",
                    "directional surface lines",
                    "selective foam breaks",
                ],
            ),
            (
                "atmosphere",
                ("wind", "air", "cloud", "atmosphere"),
                ["sparse flow traces", "soft value mass", "directional density"],
            ),
            (
                "concrete",
                ("city", "building", "wall", "infrastructure"),
                [
                    "perspective-aligned edges",
                    "irregular surface breaks",
                    "window rhythm",
                ],
            ),
            (
                "soil",
                ("soil", "ground", "slope", "land"),
                ["stratified contour", "grain marks", "compression cracks"],
            ),
            (
                "vegetation",
                ("tree", "plant", "crop", "ecosystem"),
                ["branch hierarchy", "leaf masses", "growth-direction marks"],
            ),
        ]
        for material, tokens, cues in candidates:
            if any(t in lower for t in tokens):
                materials.append(
                    MaterialMarkPlan(
                        material=material,
                        visual_cues=cues,
                        line_marks=cues,
                        value_behavior="2-4 restrained value bands",
                    )
                )
        if not materials:
            materials.append(
                MaterialMarkPlan(
                    material="primary physical system",
                    visual_cues=[
                        "observed silhouette",
                        "material-specific seams",
                        "controlled value breaks",
                    ],
                    line_marks=["form-following interior lines"],
                )
            )

        motion_seams = []
        desired = (scene.desired_change or event).strip()
        if desired and desired.lower() not in {"hold", "none", "static"}:
            # Representation is chosen from the *kind of state change*, never from
            # a noun in the text. A noun like "water" does not imply a texture
            # loop — water can rise, spread, surge, drip, freeze or evaporate, and
            # each is a different before→after. The deterministic fallback cannot
            # infer that semantics, so it uses an honest real state change
            # (replacement poses when a figure exists, else a spatial semantic-mask
            # reveal of the changed region) and lets the vision director refine it.
            method = "replacement_pose" if figures else "semantic_mask"
            motion_seams.append(
                MotionSeam(
                    seam_id="primary-causal-change",
                    subject=desired,
                    method=method,
                    region=desired,
                    resting_overlap_rule="The approved beauty frame remains visually continuous; the seam is hidden by overlap, value match or material edge.",
                    required_variants=["initial", "peak", "settled"]
                    if method == "replacement_pose"
                    else ["initial", "changed"],
                )
            )

        # On-screen scientific UI is OPT-IN (audio narrates the shot); only add
        # the overlay plane / annotations when explicitly enabled.
        overlay_on = bool(self.config.get("enable_scientific_overlay", False))
        depth_planes = [
            DepthPlane(
                plane_id="background",
                depth="background",
                contents="paper field and restrained contextual environment",
                line_weight_role="light structural",
            ),
            DepthPlane(
                plane_id="system",
                depth="midground",
                contents=event,
                line_weight_role="primary authored contour",
            ),
            DepthPlane(
                plane_id="causal-change",
                depth="foreground",
                contents=desired,
                line_weight_role="selective emphasis",
                movement_role="primary",
            ),
        ]
        if overlay_on:
            depth_planes.append(
                DepthPlane(
                    plane_id="scientific-ui",
                    depth="overlay",
                    contents="experiment identifier, status, one metric and concise labels",
                    line_weight_role="technical",
                )
            )
        scientific_annotations = (
            [
                scene.time_stage or "causal stage",
                "one measured variable or condition",
                "one direct label for the changed region",
            ]
            if overlay_on
            else []
        )
        return SceneIllustrationArchitecture(
            scene_id=scene.scene_id,
            beat_id=scene.beat_id,
            narrative_claim=scene.scientific_claim or scene.narration,
            visual_thesis=f"Show {event} as one coherent scientific environment, with the causal change dominating the eye path.",
            canvas=(storyboard.width, storyboard.height),
            composition_route=[
                "primary silhouette establishes the physical system",
                "directional contour or material flow leads to the causal change",
                "the changed region confirms the consequence",
            ],
            perspective=PerspectivePlan(
                camera_height="slightly above or at subject center according to system scale",
                view="three-quarter editorial cutaway when depth is relevant; orthographic only for explicit diagrams",
                horizon_y=0.48,
                vanishing_points=[(0.18, 0.48), (0.82, 0.48)],
            ),
            depth_planes=depth_planes,
            focal_subject=event,
            secondary_subjects=["contextual environment", "single scientific metric"],
            figure_construction=figures,
            material_marks=materials,
            negative_space="Reserve clear paper around the focal silhouette and keep the reading path unobstructed.",
            contour_architecture=[
                "one dominant silhouette with selective breaks at overlaps",
                "lighter structural lines describe perspective and material",
                "texture lines stop before becoming noise",
                "no uniform sticker outline around every internal region",
            ],
            color_script=[
                "paper and charcoal establish the instrument-like field",
                "blue-gray encodes the physical system",
                "one blue accent encodes direction or flow",
                "red appears only for critical threshold; yellow only for energy/light",
            ],
            scientific_annotations=scientific_annotations,
            motion_seams=motion_seams,
            animation_representation=[m.method for m in motion_seams] or ["hold"],
            required_pose_variants=[v for m in motion_seams for v in m.required_variants],
            director_notes=[
                "Create the full beauty composition first with Flux Kontext Pro.",
                "Do not isolate subjects before the scene reads as a complete authored illustration.",
                "Use the approved previous frame as a continuity reference, not a composition template.",
            ],
            prohibited_visual_shortcuts=list(bible.locked_canon.forbidden),
        )

    @staticmethod
    def _merge(a: list[str], b: list[str]) -> list[str]:
        out: list[str] = []
        for item in [*a, *b]:
            text = str(item).strip()
            if text and text not in out:
                out.append(text)
        return out
