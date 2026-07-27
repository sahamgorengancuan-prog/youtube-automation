"""Deterministic layout stand-ins for the keyless preview path.

These draw the *composition archetype*, not a subject. A preview panel for a
vaccine episode and one for a monsoon episode both show "two comparable forms
side by side" or "three stages joined by arrows" — abstract, obviously not
finished art, and honest about what a keyless run actually knows.

That is deliberate. Earlier versions drew a globe, a city and an ocean, which
were the reference episode's subjects; they made every other topic look wrong
and implied art direction the live path does not follow. The live path generates
its own subject inside the same archetype.

Everything here uses the identity's closed palette and contour weight, so what
you approve in PREVIEW — composition, balance, safe zones, headline fit — is
what LIVE reproduces around real artwork.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from ..style.composition import ARCHETYPES
from ..style.institutional import PALETTE, RESERVED_BOTTOM, RESERVED_TOP

SCHEMATICS = tuple(ARCHETYPES)


def _rgb(name: str) -> tuple[int, int, int]:
    h = PALETTE[name].lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def content_box(width: int, height: int, headline_anchor: str = "bottom_left") -> tuple[int, int, int, int]:
    """The band the illustration owns.

    The headline and the subject must not fight for the same rows, so a top
    headline pushes the artwork down and a bottom headline lifts it up. The
    generated-image prompt quotes these same fractions.
    """
    top = int(height * RESERVED_TOP)
    bottom = int(height * (1.0 - RESERVED_BOTTOM))
    if headline_anchor == "top_center":
        top = int(height * 0.34)
        bottom = int(height * 0.98)
    side = int(width * 0.16)
    return side, top, width - side, bottom


# --- primitives -------------------------------------------------------------

def _blob(draw, box, stroke, ink, fill, accent=None) -> None:
    """A neutral rounded mass with an inner accent — a subject-shaped placeholder
    that deliberately depicts nothing in particular."""
    x0, y0, x1, y1 = box
    r = min(x1 - x0, y1 - y0) * 0.22
    draw.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=fill, outline=ink, width=stroke)
    if accent is not None:
        w, h = x1 - x0, y1 - y0
        draw.ellipse([x0 + w * 0.28, y0 + h * 0.30, x0 + w * 0.72, y0 + h * 0.70],
                     fill=accent, outline=ink, width=stroke)


def _arrow(draw, x0, y, x1, stroke, colour) -> None:
    head = stroke * 3
    draw.line([x0, y, x1 - head, y], fill=colour, width=int(stroke * 1.8))
    draw.polygon([(x1, y), (x1 - head, y - head), (x1 - head, y + head)], fill=colour)


def _centre_band(box, frac: float = 0.62) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    h = (y1 - y0) * frac
    cy = (y0 + y1) / 2
    return int(x0), int(cy - h / 2), int(x1), int(cy + h / 2)


# --- archetypes -------------------------------------------------------------

def _single_subject(draw, box, stroke, ink) -> None:
    x0, y0, x1, y1 = box
    side = min(x1 - x0, y1 - y0) * 0.92
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    _blob(draw, (cx - side / 2, cy - side / 2, cx + side / 2, cy + side / 2),
          stroke, ink, _rgb("mist"), _rgb("primary"))


def _comparison_pair(draw, box, stroke, ink) -> None:
    x0, y0, x1, y1 = _centre_band(box, 0.8)
    w = (x1 - x0) * 0.36
    for i, accent in enumerate((_rgb("mist"), _rgb("primary"))):
        left = x0 + i * ((x1 - x0) - w)
        _blob(draw, (left, y0, left + w, y1), stroke, ink, _rgb("paper"), accent)


def _process_flow(draw, box, stroke, ink) -> None:
    x0, y0, x1, y1 = _centre_band(box, 0.58)
    w = (x1 - x0) * 0.24
    gap = ((x1 - x0) - 3 * w) / 2
    cy = (y0 + y1) / 2
    for i in range(3):
        left = x0 + i * (w + gap)
        _blob(draw, (left, y0, left + w, y1), stroke, ink, _rgb("mist"),
              _rgb("primary") if i == 2 else None)
        if i < 2:
            _arrow(draw, left + w + gap * 0.18, cy, left + w + gap * 0.82, stroke, _rgb("primary"))


def _cross_section(draw, box, stroke, ink) -> None:
    x0, y0, x1, y1 = box
    layers = ((_rgb("mist"), 0.26), (_rgb("steel"), 0.22), (_rgb("graphite"), 0.32))
    y = y0
    for fill, frac in layers:
        h = (y1 - y0) * frac
        draw.rectangle([x0, y, x1, y + h], fill=fill, outline=ink, width=stroke)
        y += h
    cx = (x0 + x1) / 2
    r = min(x1 - x0, y1 - y0) * 0.13
    inner_y = y0 + (y1 - y0) * 0.48
    draw.ellipse([cx - r, inner_y - r, cx + r, inner_y + r],
                 fill=_rgb("primary"), outline=ink, width=stroke)


def _quantity_row(draw, box, stroke, ink) -> None:
    x0, y0, x1, y1 = _centre_band(box, 0.34)
    count, filled = 8, 3
    gap = (x1 - x0) * 0.02
    w = ((x1 - x0) - gap * (count - 1)) / count
    for i in range(count):
        left = x0 + i * (w + gap)
        draw.rectangle([left, y0, left + w, y1],
                       fill=_rgb("primary") if i < filled else _rgb("paper"),
                       outline=ink, width=stroke)


def _before_after(draw, box, stroke, ink) -> None:
    x0, y0, x1, y1 = _centre_band(box, 0.72)
    w = (x1 - x0) * 0.32
    cy = (y0 + y1) / 2
    _blob(draw, (x0, y0, x0 + w, y1), stroke, ink, _rgb("mist"))
    right = x1 - w
    _blob(draw, (right, y0, x1, y1), stroke, ink, _rgb("mist"), _rgb("alert"))
    _arrow(draw, x0 + w + (x1 - x0) * 0.06, cy, right - (x1 - x0) * 0.06, stroke, _rgb("primary"))


_DRAWERS = {
    "single_subject": _single_subject,
    "comparison_pair": _comparison_pair,
    "process_flow": _process_flow,
    "cross_section": _cross_section,
    "quantity_row": _quantity_row,
    "before_after": _before_after,
}


def draw_schematic(out_path: str | Path, width: int, height: int, kind: str = "single_subject",
                   background: str = "light", headline_anchor: str = "bottom_left",
                   split_at: float = 0.56) -> Path:
    """Render one composition archetype on the panel background."""
    drawer = _DRAWERS.get(kind, _single_subject)
    paper = _rgb("night") if background == "night" else _rgb("paper")
    canvas = Image.new("RGB", (width, height), paper)
    draw = ImageDraw.Draw(canvas)
    if background == "split_right":
        draw.rectangle([int(width * split_at), 0, width, height], fill=_rgb("night"))

    ink = _rgb("paper") if background == "night" else _rgb("ink")
    stroke = max(2, round(min(width, height) * 0.0045))
    drawer(draw, content_box(width, height, headline_anchor), stroke, ink)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return out
