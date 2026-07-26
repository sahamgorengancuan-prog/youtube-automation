"""Audio alignment — the generated narration is the timeline source of truth.

Scene timings are derived from transcript word timestamps by fuzzy-matching each
scene's approved sentences; the final scene end equals the narration duration.
Scenes shorter than the minimum trigger merge; overlaps are structural errors."""

from __future__ import annotations

import difflib
import re

from ..exceptions import AlignmentError
from ..schemas import AlignmentSegment, SceneSpec, SceneTiming

MIN_SCENE_S = 2.4


def _norm_word(word: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", word.lower())


def align_scenes(
    scenes: list[SceneSpec],
    segments: list[AlignmentSegment],
    narration_duration_s: float,
    min_scene_s: float = MIN_SCENE_S,
    reveal_hold_s: float = 0.0,
) -> list[SceneTiming]:
    words = [w for seg in segments for w in seg.words]
    if not words:
        raise AlignmentError("transcript contains no word timestamps", stage="alignment")
    flat = [_norm_word(w.word) for w in words]

    timings: list[SceneTiming] = []
    cursor = 0
    for i, scene in enumerate(scenes):
        target = [_norm_word(t) for t in scene.narration.split() if _norm_word(t)]
        if not target:
            raise AlignmentError(f"scene {scene.scene_id} has empty narration", stage="alignment")
        window = flat[cursor:]
        matcher = difflib.SequenceMatcher(None, window, target)
        match = matcher.find_longest_match(0, len(window), 0, len(target))
        if match.size == 0:
            start_idx = cursor
            end_idx = min(cursor + len(target), len(words)) - 1
        else:
            start_idx = cursor + match.a - match.b
            start_idx = max(cursor, start_idx)
            end_idx = min(start_idx + len(target), len(words)) - 1
        start_s = words[start_idx].start_s if i > 0 else 0.0
        end_s = words[min(end_idx, len(words) - 1)].end_s
        cursor = min(end_idx + 1, len(words) - 1)
        timings.append(SceneTiming(scene_id=scene.scene_id, start_s=round(start_s, 3), end_s=round(end_s, 3)))

    # Stitch: monotonic, contiguous, no overlap; final end == narration duration.
    for i in range(1, len(timings)):
        timings[i].start_s = timings[i - 1].end_s
        if timings[i].end_s <= timings[i].start_s:
            timings[i].end_s = round(timings[i].start_s + min_scene_s, 3)
    timings[-1].end_s = round(narration_duration_s + reveal_hold_s, 3)

    # Merge scenes below the minimum into the previous compatible one.
    merged: list[SceneTiming] = []
    for t in timings:
        if merged and (t.end_s - t.start_s) < min_scene_s:
            merged[-1].end_s = t.end_s
        else:
            merged.append(t)
    if merged and merged[0].start_s != 0.0:
        merged[0].start_s = 0.0

    for a, b in zip(merged, merged[1:]):
        if b.start_s < a.end_s - 1e-6:
            raise AlignmentError(f"scene overlap: {a.scene_id} and {b.scene_id}", stage="alignment")
        if b.start_s - a.end_s > 1.5:
            raise AlignmentError(
                f"impossible gap {b.start_s - a.end_s:.2f}s between {a.scene_id} and {b.scene_id}",
                stage="alignment",
            )
    if abs(merged[-1].end_s - (narration_duration_s + reveal_hold_s)) > 0.05:
        raise AlignmentError("final scene end must equal narration duration", stage="alignment")
    return merged
