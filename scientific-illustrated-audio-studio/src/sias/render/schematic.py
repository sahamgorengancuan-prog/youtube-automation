"""Deterministic flat-vector schematics in the Institutional Lab Notebook style.

These stand in for the generated illustration on the keyless preview path. They
are drawn from the same closed palette and the same contour weight as the real
artwork, so a PREVIEW panel shows the true composition, headline fit and safe
zones — what you approve is what the live run reproduces, minus the artwork.

They are NOT art direction for the live path and never ship as production
assets: `sias_colab.qc.placeholders` stamps every preview panel with a visible
watermark and a PNG tEXt marker that production QC rejects.
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

from ..style.institutional import PALETTE, RESERVED_BOTTOM, RESERVED_TOP


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


def _globe(draw: ImageDraw.ImageDraw, box, stroke: int, ink, land, sea) -> None:
    x0, y0, x1, y1 = box
    r = min(x1 - x0, y1 - y0) // 2
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=sea, outline=ink, width=stroke)
    # Two blocky landmasses — symbolic, not cartographic.
    draw.polygon(
        [(cx - r * 0.62, cy - r * 0.30), (cx - r * 0.12, cy - r * 0.55),
         (cx + r * 0.10, cy - r * 0.18), (cx - r * 0.20, cy + r * 0.16),
         (cx - r * 0.58, cy + r * 0.10)],
        fill=land,
    )
    draw.polygon(
        [(cx + r * 0.18, cy + r * 0.06), (cx + r * 0.60, cy - r * 0.06),
         (cx + r * 0.52, cy + r * 0.52), (cx + r * 0.16, cy + r * 0.44)],
        fill=land,
    )
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=ink, width=stroke)


def _arc_arrow(draw: ImageDraw.ImageDraw, box, stroke: int, blue) -> None:
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    rx, ry = int((x1 - x0) * 0.60), int((y1 - y0) * 0.26)
    draw.arc([cx - rx, cy - ry, cx + rx, cy + ry], start=200, end=430,
             fill=blue, width=int(stroke * 2.2))
    head = int(stroke * 3.4)
    hx, hy = cx + int(rx * 0.86), cy + int(ry * 0.42)
    draw.polygon([(hx + head, hy), (hx - head // 2, hy - head), (hx - head // 2, hy + head)], fill=blue)


def _wind(draw: ImageDraw.ImageDraw, box, stroke: int, blue) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    for i, frac in enumerate((0.28, 0.48, 0.68)):
        y = y0 + int(h * frac)
        length = int(w * (0.52 + 0.14 * i))
        draw.line([x0 + int(w * 0.05), y, x0 + int(w * 0.05) + length, y],
                  fill=blue, width=int(stroke * 2.0))
        head = int(stroke * 3.0)
        tip = x0 + int(w * 0.05) + length
        draw.polygon([(tip + head, y), (tip, y - head), (tip, y + head)], fill=blue)


def _skyline(draw: ImageDraw.ImageDraw, box, stroke: int, ink, fill, glass) -> None:
    x0, y0, x1, y1 = box
    base = int(y1 - (y1 - y0) * 0.06)
    draw.line([x0, base, x1, base], fill=ink, width=stroke)  # buildings need ground
    widths = (0.13, 0.10, 0.16, 0.11)
    heights = (0.52, 0.78, 0.40, 0.64)
    x = x0 + int((x1 - x0) * 0.08)
    for bw, bh in zip(widths, heights):
        w = int((x1 - x0) * bw * 1.5)
        h = int((y1 - y0) * bh)
        draw.rectangle([x, base - h, x + w, base], fill=fill, outline=ink, width=stroke)
        for row in range(2, max(3, h // max(1, int(h * 0.22)))):
            wy = base - h + int(h * 0.16) * row
            if wy > base - int(h * 0.12):
                break
            draw.rectangle([x + int(w * 0.22), wy, x + int(w * 0.44), wy + int(h * 0.07)], fill=glass)
            draw.rectangle([x + int(w * 0.58), wy, x + int(w * 0.80), wy + int(h * 0.07)], fill=glass)
        x += w + int((x1 - x0) * 0.06)


def _water_terrain(draw: ImageDraw.ImageDraw, box, stroke: int, ink, blue, ground) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    draw.polygon([(x0, y1), (x0, y0 + int(h * 0.55)), (x0 + int(w * 0.30), y0 + int(h * 0.18)),
                  (x0 + int(w * 0.58), y0 + int(h * 0.50)), (x1, y0 + int(h * 0.38)), (x1, y1)],
                 fill=blue, outline=ink)
    draw.polygon([(x0, y1), (x0 + int(w * 0.22), y0 + int(h * 0.74)),
                  (x0 + int(w * 0.62), y0 + int(h * 0.86)), (x1, y0 + int(h * 0.70)), (x1, y1)],
                 fill=ground)
    draw.line([x0, y1 - stroke, x1, y1 - stroke], fill=ink, width=stroke)


def _icon_tree(draw, cx, cy, s, stroke, ink, fill) -> None:
    draw.ellipse([cx - s, cy - s, cx + s, cy + s * 0.15], fill=fill, outline=ink, width=stroke)
    draw.line([cx, cy + s, cx, cy], fill=ink, width=stroke)  # trunk drawn over the canopy


def _icon_building(draw, cx, cy, s, stroke, ink, fill, glass) -> None:
    draw.rectangle([cx - s * 0.7, cy - s, cx + s * 0.7, cy + s], fill=fill, outline=ink, width=stroke)
    step = s * 0.5
    for row in range(3):
        wy = cy - s + step * (row + 0.45)
        draw.rectangle([cx - s * 0.42, wy, cx - s * 0.10, wy + step * 0.42], fill=glass)
        draw.rectangle([cx + s * 0.10, wy, cx + s * 0.42, wy + step * 0.42], fill=glass)


def _icon_dead_tree(draw, cx, cy, s, stroke, ink) -> None:
    draw.line([cx, cy + s, cx, cy - s], fill=ink, width=stroke)
    for dx, dy in ((-s * 0.8, -s * 0.9), (s * 0.8, -s * 0.75), (-s * 0.6, -s * 0.2)):
        draw.line([cx, cy + dy + s * 0.35, cx + dx, cy + dy], fill=ink, width=stroke)


def _icon_ground(draw, cx, cy, s, stroke, ink, fill) -> None:
    """Dry cracked ground: a low mound split by two fissures."""
    draw.polygon([(cx - s, cy + s * 0.7), (cx - s * 0.55, cy - s * 0.35),
                  (cx + s * 0.10, cy - s * 0.05), (cx + s * 0.62, cy - s * 0.5),
                  (cx + s, cy + s * 0.7)], fill=fill, outline=ink)
    draw.line([cx - s, cy + s * 0.7, cx + s, cy + s * 0.7], fill=ink, width=stroke)
    for dx in (-s * 0.40, s * 0.30):
        draw.line([cx + dx, cy + s * 0.7, cx + dx + s * 0.16, cy + s * 0.05],
                  fill=ink, width=stroke)


def _icon_grid(draw: ImageDraw.ImageDraw, box, stroke: int, ink, fill, glass) -> None:
    """Eight symbols in an even 4×2 grid — the reference's "collapse" panel."""
    x0, y0, x1, y1 = box
    cols, rows = 4, 2
    cw = (x1 - x0) / cols
    ch = (y1 - y0) / rows
    s = min(cw, ch) * 0.30
    order = (_icon_tree, _icon_ground, _icon_building, _icon_dead_tree,
             _icon_dead_tree, _icon_ground, _icon_building, _icon_tree)
    for i, drawer in enumerate(order):
        cx = x0 + (i % cols) * cw + cw / 2
        cy = y0 + (i // cols) * ch + ch / 2
        if drawer is _icon_building:
            drawer(draw, cx, cy, s, stroke, ink, fill, glass)
        elif drawer is _icon_dead_tree:
            drawer(draw, cx, cy, s, stroke, ink)
        else:
            drawer(draw, cx, cy, s, stroke, ink, fill)


SCHEMATICS = ("globe_rotation", "globe", "wind_city", "water_terrain", "icon_grid")


def draw_schematic(out_path: str | Path, width: int, height: int, kind: str = "globe",
                   background: str = "light", headline_anchor: str = "bottom_left",
                   split_at: float = 0.56) -> Path:
    """Render one flat-vector schematic on the panel background."""
    if kind not in SCHEMATICS:
        kind = "globe"
    paper = _rgb("night") if background == "night" else _rgb("paper")
    canvas = Image.new("RGB", (width, height), paper)
    draw = ImageDraw.Draw(canvas)
    if background == "split_right":
        draw.rectangle([int(width * split_at), 0, width, height], fill=_rgb("night"))

    ink = _rgb("paper") if background == "night" else _rgb("ink")
    stroke = max(2, round(min(width, height) * 0.0045))
    box = content_box(width, height, headline_anchor)

    if kind == "globe_rotation":
        _globe(draw, box, stroke, ink, _rgb("graphite"), _rgb("mist"))
        _arc_arrow(draw, box, stroke, _rgb("primary"))
    elif kind == "globe":
        _globe(draw, box, stroke, ink, _rgb("graphite"), _rgb("mist"))
    elif kind == "wind_city":
        x0, y0, x1, y1 = box
        _wind(draw, (x0, y0, x0 + int((x1 - x0) * 0.60), y1), stroke, _rgb("primary"))
        _skyline(draw, (x0 + int((x1 - x0) * 0.54), y0, x1, y1), stroke,
                 ink, _rgb("paper"), _rgb("primary_soft"))
    elif kind == "water_terrain":
        _water_terrain(draw, box, stroke, ink, _rgb("primary"), _rgb("graphite"))
    else:
        _icon_grid(draw, box, stroke, ink, _rgb("mist"), _rgb("primary_soft"))

    if background == "split_right":  # sun / moon markers either side of the seam
        seam = int(width * split_at)
        r = int(min(width, height) * 0.045)
        sy = box[1] + int((box[3] - box[1]) * 0.22)
        sx = int(seam * 0.16)
        draw.ellipse([sx - r, sy - r, sx + r, sy + r], fill=_rgb("warn"),
                     outline=_rgb("ink"), width=stroke)
        for i in range(8):
            a = math.pi * i / 4
            draw.line([sx + int(math.cos(a) * r * 1.30), sy + int(math.sin(a) * r * 1.30),
                       sx + int(math.cos(a) * r * 1.80), sy + int(math.sin(a) * r * 1.80)],
                      fill=_rgb("ink"), width=stroke)
        mx = seam + int((width - seam) * 0.78)
        draw.chord([mx - r, sy - r, mx + r, sy + r], start=110, end=290, fill=_rgb("primary"))

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return out
