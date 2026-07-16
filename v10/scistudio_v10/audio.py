"""Audio engine: Edge-TTS/espeak narration and procedural support bed."""

from __future__ import annotations

import shutil
import re
from pathlib import Path
from typing import Any

from .schemas import ScriptPackage, Storyboard
from .utils import ensure_dir, ffprobe_duration, run_command, save_json


class AudioEngine:
    """Notebook-safe audio engine.

    Edge TTS is invoked through its CLI in a separate process. This avoids the
    `asyncio.run() cannot be called from a running event loop` failure in Colab.
    """

    def __init__(self, config: dict[str, Any], root: str | Path):
        self.config = config
        self.root = ensure_dir(root)

    def synthesize(self, text: str, output: str | Path) -> Path:
        output = Path(output)
        if output.exists() and output.stat().st_size > 1000:
            return output
        text_path = output.with_suffix(".txt")
        text_path.write_text(text.strip(), encoding="utf-8")
        edge_binary = shutil.which("edge-tts")
        if edge_binary:
            try:
                run_command(
                    [
                        edge_binary,
                        "--voice",
                        str(self.config.get("voice", "en-US-GuyNeural")),
                        "--rate",
                        str(self.config.get("rate", "+6%")),
                        "--pitch",
                        str(self.config.get("pitch", "+0Hz")),
                        "--file",
                        str(text_path),
                        "--write-media",
                        str(output),
                    ],
                    timeout=180,
                )
            except Exception as exc:
                print(f"[audio] Edge TTS failed; using local fallback: {exc}")
        if not output.exists() or output.stat().st_size < 1000:
            self._fallback_tts(text, output)
        text_path.unlink(missing_ok=True)
        return output

    def _fallback_tts(self, text: str, output: Path) -> None:
        wav = output.with_suffix(".wav")
        if not shutil.which("espeak-ng"):
            # Last-resort preview path: keep the pipeline executable even on a
            # minimal runtime, but mark the voice as silent so publishing QC can
            # never approve it as a finished institutional output.
            duration = max(0.8, len(re.findall(r"[A-Za-z0-9']+", text)) / 2.5)
            run_command(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    f"anullsrc=r=48000:cl=mono:d={duration}",
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "128k",
                    str(output),
                ]
            )
            output.with_suffix(output.suffix + ".silent").write_text(
                "TTS unavailable; silent preview voice generated.", encoding="utf-8"
            )
            return
        run_command(
            ["espeak-ng", "-v", str(self.config.get("fallback_voice", "en-us")), "-s", "170", "-w", str(wav), text]
        )
        run_command(["ffmpeg", "-y", "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "192k", str(output)])
        wav.unlink(missing_ok=True)

    def build(self, script: ScriptPackage, storyboard: Storyboard) -> tuple[ScriptPackage, Storyboard, dict[str, Any]]:
        voice_root = ensure_dir(self.root / "voice")
        voice_files = {}
        word_timing: dict[str, list[dict[str, Any]]] = {}
        updated_beats = []
        updated_scenes = []
        for index, beat in enumerate(script.beats):
            voice = self.synthesize(beat.spoken_line, voice_root / f"{beat.beat_id.lower()}.mp3")
            actual = ffprobe_duration(voice)
            duration = round(max(beat.duration_s, actual + beat.pause_after_ms / 1000 + 0.2), 3)
            updated_beats.append(beat.model_copy(update={"duration_s": duration}))
            scene = storyboard.scenes[index] if index < len(storyboard.scenes) else storyboard.scenes[-1]
            updated_scenes.append(scene.model_copy(update={"duration_s": duration, "narration": beat.spoken_line}))
            voice_files[beat.beat_id] = str(voice)
            word_timing[beat.beat_id] = self._word_timing(beat.spoken_line, actual)
        total = round(sum(scene.duration_s for scene in updated_scenes), 3)
        script = script.model_copy(update={"beats": updated_beats, "estimated_duration_s": total})
        storyboard = storyboard.model_copy(update={"scenes": updated_scenes, "estimated_duration_s": total})
        scene_starts = []
        _cursor = 0.0
        for scene in updated_scenes:
            scene_starts.append(round(_cursor, 3))
            _cursor += scene.duration_s
        bed = self._support_bed(total, self.root / "support_bed.m4a", scene_starts=scene_starts)
        silent_voice_files = [
            path for path in voice_files.values() if Path(path).with_suffix(Path(path).suffix + ".silent").exists()
        ]
        manifest = {
            "voice": voice_files,
            "support_bed": str(bed),
            "duration_s": total,
            "word_timing": word_timing,
            "voice_silent_preview": bool(silent_voice_files),
            "warnings": ["TTS unavailable; silent preview voice was generated."] if silent_voice_files else [],
        }
        save_json(self.root / "audio_manifest.json", manifest)
        return script, storyboard, manifest

    @staticmethod
    def _word_timing(text: str, duration: float) -> list[dict[str, Any]]:
        """Create deterministic local word timing for choreography.

        Edge-TTS media is measured with ffprobe. When provider word-boundary
        events are unavailable, duration is distributed by spoken-word weight;
        punctuation receives a small pause. The manifest clearly stores these
        as estimated timings so the orchestrator can sync events without
        pretending they are forced-alignment ground truth.
        """
        tokens = re.findall(r"[A-Za-z0-9']+|[.,!?;:]", text)
        words = [token for token in tokens if re.match(r"[A-Za-z0-9']", token)]
        if not words:
            return []
        weights = [max(1.0, len(word) ** 0.55) for word in words]
        total_weight = sum(weights)
        usable = max(0.1, float(duration))
        cursor = 0.0
        output: list[dict[str, Any]] = []
        for index, (word, weight) in enumerate(zip(words, weights)):
            segment = usable * weight / total_weight
            start = cursor
            end = min(usable, cursor + segment)
            output.append(
                {
                    "word": word,
                    "start_s": round(start, 4),
                    "end_s": round(end, 4),
                    "estimated": True,
                    "index": index,
                }
            )
            cursor = end
        if output:
            output[-1]["end_s"] = round(usable, 4)
        return output

    def _support_bed(self, duration: float, output: Path, scene_starts: list[float] | None = None) -> Path:
        """Designed music bed (not a flat drone): a minor-triad pad with slow
        tremolo movement, a filtered 'air' layer, a soft ~0.9 Hz sub pulse, and
        short filtered-noise whooshes synced to each scene start. All procedural
        via FFmpeg lavfi — no external assets, no licensing."""
        if output.exists() and output.stat().st_size > 1000 and not self.config.get("force_audio", False):
            return output
        fade_out = max(0.0, duration - 2.0)
        starts = [s for s in (scene_starts or []) if 0.05 < s < duration - 0.05]

        inputs = [
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=110:sample_rate=48000:duration={duration}",  # root A2
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=130.81:sample_rate=48000:duration={duration}",  # minor third C3
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=164.81:sample_rate=48000:duration={duration}",  # fifth E3
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=55:sample_rate=48000:duration={duration}",  # sub A1 (pulsed)
            "-f",
            "lavfi",
            "-i",
            f"anoisesrc=color=pink:sample_rate=48000:duration={duration}",  # air
        ]
        parts = [
            "[0:a]volume=0.020,tremolo=f=0.10:d=0.5,lowpass=f=360[p0]",
            "[1:a]volume=0.013,tremolo=f=0.13:d=0.5,lowpass=f=520[p1]",
            "[2:a]volume=0.012,tremolo=f=0.11:d=0.4,lowpass=f=680[p2]",
            "[3:a]volume=0.030,tremolo=f=0.9:d=0.9,lowpass=f=140[sub]",  # heartbeat pulse
            "[4:a]volume=0.006,highpass=f=120,lowpass=f=1400[air]",
            "[p0][p1][p2][sub][air]amix=inputs=5:normalize=0[bed]",
        ]
        # Whooshes at scene transitions.
        whoosh_labels = []
        wh_input_start = 5
        for i, start in enumerate(starts):
            inputs += ["-f", "lavfi", "-i", "anoisesrc=color=white:sample_rate=48000:duration=0.6"]
            lbl = f"wh{i}"
            delay = int(max(0, (start - 0.18) * 1000))
            parts.append(
                f"[{wh_input_start + i}:a]volume=0.10,highpass=f=300,lowpass=f=5000,"
                f"afade=t=in:st=0:d=0.12,afade=t=out:st=0.22:d=0.35,adelay={delay}|{delay}[{lbl}]"
            )
            whoosh_labels.append(f"[{lbl}]")

        if whoosh_labels:
            parts.append("[bed]" + "".join(whoosh_labels) + f"amix=inputs={1 + len(whoosh_labels)}:normalize=0[mixed]")
            final_in = "[mixed]"
        else:
            final_in = "[bed]"
        parts.append(f"{final_in}alimiter=limit=0.72,afade=t=in:st=0:d=1.2,afade=t=out:st={fade_out}:d=2[out]")
        run_command(
            [
                "ffmpeg",
                "-y",
                *inputs,
                "-filter_complex",
                ";".join(parts),
                "-map",
                "[out]",
                "-t",
                str(duration),
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                str(output),
            ]
        )
        return output
