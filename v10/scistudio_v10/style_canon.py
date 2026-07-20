from __future__ import annotations

from .schemas import ArtDirectionBible, HardCodedStyleCanon
from .utils import hash_value


REFERENCE_OBSERVATIONS = {
    "source": "user-supplied scientific experiment motion reference",
    "visual_grammar": [
        "off-white instrument-paper field",
        "charcoal and blue-gray authored illustration",
        "condensed uppercase experiment typography",
        "small measurement/status panels at the edges",
        "one large causal phenomenon per shot",
        "clear phase changes rather than decorative entrances",
        "restrained red warning and yellow energy accents",
    ],
    "palette_observed": {
        "paper": "#FAFAFA",
        "ink": "#20282D",
        "dark_slate": "#474F54",
        "mist": "#EBEFF0",
        "steel": "#A8BAC2",
        "force_blue": "#2E6C96",
        "water_blue": "#3F90C5",
        "warning_red": "#D8483E",
        "energy_yellow": "#F3BD38",
    },
    "motion_grammar": [
        "locked or nearly locked camera",
        "local causal movement",
        "state change within a persistent visual world",
        "simple but strong directional arrows only when explanatory",
        "minimal transitions between causal stages",
    ],
}


def build_hard_coded_canon() -> HardCodedStyleCanon:
    canon = HardCodedStyleCanon()
    canon.style_hash = hash_value(canon.model_dump(exclude={"style_hash"}), length=20)
    return canon


def base_bible(topic: str, reference_board_path: str = "") -> ArtDirectionBible:
    canon = build_hard_coded_canon()
    return ArtDirectionBible(
        topic=topic,
        locked_canon=canon,
        visual_thesis=(
            "Explain the causal chain as a clean flat vector science-explainer world (Kurzgesagt-style): bold "
            "separable shapes with flat colour fills, one clear focal subject per shot, generous negative space "
            "for labels, restrained experiment UI, and no painterly texture or icon collage."
        ),
        topic_specific_motifs=[],
        recurring_symbols=[
            "experiment identifier",
            "simulation status",
            "single measured variable",
            "causal stage label",
        ],
        scientific_readability_rules=[
            "Every visual exaggeration must clarify a real causal relationship.",
            "Labels attach to physical subjects rather than floating as presentation bullets.",
            "Magnitude, direction and sequence must be encoded consistently.",
            "The audience must identify the physical system before reading any label.",
        ],
        anti_ai_rules=[
            "Avoid perfectly mirrored silhouettes and uniformly polished contours.",
            "Interior marks must describe anatomy, material, force or depth—not random decoration.",
            "Do not invent tiny nonsensical details or pseudo-text.",
            "Do not change line pressure, palette logic or facial construction between scenes.",
            "Use one integrated composition, not isolated assets pasted together.",
        ],
        continuity_priorities=[
            "line language",
            "palette",
            "paper field",
            "UI geometry",
            "perspective language",
            "recurring subject construction",
            "environment material vocabulary",
        ],
        reference_board_path=reference_board_path,
    )
