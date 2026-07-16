from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps

from .llm import LLMRouter
from .schemas import (
    ArtDirectionBible,
    CanonAnchor,
    ContinuityCanon,
    ReferencePack,
    ReferenceRequirement,
    SceneIllustrationArchitecture,
)
from .utils import ensure_dir, load_json, save_json


class ReferenceDirector:
    SYSTEM = """You are the Reference Director for a scientific illustration studio.
Return JSON only. Request only references that materially improve observation: style, anatomy, pose,
environment, material, lighting or continuity. References are evidence and construction aids—not final art.
Never request copying a composition or another artist's exact drawing."""

    def __init__(self, llm: LLMRouter, config: dict[str, Any], root: str | Path):
        self.llm = llm
        self.config = config
        self.root = ensure_dir(root)

    def plan(
        self,
        architecture: SceneIllustrationArchitecture,
        bible: ArtDirectionBible,
        continuity: ContinuityCanon,
        previous_approved_scene: str = "",
        *,
        force: bool = False,
    ) -> ReferencePack:
        path = self.root / f"{architecture.scene_id}.json"
        if path.exists() and not force:
            return ReferencePack.model_validate(load_json(path))
        fallback = self._fallback(architecture, bible, continuity, previous_approved_scene)
        raw = self.llm.generate_json(
            system=self.SYSTEM,
            prompt=f"""Scene architecture:
{json.dumps(architecture.model_dump(mode="json"), ensure_ascii=False)}
Continuity subjects:
{json.dumps([s.model_dump(mode="json") for s in continuity.recurring_subjects], ensure_ascii=False)}

Return requirements with reference_id, purpose, query, priority, use_rule and source_preference.
Always include the locked style anchor and continuity reference. Add pose/anatomy references for human action,
environment references for perspective/materials, and material references for complex fluids, terrain or damage.
Keep the list compact and purposeful.""",
            namespace=f"v10_reference_plan_{architecture.scene_id}",
            fallback=fallback.model_dump(mode="json"),
            force=force,
        )
        try:
            pack = ReferencePack.model_validate(raw)
        except Exception:
            pack = fallback
        pack.scene_id = architecture.scene_id
        pack.style_anchor_ids = self._unique(["reference-video-board", *pack.style_anchor_ids])
        pack.previous_approved_scene = previous_approved_scene
        save_json(path, pack)
        return pack

    def build_board(
        self,
        pack: ReferencePack,
        continuity: ContinuityCanon,
        *,
        extra_paths: list[str] | None = None,
    ) -> str:
        output = self.root / "boards" / f"{pack.scene_id}_anchor_board.png"
        ensure_dir(output.parent)
        paths: list[tuple[str, str]] = []
        anchor_map = {a.anchor_id: a for a in continuity.anchors if a.approved and Path(a.path).exists()}
        for anchor_id in pack.style_anchor_ids:
            anchor = anchor_map.get(anchor_id)
            if anchor:
                paths.append((anchor.role, anchor.path))
        if pack.previous_approved_scene and Path(pack.previous_approved_scene).exists():
            paths.append(("previous approved scene", pack.previous_approved_scene))
        for path in pack.subject_anchor_paths + pack.environment_anchor_paths + list(extra_paths or []):
            if path and Path(path).exists():
                paths.append(("continuity anchor", path))
        # Avoid a single image being duplicated across all cells.
        dedup: list[tuple[str, str]] = []
        seen: set[str] = set()
        for label, path in paths:
            key = str(Path(path).resolve())
            if key not in seen:
                seen.add(key)
                dedup.append((label, path))
        if not dedup:
            raise RuntimeError("No usable visual anchors are available for the reference board")
        self._board(dedup[:4], output)
        pack.board_path = str(output)
        save_json(self.root / f"{pack.scene_id}.json", pack)
        return str(output)

    def add_approved_anchor(
        self,
        continuity: ContinuityCanon,
        scene_id: str,
        image_path: str,
        role: str = "approved_scene",
    ) -> ContinuityCanon:
        anchor_id = f"approved-{scene_id}"
        continuity.anchors = [a for a in continuity.anchors if a.anchor_id != anchor_id]
        continuity.anchors.append(
            CanonAnchor(
                anchor_id=anchor_id,
                role=role,  # type: ignore[arg-type]
                path=image_path,
                scene_id=scene_id,
                notes="Approved visual canon for downstream continuity.",
            )
        )
        return continuity

    def _fallback(
        self,
        architecture: SceneIllustrationArchitecture,
        bible: ArtDirectionBible,
        continuity: ContinuityCanon,
        previous: str,
    ) -> ReferencePack:
        requirements = [
            ReferenceRequirement(
                reference_id="style-canon",
                purpose="style",
                query="locked Experiment Ledger Editorial Ink visual language from the user-supplied reference board",
                priority=1,
                use_rule="preserve line, palette, paper field, UI and motion grammar; do not copy source content",
                source_preference=["user reference"],
            )
        ]
        for figure in architecture.figure_construction:
            requirements.extend(
                [
                    ReferenceRequirement(
                        reference_id=f"{figure.figure_id}-pose",
                        purpose="pose",
                        query=f"adult human {figure.body_orientation}; {figure.weight_distribution}; {figure.gesture_line}",
                        priority=1,
                    ),
                    ReferenceRequirement(
                        reference_id=f"{figure.figure_id}-anatomy",
                        purpose="anatomy",
                        query=f"adult anatomy and hand construction for {figure.body_orientation}",
                        priority=1,
                    ),
                ]
            )
        for material in architecture.material_marks:
            requirements.append(
                ReferenceRequirement(
                    reference_id=f"material-{self._safe(material.material)}",
                    purpose="material",
                    query=f"observational reference for {material.material}: {', '.join(material.visual_cues)}",
                    priority=2,
                )
            )
        requirements.append(
            ReferenceRequirement(
                reference_id="environment-perspective",
                purpose="environment",
                query=f"environment and perspective reference for {architecture.visual_thesis}",
                priority=2,
            )
        )
        anchors = ["reference-video-board"]
        if previous:
            anchors.append(f"approved-{architecture.scene_id}-previous")
        return ReferencePack(
            scene_id=architecture.scene_id,
            style_anchor_ids=anchors,
            requirements=requirements,
            previous_approved_scene=previous,
        )

    @staticmethod
    def _board(items: list[tuple[str, str]], output: Path) -> None:
        w, h = 1536, 1536
        board = Image.new("RGB", (w, h), "#FAFAF7")
        draw = ImageDraw.Draw(board)
        cells = [(0, 0, w // 2, h // 2), (w // 2, 0, w, h // 2), (0, h // 2, w // 2, h), (w // 2, h // 2, w, h)]
        for i, (label, path) in enumerate(items[:4]):
            x0, y0, x1, y1 = cells[i]
            source = Image.open(path).convert("RGB")
            image = ImageOps.contain(source, (x1 - x0 - 36, y1 - y0 - 70))
            x = x0 + (x1 - x0 - image.width) // 2
            y = y0 + 18
            board.paste(image, (x, y))
            draw.rectangle([x0 + 8, y0 + 8, x1 - 8, y1 - 8], outline="#475157", width=3)
            draw.rectangle([x0 + 8, y1 - 52, x1 - 8, y1 - 8], fill="#FAFAF7", outline="#475157", width=2)
            draw.text((x0 + 22, y1 - 42), label.upper()[:48], fill="#20282D")
        draw.text(
            (24, h - 24), "VISUAL LANGUAGE ONLY — DO NOT COPY CONTENT OR COMPOSITION", fill="#D8483E", anchor="ls"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        board.save(output)

    @staticmethod
    def _safe(value: str) -> str:
        return "-".join("".join(ch.lower() if ch.isalnum() else " " for ch in value).split())

    @staticmethod
    def _unique(items: list[str]) -> list[str]:
        out: list[str] = []
        for item in items:
            if item and item not in out:
                out.append(item)
        return out
