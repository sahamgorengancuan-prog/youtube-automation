"""Captions: SRT from alignment segments (max two lines, phrase chunks) and an
optional burn-in command with a configurable font path. No bundled fonts."""

from __future__ import annotations

from pathlib import Path

from ..filesystem import atomic_write_bytes
from ..schemas import AlignmentSegment

MAX_LINE_CHARS = 38


def _timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _two_lines(text: str) -> str:
    words = text.split()
    if len(text) <= MAX_LINE_CHARS or len(words) < 4:
        return text
    mid = len(words) // 2
    return " ".join(words[:mid]) + "\n" + " ".join(words[mid:])


def build_srt(segments: list[AlignmentSegment], reveal_word: str = "") -> str:
    blocks: list[str] = []
    for i, seg in enumerate(segments, start=1):
        text = seg.text.strip()
        if reveal_word and reveal_word.lower() in text.lower():
            # Highlight the reveal word (uppercase emphasis; SRT-safe).
            text = " ".join(w.upper() if w.lower().strip(".,!?") == reveal_word.lower() else w for w in text.split())
        blocks.append(f"{i}\n{_timestamp(seg.start_s)} --> {_timestamp(seg.end_s)}\n{_two_lines(text)}\n")
    return "\n".join(blocks)


def write_srt(segments: list[AlignmentSegment], out_path: str | Path, reveal_word: str = "") -> Path:
    return atomic_write_bytes(out_path, build_srt(segments, reveal_word).encode("utf-8"))


def burn_in_cmd(video: str | Path, srt: str | Path, out_path: str | Path, font_path: str = "", ffmpeg: str = "ffmpeg") -> list[str]:
    sub_filter = f"subtitles={Path(srt).as_posix()}"
    if font_path:
        sub_filter += f":fontsdir={Path(font_path).parent.as_posix()}"
    return [ffmpeg, "-y", "-i", str(video), "-vf", sub_filter, "-c:a", "copy", str(out_path)]
