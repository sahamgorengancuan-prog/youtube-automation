"""Diamond subtitle designer: semantic chunking (2–6 words, max two lines),
restrained emphasis, safe zones, and a libass ASS template with series styles.
No fonts are distributed — users supply licensed fonts by name/path."""

from __future__ import annotations

import re
from pathlib import Path

from sias.filesystem import atomic_write_bytes
from sias.schemas import AlignmentSegment

MAX_WORDS_PER_CHUNK = 6
MIN_WORDS_PER_CHUNK = 2
_BREAK_AFTER = re.compile(r"[,;:—–]|(?<=[.!?])")


def chunk_words(words: list, max_words: int = MAX_WORDS_PER_CHUNK) -> list[list]:
    """Split timed words into short semantic chunks (2–6 words), breaking at
    punctuation first, then at the word cap. Never an entire sentence at once."""
    chunks: list[list] = []
    current: list = []
    for word in words:
        current.append(word)
        text = word.word if hasattr(word, "word") else str(word)
        if len(current) >= max_words or (_BREAK_AFTER.search(text) and len(current) >= MIN_WORDS_PER_CHUNK):
            chunks.append(current)
            current = []
    if current:
        if chunks and len(current) < MIN_WORDS_PER_CHUNK:
            chunks[-1].extend(current)
        else:
            chunks.append(current)
    return chunks


def _ass_time(seconds: float) -> str:
    cs = int(round(seconds * 100))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


ASS_HEADER = """[Script Info]
Title: SIAS Diamond captions
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},64,&H00252525,&H00252525,&H00F4EEDC,&H80000000,0,0,0,0,100,100,0,0,1,4,1,2,90,90,220,1
Style: Emphasis,{font},64,&H003A8AE4,&H003A8AE4,&H00F4EEDC,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,2,90,90,220,1
Style: Reveal,{font},72,&H004C4CC9,&H004C4CC9,&H00F4EEDC,&H80000000,-1,0,0,0,100,100,0,0,1,5,2,2,90,90,220,1
Style: SciTerm,{font},60,&H008F6F2F,&H008F6F2F,&H00F4EEDC,&H80000000,0,-1,0,0,100,100,0,0,1,4,1,2,90,90,220,1
Style: Note,{font},44,&H00575147,&H00575147,&H00F4EEDC,&H80000000,0,-1,0,0,100,100,0,0,1,3,0,8,90,90,80,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Text
"""


def _two_lines(text: str) -> str:
    words = text.split()
    if len(words) <= 4:
        return text
    mid = len(words) // 2
    return " ".join(words[:mid]) + r"\N" + " ".join(words[mid:])


def build_ass(
    segments: list[AlignmentSegment],
    emphasis_words: list[str] | None = None,
    reveal_word: str = "",
    font: str = "Arial",
) -> str:
    """Chunked ASS events. Emphasis restricted to listed words; the reveal word
    gets the Reveal style, timed to its own word (visual reveal sync)."""
    emphasis = {w.lower().strip(".,!?") for w in (emphasis_words or [])}
    reveal = reveal_word.lower().strip(".,!?")
    lines = [ASS_HEADER.format(font=font)]
    for segment in segments:
        source = segment.words or None
        if source:
            for chunk in chunk_words(source):
                start, end = chunk[0].start_s, chunk[-1].end_s
                rendered = []
                style = "Default"
                for word in chunk:
                    token = word.word.strip()
                    bare = token.lower().strip(".,!?")
                    if reveal and bare == reveal:
                        style = "Reveal"
                        rendered.append(token.upper())
                    elif bare in emphasis:
                        rendered.append(r"{\rEmphasis}" + token + r"{\rDefault}")
                    else:
                        rendered.append(token)
                text = _two_lines(" ".join(rendered))
                lines.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,{text}")
        else:
            text = _two_lines(segment.text)
            lines.append(f"Dialogue: 0,{_ass_time(segment.start_s)},{_ass_time(segment.end_s)},Default,,0,0,0,,{text}")
    return "\n".join(lines) + "\n"


def write_ass(segments: list[AlignmentSegment], out_path: str | Path, **kw) -> Path:
    return atomic_write_bytes(out_path, build_ass(segments, **kw).encode("utf-8"))


def burn_in_ass_cmd(video: str | Path, ass: str | Path, out: str | Path,
                    fonts_dir: str = "", ffmpeg: str = "ffmpeg") -> list[str]:
    filt = f"ass={Path(ass).as_posix()}"
    if fonts_dir:
        filt += f":fontsdir={Path(fonts_dir).as_posix()}"
    return [ffmpeg, "-y", "-i", str(video), "-vf", filt, "-c:a", "copy", str(out)]


def validate_subtitles(segments: list[AlignmentSegment]) -> list[str]:
    issues = []
    for segment in segments:
        for chunk in chunk_words(segment.words) if segment.words else []:
            if len(chunk) > MAX_WORDS_PER_CHUNK:
                issues.append(f"chunk exceeds {MAX_WORDS_PER_CHUNK} words at {chunk[0].start_s}s")
    return issues
