"""SIAS Institutional Lab Notebook — the v5 visual identity.

The look: a clean white simulation-report panel. A hairline frame, a small
outlined status block top-left, a bracketed instrument readout top-right, one
heavy all-caps headline, and a single flat-vector diagram built from a very
small palette — black contour, one blue, three greys, one warning yellow, one
alert red. Night beats invert to a near-black panel.

Two hard rules make this reproducible instead of aspirational:

1. **The image model draws the diagram only.** Every glyph on the panel —
   headline, experiment id, status lines, readout label/value/unit — is drawn
   deterministically by `sias.render.hud`. Diffusion models misspell (the
   reference sheet this identity is derived from reads "STOPPED STOPPED
   SPINNING" and "EXPERIIMENT #034"); we remove that failure class instead of
   reviewing for it.
2. **The palette is closed.** Anything outside PALETTE is style drift and the
   structural reviewer is told to fail it.
"""

from __future__ import annotations

import re

from ..config import SIASConfig
from ..schemas import SceneSpec, StyleBible

IDENTITY_NAME = "SIAS Institutional Lab Notebook"

# Sampled from the approved reference sheet. Closed set — nothing else is legal.
PALETTE: dict[str, str] = {
    "paper": "#FFFFFF",      # panel background
    "ink": "#14161A",        # contour + headline + frame
    "primary": "#3E7EB8",    # the one saturated colour: water, wind, motion
    "primary_soft": "#B9D5EA",  # windows, fills inside blue objects
    "graphite": "#3C4046",   # dark masses: land, silhouettes, night ground
    "steel": "#7C8288",      # mid grey: secondary structures
    "mist": "#E4E6E8",       # light grey: neutral fills, sky blocks
    "night": "#23262B",      # inverted panel background
    "alert": "#C0392B",      # critical readout marker only
    "warn": "#F2C744",       # sun / caution accent only
}

LINE_LANGUAGE = (
    "uniform heavy black contour with rounded caps and joins, roughly 4px at 1080px wide, "
    "a barely-there hand wobble, no tapering, no sketch hatching, no drop shadow, no gradient"
)

PAPER = (
    "pure white report panel, no paper texture, no grid, no vignette, generous empty margins"
)

MOTIFS = [
    "flat vector diagram with one heavy black contour weight",
    "one saturated blue used only for motion, water and air",
    "greyscale masses for everything physical",
    "bold directional arrows with a slight hand wobble",
    "simple symbolic icons in an evenly spaced row when enumerating",
    "cross-section and cutaway views for mechanisms",
    "large empty white space around a single subject",
    "a night panel that inverts to near-black with white contours",
]

FORBIDDEN = [
    "any lettering, numbers, labels, captions or UI chrome inside the illustration",
    "photorealism",
    "3D rendering, bevels, gloss or specular highlights",
    "gradients, soft shadows, glow or blur",
    "watercolour, crayon, sketch hatching or painterly texture",
    "paper grain, notebook grid or coffee stains",
    "colours outside the locked palette",
    "crowded infographic collage",
    "more than one focal subject",
    "duplicated or floating limbs",
    "abstract or surreal visual logic",
    "the visual identity of any named studio or channel",
]

# Panel backgrounds. The illustration is generated ON this background so the
# artwork can cross a day/night split; the frame and glyphs are drawn on top.
BACKGROUNDS = {
    "light": "a pure white background filling the whole frame",
    "night": "a flat near-black #23262B background filling the whole frame, "
             "with the subject drawn in white contour instead of black",
    "split_right": "a vertical split background: pure white on the left, flat near-black #23262B "
                   "on the right; the subject sits across the seam and its night side is drawn "
                   "in white contour",
}

# Reserved bands, as a fraction of panel height, that the illustration must leave
# visually quiet. The HUD is composited into exactly these bands.
RESERVED_TOP = 0.13
RESERVED_BOTTOM = 0.32


def build_institutional_bible(cfg: SIASConfig) -> StyleBible:  # noqa: ARG001 - identity is closed
    """The style bible for this identity.

    The palette is deliberately NOT merged from config: a closed palette is the
    single strongest guarantee that twelve panels look like one report, and the
    structural reviewer fails anything outside it. Changing the identity's
    colours means editing PALETTE, which is a reviewable code change.
    """
    return StyleBible(
        identity_name=IDENTITY_NAME,
        palette=dict(PALETTE),
        line=LINE_LANGUAGE,
        paper=PAPER,
        motifs=list(MOTIFS),
        forbidden=list(FORBIDDEN),
        version="5",
    )


# ---------------------------------------------------------------------------
# Deterministic panel derivations
# ---------------------------------------------------------------------------

# Beat role → (readout label, readout value, alert). These describe the SCENE's
# position in the simulation, never a scientific quantity: the renderer must
# never invent a number. A real figure only ever reaches the readout through
# `SceneSpec.panel_metric`, which upstream fills from an evidence-backed claim.
_BEAT_READOUT: dict[str, tuple[str, str, bool]] = {
    "cold_open": ("EXPERIMENT", "", False),
    "common_guess": ("HYPOTHESIS", "LOGGED", False),
    "first_correction": ("SIMULATION", "RUNNING", False),
    "explanation": ("SIMULATION", "RUNNING", False),
    "fact_1": ("SIMULATION", "RUNNING", False),
    "fact_2": ("SIMULATION", "RUNNING", False),
    "fact_3": ("SIMULATION", "RUNNING", False),
    "scale_example": ("SCALE", "COMPARED", False),
    "escalation": ("STATUS", "CRITICAL", True),
    "gasp_reveal": ("STATUS", "CRITICAL", True),
    "consequence": ("IMPACT", "SEVERE", True),
    "payoff": ("SIMULATION", "COMPLETE", False),
    "outro": ("SIMULATION", "COMPLETE", False),
}

# A night panel is a statement about the SUBJECT (a world with a permanent dark
# side), not about dramatic tension — so it is chosen from what the scene shows,
# never from how exciting the beat is.
_NIGHT_KEYWORDS = ("day and night", "day & night", "night", "dark side", "darkness",
                   "sunlit", "eternal day", "midnight", "starlit")

_STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "at", "to", "and", "or", "but", "with",
    "into", "from", "for", "as", "by", "its", "it", "is", "are", "was", "were",
    "that", "this", "these", "those", "one", "two", "shows", "show", "showing",
    "showed", "scene", "image", "illustration", "panel", "view", "depicts",
    # Spoken filler: real in narration, noise in a headline.
    "then", "now", "so", "also", "just", "almost", "itself", "himself", "herself",
    "they", "them", "we", "you", "your", "our", "there", "here", "very", "really",
    "still", "even", "about", "over", "than", "when", "while", "beat",
}

# The planner emits "cold_open beat for <topic>" as a placeholder objective.
# That is scaffolding, not editorial copy, and must never reach a headline.
_SCAFFOLD = re.compile(r"^\w+ beat for ", re.IGNORECASE)


def _trim_conjunctions(words: list[str], truncated: bool = False) -> list[str]:
    """An "&" only earns its place between two substantial halves.

    Filler removal strands it at an edge ("SPINNING &") and truncation leaves it
    heading a fragment ("... WORK & DOESN'T"). Both read as mistakes, so an
    ampersand in the last two slots takes the tail with it.
    """
    while words and words[0] == "&":
        words = words[1:]
    while words and words[-1] == "&":
        words = words[:-1]
    # Only a truncated list can strand a conjunction mid-clause. "STATIC DAY &
    # NIGHT" is complete and must survive; "... WORK & DOESN'T" is a cut clause.
    if truncated and "&" in words[-2:]:
        words = words[:len(words) - 2 + words[-2:].index("&")]
    return words


def _balance(words: list[str], max_lines: int) -> list[str]:
    """Break words into near-equal lines — a ragged headline reads as an
    accident, and the reference identity is built on tight even blocks."""
    if len(words) <= 2 or max_lines == 1:
        return [" ".join(words)]
    lines = min(max_lines, max(2, round(len(words) / 2.4)))
    per = -(-len(words) // lines)  # ceil
    return [" ".join(words[i:i + per]) for i in range(0, len(words), per)]


def derive_headline(scene: SceneSpec, display: bool = False) -> list[str]:
    """The all-caps headline for the panel.

    Prefers an explicit `panel_title`, then the scientific labels, then the
    strongest nouns of the visual objective. A title card may run to three
    lines; an in-panel label stays at two. It never echoes the whole narration —
    that belongs in the subtitle track, not burnt into the artwork.
    """
    explicit = bool(scene.panel_title.strip())
    objective = "" if _SCAFFOLD.match(scene.visual_objective) else scene.visual_objective
    if explicit:
        source = scene.panel_title
    elif scene.scientific_labels:
        source = " ".join(scene.scientific_labels[:2])
    else:
        # Narration before objective: the spoken sentence is the editorial
        # content, the objective is often only a planning placeholder.
        source = scene.narration or objective
    keep_stops = explicit and display
    # "and" survives as "&": "STATIC / DAY & NIGHT" is the identity's phrasing,
    # while "DAY NIGHT" reads like a dropped word.
    source = re.sub(r"\band\b", "&", source, flags=re.IGNORECASE)
    # Apostrophes stay inside the word: splitting on them turns "isn't" into
    # "ISN" + "T", which is how a headline ends up reading "REAL SURPRISE ISN T".
    words = [w for w in re.findall(r"[A-Za-z0-9%°/&+-]+(?:['\u2019][A-Za-z]+)*", source)
             if len(w) > 1 or w in ("&", "%")]
    words = [w for w in words if keep_stops or w.lower() not in _STOPWORDS]
    words = _trim_conjunctions(words)
    max_words = 8 if display else 4
    truncated = len(words) > max_words
    words = _trim_conjunctions([w.upper() for w in words[:max_words]], truncated)
    if not words:
        return []
    return _balance(words, 3 if display else 2)


def derive_readout(scene: SceneSpec) -> dict[str, str | bool]:
    """Instrument readout for the panel. A numeric value appears only when the
    scene actually carries one — the renderer never fabricates a measurement."""
    metric = scene.panel_metric or {}
    if metric.get("value"):
        return {
            "label": str(metric.get("label", "MEASURED")).upper()[:18],
            "value": str(metric["value"])[:10],
            "unit": str(metric.get("unit", ""))[:8],
            "alert": bool(metric.get("alert", False)),
        }
    label, value, alert = _BEAT_READOUT.get(scene.beat_role, ("SIMULATION", "RUNNING", False))
    return {"label": label, "value": value, "unit": "", "alert": alert}


def derive_background(scene: SceneSpec) -> str:
    if scene.panel_background in BACKGROUNDS:
        return scene.panel_background
    haystack = " ".join([scene.panel_title, scene.visual_objective, scene.narration]).lower()
    return "split_right" if any(k in haystack for k in _NIGHT_KEYWORDS) else "light"


def derive_headline_anchor(scene: SceneSpec, index: int) -> str:
    if index == 0 or scene.beat_role == "cold_open":
        return "top_center"
    if scene.beat_role in ("payoff", "outro", "scale_example"):
        return "bottom_center"
    return "bottom_left"


def derive_schematic(scene: SceneSpec, index: int) -> str:
    """Which layout archetype the preview stand-in should draw.

    Delegates to the composition grammar: a preview must show the same layout the
    live prompt asks for, and layouts generalise across topics where subjects
    (globe, city, ocean) do not.
    """
    from .composition import derive_composition

    return derive_composition(scene, index)


# ---------------------------------------------------------------------------
# Prompt blocks
# ---------------------------------------------------------------------------

def illustration_directive(scene: SceneSpec, background: str) -> str:
    """The instruction that keeps the model in its lane: draw the diagram, leave
    the report furniture to the compositor."""
    return (
        f"Draw ONLY the flat vector diagram on {BACKGROUNDS.get(background, BACKGROUNDS['light'])}. "
        f"Render absolutely no lettering, numbers, labels, captions, arrows-with-text, frames, "
        f"brackets, panel borders or interface chrome of any kind — every word and every number on "
        f"this panel is typeset separately and any text you draw is a defect. "
        f"Keep the top {int(RESERVED_TOP * 100)}% and the bottom {int(RESERVED_BOTTOM * 100)}% of "
        f"the frame visually quiet: those bands are reserved for the report header and headline. "
        f"Centre one subject — {scene.visual_objective or scene.narration} — in the remaining band, "
        f"large, with wide empty margins."
    )


def palette_directive(bible: StyleBible) -> str:
    roles = bible.palette or PALETTE
    named = ", ".join(f"{role} {hexv}" for role, hexv in roles.items())
    return (
        f"Closed palette, no other colours: {named}. "
        "Flat fills only — one contour weight, no gradient, no shadow, no texture, no gloss."
    )
