from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageOps

from .drawing_brief import DrawingBriefCompiler
from .llm import LLMRouter
from .schemas import (
    ArtDirectionBible,
    BeautyFrame,
    ContinuityCanon,
    DirectorChangeOrder,
    DrawingBrief,
    RevisionRecord,
    SceneIllustrationArchitecture,
)
from .utils import ensure_dir, hash_value, save_json, sha256_file


class FluxKontextStudio:
    """Executive-art-directed FLUX.1 Kontext [pro] production studio.

    The model receives one visual input at a time, matching the Kontext Pro API.
    A master anchor establishes the house style. Each scene then uses either that
    anchor or the previous approved scene as its single input. Edits are local,
    explicit and sequential rather than vague multi-region rewrites.
    """

    def __init__(
        self,
        llm: LLMRouter,
        brief_compiler: DrawingBriefCompiler,
        config: dict[str, Any],
        root: str | Path,
        image_generator: Callable[[DrawingBrief], Path | None] | None = None,
    ):
        self.llm = llm
        self.briefs = brief_compiler
        self.config = config
        self.root = ensure_dir(root)
        self.image_generator = image_generator

    def generate(self, brief: DrawingBrief, *, force: bool = False) -> Path | None:
        output = Path(brief.output_path)
        ensure_dir(output.parent)
        if output.exists() and output.stat().st_size > 1024 and not force:
            return output

        prompt = brief.compiled_prompt or (
            brief.prompt_stack.compiled_prompt if brief.prompt_stack else brief.positive_prompt
        )
        if not prompt.strip():
            raise ValueError(f"Drawing brief {brief.brief_id} has no compiled FLUX prompt")
        if brief.prompt_diagnostics and not brief.prompt_diagnostics.valid:
            raise ValueError(f"Drawing brief {brief.brief_id} failed prompt diagnostics")
        init = brief.init_image_path if brief.init_image_path and Path(brief.init_image_path).exists() else None
        manifest = {
            "brief_id": brief.brief_id,
            "scene_id": brief.scene_id,
            "purpose": brief.purpose,
            "model": brief.request_metadata.get(
                "model", getattr(self.llm, "config", {}).get("bfl_model", "flux-kontext-pro")
            ),
            "prompt": prompt,
            "prompt_hash": hash_value(prompt, 24),
            "prompt_word_count": len(prompt.split()),
            "style_fingerprint_hash": brief.style_fingerprint_hash,
            "init_strategy": brief.init_strategy,
            "init_image_path": str(init or ""),
            "init_image_hash": sha256_file(init) if init else "",
            "aspect_ratio": brief.aspect_ratio,
            "seed": brief.seed,
            "prompt_upsampling": brief.prompt_upsampling,
            "safety_tolerance": brief.safety_tolerance,
            "output_format": brief.output_format,
            "output_path": str(output),
        }
        save_json(self.root / "requests" / f"{brief.brief_id}.json", manifest)

        if self.image_generator is not None:
            return self.image_generator(brief)

        # Context manager behavior without introducing a global mutable provider
        # state after the call returns.
        keys = {
            "bfl_aspect_ratio": brief.aspect_ratio,
            "bfl_seed": brief.seed,
            "bfl_prompt_upsampling": brief.prompt_upsampling,
            "bfl_safety_tolerance": brief.safety_tolerance,
            "bfl_output_format": brief.output_format,
        }
        previous = {k: self.llm.config.get(k, None) for k in keys}
        for key, value in keys.items():
            if value is None:
                self.llm.config.pop(key, None)
            else:
                self.llm.config[key] = value
        try:
            return self.llm.generate_reference_image(prompt=prompt, output_path=output, init_image=init, force=force)
        finally:
            for key, value in previous.items():
                if value is None:
                    self.llm.config.pop(key, None)
                else:
                    self.llm.config[key] = value

    def create_master_anchor(self, brief: DrawingBrief, *, force: bool = False) -> str:
        result = self.generate(brief, force=force)
        if result is None:
            raise RuntimeError("FLUX Kontext Pro could not create the master style anchor")
        save_json(
            self.root / "master_anchor_manifest.json",
            {
                "path": str(result),
                "brief_id": brief.brief_id,
                "canon_id": brief.canon_id,
                "seed": brief.seed,
                "provider": "flux-kontext-pro",
                "style_fingerprint_hash": brief.style_fingerprint_hash,
            },
        )
        return str(result)

    def direct_scene(
        self,
        brief: DrawingBrief,
        architecture: SceneIllustrationArchitecture,
        bible: ArtDirectionBible,
        continuity: ContinuityCanon,
        *,
        initial_path: str | None = None,
        force: bool = False,
    ) -> BeautyFrame:
        first = Path(initial_path) if initial_path else self.generate(brief, force=force)
        if first is None or not Path(first).exists():
            raise RuntimeError(f"FLUX Kontext Pro could not create scene {brief.scene_id}")
        revisions: list[RevisionRecord] = []
        current = str(first)
        max_cycles = int(self.config.get("maximum_director_revisions", 3))
        strict_director = bool(self.config.get("require_vision_director", True))
        approval_source = ""

        for revision_cycle in range(1, max_cycles + 1):
            comparison = self._comparison_board(current, brief.scene_id, continuity, revision_cycle)
            change = self._director_review(
                comparison,
                brief,
                architecture,
                bible,
                continuity,
                revision_number=revision_cycle,
                force=force,
            )
            save_json(self.root / "change_orders" / f"{brief.scene_id}_{revision_cycle:02d}.json", change)
            if change.status == "approve":
                approval_source = "vision-llm-art-director"
                break
            if change.status == "requires_human_or_vision_director":
                if strict_director:
                    raise RuntimeError(
                        f"Scene {brief.scene_id} requires an available vision art director. "
                        "The pipeline will not substitute procedural or unreviewed hero illustration."
                    )
                approval_source = "unreviewed-preview"
                break

            pass_briefs = self.briefs.revision_passes(brief, change, current, revision_cycle)
            for pass_number, revision_brief in enumerate(pass_briefs, 1):
                revised = self.generate(revision_brief, force=force)
                if revised is None:
                    raise RuntimeError(f"FLUX revision {revision_cycle}.{pass_number} failed for {brief.scene_id}")
                revisions.append(
                    RevisionRecord(
                        scene_id=brief.scene_id,
                        revision_number=len(revisions) + 1,
                        draft_path=current,
                        change_order=change,
                        output_path=str(revised),
                    )
                )
                current = str(revised)
        else:
            raise RuntimeError(f"Scene {brief.scene_id} exhausted revision budget without approval")

        frame = BeautyFrame(
            scene_id=brief.scene_id,
            image_path=current,
            approved=approval_source in {"vision-llm-art-director", "human-director"},
            approval_source=approval_source,
            style_anchor_ids=[brief.init_image_path, brief.anchor_board_path],
            revision_records=revisions,
            prompt_hash=hash_value(brief.model_dump(mode="json"), 20),
            seed=brief.seed,
        )
        save_json(self.root / "beauty_frames" / f"{brief.scene_id}.json", frame)
        return frame

    def create_pose_variants(
        self,
        beauty: BeautyFrame,
        original_brief: DrawingBrief,
        architecture: SceneIllustrationArchitecture,
        *,
        force: bool = False,
    ) -> dict[str, str]:
        if not beauty.approved:
            raise RuntimeError("Pose variants may only be derived from an approved beauty frame")
        variants: dict[str, str] = {}
        descriptions = self._variant_descriptions(architecture)
        for name, change in descriptions.items():
            brief = self.briefs.pose_variant(original_brief, beauty.image_path, name, change)
            result = self.generate(brief, force=force)
            if result is None:
                raise RuntimeError(f"Pose/state variant generation failed: {architecture.scene_id}/{name}")
            variants[name] = str(result)
        save_json(self.root / "pose_variants" / f"{architecture.scene_id}.json", variants)
        return variants

    def _comparison_board(
        self,
        current_path: str,
        scene_id: str,
        continuity: ContinuityCanon,
        revision_number: int,
    ) -> str:
        master = next(
            (a.path for a in continuity.anchors if a.role == "master_style_anchor" and Path(a.path).exists()), ""
        )
        approved = [a.path for a in continuity.anchors if a.role == "approved_scene" and Path(a.path).exists()]
        previous = approved[-1] if approved else ""
        items = [("MASTER STYLE", master), ("PREVIOUS APPROVED", previous), ("CURRENT DRAFT", current_path)]
        output = self.root / "director_boards" / f"{scene_id}_{revision_number:02d}.png"
        ensure_dir(output.parent)
        canvas = Image.new("RGB", (1536, 1024), "#E6E8E7")
        draw = ImageDraw.Draw(canvas)
        cells = [(0, 0, 512, 1024), (512, 0, 1024, 1024), (1024, 0, 1536, 1024)]
        for (label, path), box in zip(items, cells):
            x0, y0, x1, y1 = box
            draw.rectangle(box, fill="#FAFAF7", outline="#475157", width=3)
            if path and Path(path).exists():
                image = Image.open(path).convert("RGB")
                fitted = ImageOps.contain(image, (x1 - x0 - 28, y1 - y0 - 90))
                canvas.paste(fitted, (x0 + (x1 - x0 - fitted.width) // 2, y0 + 50))
            draw.rectangle((x0 + 8, 8, x1 - 8, 42), fill="#20282D")
            draw.text((x0 + 18, 16), label, fill="white")
        canvas.save(output)
        return str(output)

    def _director_review(
        self,
        comparison_path: str,
        brief: DrawingBrief,
        architecture: SceneIllustrationArchitecture,
        bible: ArtDirectionBible,
        continuity: ContinuityCanon,
        *,
        revision_number: int,
        force: bool,
    ) -> DirectorChangeOrder:
        fallback = DirectorChangeOrder(
            scene_id=brief.scene_id,
            revision_number=revision_number,
            status="requires_human_or_vision_director",
            diagnosis="No vision director was available; aesthetic approval cannot be inferred from technical metrics.",
            adjustments=[],
            immutable_preserve_list=brief.preserve,
        )
        raw = self.llm.critique_image(
            image_path=comparison_path,
            namespace=f"v10_1_art_director_{brief.scene_id}_{revision_number:02d}",
            force=force,
            fallback=fallback.model_dump(mode="json"),
            prompt=f"""You are the Executive Art Director reviewing a three-panel comparison board.
Left: MASTER STYLE. Center: PREVIOUS APPROVED SCENE when available. Right: CURRENT DRAFT.
Return JSON only as a DirectorChangeOrder. You are directing the next drawing pass, not assigning a score.

LOCKED BIBLE:
{json.dumps(bible.model_dump(mode="json"), ensure_ascii=False)}

SCENE ARCHITECTURE:
{json.dumps(architecture.model_dump(mode="json"), ensure_ascii=False)}

CONTINUITY:
{json.dumps(continuity.model_dump(mode="json"), ensure_ascii=False)}

FLUX PROMPT DIAGNOSTICS:
{json.dumps(brief.prompt_diagnostics.model_dump(mode="json") if brief.prompt_diagnostics else {}, ensure_ascii=False)}

Compare the CURRENT DRAFT against the MASTER and PREVIOUS panels. Inspect composition, coherent perspective,
fluid authored contour rhythm, line hierarchy, adult anatomy and weight, hand/joint construction, material marks,
palette roles, negative space, recurring subject identity and hidden motion seams. Detect generic AI smoothness,
mascot construction, sticker outlines, clip-art isolation, random details and style drift.

If ready, return status=approve with no adjustments. If revision is needed, return status=revise with one to three
ConcreteAdjustments only. Prioritize the most damaging visible issue. Each adjustment must name a precise target region,
state the visible problem, give a measurable or drawable instruction, list what must remain unchanged, and assign priority.
Do not say only 'make it better', 'more professional', 'less childish' or 'more dynamic'. Do not ask Kontext to redesign
unaffected regions. The prompt compiler will convert your adjustments into sequential local edit passes.""",
        )
        try:
            return DirectorChangeOrder.model_validate(raw)
        except Exception:
            return fallback

    @staticmethod
    def _variant_descriptions(architecture: SceneIllustrationArchitecture) -> dict[str, str]:
        variants: dict[str, str] = {}
        for seam in architecture.motion_seams:
            for variant in seam.required_variants:
                key = f"{seam.seam_id}-{variant}"
                variants[key] = (
                    f"Change {seam.region} to the '{variant}' state of {seam.subject}; "
                    f"representation method {seam.method}. Preserve all unaffected pixels and the natural overlap at the seam."
                )
        return variants
