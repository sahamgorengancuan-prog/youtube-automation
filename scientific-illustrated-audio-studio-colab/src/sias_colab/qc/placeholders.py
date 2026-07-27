"""Watermarked preview panels + production rejection.

A preview panel is a real Institutional Lab Notebook panel: the same frame,
status block, instrument readout, headline typography and safe zones as a live
episode, with a deterministic flat-vector schematic standing in for the
generated artwork. What you approve in PREVIEW is the layout you get in LIVE.

It carries a VISIBLE watermark and a PNG tEXt metadata marker
(`sias_placeholder`), so a renamed preview panel still cannot pass production
QC."""

from __future__ import annotations

from pathlib import Path

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from sias.render.hud import PanelSpec, compose_panel
from sias.render.schematic import draw_schematic
from sias.schemas import QCCheck, SceneSpec

WATERMARK = "PREVIEW — NOT FOR PRODUCTION"
_META_KEY = "sias_placeholder"


def _stamp(path: Path) -> Path:
    """Re-save with the placeholder tEXt marker (PIL drops it on compose)."""
    with Image.open(path) as img:
        meta = PngInfo()
        meta.add_text(_META_KEY, WATERMARK)
        img.save(path, pnginfo=meta)
    return path


def make_preview_panel(path: str | Path, scene: SceneSpec, index: int, experiment_id: str,
                       width: int = 540, height: int = 960) -> Path:
    """A complete watermarked panel for the keyless path."""
    from sias.style.institutional import (
        derive_background,
        derive_headline,
        derive_headline_anchor,
        derive_readout,
        derive_schematic,
    )

    background = derive_background(scene)
    anchor = derive_headline_anchor(scene, index)
    display = index == 0 or scene.beat_role == "cold_open"
    path = Path(path)
    art = path.with_name(path.stem + "_art.png")
    draw_schematic(art, width, height, derive_schematic(scene, index), background, anchor)
    readout = derive_readout(scene)
    spec = PanelSpec(
        experiment_id=experiment_id,
        status_lines=["Simulation Status:", "Preview"],
        readout={"label": readout["label"], "value": readout["value"] or experiment_id,
                 "unit": readout["unit"], "alert": readout["alert"]},
        headline=derive_headline(scene, display=display),
        headline_style="display" if display else "label",
        headline_anchor=anchor,
        background=background,
        watermark=WATERMARK,
    )
    compose_panel(spec, path, width, height, illustration=art)
    art.unlink(missing_ok=True)
    return _stamp(path)


def make_placeholder(path: str | Path, label: str, width: int = 540, height: int = 960,
                     color: str = "#2F6F8F") -> Path:
    """Back-compatible entry point: builds a preview panel from a label like
    "S03 · gasp_reveal"."""
    scene_id, _, beat = label.partition("·")
    scene = SceneSpec(scene_id=scene_id.strip() or "S01", beat_role=beat.strip(),
                      panel_title=beat.strip().replace("_", " "))
    index = 0 if scene.beat_role == "cold_open" else 1
    return make_preview_panel(path, scene, index, "#001", width, height)


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
