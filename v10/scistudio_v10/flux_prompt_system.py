from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from .schemas import (
    ArtDirectionBible,
    ContinuityCanon,
    DirectorChangeOrder,
    DrawingBrief,
    FluxGenerationPolicy,
    FluxPromptBlock,
    FluxPromptDiagnostics,
    FluxPromptStack,
    FluxStyleFingerprint,
    ReferencePack,
    SceneIllustrationArchitecture,
)
from .utils import ensure_dir, hash_value, save_json


class FluxPromptSystem:
    """Production prompt compiler for FLUX.1 Kontext [pro].

    It keeps a short immutable style fingerprint verbatim across the production,
    then describes only the scene delta. For edits it follows a strict local
    contract: name the target, state the drawable change, and explicitly preserve
    everything else. The prompt is natural language rather than a JSON dump.
    """

    VAGUE = (
        "make it better",
        "make it professional",
        "improve it",
        "fix the image",
        "more dynamic",
        "more beautiful",
        "less childish",
        "enhance quality",
    )
    CONTRADICTIONS = (
        ("camera locked", "camera movement"),
        ("flat paper field", "glossy 3d"),
        ("adult proportion", "oversized head"),
        ("preserve everything", "redesign everything"),
    )

    def __init__(self, config: dict[str, Any], root: str | Path):
        self.config = config
        self.root = ensure_dir(root)
        self.policy = FluxGenerationPolicy(
            model=str(config.get("model", "flux-kontext-pro")),
            aspect_ratio=str(config.get("aspect_ratio", "9:16")),
            prompt_upsampling=bool(config.get("prompt_upsampling", False)),
            safety_tolerance=int(config.get("safety_tolerance", 2)),
            output_format=str(config.get("output_format", "png")),
            seed_base=int(config.get("seed_base", 240921)),
            max_initial_prompt_words=int(config.get("max_initial_prompt_words", 380)),
            min_initial_prompt_words=int(config.get("min_initial_prompt_words", 80)),
            max_edit_prompt_words=int(config.get("max_edit_prompt_words", 180)),
            max_adjustments_per_pass=int(config.get("max_adjustments_per_pass", 1)),
        )
        save_json(self.root / "flux_generation_policy.json", self.policy)

    def fingerprint(self, bible: ArtDirectionBible) -> FluxStyleFingerprint:
        canon = bible.locked_canon
        fp = FluxStyleFingerprint(
            canon_id=canon.canon_id,
            medium_sentence=(
                "Create a mature scientific editorial ink illustration for adults as one authored scene on warm paper, never as assembled icons."
            ),
            contour_sentence=(
                "Use fluid charcoal contours with variable pressure, purposeful breaks and tapered ends; lighter interior lines explain form, overlap, perspective and force."
            ),
            anatomy_sentence=("Use believable adult proportions, weight, hands and joints with natural asymmetry."),
            palette_sentence=(
                f"Keep paper {canon.color.paper}, charcoal {canon.color.ink}, slate {canon.color.slate} and blue-gray {canon.color.blue_primary}, with sparse red and yellow causal accents."
            ),
            composition_sentence=(
                "Use one perspective, three to five depth planes, intentional negative space, restrained values and material-specific marks."
            ),
            texture_sentence="",
            anti_ai_sentence=(
                "It must feel drawn by one skilled illustrator, never clip-art, Office shapes, stickers or generic AI concept art."
            ),
        )
        fp.immutable_prompt = self._clean(
            " ".join(
                x
                for x in [
                    fp.medium_sentence,
                    fp.contour_sentence,
                    fp.anatomy_sentence,
                    fp.palette_sentence,
                    fp.composition_sentence,
                    fp.texture_sentence,
                    fp.anti_ai_sentence,
                ]
                if x
            )
        )
        fp.fingerprint_hash = hash_value(fp.immutable_prompt, 20)
        save_json(self.root / "style_fingerprint.json", fp)
        return fp

    def master_anchor(
        self,
        topic: str,
        bible: ArtDirectionBible,
        reference_board_path: str,
    ) -> DrawingBrief:
        fp = self.fingerprint(bible)
        blocks = [
            self._block("image", "image_type", fp.immutable_prompt, immutable=True),
            self._block(
                "subject",
                "subject_action",
                f"Create an original vertical style specimen for the scientific topic family '{topic}'. Show one integrated "
                "physical environment containing an atmospheric flow, a material structure, a measured phenomenon and one "
                "small experimental status panel. It is a studio visual canon, not a final narrative shot.",
            ),
            self._block(
                "composition",
                "composition",
                "Arrange the visual mass asymmetrically through foreground, midground and background, with a clear diagonal "
                "or curved eye path and enough clean paper for later scientific labels.",
            ),
            self._block(
                "edit",
                "edit_scope",
                "Use the input reference only to inherit the high-level experiment-ledger visual language. Replace all subject "
                "matter and composition with original content. Do not reproduce its globe, buildings, waves, labels or layout.",
            ),
            self._negative_block(bible, []),
        ]
        return self._brief(
            brief_id="MASTER_STYLE_ANCHOR",
            scene_id="MASTER",
            purpose="master_anchor",
            blocks=blocks,
            init_image_path=reference_board_path,
            init_strategy="reference_board",
            output_path=self.root / "master_style_anchor.png",
            seed=self.policy.seed_base,
            preserve=["line rhythm", "paper field", "palette roles", "scientific UI grammar"],
            change=["all content and composition"],
            fingerprint=fp,
        )

    def beauty_frame(
        self,
        architecture: SceneIllustrationArchitecture,
        bible: ArtDirectionBible,
        continuity: ContinuityCanon,
        references: ReferencePack,
        narration: str,
        headline: str,
        index: int,
        *,
        style_source_path: str,
        init_strategy: str,
    ) -> DrawingBrief:
        fp = self.fingerprint(bible)
        subject = self._scene_subject(architecture)
        environment = self._environment(architecture)
        composition = self._composition(architecture)
        construction = self._construction(architecture)
        continuity_text = self._continuity(continuity, references)
        animation = self._animation_prep(architecture)
        negatives = self._negative_block(bible, architecture.prohibited_visual_shortcuts)
        blocks = [
            self._block("image", "image_type", fp.immutable_prompt, immutable=True),
            self._block("subject", "subject_action", subject),
            self._block("environment", "environment", environment),
            self._block("composition", "composition", composition),
        ]
        if construction:
            blocks.append(self._block("construction", "anatomy_material", construction))
        blocks.extend(
            [
                self._block("continuity", "continuity", continuity_text, immutable=True),
                self._block("animation", "animation_preparation", animation),
                self._block(
                    "overlay",
                    "lighting_color",
                    "Reserve clean space for later scientific overlays. Draw no narration or final labels in the beauty art.",
                ),
                negatives,
            ]
        )
        brief = self._brief(
            brief_id=f"{architecture.scene_id}_BEAUTY",
            scene_id=architecture.scene_id,
            purpose="beauty_frame",
            blocks=blocks,
            init_image_path=style_source_path,
            init_strategy=init_strategy,
            output_path=self.root / "beauty" / f"{architecture.scene_id}_draft_00.png",
            seed=self.policy.seed_base + index,
            preserve=[
                "exact house line rhythm",
                "paper field and palette roles",
                "adult construction language",
                "continuity subjects explicitly named in the brief",
            ],
            change=["scene content", "pose", "environment state", "causal phenomenon"],
            fingerprint=fp,
            semantic_requirements=[m.seam_id + ": " + m.region for m in architecture.motion_seams],
            motion_requirements=architecture.animation_representation,
            anchor_board_path=references.board_path,
        )
        brief.request_metadata.update(
            {
                "narration_context": narration,
                "headline_context": headline,
                "reference_requirements": [r.model_dump(mode="json") for r in references.requirements],
            }
        )
        self._save(brief)
        return brief

    def revision_passes(
        self,
        original: DrawingBrief,
        order: DirectorChangeOrder,
        current_path: str,
        revision_cycle: int,
    ) -> list[DrawingBrief]:
        ranked = sorted(order.adjustments, key=lambda a: self._priority(a.priority))
        if not ranked and order.status == "revise":
            raise ValueError("Director revision requires concrete adjustments")
        groups: list[list[Any]] = []
        for adjustment in ranked:
            if (
                groups
                and len(groups[-1]) < self.policy.max_adjustments_per_pass
                and self._region_root(groups[-1][0].target_region) == self._region_root(adjustment.target_region)
            ):
                groups[-1].append(adjustment)
            else:
                groups.append([adjustment])
        briefs: list[DrawingBrief] = []
        source = current_path
        for pass_index, adjustments in enumerate(groups, 1):
            target_regions = [a.target_region for a in adjustments]
            preserve = self._unique(
                [
                    *order.immutable_preserve_list,
                    *[item for a in adjustments for item in a.preserve],
                    "all pixels, line rhythm, palette, camera and geometry outside the named target region",
                ]
            )
            change_sentences = [f"In {a.target_region}, {a.instruction.rstrip('.')}" for a in adjustments]
            instruction = (
                f"Edit only {', '.join(target_regions)}. " + " ".join(change_sentences) + ". "
                f"Preserve unchanged: {'; '.join(preserve)}. Do not restyle, recompose, crop, relight or redesign any unaffected region."
            )
            blocks = [
                self._block("edit", "edit_scope", instruction),
                self._block(
                    "consistency",
                    "continuity",
                    "Keep the exact approved authored ink language, adult anatomy, line pressure, palette, paper texture and camera from the input image.",
                    immutable=True,
                ),
            ]
            brief_id = f"{original.scene_id}_REV_{revision_cycle:02d}_{pass_index:02d}"
            output = self.root / "beauty" / f"{original.scene_id}_draft_{revision_cycle:02d}_{pass_index:02d}.png"
            brief = self._brief(
                brief_id=brief_id,
                scene_id=original.scene_id,
                purpose="revision",
                blocks=blocks,
                init_image_path=source,
                init_strategy="current_revision",
                output_path=output,
                seed=original.seed,
                preserve=preserve,
                change=change_sentences,
                fingerprint_hash=original.style_fingerprint_hash,
                semantic_requirements=original.semantic_requirements,
                motion_requirements=original.motion_requirements,
            )
            brief.request_metadata["revision_cycle"] = revision_cycle
            brief.request_metadata["revision_pass"] = pass_index
            brief.request_metadata["target_regions"] = target_regions
            briefs.append(brief)
            source = str(output)
        return briefs

    def pose_variant(
        self,
        original: DrawingBrief,
        approved_frame_path: str,
        variant_name: str,
        exact_change: str,
    ) -> DrawingBrief:
        safe = self._safe(variant_name)
        instruction = (
            f"Edit only the local animation state named '{variant_name}': {exact_change.rstrip('.')}. "
            "Keep the exact composition, camera, background, face identity, line pressure, palette, material marks and every pixel outside that local region unchanged. "
            "The unchanged area must register with the approved input frame for replacement animation; do not redesign or improve anything else."
        )
        blocks = [
            self._block("edit", "edit_scope", instruction),
            self._block(
                "style",
                "continuity",
                "Preserve the exact approved illustrated style from the input image; this is a local state edit, not a new generation.",
                immutable=True,
            ),
        ]
        return self._brief(
            brief_id=f"{original.scene_id}_POSE_{safe.upper()}",
            scene_id=original.scene_id,
            purpose="pose_variant",
            blocks=blocks,
            init_image_path=approved_frame_path,
            init_strategy="approved_beauty_frame",
            output_path=self.root / "poses" / original.scene_id / f"{safe}.png",
            seed=original.seed,
            preserve=["all unaffected pixels", *original.preserve],
            change=[exact_change],
            fingerprint_hash=original.style_fingerprint_hash,
            semantic_requirements=original.semantic_requirements,
            motion_requirements=original.motion_requirements,
        )

    def lint(self, brief: DrawingBrief) -> FluxPromptDiagnostics:
        prompt = brief.compiled_prompt or (brief.prompt_stack.compiled_prompt if brief.prompt_stack else "")
        low = prompt.lower()
        errors: list[str] = []
        warnings: list[str] = []
        vague = [x for x in self.VAGUE if x in low]
        contradictions = [f"{a} <> {b}" for a, b in self.CONTRADICTIONS if a in low and b in low]
        words = self._word_count(prompt)
        edit = brief.purpose in {"revision", "pose_variant", "layer_isolation"}
        if not prompt.strip():
            errors.append("compiled prompt is empty")
        if "{" in prompt or "}" in prompt:
            errors.append("prompt contains JSON-like braces")
        if vague:
            errors.append("vague edit language: " + ", ".join(vague))
        if contradictions:
            errors.append("contradictory instructions: " + ", ".join(contradictions))
        if edit:
            if words > self.policy.max_edit_prompt_words:
                errors.append("edit prompt is too long")
            if not any(x in low for x in ("edit only", "change only", "local animation state")):
                errors.append("edit prompt lacks a local target scope")
            if not any(x in low for x in ("preserve", "keep the exact", "unchanged")):
                errors.append("edit prompt lacks explicit preservation")
        else:
            if words < self.policy.min_initial_prompt_words:
                warnings.append("initial prompt may be under-specified")
            if words > self.policy.max_initial_prompt_words:
                errors.append("initial prompt is bloated")
            first = " ".join(prompt.split()[:55]).lower()
            if not any(x in first for x in ("scientific editorial", "editorial ink", "illustration")):
                errors.append("style and image type are not stated early")
        diagnostics = FluxPromptDiagnostics(
            valid=not errors,
            errors=errors,
            warnings=warnings,
            word_count=words,
            immutable_style_hash=brief.style_fingerprint_hash,
            has_explicit_subject=any(x in low for x in ("focal", "show", "scene", "subject")),
            has_explicit_preservation=any(x in low for x in ("preserve", "keep the exact", "unchanged")),
            has_local_edit_scope=any(x in low for x in ("edit only", "change only", "local animation state")),
            vague_language_found=vague,
            contradictory_language_found=contradictions,
        )
        return diagnostics

    def _fit_initial_budget(self, blocks: list[FluxPromptBlock], max_words: int) -> list[FluxPromptBlock]:
        """Trim descriptive blocks so the assembled initial prompt fits the
        word budget. Immutable blocks (style/image type, continuity) and the
        negative-constraints block are preserved in full; the remaining
        LLM-authored blocks are shortened proportionally to their length, each
        keeping at least a short head so no scene element is dropped entirely.
        Deterministic, so a resumed job reproduces the same prompt.
        """
        protected_roles = {"negative_constraints"}

        def word_count(text: str) -> int:
            return len(text.split())

        def is_fixed(block: FluxPromptBlock) -> bool:
            return block.immutable or block.role in protected_roles

        fixed_words = sum(word_count(b.text) for b in blocks if is_fixed(b))
        trimmable = [b for b in blocks if not is_fixed(b)]
        trimmable_total = sum(word_count(b.text) for b in trimmable) or 1
        budget = max(0, max_words - fixed_words)

        result: list[FluxPromptBlock] = []
        for block in blocks:
            if is_fixed(block):
                result.append(block)
                continue
            share = max(8, int(budget * word_count(block.text) / trimmable_total))
            words = block.text.split()
            if len(words) > share:
                trimmed = " ".join(words[:share]).rstrip(",;: ") + "."
                result.append(
                    self._block(block.block_id, block.role, trimmed, immutable=block.immutable, priority=block.priority)
                )
            else:
                result.append(block)
        return result

    def _brief(
        self,
        *,
        brief_id: str,
        scene_id: str,
        purpose: str,
        blocks: list[FluxPromptBlock],
        init_image_path: str,
        init_strategy: str,
        output_path: str | Path,
        seed: int | None,
        preserve: list[str],
        change: list[str],
        fingerprint: FluxStyleFingerprint | None = None,
        fingerprint_hash: str = "",
        semantic_requirements: list[str] | None = None,
        motion_requirements: list[str] | None = None,
        anchor_board_path: str = "",
    ) -> DrawingBrief:
        # Fit an over-long INITIAL prompt to the word budget by trimming the
        # descriptive LLM-authored blocks — never the immutable style/continuity
        # blocks or the negative constraints — instead of aborting an expensive
        # production run. Edit prompts keep their own (stricter) length gate.
        edit = purpose in {"revision", "pose_variant", "layer_isolation"}
        if not edit and self._word_count(self._compile(blocks)) > self.policy.max_initial_prompt_words:
            blocks = self._fit_initial_budget(blocks, self.policy.max_initial_prompt_words)
        compiled = self._compile(blocks)
        style_hash = fingerprint.fingerprint_hash if fingerprint else fingerprint_hash
        stack = FluxPromptStack(
            purpose=purpose,
            blocks=blocks,
            compiled_prompt=compiled,
            immutable_style_hash=style_hash,
            scene_delta_hash=hash_value([b.text for b in blocks if not b.immutable], 20),
            word_count=self._word_count(compiled),
            init_strategy=init_strategy,
        )
        negative_text = next((b.text for b in blocks if b.role == "negative_constraints"), "")
        edit_text = next((b.text for b in blocks if b.role == "edit_scope"), "")
        brief = DrawingBrief(
            brief_id=brief_id,
            scene_id=scene_id,
            purpose=purpose,
            positive_prompt=compiled,
            negative_prompt=negative_text,
            kontext_instruction=edit_text or compiled,
            anchor_board_path=anchor_board_path,
            init_image_path=init_image_path,
            output_path=str(output_path),
            aspect_ratio=self.policy.aspect_ratio,
            seed=seed,
            preserve=preserve,
            change=change,
            semantic_requirements=semantic_requirements or [],
            motion_requirements=motion_requirements or [],
            prompt_stack=stack,
            compiled_prompt=compiled,
            style_fingerprint_hash=style_hash,
            init_strategy=init_strategy,
            prompt_upsampling=self.policy.prompt_upsampling,
            safety_tolerance=self.policy.safety_tolerance,
            output_format=self.policy.output_format,
            request_metadata={"model": self.policy.model, "policy": self.policy.model_dump(mode="json")},
        )
        brief.prompt_diagnostics = self.lint(brief)
        if not brief.prompt_diagnostics.valid:
            raise ValueError(f"Invalid FLUX prompt {brief_id}: {'; '.join(brief.prompt_diagnostics.errors)}")
        self._save(brief)
        return brief

    def _save(self, brief: DrawingBrief) -> None:
        ensure_dir(Path(brief.output_path).parent)
        save_json(self.root / "briefs" / f"{brief.brief_id}.json", brief)
        if brief.prompt_diagnostics:
            save_json(self.root / "diagnostics" / f"{brief.brief_id}.json", brief.prompt_diagnostics)

    @staticmethod
    def _block(block_id: str, role: str, text: str, immutable: bool = False, priority: int = 1) -> FluxPromptBlock:
        return FluxPromptBlock(
            block_id=block_id, role=role, text=FluxPromptSystem._clean(text), immutable=immutable, priority=priority
        )

    def _negative_block(self, bible: ArtDirectionBible, extra: list[str]) -> FluxPromptBlock:
        items = self._unique(
            [
                "child mascot anatomy",
                "oversized round heads",
                "tube or capsule limbs",
                "mitten or oval hands",
                "uniform sticker outlines",
                "isolated icon collage",
                "Microsoft Word shape assembly",
                "Office-shape geometry",
                "glossy generic AI rendering",
                "random ornamental detail",
                "incoherent perspective",
                "visible animation cutout seams",
                *bible.locked_canon.forbidden,
                *extra,
            ]
        )
        # Keep negatives concise. FLUX responds better to a clear positive image description;
        # this final sentence only blocks the most damaging failure modes.
        return self._block("negative", "negative_constraints", "Avoid " + ", ".join(items[:8]) + ".")

    @staticmethod
    def _compile(blocks: Iterable[FluxPromptBlock]) -> str:
        # Preserve the director-authored order. FLUX benefits from image type and
        # main subject appearing early; alphabetical sorting previously buried them.
        return FluxPromptSystem._clean(" ".join(b.text for b in blocks if b.text.strip()))

    @staticmethod
    def _scene_subject(a: SceneIllustrationArchitecture) -> str:
        focus = FluxPromptSystem._strip_directive(
            FluxPromptSystem._clean(a.focal_subject or a.visual_thesis).rstrip(" .;:")
        )
        claim = FluxPromptSystem._strip_directive(
            FluxPromptSystem._clean(a.narrative_claim or a.visual_thesis).rstrip(" .;:")
        )
        secondaries = FluxPromptSystem._unique(
            FluxPromptSystem._clean(x).rstrip(" .;:")
            for x in a.secondary_subjects
            if FluxPromptSystem._clean(x).lower() not in {focus.lower(), claim.lower()}
        )[:3]
        text = f"Show {focus}. Make {claim} the dominant causal event."
        if secondaries:
            text += f" Integrate {', '.join(secondaries)} into that same scene, never as separate icons."
        return text

    @staticmethod
    def _environment(a: SceneIllustrationArchitecture) -> str:
        focus = FluxPromptSystem._clean(a.focal_subject or a.visual_thesis).lower()
        parts = FluxPromptSystem._unique(
            FluxPromptSystem._clean(p.contents).rstrip(" .;:")
            for p in a.depth_planes
            if p.depth != "overlay" and FluxPromptSystem._clean(p.contents).lower() != focus
        )[:3]
        contents = ", ".join(parts) if parts else "a coherent physical setting"
        return f"Build one integrated environment from {contents}. Connect foreground, subject and atmosphere through overlap, scale and perspective; keep the causal action legible on a phone."

    @staticmethod
    def _composition(a: SceneIllustrationArchitecture) -> str:
        route_items = [
            FluxPromptSystem._route_phrase(x)
            for x in (a.composition_route[:3] or ["focal subject", "causal consequence"])
        ]
        route = " to ".join(route_items)
        p = a.perspective
        view = re.split(r"\bwhen\b", FluxPromptSystem._clean(p.view).split(";")[0], maxsplit=1, flags=re.I)[0].rstrip(
            " ."
        )
        height = re.split(
            r"\baccording\b", FluxPromptSystem._clean(p.camera_height).split(";")[0], maxsplit=1, flags=re.I
        )[0].rstrip(" .")
        negative = FluxPromptSystem._clean(a.negative_space or "Keep deliberate negative space beside the focal route.")
        return f"Use {view} from {height}, one horizon near {p.horizon_y:.2f} of frame height, and guide the eye from {route}. {negative}"

    @staticmethod
    def _construction(a: SceneIllustrationArchitecture) -> str:
        chunks: list[str] = []
        for f in a.figure_construction[:2]:
            chunks.append(
                f"Construct {f.figure_id} at {f.proportion_heads:.1f} adult heads, {f.body_orientation}, with {f.weight_distribution}; "
                f"hands use palm, knuckle and thumb structure and clothing follows tension and gravity."
            )
        for m in a.material_marks[:3]:
            cues = ", ".join(m.visual_cues[:2])
            marks = ", ".join(m.line_marks[:2])
            chunks.append(f"Describe {m.material} through {cues}, using {marks} and restrained values.")
        if a.contour_architecture:
            chunks.append("Contour rule: " + "; ".join(a.contour_architecture[:2]) + ".")
        return " ".join(chunks)

    @staticmethod
    def _continuity(c: ContinuityCanon, r: ReferencePack) -> str:
        prior = (
            "Use the previous approved scene as visual DNA"
            if r.previous_approved_scene
            else "Use the master anchor as visual DNA"
        )
        recurring_names = [
            getattr(x, "description", "") or getattr(x, "subject_id", "") or str(x) for x in c.recurring_subjects[:3]
        ]
        recurring = (
            ", ".join(FluxPromptSystem._clean(x) for x in recurring_names if FluxPromptSystem._clean(x))
            or "recurring subjects"
        )
        return (
            f"Continuity lock: {prior}. Preserve house line rhythm, palette roles, paper field, value hierarchy and the construction of {recurring}. "
            "Change only this shot's narrative content; never drift into another illustrator, anatomy system or finish."
        )

    @staticmethod
    def _animation_prep(a: SceneIllustrationArchitecture) -> str:
        seams = "; ".join(f"{m.region} via {m.method}" for m in a.motion_seams[:3]) or "no local moving region"
        return f"Prepare later motion at {seams}. Hide seams inside natural overlaps, material boundaries or atmosphere; never show puppet joints or sticker edges."

    @staticmethod
    def _strip_directive(text: str) -> str:
        return re.sub(
            r"^(show|establish|depict|illustrate|visualize|create|resolve|conclude|summarize)\s+", "", text, flags=re.I
        ).strip()

    @staticmethod
    def _route_phrase(text: str) -> str:
        value = FluxPromptSystem._clean(text).rstrip(" .;:")
        value = re.split(
            r"\b(?:establishes|reveals|shows|leads|guides|indicates|explains)\b", value, maxsplit=1, flags=re.I
        )[0]
        return FluxPromptSystem._strip_directive(value).strip() or "causal consequence"

    @staticmethod
    def _clean(text: str) -> str:
        return re.sub(r"\s+", " ", str(text)).strip()

    @staticmethod
    def _word_count(text: str) -> int:
        return len(re.findall(r"\b[\w#./'-]+\b", text))

    @staticmethod
    def _safe(value: str) -> str:
        return "-".join("".join(ch.lower() if ch.isalnum() else " " for ch in value).split())

    @staticmethod
    def _unique(items: Iterable[str]) -> list[str]:
        out: list[str] = []
        for item in items:
            item = str(item).strip()
            if item and item not in out:
                out.append(item)
        return out

    @staticmethod
    def _priority(value: str) -> int:
        return {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(str(value), 2)

    @staticmethod
    def _region_root(value: str) -> str:
        return re.split(r"[./:_-]", value.lower())[0]
