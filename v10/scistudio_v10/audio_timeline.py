"""Audio-first timeline — direct the motion to the narration, not to guesses.

Instead of "scene -> estimated duration -> voice-over", build the timeline from
the audio:

    Script -> voice (per beat) -> word-level timestamps -> beat/emphasis
    detection -> a per-scene timeline the animation director snaps motion to.

The audio engine already produces per-beat voice + estimated word timings and
voice-aligned scene durations. This assembles them into an explicit timeline:
each scene gets its absolute ``audio`` window and a list of ``beats`` — impact/
emphasis moments detected from the beat's ``emphasis_words`` (located in the word
timing), sound effects and end-of-line pauses — each with an absolute time, a
scene-relative frame, a type and an intensity. The animation director then snaps
the primary motion (a punch, a state change) to the strongest impact beat so the
action lands on the word, and effects/particles fire on their beat.

Deterministic and offline: it reads the audio manifest the pipeline already
writes; it never invents timing it does not have (estimated timings are marked).
"""

from __future__ import annotations

import re
from typing import Any

from .utils import ensure_dir, save_json


def _norm(word: str) -> str:
    return re.sub(r"[^a-z0-9']", "", str(word).lower())


class AudioTimeline:
    def __init__(self, config: dict[str, Any] | None = None, root: Any = None):
        self.config = config or {}
        self.root = ensure_dir(root) if root is not None else None
        self.scenes: dict[str, dict[str, Any]] = {}
        self.fps = 30

    def build(self, storyboard: Any, script: Any, audio_manifest: dict[str, Any], fps: int) -> dict[str, Any]:
        self.fps = int(fps or 30)
        beats_by_id = {getattr(b, "beat_id", str(i)): b for i, b in enumerate(getattr(script, "beats", []) or [])}
        word_timing = (audio_manifest or {}).get("word_timing", {}) or {}
        cursor = 0.0
        out: dict[str, dict[str, Any]] = {}
        for scene in getattr(storyboard, "scenes", []) or []:
            sid = getattr(scene, "scene_id", "")
            dur = float(getattr(scene, "duration_s", 0.0) or 0.0)
            bid = getattr(scene, "beat_id", "")
            beat = beats_by_id.get(bid)
            words = list(word_timing.get(bid, []) or [])
            events = self._detect_beats(scene, beat, words, scene_start=cursor, duration=dur)
            out[sid] = {
                "audio": {"start": round(cursor, 3), "end": round(cursor + dur, 3), "duration": round(dur, 3)},
                "beats": events,
                "words": words,
            }
            cursor += dur
        self.scenes = out
        result = {"fps": self.fps, "total_duration": round(cursor, 3), "scenes": out}
        if self.root is not None:
            save_json(self.root / "audio_timeline.json", result)
        return result

    def _detect_beats(
        self, scene: Any, beat: Any, words: list[dict], scene_start: float, duration: float
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        emphasis = {_norm(w) for w in (getattr(beat, "emphasis_words", []) or [])} if beat else set()
        word_index = {}
        for w in words:
            word_index.setdefault(_norm(w.get("word", "")), w)
        # Emphasis words -> impact beats at their spoken timestamp.
        for token in emphasis:
            w = word_index.get(token)
            if not w:
                continue
            t = float(w.get("start_s", 0.0))
            intensity = min(1.0, 0.5 + len(token) / 16.0)
            events.append(self._beat(scene_start + t, t, "impact", intensity, token, duration))
        # A sound effect on the beat -> an accent at the line start.
        if beat and getattr(beat, "sfx", ""):
            events.append(self._beat(scene_start, 0.0, "accent", 0.7, str(getattr(beat, "sfx", "")), duration))
        # End-of-line pause -> a settle/hold beat.
        pause = float(getattr(beat, "pause_after_ms", 0) or 0) / 1000.0 if beat else 0.0
        if pause > 0.15 and duration > 0:
            t = max(0.0, duration - pause)
            events.append(self._beat(scene_start + t, t, "settle", 0.4, "pause", duration))
        # If nothing was detected, place one impact at the emphasis centre (~40%),
        # so the shot still has an authored landing beat rather than a fixed 20%.
        if not events and duration > 0:
            t = duration * 0.4
            events.append(self._beat(scene_start + t, t, "impact", 0.6, "", duration))
        events.sort(key=lambda e: e["time"])
        return events

    def _beat(
        self, abs_t: float, scene_t: float, kind: str, intensity: float, word: str, duration: float
    ) -> dict[str, Any]:
        frame = int(round(scene_t * self.fps))
        return {
            "time": round(abs_t, 3),
            "scene_time": round(scene_t, 3),
            "frame": frame,
            "type": kind,
            "intensity": round(intensity, 3),
            "word": word,
            "estimated": True,
        }

    def primary_impact_frame(self, scene_id: str, duration_frames: int) -> int | None:
        """The frame of the strongest impact beat in a scene (for snapping the
        primary motion), clamped inside the shot."""
        scene = self.scenes.get(scene_id)
        if not scene:
            return None
        impacts = [b for b in scene["beats"] if b["type"] == "impact"]
        if not impacts:
            return None
        best = max(impacts, key=lambda b: b["intensity"])
        return max(0, min(int(duration_frames) - 1, int(best["frame"])))

    def scene_beats(self, scene_id: str) -> list[dict[str, Any]]:
        return list(self.scenes.get(scene_id, {}).get("beats", []) or [])
