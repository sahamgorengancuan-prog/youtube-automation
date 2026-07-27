"""FFmpeg baseline renderer: deterministic per-scene clips (zoom ≤4%, pan ≤3%),
concat, audio mux. Command builders are pure functions (unit-testable); the
executor captures output and raises RenderError with detail on failure."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..exceptions import RenderError
from ..schemas import RenderScene


def _motion_filter(motion: str, width: int, height: int, fps: int, duration_s: float, max_zoom_pct: float, max_pan_pct: float) -> str:
    frames = max(1, int(round(duration_s * fps)))
    zoom_target = 1.0 + max_zoom_pct / 100.0
    zoom_step = (zoom_target - 1.0) / frames
    base = f"scale={width * 2}:{height * 2}:flags=lanczos"
    size = f"s={width}x{height}"
    if motion == "slow_push_in":
        return f"{base},zoompan=z='min(zoom+{zoom_step:.6f},{zoom_target:.4f})':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':{size}:fps={fps}"
    if motion == "slow_pull_out":
        return f"{base},zoompan=z='max({zoom_target:.4f}-{zoom_step:.6f}*on,1.0)':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':{size}:fps={fps}"
    if motion in ("pan_left", "pan_right"):
        span = (max_pan_pct / 100.0) * width * 2
        direction = f"(iw-iw/zoom)/2-{span:.1f}*on/{frames}" if motion == "pan_left" else f"(iw-iw/zoom)/2+{span:.1f}*on/{frames}"
        return f"{base},zoompan=z='1.031':d={frames}:x='{direction}':y='ih/2-(ih/zoom/2)':{size}:fps={fps}"
    # hold / label_reveal / page_turn fall back to a stable hold clip.
    return f"scale={width}:{height}:flags=lanczos,fps={fps}"


MAX_CLIP_SECONDS = 900.0  # a still-image clip longer than this is a bug, not a design


def quantized_frames(start_s: float, end_s: float, fps: int) -> int:
    """Frame count derived from CUMULATIVE timeline positions, not from the
    clip's own length.

    Rounding each clip independently loses up to half a frame per scene, and
    across a 7-scene episode that compounds into a video measurably shorter than
    the narration — which the final duration gate (±0.08s) then rejects. Taking
    the difference of two rounded absolute positions keeps the sum of all clips
    equal to the rounded total, so the drift cannot accumulate.
    """
    return max(1, int(round(end_s * fps)) - int(round(start_s * fps)))


def clip_cmd(
    scene: RenderScene,
    out_path: str | Path,
    width: int,
    height: int,
    fps: int,
    max_zoom_pct: float = 4.0,
    max_pan_pct: float = 3.0,
    ffmpeg: str = "ffmpeg",
) -> list[str]:
    duration = max(0.1, scene.end_s - scene.start_s)
    if duration > MAX_CLIP_SECONDS:
        # Guards against a corrupt upstream duration (e.g. a streamed-WAV header
        # reporting a phantom 24-hour narration) burning the whole render budget.
        raise RenderError(
            f"scene {scene.scene_id}: clip duration {duration:.1f}s exceeds the "
            f"{MAX_CLIP_SECONDS:.0f}s sanity cap — upstream timing is wrong; "
            "check the narration duration before rendering",
            stage="render.clip",
        )
    frames = quantized_frames(scene.start_s, scene.end_s, fps)
    duration = frames / float(fps)
    vf = _motion_filter(scene.motion, width, height, fps, duration, max_zoom_pct, max_pan_pct)
    return [
        ffmpeg,
        "-y",
        "-loop",
        "1",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(scene.image_path),
        "-vf",
        vf + ",format=yuv420p",
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        # Exact frame count, so concatenating the clips reproduces the timeline
        # to the frame instead of drifting a few hundredths of a second short.
        "-frames:v",
        str(frames),
        str(out_path),
    ]


def concat_cmd(list_file: str | Path, out_path: str | Path, ffmpeg: str = "ffmpeg") -> list[str]:
    return [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_path)]


def mux_cmd(video: str | Path, audio: str | Path, out_path: str | Path, ffmpeg: str = "ffmpeg") -> list[str]:
    return [
        ffmpeg,
        "-y",
        "-i",
        str(video),
        "-i",
        str(audio),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(out_path),
    ]


def run(cmd: list[str], stage: str, timeout: float = 1800.0) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise RenderError(f"ffmpeg failed (exit {proc.returncode}): {detail}", stage=stage)


def render_episode(
    scenes: list[RenderScene],
    audio_path: str | Path,
    work_dir: str | Path,
    out_path: str | Path,
    width: int,
    height: int,
    fps: int,
    max_zoom_pct: float = 4.0,
    max_pan_pct: float = 3.0,
) -> Path:
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    clip_paths: list[Path] = []
    for scene in scenes:
        clip = work / f"clip_{scene.scene_id}.mp4"
        run(clip_cmd(scene, clip, width, height, fps, max_zoom_pct, max_pan_pct), stage=f"render.clip.{scene.scene_id}")
        clip_paths.append(clip)
    list_file = work / "concat.txt"
    list_file.write_text("".join(f"file '{p.as_posix()}'\n" for p in clip_paths), encoding="utf-8")
    silent = work / "video_noaudio.mp4"
    run(concat_cmd(list_file, silent), stage="render.concat")
    run(mux_cmd(silent, audio_path, out_path), stage="render.mux")
    return Path(out_path)
