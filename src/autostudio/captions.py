from __future__ import annotations

import re
from pathlib import Path

from .config import StudioConfig
from .hashing import atomic_write_json, atomic_write_text
from .schemas import AudioTimeline, CaptionSegment


def srt_time(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def ass_time(seconds: float) -> str:
    centiseconds = int(round(seconds * 100))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, cs = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


class CaptionGenerator:
    """Word-timed captions in both SRT and safe-zone ASS. Chunks narration into
    short karaoke-style lines and distributes each clip's duration by word count."""

    def __init__(self, config: StudioConfig):
        self.config = config

    def _chunks(self, text: str) -> list[str]:
        words = text.split()
        chunks: list[str] = []
        current: list[str] = []
        for word in words:
            current.append(word)
            joined = " ".join(current)
            if (
                len(current) >= self.config.captions.max_words
                or len(joined) >= self.config.captions.max_chars
                or re.search(r"[.!?]$", word)
            ):
                chunks.append(joined)
                current = []
        if current:
            chunks.append(" ".join(current))
        return chunks

    def segments(self, timeline: AudioTimeline) -> list[CaptionSegment]:
        output: list[CaptionSegment] = []
        index = 1
        for clip in timeline.clips:
            chunks = self._chunks(clip.text)
            weights = [max(1, len(chunk.split())) for chunk in chunks]
            total = max(1, sum(weights))
            cursor = clip.start_s
            for chunk, weight in zip(chunks, weights):
                chunk_duration = clip.duration_s * weight / total
                output.append(CaptionSegment(
                    index=index, text=chunk, start_s=round(cursor, 3),
                    end_s=round(min(clip.end_s, cursor + chunk_duration), 3), scene_id=clip.scene_id,
                ))
                cursor += chunk_duration
                index += 1
        return output

    def write(self, timeline: AudioTimeline, run_dir: Path) -> tuple[Path, Path, list[CaptionSegment]]:
        caption_dir = run_dir / "captions"
        caption_dir.mkdir(parents=True, exist_ok=True)
        segments = self.segments(timeline)

        srt_lines: list[str] = []
        for segment in segments:
            srt_lines.extend([
                str(segment.index),
                f"{srt_time(segment.start_s)} --> {srt_time(segment.end_s)}",
                segment.text, "",
            ])
        srt_path = caption_dir / "captions.srt"
        atomic_write_text(srt_path, "\n".join(srt_lines))

        cfg = self.config.captions
        ass_header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {self.config.render.width}
PlayResY: {self.config.render.height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{cfg.font_name},{cfg.font_size},{cfg.primary_color},&H0000D7FF,{cfg.outline_color},{cfg.back_color},-1,0,0,0,100,100,0,0,3,2,0,2,{cfg.margin_left},{cfg.margin_right},{cfg.margin_vertical},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        events = []
        for segment in segments:
            safe = segment.text.replace("{", "(").replace("}", ")")
            events.append(
                f"Dialogue: 0,{ass_time(segment.start_s)},{ass_time(segment.end_s)},Default,,0,0,0,,"
                f"{{\\fad(60,60)}}{safe}"
            )
        ass_path = caption_dir / "captions.ass"
        atomic_write_text(ass_path, ass_header + "\n".join(events))
        atomic_write_json(caption_dir / "segments.json", [segment.model_dump(mode="json") for segment in segments])
        return srt_path, ass_path, segments
