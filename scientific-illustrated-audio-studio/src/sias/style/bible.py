"""Style bible for the Scientific Notebook Cartoon identity. Palette roles are
configurable, never hard-coded across modules."""

from __future__ import annotations

from pathlib import Path

from ..config import SIASConfig
from ..filesystem import atomic_write_json, load_json
from ..schemas import StyleBible

MOTIFS = [
    "faint square grid",
    "margin hypothesis notes",
    "orange circle around surprising details",
    "tiny scale ruler",
    "causal arrows",
    "page-turn corner motif",
    "small hand-drawn annotations",
    "light visual humor",
]

FORBIDDEN = [
    "abstract art",
    "surrealism",
    "psychedelic visual logic",
    "body deformation",
    "missing or duplicated limbs",
    "disconnected body parts",
    "photorealism",
    "glossy 3D mascot rendering",
    "sticker collage",
    "generic corporate vector art",
    "crowded infographic layout",
    "large hallucinated text blocks",
    "no-limbs character design",
    "empty decorative backgrounds",
]


def build_style_bible(cfg: SIASConfig) -> StyleBible:
    # One dispatch point: every caller (orchestrator, agent registry, notebook)
    # gets the institutional identity when config selects it.
    from .institutional import IDENTITY_NAME, build_institutional_bible

    if cfg.visual.identity_name == IDENTITY_NAME:
        return build_institutional_bible(cfg)
    return StyleBible(
        identity_name=cfg.visual.identity_name,
        palette=dict(cfg.visual.palette),
        motifs=list(MOTIFS),
        forbidden=list(FORBIDDEN),
    )


def save_style_bible(bible: StyleBible, path: str | Path) -> Path:
    return atomic_write_json(path, bible.model_dump())


def load_style_bible(path: str | Path) -> StyleBible | None:
    data = load_json(path)
    return StyleBible.model_validate(data) if data else None
