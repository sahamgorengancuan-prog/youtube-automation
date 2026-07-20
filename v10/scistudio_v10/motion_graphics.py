"""Cinematic finishing pass for the deterministic renderer.

Turns a static composited beauty frame into an *explainer-style* motion-graphics
frame — the thing that made the reference video feel alive — without needing a
rig, SAM2 or a browser:

* **Camera motion** — an always-on, deterministic Ken-Burns push/pan so the
  frame is never frozen even when the LLM authored no per-layer motion.
* **Procedural particles** — animated rain / wind / snow / spark / bubble
  layers inferred from the scene text (rain by default for this topic). These
  move every frame, which is what carries the sense of life.
* **Captions + HUD** — a headline lower-third and an "experiment ledger" HUD
  (id + simulation status + measured variable) in the reference's language,
  animated in with a slide/reveal.

Everything is deterministic (seeded per scene) and fully guarded: any failure
returns the input frame unchanged, so the render never breaks.
"""

from __future__ import annotations

import math
import random
from typing import Any

from PIL import Image, ImageDraw, ImageFont

_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

# Keyword -> particle effect. First match wins; falls back to a gentle drift.
_FX_KEYWORDS = (
    (
        "rain",
        ("rain", "rainfall", "downpour", "storm", "flood", "monsoon", "drizzle", "wet"),
    ),
    (
        "water",
        ("river", "ocean", "sea", "wave", "tide", "water", "current", "submerge"),
    ),
    ("snow", ("snow", "ice", "frost", "blizzard", "freeze", "glacier")),
    ("wind", ("wind", "gale", "gust", "hurricane", "cyclone", "breeze")),
    (
        "spark",
        ("fire", "ember", "spark", "lava", "heat", "explosion", "energy", "electric"),
    ),
    ("bubble", ("bubble", "gas", "boil", "steam", "vapor", "float")),
)


class CinematicFinisher:
    """Applies camera motion, particles and captions to composited frames."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.enabled = bool(self.config.get("cinematic_finish", True))
        self.show_captions = bool(self.config.get("cinematic_captions", True))
        self.show_hud = bool(self.config.get("cinematic_hud", True))
        self.show_particles = bool(self.config.get("cinematic_particles", True))
        self.zoom = float(self.config.get("cinematic_zoom", 0.08))
        self._font_cache: dict[int, Any] = {}

    # -- public API ---------------------------------------------------------
    def finish(self, base: Image.Image, ctx: dict[str, Any], frame: int, total: int) -> Image.Image:
        if not self.enabled:
            return base
        try:
            total = max(1, int(total))
            progress = min(1.0, max(0.0, frame / total))
            seed = abs(hash(str(ctx.get("scene_id", "")))) % (2**31)
            image = base.convert("RGB")
            fx = ctx.get("fx_type") or self._infer_fx(f"{ctx.get('headline', '')} {ctx.get('narration', '')}")
            image = self._ken_burns(image, progress, seed)
            if self.show_particles and fx:
                image = self._particles(image, fx, frame, seed)
            if self.show_hud:
                image = self._hud(image, ctx, progress, fx)
            if self.show_captions:
                image = self._captions(image, ctx, progress)
            return image
        except Exception:
            return base.convert("RGB")

    @staticmethod
    def infer_fx(text: str) -> str:
        return CinematicFinisher._infer_fx(text)

    # -- camera -------------------------------------------------------------
    def _ken_burns(self, image: Image.Image, progress: float, seed: int) -> Image.Image:
        width, height = image.size
        rng = random.Random(seed)
        # Alternate push-in / pull-out and pan direction per scene so cuts feel varied.
        direction = 1 if rng.random() > 0.5 else -1
        scale = 1.0 + self.zoom * (progress if direction > 0 else (1.0 - progress))
        pan_x = (rng.random() - 0.5) * 0.04 * width * progress
        pan_y = (rng.random() - 0.5) * 0.04 * height * progress
        new_w, new_h = int(width * scale), int(height * scale)
        zoomed = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
        left = int((new_w - width) / 2 + pan_x)
        top = int((new_h - height) / 2 + pan_y)
        left = max(0, min(new_w - width, left))
        top = max(0, min(new_h - height, top))
        return zoomed.crop((left, top, left + width, top + height))

    # -- particles ----------------------------------------------------------
    @staticmethod
    def _infer_fx(text: str) -> str:
        low = text.lower()
        for fx, words in _FX_KEYWORDS:
            if any(word in low for word in words):
                return fx
        return "drift"

    def _particles(self, image: Image.Image, fx: str, frame: int, seed: int) -> Image.Image:
        width, height = image.size
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        rng = random.Random(seed ^ 0x9E3779B9)
        count = int(self.config.get("particle_count", 130))
        t = frame
        for _ in range(count):
            px = rng.random()
            py = rng.random()
            speed = 0.6 + rng.random() * 0.8
            if fx in ("rain", "water"):
                y = (py + (t * 0.045 * speed)) % 1.0
                x = (px + 0.06 * math.sin(y * 6.28)) % 1.0
                sx, sy = x * width, y * height
                length = 22 + speed * 26
                draw.line(
                    [(sx, sy), (sx - 3, sy + length)],
                    fill=(200, 218, 236, 150),
                    width=2,
                )
            elif fx == "snow":
                y = (py + (t * 0.012 * speed)) % 1.0
                x = (px + 0.03 * math.sin(t * 0.05 + py * 12)) % 1.0
                r = 2 + speed * 2
                draw.ellipse(
                    [x * width - r, y * height - r, x * width + r, y * height + r],
                    fill=(245, 248, 252, 190),
                )
            elif fx == "wind":
                x = (px + (t * 0.05 * speed)) % 1.0
                y = (py + 0.02 * math.sin(t * 0.08 + px * 10)) % 1.0
                length = 40 + speed * 40
                draw.line(
                    [(x * width, y * height), (x * width + length, y * height - 5)],
                    fill=(220, 228, 236, 90),
                    width=2,
                )
            elif fx == "spark":
                y = (py - (t * 0.03 * speed)) % 1.0
                x = (px + 0.05 * math.sin(t * 0.1 + py * 8)) % 1.0
                r = 1 + speed * 3
                draw.ellipse(
                    [x * width - r, y * height - r, x * width + r, y * height + r],
                    fill=(255, 176, 92, 200),
                )
            elif fx == "bubble":
                y = (py - (t * 0.02 * speed)) % 1.0
                x = (px + 0.02 * math.sin(t * 0.06 + py * 9)) % 1.0
                r = 3 + speed * 5
                draw.ellipse(
                    [x * width - r, y * height - r, x * width + r, y * height + r],
                    outline=(210, 230, 245, 150),
                    width=2,
                )
            else:  # drift — subtle floating motes so nothing is ever fully static
                y = (py - (t * 0.008 * speed)) % 1.0
                x = (px + 0.015 * math.sin(t * 0.04 + py * 7)) % 1.0
                r = 1 + speed * 2
                draw.ellipse(
                    [x * width - r, y * height - r, x * width + r, y * height + r],
                    fill=(255, 255, 255, 70),
                )
        return Image.alpha_composite(image.convert("RGBA"), layer).convert("RGB")

    # -- captions & HUD -----------------------------------------------------
    def _font(self, size: int, bold: bool = True):
        key = size * 2 + (1 if bold else 0)
        if key not in self._font_cache:
            try:
                self._font_cache[key] = ImageFont.truetype(_FONT_BOLD if bold else _FONT_REG, size)
            except Exception:
                self._font_cache[key] = ImageFont.load_default()
        return self._font_cache[key]

    def _captions(self, image: Image.Image, ctx: dict[str, Any], progress: float) -> Image.Image:
        headline = str(ctx.get("headline", "")).strip()
        narration = str(ctx.get("narration", "")).strip()
        if not headline and not narration:
            return image
        width, height = image.size
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        # Slide the lower-third up during the first ~12% of the scene.
        reveal = min(1.0, progress / 0.12) if progress < 0.12 else 1.0
        band_h = int(height * 0.20)
        band_top = height - int(band_h * reveal)
        draw.rectangle([0, band_top, width, height], fill=(14, 20, 26, 205))
        draw.rectangle([0, band_top, width, band_top + 6], fill=(46, 119, 166, 255))
        if headline:
            self._wrapped_text(
                draw,
                headline.upper(),
                self._font(int(height * 0.030), True),
                (int(width * 0.05), band_top + int(band_h * 0.16)),
                width - int(width * 0.10),
                (245, 247, 250),
                int(width * 0.045),
            )
        if narration:
            self._wrapped_text(
                draw,
                narration,
                self._font(int(height * 0.020), False),
                (int(width * 0.05), band_top + int(band_h * 0.52)),
                width - int(width * 0.10),
                (183, 197, 208),
                int(width * 0.030),
            )
        return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")

    _FX_CHIP = {
        "rain": "RAINFALL RATE",
        "water": "WATER LEVEL",
        "snow": "TEMPERATURE",
        "wind": "WIND SPEED",
        "spark": "ENERGY OUTPUT",
        "bubble": "PRESSURE",
        "drift": "SYSTEM STATE",
    }

    def _hud(
        self,
        image: Image.Image,
        ctx: dict[str, Any],
        progress: float,
        fx: str = "drift",
    ) -> Image.Image:
        width, height = image.size
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        idx = int(ctx.get("index", 1))
        total = int(ctx.get("total_scenes", 1))
        variable = str(ctx.get("variable") or self._FX_CHIP.get(fx, "MEASURED VARIABLE"))[:26]
        font_s = self._font(int(height * 0.014), True)
        font_xs = self._font(int(height * 0.011), False)
        # Top-left experiment ledger.
        pad = int(width * 0.035)
        draw.text((pad, pad), f"EXPERIMENT #{idx:03d}", font=font_s, fill=(30, 40, 48))
        draw.text(
            (pad, pad + int(height * 0.022)),
            "SIMULATION STATUS:",
            font=font_xs,
            fill=(90, 105, 116),
        )
        draw.text(
            (pad, pad + int(height * 0.036)),
            "RUNNING" if progress < 0.9 else "COMPLETE",
            font=font_s,
            fill=(46, 119, 166),
        )
        draw.text(
            (pad, pad + int(height * 0.052)),
            f"STAGE {idx:02d} / {total:02d}",
            font=font_xs,
            fill=(90, 105, 116),
        )
        # Top-right measured-variable chip (red-ish "critical" accent late in scene).
        chip = variable.upper()
        chip_w = int(draw.textlength(chip, font=font_s)) + int(width * 0.05)
        chip_x = width - pad - chip_w
        accent = (216, 72, 62) if progress > 0.6 else (46, 119, 166)
        draw.rectangle(
            [chip_x, pad, width - pad, pad + int(height * 0.030)],
            fill=(250, 250, 247, 235),
            outline=accent,
            width=3,
        )
        draw.text(
            (chip_x + int(width * 0.02), pad + int(height * 0.007)),
            chip,
            font=font_s,
            fill=accent,
        )
        # Scene progress ticks bottom-center.
        return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")

    @staticmethod
    def _wrapped_text(draw, text, font, xy, max_width, fill, line_height):
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            trial = (current + " " + word).strip()
            if draw.textlength(trial, font=font) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        x, y = xy
        for line in lines[:3]:
            draw.text((x, y), line, font=font, fill=fill)
            y += line_height
