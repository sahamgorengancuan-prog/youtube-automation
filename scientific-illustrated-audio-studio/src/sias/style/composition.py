"""Composition archetypes — the layout grammar of the identity.

The reference sheet showed a globe, a city under wind, an ocean cross-section.
Those are that episode's *subjects*, not the design. What actually repeats is the
**layout**: one subject alone in white space; two states compared; a chain of
causes; a cutaway; a row of counted things; a before/after.

Naming those six lets an auto-generated episode about vaccines, black holes or
monsoon rain share a visual grammar with one about a stopped Earth — the model
invents the subject, the archetype fixes how it sits on the panel.

Each archetype is used twice: as the composition block of the generation prompt,
and as the shape of the deterministic preview stand-in.
"""

from __future__ import annotations

from ..schemas import SceneSpec

ARCHETYPES: dict[str, str] = {
    "single_subject": (
        "One subject alone, centred, occupying roughly half the frame height, "
        "surrounded by wide empty space. Nothing else competes for attention."
    ),
    "comparison_pair": (
        "Two comparable subjects side by side at the same scale, separated by a "
        "generous gap, so the difference between them is the whole message."
    ),
    "process_flow": (
        "Three stages left to right, each a simple form, joined by two bold "
        "directional arrows. The eye must read cause into effect without labels."
    ),
    "cross_section": (
        "A cutaway seen straight on: horizontal layers stacked in depth with the "
        "subject of interest sitting inside one of them, drawn in the accent colour."
    ),
    "quantity_row": (
        "A row of identical marks representing a count or proportion, some filled "
        "in the accent colour and the rest left as empty outlines."
    ),
    "before_after": (
        "The same subject twice, unchanged on the left and altered on the right, "
        "with a single transformation arrow between them."
    ),
}

# Narrative function → layout. Beat roles generalise across topics; subjects do
# not, which is exactly why the mapping keys on the role.
_BEAT_ARCHETYPE: dict[str, str] = {
    "cold_open": "single_subject",
    "common_guess": "comparison_pair",
    "first_correction": "before_after",
    "explanation": "process_flow",
    "fact_1": "process_flow",
    "fact_2": "cross_section",
    "fact_3": "comparison_pair",
    "scale_example": "quantity_row",
    "escalation": "process_flow",
    "gasp_reveal": "before_after",
    "consequence": "cross_section",
    "payoff": "single_subject",
    "outro": "single_subject",
}

# When a beat role is unknown, rotate rather than repeat: an episode of eight
# identical layouts reads as a template, not as a story.
_ROTATION = ("single_subject", "process_flow", "comparison_pair",
             "cross_section", "quantity_row", "before_after")


def derive_composition(scene: SceneSpec, index: int) -> str:
    """The layout archetype for this scene."""
    if scene.composition_archetype in ARCHETYPES:
        return scene.composition_archetype
    role = _BEAT_ARCHETYPE.get(scene.beat_role)
    if role:
        return role
    return _ROTATION[index % len(_ROTATION)]


def composition_directive(scene: SceneSpec, index: int) -> str:
    archetype = derive_composition(scene, index)
    return f"Layout archetype '{archetype}': {ARCHETYPES[archetype]}"
