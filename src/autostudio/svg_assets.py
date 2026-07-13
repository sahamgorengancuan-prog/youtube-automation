from __future__ import annotations

import math
from pathlib import Path
from typing import Callable

import svgwrite

from .config import StyleConfig
from .schemas import AssetRequirement


class AssetFactory:
    """Deterministic, flat-vector SVG primitives.

    Each method emits a single 1000x1000 illustration built only from geometric
    shapes, flat fills, and solid strokes (no gradients, textures, or raster).
    The LLM never writes SVG; it only names an ``asset_type`` from a whitelist,
    which makes every asset reproducible and cacheable by content hash.
    """

    VERSION = "svg-template-v1"

    def __init__(self, style: StyleConfig, viewbox: int = 1000):
        self.style = style
        self.palette = style.palette
        self.viewbox = viewbox
        self.stroke = style.stroke_width * 2.0

    def _drawing(self) -> svgwrite.Drawing:
        return svgwrite.Drawing(size=("100%", "100%"), viewBox=f"0 0 {self.viewbox} {self.viewbox}")

    def _planet(self, req: AssetRequirement) -> svgwrite.Drawing:
        dwg = self._drawing()
        p = self.palette
        dwg.add(dwg.circle(center=(500, 500), r=340, fill=p["white"], stroke=p["ink"], stroke_width=self.stroke))
        for path in [
            "M280,350 C340,250 460,250 500,330 C450,380 390,410 330,430 Z",
            "M520,270 C650,250 760,340 720,430 C650,390 610,350 520,360 Z",
            "M440,520 C520,470 640,500 650,590 C590,650 520,700 450,650 Z",
            "M240,520 C330,470 390,520 390,600 C330,640 280,610 240,570 Z",
        ]:
            dwg.add(dwg.path(d=path, fill=p["secondary"], stroke=p["ink"], stroke_width=self.stroke))
        return dwg

    def _moon(self, req: AssetRequirement) -> svgwrite.Drawing:
        dwg = self._drawing(); p = self.palette
        dwg.add(dwg.circle(center=(500, 500), r=320, fill=p["white"], stroke=p["ink"], stroke_width=self.stroke))
        for cx, cy, r in [(390, 380, 70), (610, 520, 95), (430, 650, 55), (640, 330, 40)]:
            dwg.add(dwg.circle(center=(cx, cy), r=r, fill=p["paper"], stroke=p["secondary"], stroke_width=self.stroke * .65))
        return dwg

    def _sun(self, req: AssetRequirement) -> svgwrite.Drawing:
        dwg = self._drawing(); p = self.palette
        for angle in range(0, 360, 30):
            rad = math.radians(angle)
            dwg.add(dwg.line((500 + math.cos(rad) * 300, 500 + math.sin(rad) * 300), (500 + math.cos(rad) * 420, 500 + math.sin(rad) * 420), stroke=p["ink"], stroke_width=self.stroke, stroke_linecap="round"))
        dwg.add(dwg.circle(center=(500, 500), r=220, fill=p["sun"], stroke=p["ink"], stroke_width=self.stroke))
        return dwg

    def _human(self, req: AssetRequirement) -> svgwrite.Drawing:
        dwg = self._drawing(); p = self.palette
        dwg.add(dwg.circle(center=(500, 260), r=105, fill=p["white"], stroke=p["ink"], stroke_width=self.stroke))
        dwg.add(dwg.path(d="M380,420 Q500,350 620,420 L680,720 Q500,820 320,720 Z", fill=p["secondary"], stroke=p["ink"], stroke_width=self.stroke))
        for start, end in [((390, 470), (220, 650)), ((610, 470), (780, 650)), ((430, 720), (380, 930)), ((570, 720), (620, 930))]:
            dwg.add(dwg.line(start, end, stroke=p["ink"], stroke_width=self.stroke * 1.5, stroke_linecap="round"))
        for cx in (465, 535):
            dwg.add(dwg.circle(center=(cx, 245), r=9, fill=p["ink"]))
        return dwg

    def _cloud(self, req: AssetRequirement) -> svgwrite.Drawing:
        dwg = self._drawing(); p = self.palette
        group = dwg.g(fill=p["white"], stroke=p["ink"], stroke_width=self.stroke)
        for cx, cy, r in [(320, 560, 150), (470, 450, 190), (650, 520, 165), (770, 600, 120)]:
            group.add(dwg.circle(center=(cx, cy), r=r))
        group.add(dwg.rect(insert=(250, 550), size=(560, 210), rx=100, ry=100)); dwg.add(group); return dwg

    def _building(self, req: AssetRequirement) -> svgwrite.Drawing:
        dwg = self._drawing(); p = self.palette
        dwg.add(dwg.rect(insert=(260, 170), size=(480, 700), fill=p["secondary"], stroke=p["ink"], stroke_width=self.stroke))
        for row in range(5):
            for col in range(3):
                dwg.add(dwg.rect(insert=(330 + col * 130, 250 + row * 110), size=(65, 65), fill=p["primary"], stroke=p["ink"], stroke_width=self.stroke * .55))
        dwg.add(dwg.rect(insert=(445, 720), size=(110, 150), fill=p["paper"], stroke=p["ink"], stroke_width=self.stroke)); return dwg

    def _arrow(self, req: AssetRequirement) -> svgwrite.Drawing:
        dwg = self._drawing(); p = self.palette
        dwg.add(dwg.path(d="M120,430 H650 V250 L900,500 L650,750 V570 H120 Z", fill=p["primary"], stroke=p["ink"], stroke_width=self.stroke)); return dwg

    def _wave(self, req: AssetRequirement) -> svgwrite.Drawing:
        dwg = self._drawing(); p = self.palette
        dwg.add(dwg.path(d="M80,620 C220,450 350,790 500,610 C650,430 790,740 930,560 L930,850 L80,850 Z", fill=p["primary"], stroke=p["ink"], stroke_width=self.stroke)); return dwg

    def _tree(self, req: AssetRequirement) -> svgwrite.Drawing:
        dwg = self._drawing(); p = self.palette
        dwg.add(dwg.rect(insert=(450, 500), size=(100, 350), fill=p["secondary"], stroke=p["ink"], stroke_width=self.stroke))
        for cx, cy, r in [(360, 430, 170), (520, 330, 200), (680, 450, 170)]:
            dwg.add(dwg.circle(center=(cx, cy), r=r, fill=p["primary"], stroke=p["ink"], stroke_width=self.stroke))
        return dwg

    def _rock(self, req: AssetRequirement) -> svgwrite.Drawing:
        dwg = self._drawing(); p = self.palette
        dwg.add(dwg.polygon([(200, 650), (280, 350), (520, 180), (760, 330), (850, 650), (650, 840), (330, 820)], fill=p["secondary"], stroke=p["ink"], stroke_width=self.stroke)); return dwg

    def _black_hole(self, req: AssetRequirement) -> svgwrite.Drawing:
        dwg = self._drawing(); p = self.palette
        dwg.add(dwg.circle(center=(500, 500), r=190, fill=p["dark"], stroke=p["ink"], stroke_width=self.stroke))
        for radius, width in [(280, 55), (350, 35)]:
            dwg.add(dwg.ellipse(center=(500, 500), r=(radius, radius * .42), fill="none", stroke=p["primary"], stroke_width=width))
        return dwg

    def _atom(self, req: AssetRequirement) -> svgwrite.Drawing:
        dwg = self._drawing(); p = self.palette
        for rotation in (0, 60, 120):
            dwg.add(dwg.ellipse(center=(500, 500), r=(360, 150), fill="none", stroke=p["primary"], stroke_width=self.stroke, transform=f"rotate({rotation} 500 500)"))
        dwg.add(dwg.circle(center=(500, 500), r=85, fill=p["warning"], stroke=p["ink"], stroke_width=self.stroke)); return dwg

    def _rocket(self, req: AssetRequirement) -> svgwrite.Drawing:
        dwg = self._drawing(); p = self.palette
        dwg.add(dwg.path(d="M500,100 C690,260 680,650 500,800 C320,650 310,260 500,100 Z", fill=p["white"], stroke=p["ink"], stroke_width=self.stroke))
        dwg.add(dwg.circle(center=(500, 400), r=95, fill=p["primary"], stroke=p["ink"], stroke_width=self.stroke))
        dwg.add(dwg.polygon([(430, 790), (500, 940), (570, 790)], fill=p["warning"], stroke=p["ink"], stroke_width=self.stroke)); return dwg

    def _battery(self, req: AssetRequirement) -> svgwrite.Drawing:
        dwg = self._drawing(); p = self.palette
        dwg.add(dwg.rect(insert=(210, 260), size=(580, 520), rx=45, ry=45, fill=p["white"], stroke=p["ink"], stroke_width=self.stroke))
        dwg.add(dwg.rect(insert=(420, 180), size=(160, 90), fill=p["secondary"], stroke=p["ink"], stroke_width=self.stroke))
        dwg.add(dwg.polygon([(520, 320), (390, 540), (500, 540), (450, 710), (620, 470), (510, 470)], fill=p["warning"], stroke=p["ink"], stroke_width=self.stroke)); return dwg

    def _computer(self, req: AssetRequirement) -> svgwrite.Drawing:
        dwg = self._drawing(); p = self.palette
        dwg.add(dwg.rect(insert=(140, 160), size=(720, 500), rx=35, ry=35, fill=p["white"], stroke=p["ink"], stroke_width=self.stroke))
        dwg.add(dwg.rect(insert=(200, 220), size=(600, 370), fill=p["primary"], stroke=p["ink"], stroke_width=self.stroke))
        dwg.add(dwg.rect(insert=(450, 660), size=(100, 160), fill=p["secondary"], stroke=p["ink"], stroke_width=self.stroke)); return dwg

    def _clock(self, req: AssetRequirement) -> svgwrite.Drawing:
        dwg = self._drawing(); p = self.palette
        dwg.add(dwg.circle(center=(500, 500), r=350, fill=p["white"], stroke=p["ink"], stroke_width=self.stroke))
        dwg.add(dwg.line((500, 500), (500, 280), stroke=p["ink"], stroke_width=self.stroke * 1.5))
        dwg.add(dwg.line((500, 500), (690, 610), stroke=p["primary"], stroke_width=self.stroke * 1.5)); return dwg

    def _thermometer(self, req: AssetRequirement) -> svgwrite.Drawing:
        dwg = self._drawing(); p = self.palette
        dwg.add(dwg.rect(insert=(420, 160), size=(160, 560), rx=80, ry=80, fill=p["white"], stroke=p["ink"], stroke_width=self.stroke))
        dwg.add(dwg.circle(center=(500, 760), r=170, fill=p["warning"], stroke=p["ink"], stroke_width=self.stroke)); return dwg

    def _star(self, req: AssetRequirement) -> svgwrite.Drawing:
        dwg = self._drawing(); p = self.palette; points = []
        for index in range(10):
            angle = math.radians(-90 + index * 36); radius = 330 if index % 2 == 0 else 145
            points.append((500 + math.cos(angle) * radius, 500 + math.sin(angle) * radius))
        dwg.add(dwg.polygon(points, fill=p["sun"], stroke=p["ink"], stroke_width=self.stroke)); return dwg

    def _generic_object(self, req: AssetRequirement) -> svgwrite.Drawing:
        dwg = self._drawing(); p = self.palette
        dwg.add(dwg.polygon([(250, 250), (700, 180), (840, 520), (650, 830), (250, 760), (130, 430)], fill=p["secondary"], stroke=p["ink"], stroke_width=self.stroke)); return dwg

    # Types that map onto an existing primitive rather than owning a bespoke shape.
    ALIASES = {
        "dashboard": "computer", "animal": "human", "satellite": "rocket",
        "cell": "atom", "molecule": "atom", "volcano": "mountain",
        "mountain": "rock", "tornado": "arrow", "robot": "computer",
        "dna": "atom", "car": "generic_object", "airplane": "rocket",
        "shield": "generic_object",
    }

    def generate(self, requirement: AssetRequirement, output_path: Path) -> Path:
        asset_type = self.ALIASES.get(requirement.asset_type, requirement.asset_type)
        method: Callable[[AssetRequirement], svgwrite.Drawing] = getattr(self, f"_{asset_type}", self._generic_object)
        drawing = method(requirement)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        drawing.saveas(str(output_path), pretty=True)
        return output_path
