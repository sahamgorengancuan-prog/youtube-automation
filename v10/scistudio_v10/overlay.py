from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from .schemas import SceneRequest
from .style_canon import build_hard_coded_canon
from .utils import ensure_dir


class ScientificOverlayBuilder:
    """Builds only scientific UI/labels as SVG.

    Hero illustration pixels remain raster/authored. SVG is intentionally limited
    to typography, experiment panels, arrows, metrics and machine-readable masks.
    """

    def __init__(self, config: dict[str, Any], root: str | Path):
        self.config = config
        self.root = ensure_dir(root)
        self.canon = build_hard_coded_canon()

    def build(self, scene: SceneRequest, scene_index: int, total_scenes: int) -> str:
        width = int(self.config.get("width", 1080))
        height = int(self.config.get("height", 1920))
        margin = int(width * 0.055)
        ink = self.canon.color.ink
        paper = self.canon.color.paper
        red = self.canon.color.warning_red
        blue = self.canon.color.blue_primary
        experiment = f"EXPERIMENT {scene_index:03d}"
        stage = (scene.time_stage or f"STAGE {scene_index}/{total_scenes}").upper()
        headline = self._wrap(scene.headline.upper(), 23)
        metric, critical = self._metric(scene)
        body = [
            f'<rect x="{margin}" y="{margin}" width="300" height="92" fill="{paper}" stroke="{ink}" stroke-width="3"/>',
            f'<text x="{margin + 18}" y="{margin + 32}" font-family="Arial Narrow, Arial, sans-serif" font-size="24" font-weight="900" fill="{ink}">{html.escape(experiment)}</text>',
            f'<line x1="{margin + 18}" y1="{margin + 44}" x2="{margin + 282}" y2="{margin + 44}" stroke="{ink}" stroke-width="2"/>',
            f'<text x="{margin + 18}" y="{margin + 70}" font-family="Arial Narrow, Arial, sans-serif" font-size="18" font-weight="700" fill="{ink}">SIMULATION STATUS: ACTIVE</text>',
            f'<rect x="{width - margin - 290}" y="{margin}" width="290" height="92" fill="{paper}" stroke="{ink}" stroke-width="3"/>',
            f'<text x="{width - margin - 272}" y="{margin + 30}" font-family="Arial Narrow, Arial, sans-serif" font-size="18" font-weight="700" fill="{ink}">{html.escape(stage)}</text>',
            f'<text x="{width - margin - 272}" y="{margin + 67}" font-family="Arial Narrow, Arial, sans-serif" font-size="27" font-weight="900" fill="{red if critical else ink}">{html.escape(metric)}</text>',
        ]
        y = 245
        for line in headline:
            body.append(
                f'<text x="{margin}" y="{y}" font-family="Arial Narrow, Arial, sans-serif" font-size="68" '
                f'font-weight="900" letter-spacing="-1.5" fill="{ink}">{html.escape(line)}</text>'
            )
            y += 70
        body.extend(
            [
                f'<line x1="{margin}" y1="{y + 12}" x2="{margin + 300}" y2="{y + 12}" stroke="{blue}" stroke-width="10"/>',
                f'<text x="{width - margin}" y="{height - margin}" text-anchor="end" font-family="Arial Narrow, Arial, sans-serif" font-size="18" font-weight="900" fill="{ink}">SCIENTIFIC MOTION STUDIO</text>',
            ]
        )
        svg = f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}">{"".join(body)}</svg>'
        output = self.root / f"{scene.scene_id}_overlay.svg"
        output.write_text(svg, encoding="utf-8")
        return str(output)

    @staticmethod
    def _wrap(text: str, max_chars: int) -> list[str]:
        words = text.split()
        lines: list[str] = []
        current: list[str] = []
        for word in words:
            trial = " ".join([*current, word])
            if current and len(trial) > max_chars:
                lines.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            lines.append(" ".join(current))
        return lines[:3] or ["EXPERIMENT"]

    @staticmethod
    def _metric(scene: SceneRequest) -> tuple[str, bool]:
        text = (scene.desired_change + " " + scene.scientific_claim).lower()
        critical = any(k in text for k in ("critical", "collapse", "failure", "danger", "fatal", "uninhabitable"))
        if critical:
            return "CRITICAL", True
        if scene.time_stage:
            return scene.time_stage.upper()[:18], False
        return "ACTIVE", False
