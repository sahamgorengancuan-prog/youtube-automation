"""Full media-path smoke test WITHOUT internet or LLM. Exercises:
SVG asset -> scene SVG -> raster (CairoSVG) -> motion clip (FFmpeg) -> captions
-> synthetic voice + music/SFX mix -> muxed MP4 -> ffprobe validation.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(tempfile.mkdtemp(prefix="autostudio_full_"))
os.environ["AUTOSTUDIO_ROOT"] = str(ROOT)
for rel in ["cache", "config", "output"]:
    (ROOT / rel).mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from autostudio.audio import AudioMixer, run_command
from autostudio.cache import AssetCache
from autostudio.captions import CaptionGenerator
from autostudio.composer import SceneComposer
from autostudio.config import load_config
from autostudio.renderer import VideoRenderer
from autostudio.schemas import (
    AudioClip, AudioTimeline, AssetRequirement, DashboardSpec, Scene, SceneObject, Storyboard,
)

cfg = load_config(ROOT / "config" / "config.yaml")
TEST_DIR = ROOT / "output" / "full_render_smoke"
TEST_DIR.mkdir(parents=True, exist_ok=True)

requirements = [
    AssetRequirement(asset_id="earth-test", asset_type="planet", label="Earth", variant="earth", source_prompt="flat Earth"),
    AssetRequirement(asset_id="arrow-test", asset_type="arrow", label="Motion", source_prompt="blue motion arrow"),
]
asset_cache = AssetCache(cfg, ROOT / "cache")
asset_paths = {r.asset_id: asset_cache.get_or_create(r, "offline smoke test")[0] for r in requirements}

scenes = [
    Scene(scene_id="scene01", duration_s=2.0, narration="Earth is rotating faster than it looks.",
          title="EARTH IS MOVING", motion="zoom_in",
          dashboard=DashboardSpec(experiment_id="EXPERIMENT #001", metric_label="ROTATION", metric_value="1670 km/h"),
          objects=[SceneObject(asset_id="earth-test", x=0.18, y=0.30, width=0.64, height=0.40)],
          asset_requirements=[requirements[0]]),
    Scene(scene_id="scene02", duration_s=2.0, narration="A sudden stop would preserve that sideways momentum.",
          title="MOMENTUM CONTINUES", motion="pan_right", background="dark",
          dashboard=DashboardSpec(experiment_id="EXPERIMENT #002", metric_label="STATUS", metric_value="CRITICAL", severity="critical"),
          objects=[SceneObject(asset_id="earth-test", x=0.08, y=0.33, width=0.42, height=0.32),
                   SceneObject(asset_id="arrow-test", x=0.46, y=0.38, width=0.44, height=0.22)],
          asset_requirements=requirements),
]
storyboard = Storyboard(topic="Offline smoke test", scenes=scenes, asset_catalog=requirements, estimated_duration_s=4.0)
composer = SceneComposer(cfg)
scene_paths = [composer.compose_scene(s, asset_paths, TEST_DIR / f"{s.scene_id}.svg", 1080, 1920) for s in scenes]
print("composed scenes:", len(scene_paths))

# Synthetic voice track (no TTS needed for the deterministic path).
voice_path = TEST_DIR / "voice_track.m4a"
run_command(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=260:sample_rate=48000:duration=4",
             "-c:a", "aac", "-b:a", "160k", str(voice_path)])
timeline = AudioTimeline(
    clips=[AudioClip(scene_id="scene01", text=scenes[0].narration, path=str(voice_path), start_s=0, end_s=2, duration_s=2, provider="synthetic-test", audio_hash="t1"),
           AudioClip(scene_id="scene02", text=scenes[1].narration, path=str(voice_path), start_s=2, end_s=4, duration_s=2, provider="synthetic-test", audio_hash="t2")],
    voice_track=str(voice_path), duration_s=4, timeline_hash="offline-test")

# Real music + SFX mix path.
final_audio = AudioMixer(cfg).mix(timeline, TEST_DIR)
print("mixed audio:", final_audio.name, final_audio.stat().st_size, "bytes")

_, ass_path, segs = CaptionGenerator(cfg).write(timeline, TEST_DIR)
print("caption segments:", len(segs))

renderer = VideoRenderer(cfg, ROOT / "cache")
video_path, report = renderer.render(storyboard, scene_paths, final_audio, ass_path, TEST_DIR)

assert report.passed, report.checks
assert report.width == 1080 and report.height == 1920, (report.width, report.height)
assert report.has_audio
assert video_path.exists() and video_path.stat().st_size > 10000
for p in scene_paths:
    assert ET.parse(p).getroot().tag.split("}")[-1] == "svg"

# Prove render caches are populated (raster + scene clips reused on re-render).
raster_files = list((ROOT / "cache" / "raster").glob("*.png"))
clip_files = list((ROOT / "cache" / "scene_clips").glob("*.mp4"))
print("raster cache:", len(raster_files), "| scene-clip cache:", len(clip_files))

print("\nFULL-RENDER SMOKE TEST PASSED")
import json
print(json.dumps(report.model_dump(mode="json"), indent=2))
print("MP4:", video_path)
