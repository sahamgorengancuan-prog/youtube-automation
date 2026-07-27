"""Prompt compiler — prompts are compiled from structured scene data, not
stored as free-form text. Both compiled_prompt.txt and compiled_prompt.json are
persisted; the JSON preserves the ten blocks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..filesystem import atomic_write_bytes, atomic_write_json, sha256_text
from ..schemas import SceneSpec, StyleBible

BLOCK_ORDER = [
    "identity_lock",
    "reference_interpretation",
    "single_visual_objective",
    "composition",
    "character_action",
    "props",
    "scientific_mechanism",
    "emotional_beat",
    "preserve_contract",
    "hard_negatives",
]

REQUIRED_CLAUSES = [
    "complete readable figures",
    "stable recurring identity",
    "one focal idea",
    "safe caption zone",
    "notebook-page identity",
    "short labels only",
    "no generated paragraphs",
    "no abstract or surreal visual logic",
    "no body-part duplication",
    "no floating limbs",
]


INSTITUTIONAL_BLOCK_ORDER = [
    "identity_lock",
    "reference_interpretation",
    "single_visual_objective",
    "composition",
    "palette_lock",
    "scientific_mechanism",
    "continuity",
    "no_typography",
    "hard_negatives",
]

INSTITUTIONAL_REQUIRED_CLAUSES = [
    "draw only the flat vector diagram",
    "closed palette",
    "one focal idea",
    "reserved for the report header",
    "any text you draw is a defect",
]


def _compile_institutional(scene: SceneSpec, bible: StyleBible, refs: dict[str, Any],
                           index: int, emotional_beat: str) -> dict[str, str]:
    """Prompt for the Institutional Lab Notebook identity.

    The model is asked for the diagram and nothing else — no headline, no
    readout, no frame. Those are composited by `sias.render.hud`, which is why
    this identity can hold a consistent look across twelve panels.
    """
    from .institutional import (
        derive_background,
        derive_headline_anchor,
        illustration_directive,
        palette_directive,
    )

    background = derive_background(scene)
    anchor = derive_headline_anchor(scene, index)
    return {
        "identity_lock": (
            f"{bible.identity_name}: {bible.paper}; {bible.line}. "
            "Flat editorial vector diagram, one focal idea, stable recurring identity."
        ),
        "reference_interpretation": (
            f"Follow the {refs['count']} attached references in priority order "
            "(style board > character sheet > prop sheet > environment); "
            "references define the drawing language, not the composition."
        ),
        "single_visual_objective": illustration_directive(scene, background),
        "composition": (
            f"{scene.composition or 'one subject, centred, wide empty margins'}. "
            + ("The headline is typeset across the upper third, so keep the subject in the "
               "lower two-thirds. " if anchor == "top_center" else
               "The headline is typeset across the lower third, so keep the subject above it. ")
            + "One focal idea; complete readable figures; nothing cropped at the frame edge."
        ),
        "palette_lock": palette_directive(bible),
        "scientific_mechanism": (
            "Show the mechanism through shape and arrow direction alone: "
            + (", ".join(scene.scientific_labels) if scene.scientific_labels
               else "a single clear cause-and-effect reading")
            + ". Any naming is done by the typeset headline, never inside the drawing."
        ),
        "continuity": (
            "Continuity: " + (", ".join(scene.continuity_refs) if scene.continuity_refs
                              else "same drawing language, weight and palette as the adjacent panels")
            + f". Emotional beat: {emotional_beat or scene.beat_role}."
        ),
        "no_typography": (
            "Render NO typography of any kind: no letters, digits, units, tick labels, legends, "
            "logos, watermarks, speech bubbles with text, panel borders, corner brackets or HUD "
            "chrome. Every glyph on the finished panel is typeset by the compositor, so any text "
            "you draw is a defect that fails review."
        ),
        "hard_negatives": "Forbidden: " + "; ".join(bible.forbidden) + ".",
    }


def compile_prompt(
    scene: SceneSpec,
    bible: StyleBible,
    reference_record: dict[str, Any] | None = None,
    emotional_beat: str = "",
    index: int = 0,
) -> dict[str, Any]:
    from .institutional import IDENTITY_NAME

    refs = reference_record or {"selected": [], "count": 0}
    if bible.identity_name == IDENTITY_NAME:
        blocks = _compile_institutional(scene, bible, refs, index, emotional_beat)
        text = "\n\n".join(f"[{k.upper()}]\n{blocks[k]}" for k in INSTITUTIONAL_BLOCK_ORDER)
        return {"scene_id": scene.scene_id, "blocks": blocks, "text": text,
                "identity": IDENTITY_NAME, "prompt_hash": sha256_text(text)}
    palette = ", ".join(f"{role}={hexv}" for role, hexv in bible.palette.items())
    blocks: dict[str, str] = {
        "identity_lock": (
            f"{bible.identity_name}: {bible.paper}; {bible.line}; palette roles: {palette}; "
            "notebook-page identity; stable recurring identity."
        ),
        "reference_interpretation": (
            f"Follow the {refs['count']} attached references in priority order "
            "(style board > character sheet > prop sheet > environment > continuity); "
            "references define identity, not composition."
        ),
        "single_visual_objective": (
            f"One focal idea only: {scene.visual_objective or scene.narration}. "
            "One hero idea per scene; no dense infographic poster."
        ),
        "composition": (
            f"{scene.composition or 'clear silhouette, generous margins'}; "
            "safe caption zone at the lower quarter; complete readable figures."
        ),
        "character_action": (
            "Characters: " + (", ".join(scene.characters) if scene.characters else "none") + ". "
            "Readable human poses; one head, one torso, two arms, two legs; logically connected joints."
        ),
        "props": "Props: " + (", ".join(scene.props) if scene.props else "minimal notebook props") + ".",
        "scientific_mechanism": (
            "Scientific labels (short noun phrases, short labels only): "
            + (", ".join(scene.scientific_labels) if scene.scientific_labels else "none")
            + ". No generated paragraphs."
        ),
        "emotional_beat": f"Emotional beat: {emotional_beat or scene.beat_role}."
        + (f" Visual joke: {scene.visual_joke}." if scene.visual_joke else ""),
        "preserve_contract": (
            "Preserve: character identity, correct anatomy, palette roles, paper texture, "
            "stable recurring identity across scenes."
        ),
        "hard_negatives": (
            "Forbidden: " + "; ".join(bible.forbidden) + "; "
            "no abstract or surreal visual logic; no body-part duplication; no floating limbs; "
            "no large hallucinated text blocks."
        ),
    }
    text = "\n\n".join(f"[{k.upper()}]\n{blocks[k]}" for k in BLOCK_ORDER)
    return {
        "scene_id": scene.scene_id,
        "blocks": blocks,
        "text": text,
        "prompt_hash": sha256_text(text),
    }


def persist_prompt(compiled: dict[str, Any], out_dir: str | Path) -> tuple[Path, Path]:
    out = Path(out_dir)
    txt = atomic_write_bytes(out / "compiled_prompt.txt", compiled["text"].encode("utf-8"))
    js = atomic_write_json(out / "compiled_prompt.json", compiled)
    return txt, js


def missing_required_clauses(compiled_text: str) -> list[str]:
    lower = compiled_text.lower()
    from .institutional import IDENTITY_NAME

    required = (INSTITUTIONAL_REQUIRED_CLAUSES
                if IDENTITY_NAME.lower() in lower else REQUIRED_CLAUSES)
    return [clause for clause in required if clause not in lower]
