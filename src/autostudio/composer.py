from __future__ import annotations

import base64
import html
import re
from pathlib import Path

from .config import StudioConfig
from .hashing import atomic_write_text
from .logging_utils import configure_logging
from .schemas import Scene


class SceneComposer:
    """Composes cached SVG assets into a full 1080x1920 static scene.

    Output is one self-contained SVG per scene: background, dashboard cards,
    headline, and every placed asset inlined (no external references). This is
    the storyboard preview — deliberately static. Animation is Phase 2.
    """

    def __init__(self, config: StudioConfig):
        self.config = config
        self.palette = config.style.palette
        self.logger = configure_logging("autostudio.composer")

    def _inner_svg(self, path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        match = re.search(r"<svg[^>]*>(.*)</svg>", text, flags=re.DOTALL | re.IGNORECASE)
        if not match:
            raise ValueError(f"Could not parse asset SVG: {path}")
        return re.sub(r'\s+xmlns(:\w+)?="[^"]+"', "", match.group(1))

    def _wrap(self, text: str, max_chars: int = 21) -> list[str]:
        lines, current = [], []
        for word in text.split():
            candidate = " ".join(current + [word])
            if len(candidate) > max_chars and current:
                lines.append(" ".join(current)); current = [word]
            else:
                current.append(word)
        if current:
            lines.append(" ".join(current))
        return lines[:4]

    def _dashboard(self, scene: Scene, width: int, margin: int) -> str:
        d = scene.dashboard; p = self.palette
        font = self.config.style.font_family
        metric_fill = p["dark"] if scene.background == "dark" else p["paper"]
        metric_text = p["white"] if scene.background == "dark" else p["ink"]
        marker = p["warning"] if d.severity in {"critical", "warning"} else p["primary"]
        return (
            f'<g id="dashboard">'
            f'<rect x="{margin}" y="{margin}" width="300" height="120" fill="{p["paper"]}" stroke="{p["ink"]}" stroke-width="4"/>'
            f'<text x="{margin + 18}" y="{margin + 30}" font-family="{font}" font-size="20" font-weight="700" fill="{p["ink"]}">{html.escape(d.experiment_id)}</text>'
            f'<text x="{margin + 18}" y="{margin + 60}" font-family="{font}" font-size="17" fill="{p["ink"]}">{html.escape(d.status_label)}:</text>'
            f'<text x="{margin + 18}" y="{margin + 90}" font-family="{font}" font-size="22" font-weight="700" fill="{p["ink"]}">{html.escape(d.status_value)}</text>'
            f'<rect x="{width - margin - 300}" y="{margin}" width="300" height="100" fill="{metric_fill}" stroke="{p["ink"]}" stroke-width="4"/>'
            f'<polygon points="{width - margin - 285},{margin + 78} {width - margin - 270},{margin + 42} {width - margin - 255},{margin + 78}" fill="{marker}"/>'
            f'<text x="{width - margin - 235}" y="{margin + 35}" font-family="{font}" font-size="18" font-weight="700" fill="{metric_text}">{html.escape(d.metric_label)}</text>'
            f'<text x="{width - margin - 235}" y="{margin + 74}" font-family="{font}" font-size="30" font-weight="800" fill="{metric_text}">{html.escape(d.metric_value)}</text>'
            f'</g>'
        )

    def compose_scene(self, scene: Scene, asset_paths: dict[str, Path], output_path: Path, canvas_width: int, canvas_height: int) -> Path:
        p = self.palette; margin = self.config.canvas.safe_margin
        font = self.config.style.font_family
        background = p["dark"] if scene.background == "dark" else p["paper"]
        foreground = p["white"] if scene.background == "dark" else p["ink"]
        groups = []
        for obj in sorted(scene.objects, key=lambda item: item.z_index):
            path = asset_paths.get(obj.asset_id)
            if not path:
                continue
            x, y, w, h = obj.x * canvas_width, obj.y * canvas_height, obj.width * canvas_width, obj.height * canvas_height
            scale = self.config.svg.asset_viewbox
            groups.append(
                f'<g id="{html.escape(obj.asset_id)}" opacity="{obj.opacity}" '
                f'transform="translate({x + w / 2:.2f} {y + h / 2:.2f}) rotate({obj.rotation:.2f}) '
                f'translate({-w / 2:.2f} {-h / 2:.2f}) scale({w / scale:.6f} {h / scale:.6f})">'
                f'{self._inner_svg(path)}</g>'
            )
        lines = self._wrap(scene.title or (scene.text[0] if scene.text else ""))
        tspans = "".join(
            f'<tspan x="{canvas_width / 2:.1f}" dy="{0 if i == 0 else 72}">{html.escape(line.upper())}</tspan>'
            for i, line in enumerate(lines)
        )
        svg = (
            f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{canvas_width}" height="{canvas_height}" viewBox="0 0 {canvas_width} {canvas_height}">\n'
            f'<rect width="{canvas_width}" height="{canvas_height}" fill="{background}"/>{self._dashboard(scene, canvas_width, margin)}\n'
            f'<text x="{canvas_width / 2}" y="220" text-anchor="middle" font-family="{font}" font-size="64" font-weight="800" fill="{foreground}" letter-spacing="-1.5">{tspans}</text>\n'
            f'{"".join(groups)}\n'
            f'<text x="{margin}" y="{canvas_height - margin}" font-family="{font}" font-size="20" fill="{foreground}" opacity="0.65">{html.escape(scene.scene_id.upper())}</text>\n'
            f'</svg>'
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(output_path, svg)
        return output_path

    def compose_contact_sheet(self, scene_paths: list[Path], output_path: Path, columns: int = 3, thumb_width: int = 360, thumb_height: int = 640, gap: int = 16) -> Path:
        """A single SVG grid embedding each scene as a data-URI thumbnail."""
        rows = (len(scene_paths) + columns - 1) // columns
        width = columns * thumb_width + (columns + 1) * gap
        height = rows * thumb_height + (rows + 1) * gap
        images = []
        for index, path in enumerate(scene_paths):
            row, col = divmod(index, columns)
            x = gap + col * (thumb_width + gap)
            y = gap + row * (thumb_height + gap)
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            images.append(f'<image x="{x}" y="{y}" width="{thumb_width}" height="{thumb_height}" href="data:image/svg+xml;base64,{encoded}"/>')
        atomic_write_text(output_path, f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="{width}" height="{height}" fill="#D7DBDD"/>{"".join(images)}</svg>')
        return output_path

    def compose_preview_html(self, scene_paths: list[Path], output_path: Path) -> Path:
        """Self-contained HTML gallery of the storyboard (openable in any browser)."""
        cards = []
        for path in scene_paths:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            cards.append(f'<article><img src="data:image/svg+xml;base64,{encoded}"><p>{html.escape(path.name)}</p></article>')
        document = (
            '<!doctype html><html><head><meta charset="utf-8"><title>Storyboard Preview</title>'
            '<style>body{font-family:Arial;background:#eceff1;padding:24px}'
            'main{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:18px}'
            'article{background:white;border:1px solid #b0bec5;padding:10px}'
            'img{width:100%}p{font-weight:700}</style></head><body><main>'
            f'{"".join(cards)}</main></body></html>'
        )
        atomic_write_text(output_path, document)
        return output_path

    def rasterize(self, svg_path: Path, output_path: Path, output_width: int | None = None) -> Path | None:
        """Optional CairoSVG rasterization for reliable inline previews.

        Returns None (and logs) if CairoSVG/Cairo is unavailable, so the pipeline
        never hard-fails just because a preview couldn't be rasterized.
        """
        try:
            import cairosvg
        except Exception as exc:
            self.logger.warning("CairoSVG unavailable, skipping raster preview: %s", exc)
            return None
        try:
            cairosvg.svg2png(url=str(svg_path), write_to=str(output_path), output_width=output_width)
            return output_path
        except Exception as exc:
            self.logger.warning("Rasterization failed for %s: %s", svg_path, exc)
            return None
