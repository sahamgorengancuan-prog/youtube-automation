from __future__ import annotations

from pathlib import Path
from typing import Any

from .flux_prompt_system import FluxPromptSystem
from .schemas import (
    ArtDirectionBible,
    ContinuityCanon,
    DirectorChangeOrder,
    DrawingBrief,
    ReferencePack,
    SceneIllustrationArchitecture,
)


class DrawingBriefCompiler:
    """Compatibility facade around the V10 FLUX prompting system."""

    def __init__(self, config: dict[str, Any], root: str | Path):
        self.config = config
        self.root = Path(root)
        self.prompts = FluxPromptSystem(config, root)
        self.seed_base = self.prompts.policy.seed_base

    def master_anchor(self, topic: str, bible: ArtDirectionBible, reference_board_path: str) -> DrawingBrief:
        return self.prompts.master_anchor(topic, bible, reference_board_path)

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
        style_source_path: str | None = None,
        init_strategy: str | None = None,
    ) -> DrawingBrief:
        source = style_source_path or references.previous_approved_scene or references.board_path
        strategy = init_strategy or (
            "previous_approved_scene"
            if references.previous_approved_scene
            else "master_style_anchor"
            if style_source_path
            else "reference_board"
        )
        return self.prompts.beauty_frame(
            architecture,
            bible,
            continuity,
            references,
            narration,
            headline,
            index,
            style_source_path=source,
            init_strategy=strategy,
        )

    def revision_passes(
        self,
        original: DrawingBrief,
        order: DirectorChangeOrder,
        current_path: str,
        revision_cycle: int,
    ) -> list[DrawingBrief]:
        return self.prompts.revision_passes(original, order, current_path, revision_cycle)

    def revision(
        self,
        original: DrawingBrief,
        revision_number: int,
        revised_instruction: str,
        draft_path: str,
    ) -> DrawingBrief:
        # Legacy callers are mapped to a single explicit local-edit pass.
        from .schemas import ConcreteAdjustment, DirectorChangeOrder

        order = DirectorChangeOrder(
            scene_id=original.scene_id,
            revision_number=revision_number,
            status="revise",
            adjustments=[
                ConcreteAdjustment(
                    adjustment_id=f"LEGACY_{revision_number:02d}",
                    target_region="director-specified region",
                    problem="legacy revision instruction",
                    instruction=revised_instruction,
                    preserve=original.preserve,
                    priority="high",
                )
            ],
            immutable_preserve_list=original.preserve,
        )
        return self.revision_passes(original, order, draft_path, revision_number)[0]

    def pose_variant(
        self,
        original: DrawingBrief,
        approved_frame_path: str,
        variant_name: str,
        exact_change: str,
    ) -> DrawingBrief:
        return self.prompts.pose_variant(original, approved_frame_path, variant_name, exact_change)
