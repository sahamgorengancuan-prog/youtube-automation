"""Local render smoke test: 4 watermarked placeholders + sine WAV → 4 clips →
concat → mux → ffprobe validation. Also proves production QC REJECTS the
placeholders. Writes the MP4 under workspace_smoke/. Exit non-zero on failure."""

from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT.parent / "scientific-illustrated-audio-studio" / "src"))

from sias.render.ffmpeg import render_episode  # noqa: E402
from sias.render.validator import detect_black_frames, validate_final  # noqa: E402
from sias.schemas import RenderScene  # noqa: E402
from sias_colab.qc.placeholders import make_placeholder, reject_placeholders_in_production  # noqa: E402


def _sine(path: Path, seconds: float, rate: int = 16000) -> Path:
    n = int(seconds * rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"".join(
            struct.pack("<h", int(0.4 * 32767 * math.sin(2 * math.pi * 300 * i / rate))) for i in range(n)))
    return path


def main() -> int:
    work = ROOT / "workspace_smoke"
    work.mkdir(exist_ok=True)
    images = [make_placeholder(work / f"scene_{i}.png", f"SMOKE S{i}") for i in range(1, 5)]
    audio = _sine(work / "narration.wav", 10.0)
    scenes = [
        RenderScene(scene_id=f"S{i:02d}", image_path=str(img), start_s=(i - 1) * 2.5, end_s=i * 2.5,
                    motion=m)
        for i, (img, m) in enumerate(zip(images, ["slow_push_in", "hold", "pan_right", "slow_pull_out"]), start=1)
    ]
    out = render_episode(scenes, audio, work / "clips", work / "smoke.mp4", width=540, height=960, fps=12)
    report = validate_final(out, narration_duration_s=10.0, tolerance_s=0.25)
    black = detect_black_frames(out)
    rejected = reject_placeholders_in_production([str(i) for i in images], production_mode=True)
    fails = [c for c in rejected if c.status == "FAIL"]
    print("video:", out)
    print("streams:", report["has_video"], report["has_audio"], "| duration Δ:", report["duration_delta_s"],
          "| size:", report["size_bytes"], "| black frames:", black)
    print("production rejects placeholders:", len(fails), "of", len(images))
    ok = report["has_video"] and report["has_audio"] and not black and len(fails) == 4
    print("SMOKE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
