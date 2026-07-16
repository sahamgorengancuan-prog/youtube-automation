from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps

from .utils import ensure_dir, save_json


class StyleReferenceExtractor:
    """Extracts a compact visual board from a user-provided reference video.

    The board is used as a visual-language anchor only. Drawing briefs explicitly
    prohibit copying its content, composition, labels or exact objects.
    """

    def __init__(self, root: str | Path, config: dict[str, Any] | None = None):
        self.root = ensure_dir(root)
        self.config = config or {}

    def extract(self, video_path: str | Path, *, force: bool = False) -> dict[str, Any]:
        video_path = Path(video_path)
        if not video_path.exists():
            raise FileNotFoundError(video_path)
        board = self.root / "reference_style_board.png"
        profile_path = self.root / "reference_style_profile.json"
        if board.exists() and profile_path.exists() and not force:
            import json

            return json.loads(profile_path.read_text(encoding="utf-8"))

        frames_dir = ensure_dir(self.root / "frames")
        for old in frames_dir.glob("*.png"):
            old.unlink()
        duration = self._duration(video_path)
        sample_count = int(self.config.get("sample_count", 8))
        times = [duration * (i + 0.65) / sample_count for i in range(sample_count)]
        images: list[Image.Image] = []
        frame_paths: list[str] = []
        for index, second in enumerate(times):
            out = frames_dir / f"style_{index:02d}.png"
            self._frame(video_path, second, out)
            if out.exists():
                im = Image.open(out).convert("RGB")
                images.append(im)
                frame_paths.append(str(out))
        if not images:
            raise RuntimeError("Could not extract reference frames")

        self._contact_sheet(images, board)
        palette = self._palette(images)
        profile = {
            "video_path": str(video_path),
            "duration_s": duration,
            "frame_paths": frame_paths,
            "board_path": str(board),
            "palette": palette,
            "usage_rule": "Use visual language only. Never copy source composition, text, subjects or sequence.",
        }
        save_json(profile_path, profile)
        return profile

    @staticmethod
    def _duration(path: Path) -> float:
        if not shutil.which("ffprobe"):
            return 10.0
        command = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        try:
            return max(1.0, float(subprocess.check_output(command, text=True).strip()))
        except Exception:
            return 10.0

    @staticmethod
    def _frame(video: Path, second: float, output: Path) -> None:
        if not shutil.which("ffmpeg"):
            return
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-ss",
                f"{second:.3f}",
                "-i",
                str(video),
                "-frames:v",
                "1",
                "-vf",
                "scale=640:-2",
                str(output),
            ],
            check=False,
        )

    @staticmethod
    def _contact_sheet(images: list[Image.Image], output: Path) -> None:
        cols = 2
        cell_w, cell_h = 680, 400
        rows = math.ceil(len(images) / cols)
        sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), "#F2F2EF")
        draw = ImageDraw.Draw(sheet)
        for index, source in enumerate(images):
            card = Image.new("RGB", (cell_w - 18, cell_h - 18), "#FAFAF7")
            image = ImageOps.contain(source, (cell_w - 38, cell_h - 52))
            card.paste(image, ((card.width - image.width) // 2, 12))
            d = ImageDraw.Draw(card)
            d.text((14, card.height - 26), f"STYLE FRAME {index + 1:02d}", fill="#20282D")
            x = (index % cols) * cell_w + 9
            y = (index // cols) * cell_h + 9
            sheet.paste(card, (x, y))
            draw.rectangle([x, y, x + card.width, y + card.height], outline="#475157", width=2)
        output.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(output)

    @staticmethod
    def _palette(images: list[Image.Image], colors: int = 10) -> list[str]:
        strip = Image.new("RGB", (256, max(1, 128 * len(images))), "white")
        for index, image in enumerate(images):
            reduced = ImageOps.fit(image.convert("RGB"), (256, 128))
            strip.paste(reduced, (0, index * 128))
        quantized = strip.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
        palette = quantized.getpalette() or []
        counts = quantized.getcolors() or []
        counts.sort(reverse=True)
        output: list[str] = []
        for _, idx in counts:
            rgb = palette[idx * 3 : idx * 3 + 3]
            if len(rgb) == 3:
                output.append("#" + "".join(f"{int(v):02X}" for v in rgb))
        return output
