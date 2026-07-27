"""Watermarked placeholders + production rejection.

Plan-mode placeholders carry a VISIBLE watermark and a PNG tEXt metadata marker
(`sias_placeholder`). Production QC rejects on the metadata marker, so a
renamed placeholder still cannot pass."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw
from PIL.PngImagePlugin import PngInfo

from sias.schemas import QCCheck

WATERMARK = "PLAN MODE — NOT FOR PRODUCTION"
_META_KEY = "sias_placeholder"


def make_placeholder(path: str | Path, label: str, width: int = 540, height: int = 960,
                     color: str = "#2F6F8F") -> Path:
    img = Image.new("RGB", (width, height), "#F4EEDC")
    draw = ImageDraw.Draw(img)
    for gx in range(0, width, 27):
        draw.line((gx, 0, gx, height), fill="#E8E1CB")
    for gy in range(0, height, 27):
        draw.line((0, gy, width, gy), fill="#E8E1CB")
    draw.rectangle((40, 90, width - 40, height - 120), outline="#252525", width=5)
    draw.ellipse((width // 3, height // 3, 2 * width // 3, height // 2), fill=color)
    draw.text((50, 40), label, fill="#252525")
    for y in (height - 90, height // 2):
        draw.text((50, y), WATERMARK, fill="#C94C4C")
    meta = PngInfo()
    meta.add_text(_META_KEY, WATERMARK)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, pnginfo=meta)
    return path


def is_placeholder(path: str | Path) -> bool:
    try:
        with Image.open(path) as img:
            return _META_KEY in getattr(img, "text", {}) or "placeholder" in Path(path).name.lower()
    except Exception:
        return False


def reject_placeholders_in_production(image_paths: list[str], production_mode: bool) -> list[QCCheck]:
    checks: list[QCCheck] = []
    for p in image_paths:
        if production_mode and is_placeholder(p):
            checks.append(QCCheck(check_id=f"placeholder_{Path(p).stem}", level="visual",
                                  status="FAIL", detail=f"placeholder asset in production: {p}"))
    if not checks:
        checks.append(QCCheck(check_id="no_placeholders", level="visual", status="PASS"))
    return checks
