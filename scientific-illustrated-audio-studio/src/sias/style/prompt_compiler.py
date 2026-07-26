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


def compile_prompt(
    scene: SceneSpec,
    bible: StyleBible,
    reference_record: dict[str, Any] | None = None,
    emotional_beat: str = "",
) -> dict[str, Any]:
    refs = reference_record or {"selected": [], "count": 0}
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
    return [clause for clause in REQUIRED_CLAUSES if clause not in lower]
