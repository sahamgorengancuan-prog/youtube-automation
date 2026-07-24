"""Optional isolated hero-object assets (F7).

The pipeline's default object layers are **cutouts of the one beauty frame** —
the most identity-consistent path, because every object shares one coherently
lit, single FLUX render. This module adds an **opt-in** alternative: for a
designated hero object, generate a *separate* FLUX render of that subject alone
on a flat background, then key the background out to a transparent PNG — useful
when the hero must be composited at full detail or when its in-frame cutout is
occluded.

It is opt-in and honestly gated:

* FLUX generation runs only when an ``image_generator`` is injected (production).
  With no generator (offline/plan) ``build`` returns ``None`` — no procedural
  hero art is ever fabricated.
* The background-keying step (``key_flat_background``) is deterministic and fully
  testable offline: it removes a flat/near-uniform background sampled from the
  image corners, leaving the subject opaque. This is the part that turns a
  "subject on plain background" render into a clean transparent cutout.

The default remains beauty-frame cutouts; nothing here changes unless
``flux_studio.hero_isolated_asset`` is enabled.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PIL import Image

from .schemas import DrawingBrief
from .utils import ensure_dir, hash_value, save_json

# Flat backgrounds the hero brief asks for (keyed out afterwards).
_BG_COLORS = {
    "white": (255, 255, 255),
    "chroma": (0, 255, 0),
    "neutral": (240, 240, 240),
}


def key_flat_background(
    image: Image.Image, tolerance: int = 28, bg_rgb: tuple[int, int, int] | None = None
) -> Image.Image:
    """Return an RGBA copy of *image* with a flat background made transparent.

    The background colour is *bg_rgb* if given, else the median of the four
    corner pixels (a subject-on-plain-background render). A pixel within
    *tolerance* (Manhattan distance) of that colour becomes transparent; the
    subject stays opaque. Deterministic — no model required.
    """
    rgb = image.convert("RGB")
    w, h = rgb.size
    px = rgb.load()
    if bg_rgb is None:
        corners = [px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1]]
        bg_rgb = tuple(sorted(c[i] for c in corners)[len(corners) // 2] for i in range(3))
    br, bg_, bb = bg_rgb
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    op = out.load()
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            if abs(r - br) + abs(g - bg_) + abs(b - bb) <= tolerance:
                op[x, y] = (r, g, b, 0)
            else:
                op[x, y] = (r, g, b, 255)
    return out


def _opaque_ratio(rgba: Image.Image) -> float:
    alpha = rgba.getchannel("A")
    hist = alpha.histogram()
    opaque = sum(hist[200:])
    total = rgba.size[0] * rgba.size[1]
    return round(opaque / total, 4) if total else 0.0


class HeroAssetStudio:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
        root: Any = None,
        image_generator: Callable[[DrawingBrief], Path | None] | None = None,
    ):
        self.config = config or {}
        self.root = ensure_dir(root) if root is not None else None
        self.image_generator = image_generator
        self.bg_name = str(self.config.get("hero_background", "white"))
        self.bg_rgb = _BG_COLORS.get(self.bg_name, _BG_COLORS["white"])

    @property
    def enabled(self) -> bool:
        # Opt-in AND requires a real image generator (never fabricates offline).
        return bool(self.config.get("hero_isolated_asset", False)) and self.image_generator is not None

    def _brief(self, scene_id: str, subject: str) -> DrawingBrief:
        pos = (
            f"A single isolated {subject}, centered, full subject visible, flat "
            f"{self.bg_name} background, even studio lighting, flat vector "
            "science-explainer style, crisp clean edges, no scene, no shadow cast "
            "on background, no props."
        )
        neg = "busy background, gradient, scene, multiple subjects, drop shadow, text, watermark"
        out = ""
        if self.root is not None:
            out = str(self.root / f"hero_{scene_id}_{hash_value(subject, 8)}.png")
        return DrawingBrief(
            brief_id=f"hero-{scene_id}-{hash_value(subject, 8)}",
            scene_id=scene_id,
            purpose="layer_isolation",
            positive_prompt=pos,
            negative_prompt=neg,
            kontext_instruction=(
                f"Render only the {subject} as an isolated asset on a flat "
                f"{self.bg_name} background for clean cutout."
            ),
            output_path=out,
        )

    def build(self, scene_id: str, subject: str, force: bool = False) -> dict[str, Any] | None:
        """Generate one isolated hero asset and key its background to transparent.
        Returns a record, or ``None`` when disabled/ungated (offline)."""
        if not self.enabled:
            return None
        brief = self._brief(scene_id, subject)
        raw = self.image_generator(brief)  # BFL; may return None on failure
        if not raw or not Path(raw).exists():
            return None
        keyed = key_flat_background(
            Image.open(raw),
            tolerance=int(self.config.get("hero_key_tolerance", 28)),
            bg_rgb=self.bg_rgb,
        )
        out_path = Path(brief.output_path)
        ensure_dir(out_path.parent)
        keyed.save(out_path)
        return {
            "scene_id": scene_id,
            "subject": subject,
            "raw_render": str(raw),
            "cutout": str(out_path),
            "opaque_ratio": _opaque_ratio(keyed),
            "background": self.bg_name,
        }

    def generate_all(self, subjects: list[tuple[str, str]], force: bool = False) -> dict[str, Any]:
        """subjects: [(scene_id, subject)]. Emits a manifest and returns it."""
        records = []
        for scene_id, subject in subjects:
            rec = self.build(scene_id, subject, force=force)
            if rec is not None:
                records.append(rec)
        manifest = {
            "enabled": self.enabled,
            "default_note": "Default object layers are beauty-frame cutouts; "
            "isolated hero assets are opt-in (flux_studio.hero_isolated_asset).",
            "assets": records,
        }
        if self.root is not None:
            save_json(self.root / "hero_assets.json", manifest)
        return manifest
