"""Deterministic panel compositor for the Institutional Lab Notebook identity.

Everything that is *typed* on a SIAS panel is drawn here with PIL, never by the
image model: the hairline frame, the top-left status block, the bracketed
instrument readout, and the all-caps headline. That is the whole point — a
diffusion model cannot be trusted to spell "EXPERIMENT #024" the same way
twelve times in a row, and text hallucination is already a hard-fail code.

Layout is expressed in fractions of the panel, so the same spec composes a
1080×1920 short and a 1920×1080 report panel without a second set of numbers.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field, field_validator

from ..exceptions import RenderError

BACKGROUND_MODES = ("light", "night", "split_right")
HEADLINE_ANCHORS = ("top_center", "bottom_center", "bottom_left")
HEADLINE_STYLES = ("display", "label")

# Helvetica-metric grotesque first (what the reference identity uses), then the
# fonts every Colab image ships with. Resolution is checked at render time.
_BOLD_CANDIDATES = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
]
_REGULAR_CANDIDATES = [
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
]


def _font_path(candidates: list[str]) -> str:
    for path in candidates:
        if Path(path).exists():
            return path
    raise RenderError(
        "no usable sans-serif font found for the panel HUD "
        f"(looked for {candidates}); install fonts-liberation or fonts-dejavu",
        stage="render.hud",
    )


def _font(candidates: list[str], size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_font_path(candidates), max(6, int(size)))


class HudReadout(BaseModel):
    """Top-right instrument box. `value` is only ever a real figure supplied by
    the scene — this module never invents a measurement."""

    label: str = "SIMULATION"
    value: str = "RUNNING"
    unit: str = ""
    alert: bool = False


class PanelSpec(BaseModel):
    experiment_id: str = "#001"
    status_lines: list[str] = Field(default_factory=lambda: ["Simulation Status:", "Idle"])
    readout: HudReadout = Field(default_factory=HudReadout)
    headline: list[str] = Field(default_factory=list)
    headline_style: str = "label"
    headline_anchor: str = "bottom_left"
    background: str = "light"
    split_at: float = 0.56
    watermark: str = ""

    @field_validator("background")
    @classmethod
    def _bg(cls, v: str) -> str:
        if v not in BACKGROUND_MODES:
            raise ValueError(f"background must be one of {BACKGROUND_MODES}")
        return v

    @field_validator("headline_anchor")
    @classmethod
    def _anchor(cls, v: str) -> str:
        if v not in HEADLINE_ANCHORS:
            raise ValueError(f"headline_anchor must be one of {HEADLINE_ANCHORS}")
        return v

    @field_validator("headline_style")
    @classmethod
    def _style(cls, v: str) -> str:
        if v not in HEADLINE_STYLES:
            raise ValueError(f"headline_style must be one of {HEADLINE_STYLES}")
        return v

    @field_validator("headline")
    @classmethod
    def _headline(cls, v: list[str]) -> list[str]:
        return [line.strip().upper() for line in v if line.strip()][:3]


# ---------------------------------------------------------------------------
# Geometry (fractions of the short edge unless noted)
# ---------------------------------------------------------------------------

class _Metrics:
    def __init__(self, width: int, height: int):
        self.w, self.h = width, height
        self.unit = min(width, height)          # scale reference
        self.inset = round(self.unit * 0.020)   # frame inset from the edge
        self.hair = max(1, round(self.unit * 0.0022))   # HUD hairlines
        self.frame = max(2, round(self.unit * 0.0042))  # panel frame
        self.pad = round(self.unit * 0.022)     # gap between frame and HUD
        self.tiny = round(width * 0.0155)       # status block type
        self.small = round(width * 0.0165)      # readout label
        self.big = round(width * 0.034)         # readout value
        # Headlines scale with WIDTH: the identity's block of caps should span a
        # consistent fraction of the panel in both 16:9 and 9:16, which a
        # short-edge scale cannot do.
        self.display = round(width * 0.088)     # title-card headline
        self.label = round(width * 0.058)       # in-panel headline
        self.readout_max = round(width * 0.34)  # readout may not eat the frame


def _colors(spec: PanelSpec) -> dict[str, tuple[int, int, int]]:
    from ..style.institutional import PALETTE

    def rgb(name: str) -> tuple[int, int, int]:
        h = PALETTE[name].lstrip("#")
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]

    dark_panel = spec.background == "night"
    return {
        "paper": rgb("night") if dark_panel else rgb("paper"),
        "ink": rgb("paper") if dark_panel else rgb("ink"),
        "alert": rgb("alert"),
        "night": rgb("night"),
        "light": rgb("paper"),
        "dark_ink": rgb("ink"),
    }


def _text_w(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> int:
    return int(draw.textbbox((0, 0), text, font=font)[2])


def _fit_font(draw: ImageDraw.ImageDraw, lines: list[str], size: int, max_width: int,
              candidates: list[str]) -> ImageFont.FreeTypeFont:
    """Shrink until the widest line fits. Headlines are never allowed to spill
    past the safe margin, so long derivations degrade gracefully."""
    while size > 8:
        font = _font(candidates, size)
        if all(_text_w(draw, line, font) <= max_width for line in lines):
            return font
        size = int(size * 0.94)
    return _font(candidates, 8)


# ---------------------------------------------------------------------------
# HUD parts
# ---------------------------------------------------------------------------

def _draw_frame(draw: ImageDraw.ImageDraw, m: _Metrics, ink) -> None:
    draw.rectangle(
        [m.inset, m.inset, m.w - m.inset - 1, m.h - m.inset - 1],
        outline=ink, width=m.frame,
    )


def _draw_status_block(draw: ImageDraw.ImageDraw, m: _Metrics, spec: PanelSpec, ink) -> None:
    """Top-left: outlined box, experiment id on the first row, a hairline rule,
    then the status lines."""
    font = _font(_REGULAR_CANDIDATES, m.tiny)
    rows = [f"Experiment {spec.experiment_id}", *spec.status_lines[:2]]
    pad_x = round(m.unit * 0.010)
    pad_y = round(m.unit * 0.008)
    line_h = round(m.tiny * 1.45)
    box_w = max(_text_w(draw, r, font) for r in rows) + 2 * pad_x
    box_w = max(box_w, round(m.unit * 0.16))
    box_h = len(rows) * line_h + 2 * pad_y
    x0 = m.inset + m.pad
    y0 = m.inset + m.pad
    draw.rectangle([x0, y0, x0 + box_w, y0 + box_h], outline=ink, width=m.hair)
    for i, row in enumerate(rows):
        draw.text((x0 + pad_x, y0 + pad_y + i * line_h), row, font=font, fill=ink)
        if i == 0:
            rule_y = y0 + pad_y + line_h - round(line_h * 0.16)
            draw.line([x0 + m.hair, rule_y, x0 + box_w - m.hair, rule_y], fill=ink, width=m.hair)


def _draw_alert_marker(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, alert) -> None:
    """The small filled right-triangle that flags a critical readout."""
    draw.polygon([(x, y), (x + size, y), (x, y + size)], fill=alert)


def _draw_readout(draw: ImageDraw.ImageDraw, m: _Metrics, spec: PanelSpec, ink, alert) -> None:
    """Top-right: a stacked instrument reading inside drawn square brackets.

    Always stacked — small letterspaced label over a heavy value — because that
    is what the reference identity does for every readout it shows, from
    "EXPERIMENT / #024" to "HUMAN SURVIVAL / 5%".
    """
    r = spec.readout
    label = r.label.upper()
    label_size, value_size = m.small, m.big
    # Shrink before overflowing: a readout that runs past the frame is worse
    # than one set a point smaller.
    for _ in range(14):
        lf = _font(_REGULAR_CANDIDATES, label_size)
        vf = _font(_BOLD_CANDIDATES, value_size)
        uf = _font(_REGULAR_CANDIDATES, label_size)
        widest = max(_text_w(draw, label, lf),
                     _text_w(draw, r.value, vf) + (_text_w(draw, r.unit, uf) if r.unit else 0))
        if widest <= m.readout_max or label_size <= 7:
            break
        label_size = max(7, int(label_size * 0.92))
        value_size = max(8, int(value_size * 0.92))

    label_font = _font(_REGULAR_CANDIDATES, label_size)
    value_font = _font(_BOLD_CANDIDATES, value_size)
    unit_font = _font(_REGULAR_CANDIDATES, label_size)

    gap = round(m.unit * 0.008)
    tri = round(value_size * 0.62) if r.alert else 0
    label_w = _text_w(draw, label, label_font) if label else 0
    value_w = _text_w(draw, r.value, value_font) if r.value else 0
    unit_w = _text_w(draw, r.unit, unit_font) if r.unit else 0
    value_row_w = (tri + gap if tri else 0) + value_w + (gap + unit_w if unit_w else 0)

    pad = round(m.unit * 0.012)
    label_h = round(label_size * 1.35) if label else 0
    value_h = round(value_size * 1.25) if r.value else 0
    box_w = max(label_w, value_row_w) + 2 * pad
    box_h = label_h + value_h + 2 * pad
    x1 = m.w - m.inset - m.pad
    x0 = x1 - box_w
    y0 = m.inset + m.pad

    # Square brackets rather than a full box — the reference's signature.
    arm = max(round(box_w * 0.11), m.hair * 3)
    for x, direction in ((x0, 1), (x1, -1)):
        draw.line([x, y0, x, y0 + box_h], fill=ink, width=m.hair)
        draw.line([x, y0, x + direction * arm, y0], fill=ink, width=m.hair)
        draw.line([x, y0 + box_h, x + direction * arm, y0 + box_h], fill=ink, width=m.hair)

    y = y0 + pad
    if label:
        draw.text((x0 + pad, y), label, font=label_font, fill=ink)
        y += label_h
    if r.value:
        x = x0 + pad
        if tri:
            _draw_alert_marker(draw, x, y + round(value_size * 0.34), tri, alert)
            x += tri + gap
        draw.text((x, y), r.value, font=value_font, fill=ink)
        x += value_w + gap
        if r.unit:
            draw.text((x, y + round(value_size * 0.42)), r.unit, font=unit_font, fill=ink)


def _draw_headline(draw: ImageDraw.ImageDraw, m: _Metrics, spec: PanelSpec, ink) -> None:
    if not spec.headline:
        return
    safe = m.inset + m.pad
    max_width = m.w - 2 * safe
    if spec.background == "split_right":
        # The headline lives on the white side; constraining its width here is
        # what keeps it from bleeding across the seam onto black.
        max_width = int(m.w * spec.split_at) - safe - m.pad
    size = m.display if spec.headline_style == "display" else m.label
    font = _fit_font(draw, spec.headline, size, max_width, _BOLD_CANDIDATES)
    ascent, descent = font.getmetrics()
    line_h = round((ascent + descent) * 0.98)  # tight leading, as in the reference
    block_h = line_h * len(spec.headline)

    if spec.headline_anchor == "top_center":
        y = m.inset + round(m.h * 0.075)
    else:
        y = m.h - m.inset - m.pad - block_h - round(m.h * 0.030)

    for i, line in enumerate(spec.headline):
        w = _text_w(draw, line, font)
        if spec.headline_anchor == "bottom_left" or spec.background == "split_right":
            x = safe
        else:
            x = (m.w - w) // 2
        draw.text((x, y + i * line_h), line, font=font, fill=ink)


def _draw_watermark(draw: ImageDraw.ImageDraw, m: _Metrics, text: str, alert, paper) -> None:
    """A solid bar along the bottom edge — unmissable, but it never sits on top
    of the artwork or the headline, so the preview still shows the real layout."""
    font = _font(_BOLD_CANDIDATES, round(m.unit * 0.022))
    ascent, descent = font.getmetrics()
    bar_h = ascent + descent + round(m.unit * 0.012)
    draw.rectangle([0, m.h - bar_h, m.w, m.h], fill=alert)
    w = _text_w(draw, text, font)
    draw.text(((m.w - w) // 2, m.h - bar_h + round(m.unit * 0.006)), text, font=font, fill=paper)


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

def compose_panel(spec: PanelSpec, out_path: str | Path, width: int, height: int,
                  illustration: str | Path | None = None) -> Path:
    """Draw the report furniture over an illustration (or over bare paper).

    The illustration is expected to already carry the panel background — that is
    how a day/night subject can cross the seam. When it does not fill the frame
    it is centred and the panel background shows through.
    """
    if width < 64 or height < 64:
        raise RenderError(f"panel too small to compose: {width}x{height}", stage="render.hud")
    m = _Metrics(width, height)
    c = _colors(spec)

    canvas = Image.new("RGB", (width, height), c["paper"])
    if spec.background == "split_right":
        seam = int(width * spec.split_at)
        ImageDraw.Draw(canvas).rectangle([seam, 0, width, height], fill=c["night"])

    if illustration is not None:
        art = Image.open(illustration).convert("RGB")
        if art.size != (width, height):
            art = art.resize((width, height), Image.LANCZOS)
        canvas.paste(art, (0, 0))

    draw = ImageDraw.Draw(canvas)
    ink = c["ink"]
    readout_ink = ink
    if spec.background == "split_right":
        # Frame, status block and headline sit on the white side; the readout is
        # top-right, which is the black side, so it inverts.
        ink = c["dark_ink"]
        readout_ink = c["light"]
    _draw_frame(draw, m, ink)
    _draw_status_block(draw, m, spec, ink)
    _draw_readout(draw, m, spec, readout_ink, c["alert"])
    _draw_headline(draw, m, spec, ink)
    if spec.watermark:
        _draw_watermark(draw, m, spec.watermark, c["alert"], c["light"])

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return out
