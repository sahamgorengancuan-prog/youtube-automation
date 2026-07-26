"""Transcription of the GENERATED narration (the alignment target), plus a
similarity gate against the approved script."""

from __future__ import annotations

import difflib
import re
from typing import Any

from ..schemas import AlignmentSegment, AlignmentWord


def parse_transcription(raw: dict[str, Any]) -> list[AlignmentSegment]:
    segments: list[AlignmentSegment] = []
    words = [
        AlignmentWord(word=w.get("word", ""), start_s=float(w.get("start", 0)), end_s=float(w.get("end", 0)))
        for w in raw.get("words", [])
    ]
    for seg in raw.get("segments", []):
        s, e = float(seg.get("start", 0)), float(seg.get("end", 0))
        segments.append(
            AlignmentSegment(
                text=str(seg.get("text", "")).strip(),
                start_s=s,
                end_s=e,
                words=[w for w in words if s - 1e-6 <= w.start_s < e + 1e-6],
            )
        )
    if not segments and words:
        segments = [AlignmentSegment(text=" ".join(w.word for w in words), start_s=words[0].start_s, end_s=words[-1].end_s, words=words)]
    return segments


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", text.lower().replace("—", " ").replace("-", " "))


def transcript_similarity(approved_text: str, transcript_text: str) -> float:
    return difflib.SequenceMatcher(None, _normalize(approved_text), _normalize(transcript_text)).ratio()
