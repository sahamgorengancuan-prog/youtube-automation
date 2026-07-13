from __future__ import annotations

import json
from pathlib import Path

import cairosvg

from .audio import run_command
from .config import StudioConfig
from .hashing import atomic_write_json, file_sha256, hash_value
from .logging_utils import configure_logging
from .schemas import RenderReport, Storyboard


class VideoRenderer:
    """Static scene SVG -> PNG (CairoSVG) -> per-scene motion clip (FFmpeg
    zoompan) -> concatenated visuals -> muxed with audio + burned-in captions.
    Rasters and scene clips are cached by content hash; the final output is
    validated with ffprobe (resolution, fps, duration, audio stream)."""

    def __init__(self, config: StudioConfig, cache_root: Path):
        self.config = config
        self.raster_cache = cache_root / "raster"
        self.clip_cache = cache_root / "scene_clips"
        self.raster_cache.mkdir(parents=True, exist_ok=True)
        self.clip_cache.mkdir(parents=True, exist_ok=True)
        self.logger = configure_logging("autostudio.renderer")

    def rasterize(self, svg_path: Path) -> Path:
        key = hash_value({
            "svg_hash": file_sha256(svg_path),
            "width": self.config.render.width, "height": self.config.render.height,
        })
        png_path = self.raster_cache / f"{key}.png"
        if not png_path.exists():
            cairosvg.svg2png(
                url=str(svg_path), write_to=str(png_path),
                output_width=self.config.render.width, output_height=self.config.render.height,
            )
        return png_path

    def _motion_filter(self, motion: str, frames: int) -> str:
        width = self.config.render.width
        height = self.config.render.height
        frames = max(1, frames)
        motion = (motion or "hold").lower()
        if motion == "zoom_in":
            z = "min(zoom+0.0009,1.12)"; x = "(iw-iw/zoom)/2"; y = "(ih-ih/zoom)/2"
        elif motion == "zoom_out":
            z = "if(eq(on,1),1.12,max(zoom-0.0009,1.0))"; x = "(iw-iw/zoom)/2"; y = "(ih-ih/zoom)/2"
        elif motion == "pan_left":
            z = "1.08"; x = f"(iw-iw/zoom)*(1-on/{frames})"; y = "(ih-ih/zoom)/2"
        elif motion == "pan_right":
            z = "1.08"; x = f"(iw-iw/zoom)*(on/{frames})"; y = "(ih-ih/zoom)/2"
        elif motion == "pulse":
            z = "1.035+0.018*sin(on/7)"; x = "(iw-iw/zoom)/2"; y = "(ih-ih/zoom)/2"
        else:
            z = "min(zoom+0.00018,1.025)"; x = "(iw-iw/zoom)/2"; y = "(ih-ih/zoom)/2"
        fade_out_start = max(0.0, frames / self.config.render.fps - self.config.render.scene_fade_seconds)
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={self.config.render.fps},"
            f"fade=t=in:st=0:d={self.config.render.scene_fade_seconds},"
            f"fade=t=out:st={fade_out_start}:d={self.config.render.scene_fade_seconds},"
            "format=yuv420p"
        )

    def make_scene_clip(self, scene, png_path: Path) -> Path:
        frames = max(1, int(round(scene.duration_s * self.config.render.fps)))
        key = hash_value({
            "png_hash": file_sha256(png_path), "duration": round(scene.duration_s, 3),
            "motion": scene.motion, "fps": self.config.render.fps, "crf": self.config.render.crf,
        })
        clip_path = self.clip_cache / f"{key}.mp4"
        if clip_path.exists():
            return clip_path
        run_command([
            "ffmpeg", "-y", "-loop", "1", "-i", str(png_path),
            "-vf", self._motion_filter(scene.motion, frames),
            "-frames:v", str(frames), "-an", "-c:v", "libx264",
            "-preset", self.config.render.preset, "-crf", str(self.config.render.crf),
            "-pix_fmt", self.config.render.pixel_format, "-r", str(self.config.render.fps),
            str(clip_path),
        ])
        return clip_path

    def concatenate(self, clips: list[Path], output_path: Path) -> Path:
        concat_file = output_path.with_suffix(".txt")
        concat_file.write_text("\n".join(f"file '{path.as_posix()}'" for path in clips), encoding="utf-8")
        run_command([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-c", "copy", "-fflags", "+genpts", str(output_path),
        ])
        return output_path

    def render(self, storyboard: Storyboard, scene_paths: list[Path], audio_path: Path, ass_path: Path, run_dir: Path) -> tuple[Path, RenderReport]:
        if len(scene_paths) != len(storyboard.scenes):
            raise ValueError("Scene SVG count does not match storyboard scenes.")
        video_dir = run_dir / "video"
        video_dir.mkdir(parents=True, exist_ok=True)
        clips: list[Path] = []
        for scene, svg_path in zip(storyboard.scenes, scene_paths):
            png = self.rasterize(svg_path)
            clips.append(self.make_scene_clip(scene, png))
        visuals_path = self.concatenate(clips, video_dir / "visuals.mp4")

        final_path = video_dir / "short.mp4"
        ass_filter_path = ass_path.as_posix().replace("'", "\\'").replace(":", "\\:")
        run_command([
            "ffmpeg", "-y", "-i", str(visuals_path), "-i", str(audio_path),
            "-vf", f"ass='{ass_filter_path}'", "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", self.config.render.preset,
            "-crf", str(self.config.render.crf), "-pix_fmt", self.config.render.pixel_format,
            "-c:a", "aac", "-b:a", self.config.render.audio_bitrate, "-ar", "48000",
            "-movflags", "+faststart", "-shortest", str(final_path),
        ])
        report = self.validate(final_path)
        atomic_write_json(video_dir / "render_report.json", report)
        if self.config.render.validate_output and not report.passed:
            raise RuntimeError("Final video failed validation: " + "; ".join(report.checks))
        return final_path, report

    def validate(self, video_path: Path) -> RenderReport:
        probe = run_command([
            "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video_path),
        ])
        data = json.loads(probe.stdout)
        streams = data.get("streams", [])
        video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
        audio_stream = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
        width = int(video_stream.get("width") or 0)
        height = int(video_stream.get("height") or 0)
        frame_rate = str(video_stream.get("avg_frame_rate") or "0/1")
        numerator, denominator = [float(value) for value in frame_rate.split("/")]
        fps = numerator / denominator if denominator else 0.0
        duration = float((data.get("format") or {}).get("duration") or 0.0)
        checks: list[str] = []
        if width != self.config.render.width or height != self.config.render.height:
            checks.append(f"resolution={width}x{height}")
        if abs(fps - self.config.render.fps) > 0.5:
            checks.append(f"fps={fps:.2f}")
        if not audio_stream:
            checks.append("missing audio stream")
        if duration <= 1.0:
            checks.append(f"duration={duration:.2f}s")
        passed = not checks
        return RenderReport(
            video_path=str(video_path), width=width, height=height, fps=round(fps, 3),
            duration_s=round(duration, 3), video_codec=str(video_stream.get("codec_name") or ""),
            audio_codec=str(audio_stream.get("codec_name") or "") or None, has_audio=bool(audio_stream),
            file_size_bytes=video_path.stat().st_size, render_hash=file_sha256(video_path), passed=passed,
            checks=checks or ["resolution, fps, duration, video stream, and audio stream passed"],
        )
