"""Shot executor for the authored animation DSL.

This module is a **pure executor**, not a creative director. It renders exactly
the camera / effect / caption directives the vision director authored in the
``AnimationPlan`` — and nothing else. There is no always-on camera move, no
keyword-inferred particles, and no default caption/HUD. What the director did
not author does not happen (the shot holds).

Object motion (the *primary* motion that carries the causal action) is executed
elsewhere, by the renderer's per-layer ``MotionEvent`` pass. This executor only
adds the *supporting* motion the director explicitly requested.

Everything is deterministic and fully guarded: any failure returns the input
frame unchanged, so the render never breaks.
"""

from __future__ import annotations

import math
import random
from typing import Any

from PIL import Image, ImageDraw, ImageFont

_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

_RGBA = (0, 0, 0, 0)


def _get(plan: Any, key: str, default: Any = None) -> Any:
    if isinstance(plan, dict):
        return plan.get(key, default)
    return getattr(plan, key, default)


def _ease(t: float, kind: str) -> float:
    t = min(1.0, max(0.0, t))
    if kind in ("ease_in_out", "smooth"):
        return t * t * (3 - 2 * t)
    if kind == "ease_in":
        return t * t
    if kind == "ease_out":
        return 1 - (1 - t) * (1 - t)
    return t


class ShotExecutor:
    """Executes authored camera / effect / caption directives onto a frame."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        # Global kill-switch only — there is no "auto" mode.
        self.enabled = bool(self.config.get("execute_shot_directives", True))
        self._font_cache: dict[int, Any] = {}

    # -- public API ---------------------------------------------------------
    def execute(self, base: Image.Image, plan: Any, frame: int, total: int) -> Image.Image:
        if not self.enabled:
            return base.convert("RGB")
        try:
            total = max(1, int(total))
            image = base.convert("RGB")
            seed = abs(hash(str(_get(plan, "scene_id", "")))) % (2**31)
            image = self._apply_camera(image, _get(plan, "camera"), frame, total)
            for effect in _get(plan, "effects", []) or []:
                image = self._apply_effect(image, effect, frame, total, seed)
            for caption in _get(plan, "captions", []) or []:
                image = self._apply_caption(image, caption, frame, total)
            return image
        except Exception:
            return base.convert("RGB")

    # -- windows ------------------------------------------------------------
    @staticmethod
    def _progress(start: int, end: int, frame: int, total: int) -> float | None:
        start = int(start or 0)
        end = int(end or 0)
        if end <= start:  # unspecified window -> active for the whole shot
            start, end = 0, total
        if frame < start or frame > end:
            return None
        return (frame - start) / max(1, end - start)

    # -- camera -------------------------------------------------------------
    def _apply_camera(self, image: Image.Image, camera: Any, frame: int, total: int) -> Image.Image:
        if camera is None:
            return image
        move = str(_get(camera, "move", "hold"))
        magnitude = float(_get(camera, "magnitude", 0.0) or 0.0)
        if move == "hold" or magnitude <= 0.0:
            return image  # the director asked for no camera move -> honour it
        p = self._progress(_get(camera, "start_frame", 0), _get(camera, "end_frame", 0), frame, total)
        if p is None:
            return image
        p = _ease(p, str(_get(camera, "easing", "ease_in_out")))
        width, height = image.size
        zoom = 1.0
        dx = dy = 0.0
        if move == "push_in":
            zoom = 1.0 + magnitude * p
        elif move == "pull_out":
            zoom = 1.0 + magnitude * (1.0 - p)
        else:  # pans zoom slightly so there is room to translate within frame
            zoom = 1.0 + max(magnitude, 0.04)
            span = magnitude * p
            if move == "pan_left":
                dx = span * width
            elif move == "pan_right":
                dx = -span * width
            elif move == "pan_up":
                dy = span * height
            elif move == "pan_down":
                dy = -span * height
        new_w, new_h = int(width * zoom), int(height * zoom)
        zoomed = image.resize((new_w, new_h), Image.Resampling.LANCZOS)
        left = int((new_w - width) / 2 + dx)
        top = int((new_h - height) / 2 + dy)
        left = max(0, min(new_w - width, left))
        top = max(0, min(new_h - height, top))
        return zoomed.crop((left, top, left + width, top + height))

    # -- effects ------------------------------------------------------------
    def _apply_effect(self, image: Image.Image, effect: Any, frame: int, total: int, seed: int) -> Image.Image:
        kind = str(_get(effect, "effect", "none"))
        intensity = float(_get(effect, "intensity", 0.0) or 0.0)
        if kind == "none" or intensity <= 0.0:
            return image
        if self._progress(_get(effect, "start_frame", 0), _get(effect, "end_frame", 0), frame, total) is None:
            return image
        width, height = image.size
        region = _get(effect, "region", (0.0, 0.0, 1.0, 1.0)) or (0.0, 0.0, 1.0, 1.0)
        x0, y0, x1, y1 = (float(v) for v in region)
        rx0, ry0, rw, rh = x0 * width, y0 * height, max(1.0, (x1 - x0) * width), max(1.0, (y1 - y0) * height)
        direction = math.radians(float(_get(effect, "direction_deg", 90.0) or 90.0))
        dx, dy = math.cos(direction), math.sin(direction)
        layer = Image.new("RGBA", (width, height), _RGBA)
        draw = ImageDraw.Draw(layer)
        rng = random.Random(seed ^ (hash(kind) & 0xFFFFFFFF))
        count = int(min(400, max(4, intensity * float(self.config.get("particle_density", 90)))))
        t = frame
        for _ in range(count):
            bx, by = rng.random(), rng.random()
            speed = 0.6 + rng.random() * 0.9
            phase = (t * 0.02 * speed) % 1.0
            u = (bx + dx * phase) % 1.0
            v = (by + dy * phase) % 1.0
            px, py = rx0 + u * rw, ry0 + v * rh
            if kind in ("rain", "water"):
                ln = 14 + speed * 20 * (0.5 + intensity)
                draw.line([(px, py), (px - dx * ln * 0.3, py + ln)], fill=(200, 218, 236, 150), width=2)
            elif kind == "snow":
                r = 2 + speed * 2
                draw.ellipse([px - r, py - r, px + r, py + r], fill=(245, 248, 252, 190))
            elif kind == "wind":
                ln = 30 + speed * 40
                draw.line([(px, py), (px + dx * ln, py + dy * ln)], fill=(220, 228, 236, 90), width=2)
            elif kind == "spark":
                r = 1 + speed * 3
                draw.ellipse([px - r, py - r, px + r, py + r], fill=(255, 176, 92, 200))
            elif kind == "bubble":
                r = 3 + speed * 5
                draw.ellipse([px - r, py - r, px + r, py + r], outline=(210, 230, 245, 150), width=2)
            else:  # dust
                r = 1 + speed * 2
                draw.ellipse([px - r, py - r, px + r, py + r], fill=(230, 224, 210, 90))
        return Image.alpha_composite(image.convert("RGBA"), layer).convert("RGB")

    # -- captions -----------------------------------------------------------
    def _font(self, size: int, bold: bool = True):
        key = size * 2 + (1 if bold else 0)
        if key not in self._font_cache:
            try:
                self._font_cache[key] = ImageFont.truetype(_FONT_BOLD if bold else _FONT_REG, size)
            except Exception:
                self._font_cache[key] = ImageFont.load_default()
        return self._font_cache[key]

    def _apply_caption(self, image: Image.Image, caption: Any, frame: int, total: int) -> Image.Image:
        kind = str(_get(caption, "kind", "none"))
        text = str(_get(caption, "text", "")).strip()
        if kind == "none" or not text:
            return image
        p = self._progress(_get(caption, "start_frame", 0), _get(caption, "end_frame", 0), frame, total)
        if p is None:
            return image
        width, height = image.size
        overlay = Image.new("RGBA", (width, height), _RGBA)
        draw = ImageDraw.Draw(overlay)
        position = str(_get(caption, "position", "lower_third"))
        if kind in ("hud_status", "hud_metric", "label"):
            font = self._font(int(height * 0.016), True)
            pad = int(width * 0.04)
            if position == "top_right":
                w = int(draw.textlength(text.upper(), font=font)) + int(width * 0.05)
                x = width - pad - w
            elif position == "center":
                x = (width - int(draw.textlength(text.upper(), font=font))) // 2
            else:
                x = pad
            y = pad if "top" in position or position == "center" else int(height * 0.90)
            chip = kind == "hud_metric"
            if chip:
                draw.rectangle(
                    [
                        x - int(width * 0.02),
                        y - int(height * 0.006),
                        x + int(draw.textlength(text.upper(), font=font)) + int(width * 0.02),
                        y + int(height * 0.026),
                    ],
                    fill=(250, 250, 247, 235),
                    outline=(46, 119, 166),
                    width=3,
                )
            draw.text((x, y), text.upper(), font=font, fill=(30, 40, 48) if not chip else (46, 119, 166))
        else:  # headline lower-third
            reveal = min(1.0, p / 0.15) if p < 0.15 else 1.0
            band_h = int(height * 0.16)
            band_top = height - int(band_h * reveal)
            draw.rectangle([0, band_top, width, height], fill=(14, 20, 26, 205))
            draw.rectangle([0, band_top, width, band_top + 5], fill=(46, 119, 166, 255))
            self._wrapped(
                draw,
                text.upper(),
                self._font(int(height * 0.030), True),
                (int(width * 0.05), band_top + int(band_h * 0.24)),
                width - int(width * 0.10),
                (245, 247, 250),
                int(width * 0.045),
            )
        return Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")

    @staticmethod
    def _wrapped(draw, text, font, xy, max_width, fill, line_height):
        words, lines, current = text.split(), [], ""
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
        for line in lines[:2]:
            draw.text((x, y), line, font=font, fill=fill)
            y += line_height
