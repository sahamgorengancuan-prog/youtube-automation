"""Style lock — how auto-generated illustrations stay consistent.

Nothing about the subject matter is fixed: the model invents whatever the scene
needs. What is fixed is the *drawing language*, and it is held by three
mechanisms that work together:

1. **A style anchor image**, generated once per episode before any scene, that
   shows the contour weight, palette and fill language on neutral shapes and no
   subject at all.
2. **Reference conditioning**: that anchor — plus the previous approved scene —
   is passed to every scene generation, so each panel is drawn *from* the
   established look rather than re-derived from words.
3. **A drift gate**: each finished panel is measured against the anchor, and one
   bounded regeneration is spent on any panel that drifts.

A prompt alone cannot do this. Two calls with the same style paragraph and
different subjects diverge; a shared reference image is what actually holds a
sequence together.
"""

from __future__ import annotations

from pathlib import Path

from ..schemas import ReferenceAsset, StyleBible


def style_anchor_prompt(bible: StyleBible) -> str:
    """The style board: the visual language with no subject to distract it.

    Deliberately abstract — a subject here would leak into every scene that
    references it, which is the failure mode of using scene 1 as the anchor.
    """
    from .institutional import palette_directive

    return (
        f"A style reference sheet for {bible.identity_name}. "
        f"{bible.paper}. {bible.line}. "
        "Arrange six neutral geometric studies in two even rows on the white field: "
        "a rounded square, a circle, a three-step arrow chain, a stack of three "
        "horizontal layers, a row of small tally marks, and a pair of matching shapes. "
        "These are language samples, not illustrations of anything. "
        f"{palette_directive(bible)} "
        "Render NO lettering, numbers, swatch labels, borders or interface chrome of any "
        "kind — this sheet defines line weight, fill language and colour only."
    )


def anchor_assets(anchor_path: str | Path, previous_scene: str | Path | None = None) -> list[ReferenceAsset]:
    """The reference pack every scene generation is conditioned on.

    Ordered by authority: the style board defines the language; the previous
    approved scene carries short-range continuity (spacing, weight, how much
    white space the episode has been using).
    """
    assets = [ReferenceAsset(ref_id="STYLE_ANCHOR", kind="master_style_board",
                             path=str(anchor_path),
                             reason="episode style lock — defines line, palette and fill language")]
    if previous_scene and Path(previous_scene).exists():
        assets.append(ReferenceAsset(ref_id="PREV_SCENE", kind="previous_scene",
                                     path=str(previous_scene),
                                     reason="short-range continuity with the preceding panel"))
    return assets


def drift_repair_directive(flags: list[str]) -> str:
    """Appended to a regeneration prompt when a panel drifts from the anchor."""
    return (
        "\n[STYLE DRIFT] The previous attempt broke the episode's visual language: "
        + "; ".join(flags)
        + ". Match the attached style reference exactly — same contour weight, same closed "
        "palette, same flat fills, same amount of empty white space. Keep the subject and "
        "the composition; change only the drawing language."
    )
