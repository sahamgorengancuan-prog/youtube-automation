from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

from .config import StudioConfig
from .hashing import atomic_write_json, file_sha256, hash_value, read_json
from .logging_utils import configure_logging
from .schemas import AudioClip, AudioTimeline, Storyboard

try:  # Allows re-entrant event loops inside notebooks; optional at import time.
    import nest_asyncio

    nest_asyncio.apply()
except Exception:  # pragma: no cover - nest_asyncio missing outside notebooks
    pass


def run_command(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    """Thin, loud subprocess wrapper. Surfaces the tail of stderr on failure."""
    result = subprocess.run(command, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(command)}\n{result.stderr[-4000:]}")
    return result


def media_duration(path: Path) -> float:
    result = run_command([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    return float(result.stdout.strip())


class VoiceoverEngine:
    """Edge TTS (free, online) with an offline espeak-ng fallback. Clips are
    content-addressed, so identical narration is never re-synthesised."""

    def __init__(self, config: StudioConfig, cache_root: Path):
        self.config = config
        self.cache_dir = cache_root / "audio"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.logger = configure_logging("autostudio.voice")

    async def _edge_tts(self, text: str, output_path: Path) -> None:
        import edge_tts

        communicate = edge_tts.Communicate(
            text=text, voice=self.config.voice.voice,
            rate=self.config.voice.rate, pitch=self.config.voice.pitch,
        )
        await communicate.save(str(output_path))

    def _run_async(self, coroutine) -> None:
        try:
            loop = asyncio.get_event_loop()
            loop.run_until_complete(coroutine)
        except RuntimeError:
            asyncio.run(coroutine)

    def _synthesize_edge(self, text: str, output_path: Path) -> None:
        last_error: Exception | None = None
        for _ in range(2):
            try:
                self._run_async(self._edge_tts(text, output_path))
                if output_path.exists() and output_path.stat().st_size >= 1000:
                    return
                raise RuntimeError("Edge TTS produced no valid audio.")
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"Edge TTS failed after two attempts: {last_error}")

    def _synthesize_local(self, text: str, output_path: Path) -> None:
        if not shutil.which("espeak-ng"):
            raise RuntimeError("espeak-ng is not installed.")
        wav_path = output_path.with_suffix(".wav")
        run_command([
            "espeak-ng", "-v", self.config.voice.local_voice,
            "-s", str(self.config.voice.local_words_per_minute),
            "-w", str(wav_path), text,
        ])
        run_command([
            "ffmpeg", "-y", "-i", str(wav_path), "-ar", str(self.config.voice.sample_rate),
            "-c:a", "aac", "-b:a", "160k", str(output_path),
        ])
        wav_path.unlink(missing_ok=True)

    def synthesize_clip(self, scene_id: str, text: str) -> tuple[Path, str, str]:
        key = hash_value({
            "text": text, "voice": self.config.voice.voice,
            "rate": self.config.voice.rate, "pitch": self.config.voice.pitch,
            "tail": self.config.voice.scene_tail_silence,
        })
        final_path = self.cache_dir / f"{key}.m4a"
        metadata_path = self.cache_dir / f"{key}.json"
        if final_path.exists() and metadata_path.exists():
            metadata = read_json(metadata_path, {})
            return final_path, str(metadata.get("provider", "cache")), key

        raw_path = self.cache_dir / f"{key}.raw.mp3"
        provider = "edge-tts"
        try:
            self._synthesize_edge(text, raw_path)
        except Exception as exc:
            self.logger.warning("Edge TTS failed for %s: %s. Using espeak-ng.", scene_id, exc)
            provider = "espeak-ng"
            raw_path = self.cache_dir / f"{key}.raw.m4a"
            self._synthesize_local(text, raw_path)

        run_command([
            "ffmpeg", "-y", "-i", str(raw_path),
            "-af", f"apad=pad_dur={self.config.voice.scene_tail_silence},aresample={self.config.voice.sample_rate}",
            "-c:a", "aac", "-b:a", "160k", str(final_path),
        ])
        raw_path.unlink(missing_ok=True)
        atomic_write_json(metadata_path, {
            "provider": provider, "text": text, "audio_hash": key,
            "duration_s": media_duration(final_path), "file_sha256": file_sha256(final_path),
        })
        return final_path, provider, key

    def build_timeline(self, storyboard: Storyboard, run_dir: Path) -> tuple[AudioTimeline, Storyboard]:
        audio_dir = run_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        clips: list[AudioClip] = []
        updated_scenes = []
        cursor = 0.0
        clip_paths: list[Path] = []

        for scene in storyboard.scenes:
            cached_path, provider, audio_hash = self.synthesize_clip(scene.scene_id, scene.narration)
            destination = audio_dir / f"{scene.scene_id}.m4a"
            if not destination.exists() or file_sha256(destination) != file_sha256(cached_path):
                shutil.copy2(cached_path, destination)
            duration = media_duration(destination)
            clips.append(AudioClip(
                scene_id=scene.scene_id, text=scene.narration, path=str(destination),
                start_s=round(cursor, 3), end_s=round(cursor + duration, 3),
                duration_s=round(duration, 3), provider=provider, audio_hash=audio_hash,
            ))
            # The audio duration is authoritative: the scene stretches to match it.
            updated_scenes.append(scene.model_copy(update={"duration_s": round(duration, 3)}))
            cursor += duration
            clip_paths.append(destination)

        concat_path = audio_dir / "voice_concat.txt"
        concat_path.write_text("\n".join(f"file '{path.as_posix()}'" for path in clip_paths), encoding="utf-8")
        voice_track = audio_dir / "voice_track.m4a"
        run_command([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_path),
            "-c:a", "aac", "-b:a", "192k", "-ar", str(self.config.voice.sample_rate), str(voice_track),
        ])
        actual_duration = media_duration(voice_track)
        timeline = AudioTimeline(
            clips=clips, voice_track=str(voice_track), duration_s=round(actual_duration, 3),
            timeline_hash=hash_value([clip.model_dump(mode="json") for clip in clips]),
        )
        updated_storyboard = storyboard.model_copy(update={"scenes": updated_scenes, "estimated_duration_s": round(actual_duration, 3)})
        atomic_write_json(audio_dir / "timeline.json", timeline)
        return timeline, updated_storyboard


class AudioMixer:
    """Procedural music bed + per-beat sound cues, side-chain ducked under the
    voice and loudness-normalised. Everything is synthesised with FFmpeg lavfi —
    no external audio assets, no licensing."""

    def __init__(self, config: StudioConfig):
        self.config = config
        self.logger = configure_logging("autostudio.audio_mixer")

    def build_music_bed(self, duration: float, output_path: Path) -> Path:
        fade_out = max(0.0, duration - 2.0)
        run_command([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"sine=frequency=110:sample_rate=48000:duration={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=48000:duration={duration}",
            "-f", "lavfi", "-i", f"anoisesrc=color=pink:sample_rate=48000:duration={duration}",
            "-filter_complex",
            f"[0:a]volume=0.035[a0];[1:a]volume=0.018[a1];"
            f"[2:a]volume=0.006,highpass=f=100,lowpass=f=1200[a2];"
            f"[a0][a1][a2]amix=inputs=3:normalize=0,"
            f"afade=t=in:st=0:d=1.2,afade=t=out:st={fade_out}:d=2,alimiter=limit=0.85[a]",
            "-map", "[a]", "-c:a", "aac", "-b:a", "128k", str(output_path),
        ])
        return output_path

    def build_sfx_track(self, timeline: AudioTimeline, output_path: Path) -> Path:
        duration = timeline.duration_s
        inputs: list[str] = []
        filter_parts: list[str] = []
        labels: list[str] = []
        for index, clip in enumerate(timeline.clips):
            delay = max(0, int(clip.start_s * 1000))
            frequency = 520 if index % 3 == 0 else 360
            inputs.extend(["-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration=0.10"])
            label = f"s{index}"
            filter_parts.append(f"[{index}:a]volume=0.10,afade=t=out:st=0.03:d=0.07,adelay={delay}|{delay}[{label}]")
            labels.append(f"[{label}]")
        if not labels:
            run_command(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={duration}", str(output_path)])
            return output_path
        filter_parts.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0,apad=pad_dur={duration}[mix]")
        run_command([
            "ffmpeg", "-y", *inputs, "-filter_complex", ";".join(filter_parts),
            "-map", "[mix]", "-t", str(duration), "-c:a", "aac", "-b:a", "128k", str(output_path),
        ])
        return output_path

    def mix(self, timeline: AudioTimeline, run_dir: Path) -> Path:
        audio_dir = run_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        voice = Path(timeline.voice_track)
        duration = timeline.duration_s
        music = audio_dir / "background_music.m4a"
        sfx = audio_dir / "sound_cues.m4a"
        final_audio = audio_dir / "final_audio.m4a"

        if self.config.audio.background_enabled:
            self.build_music_bed(duration, music)
        else:
            run_command(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={duration}", str(music)])
        if self.config.audio.sfx_enabled:
            self.build_sfx_track(timeline, sfx)
        else:
            run_command(["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={duration}", str(sfx)])

        run_command([
            "ffmpeg", "-y", "-i", str(voice), "-i", str(music), "-i", str(sfx),
            "-filter_complex",
            f"[1:a]volume={self.config.audio.music_volume}[music];"
            f"[2:a]volume={self.config.audio.sfx_volume}[sfx];"
            f"[music][0:a]sidechaincompress=threshold=0.025:ratio=8:attack=20:release=250[ducked];"
            f"[0:a][ducked][sfx]amix=inputs=3:weights=1 1 1:normalize=0,"
            f"loudnorm=I={self.config.audio.voice_loudness_lufs}:TP=-1.5:LRA=9[a]",
            "-map", "[a]", "-t", str(duration), "-c:a", "aac", "-b:a", "192k", str(final_audio),
        ])
        return final_audio
