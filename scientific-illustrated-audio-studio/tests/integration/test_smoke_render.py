"""Local render smoke test: Pillow placeholder images + a sine-wave WAV →
FFmpeg clips → concat → mux → validated MP4. Placeholder assets are for this
renderer test ONLY — production mode rejects them (see qc.visual)."""

import math
import shutil
import struct
import wave
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from sias.render.ffmpeg import render_episode
from sias.render.validator import detect_black_frames, validate_final
from sias.schemas import RenderScene

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")


def _placeholder(path: Path, label: str, color: str) -> Path:
    img = Image.new("RGB", (540, 960), "#F4EEDC")
    d = ImageDraw.Draw(img)
    d.rectangle((60, 120, 480, 840), outline="#252525", width=6)
    d.ellipse((180, 300, 360, 480), fill=color)
    d.text((80, 60), label, fill="#252525")
    img.save(path)
    return path


def _sine_wav(path: Path, seconds: float, rate: int = 16000) -> Path:
    n = int(seconds * rate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"".join(
            struct.pack("<h", int(0.4 * 32767 * math.sin(2 * math.pi * 330 * i / rate))) for i in range(n)
        ))
    return path


def test_smoke_render_end_to_end(tmp_path):
    imgs = [
        _placeholder(tmp_path / "s1.png", "SMOKE S1", "#2F6F8F"),
        _placeholder(tmp_path / "s2.png", "SMOKE S2", "#E48A3A"),
        _placeholder(tmp_path / "s3.png", "SMOKE S3", "#5F8A55"),
    ]
    audio = _sine_wav(tmp_path / "narration.wav", seconds=7.5)
    scenes = [
        RenderScene(scene_id="S01", image_path=str(imgs[0]), start_s=0.0, end_s=2.5, motion="slow_push_in"),
        RenderScene(scene_id="S02", image_path=str(imgs[1]), start_s=2.5, end_s=5.0, motion="hold"),
        RenderScene(scene_id="S03", image_path=str(imgs[2]), start_s=5.0, end_s=7.5, motion="pan_right"),
    ]
    out = render_episode(scenes, audio, tmp_path / "work", tmp_path / "pilot.mp4",
                         width=540, height=960, fps=12)
    report = validate_final(out, narration_duration_s=7.5, tolerance_s=0.25)
    assert report["has_video"] and report["has_audio"]
    assert report["duration_delta_s"] <= 0.25
    assert detect_black_frames(out) == []
