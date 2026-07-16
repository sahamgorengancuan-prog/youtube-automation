from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .llm import LLMRouter
from .schemas import Beat, ResearchPack, SceneRequest, ScriptPackage, Storyboard
from .utils import ensure_dir, hash_value, load_json, save_json


class NarrativeDirector:
    SCRIPT_SYSTEM = """You are the narrative director of an institutional science-animation studio.
Return JSON only. Answer one hypothetical question through a direct, escalating causal chain.
Use short spoken sentences, concrete physical consequences, explicit uncertainty where inputs are unspecified,
and a concise payoff. Do not imitate or name another channel or studio."""

    STORY_SYSTEM = """You are a scene-level scientific storyboard director.
Return JSON only. Think in persistent worlds and causal state changes, not slides and not isolated asset lists.
For each beat, define the physical visual event, the scientific claim, the attention goal, and the exact change the
audience should notice. Do not choose an art style here; the Executive Art Director owns style."""

    def __init__(self, llm: LLMRouter, config: dict[str, Any], root: str | Path):
        self.llm = llm
        self.config = config
        self.root = ensure_dir(root)

    def script(self, research: ResearchPack, *, force: bool = False) -> ScriptPackage:
        cache = self.root / f"script-{research.research_hash or hash_value(research.topic)}.json"
        if cache.exists() and not force:
            return ScriptPackage.model_validate(load_json(cache))
        fallback = self._fallback_script(research.topic)
        raw = self.llm.generate_json(
            system=self.SCRIPT_SYSTEM,
            prompt=f"""Topic: {research.topic}
Research summary: {research.summary[:1400]}
Limitations: {json.dumps(research.limitations, ensure_ascii=False)}
Useful facts: {json.dumps([f.claim for f in research.facts[:10]], ensure_ascii=False)}

Create 7-9 beats for a 40-60 second science short. Each spoken line should generally stay below 18 words.
Each beat must include beat_id, time_stage, spoken_line, visual_event, retention_function and optional sfx.
Do not state a precise global outcome when the hypothetical omits intensity, location or boundary conditions.
Return a ScriptPackage-shaped JSON object.""",
            namespace="v10_script",
            fallback=fallback.model_dump(mode="json"),
            force=force,
        )
        script = self._normalize_script(raw, fallback, research.topic)
        save_json(cache, script)
        return script

    def storyboard(self, script: ScriptPackage, research: ResearchPack, *, force: bool = False) -> Storyboard:
        cache = self.root / f"storyboard-{script.script_hash}.json"
        if cache.exists() and not force:
            return Storyboard.model_validate(load_json(cache))
        fallback = self._fallback_storyboard(script)
        raw = self.llm.generate_json(
            system=self.STORY_SYSTEM,
            prompt=f"""Topic: {script.topic}
Script: {json.dumps(script.model_dump(mode="json"), ensure_ascii=False)}
Evidence constraints: {json.dumps(research.limitations, ensure_ascii=False)}

Return one scene for every beat. Required fields per scene:
scene_id, beat_id, duration_s, narration, headline, visual_event, scientific_claim,
time_stage, attention_goal, desired_change, transition.

Rules:
- A scene is one integrated physical world, not a list of icons.
- Preserve useful locations and subjects between adjacent scenes when continuity improves comprehension.
- desired_change names only the causal state change that should animate.
- Do not prescribe SVG, isolated object assets, art style, camera gimmicks or decorative movement.
- Use cut or motivated match-cut by default.
""",
            namespace="v10_storyboard",
            fallback=fallback.model_dump(mode="json"),
            force=force,
        )
        board = self._normalize_storyboard(raw, fallback, script)
        save_json(cache, board)
        return board

    def _normalize_script(self, raw: Any, fallback: ScriptPackage, topic: str) -> ScriptPackage:
        if not isinstance(raw, dict):
            raw = fallback.model_dump(mode="json")
        raw_beats = raw.get("beats", fallback.model_dump(mode="json")["beats"])
        if isinstance(raw_beats, dict):
            raw_beats = [raw_beats[k] for k in sorted(raw_beats)]
        beats: list[Beat] = []
        for i, item in enumerate(raw_beats if isinstance(raw_beats, list) else [], 1):
            if isinstance(item, str):
                item = {"spoken_line": item}
            if not isinstance(item, dict):
                continue
            beats.append(
                Beat(
                    beat_id=str(item.get("beat_id", item.get("id", f"B{i:02d}"))),
                    duration_s=item.get("duration_s", item.get("duration", 4.5)),
                    spoken_line=str(item.get("spoken_line", item.get("narration", ""))),
                    visual_event=str(item.get("visual_event", item.get("visual", item.get("spoken_line", "")))),
                    retention_function=str(item.get("retention_function", item.get("purpose", "information_gain"))),
                    sfx=str(item.get("sfx", "none")),
                    raw={**item, "time_stage": item.get("time_stage", "")},
                )
            )
        if not beats:
            beats = fallback.beats
        words_per_second = float(self.config.get("words_per_second", 2.65))
        for beat in beats:
            words = max(1, len(re.findall(r"\b\w+\b", beat.spoken_line)))
            beat.duration_s = max(2.6, beat.duration_s, words / words_per_second + 0.3)
        result = ScriptPackage(
            topic=topic,
            title=str(raw.get("title", fallback.title)),
            hook=str(raw.get("hook", beats[0].spoken_line)),
            beats=beats,
            closing=str(raw.get("closing", beats[-1].spoken_line)),
            raw_llm_output=raw,
        )
        result.total_words = sum(len(re.findall(r"\b\w+\b", b.spoken_line)) for b in beats)
        result.estimated_duration_s = round(sum(b.duration_s for b in beats), 3)
        result.script_hash = hash_value(result.model_dump(exclude={"script_hash", "raw_llm_output"}))
        return result

    def _normalize_storyboard(self, raw: Any, fallback: Storyboard, script: ScriptPackage) -> Storyboard:
        if not isinstance(raw, dict):
            return fallback
        scenes_raw = raw.get("scenes", raw.get("storyboard", []))
        if isinstance(scenes_raw, dict):
            scenes_raw = [scenes_raw[k] for k in sorted(scenes_raw)]
        if not isinstance(scenes_raw, list):
            scenes_raw = []
        scenes: list[SceneRequest] = []
        for i, beat in enumerate(script.beats):
            item = scenes_raw[i] if i < len(scenes_raw) and isinstance(scenes_raw[i], dict) else {}
            scenes.append(
                SceneRequest(
                    scene_id=str(item.get("scene_id", f"SC{i + 1:02d}")),
                    beat_id=beat.beat_id,
                    duration_s=item.get("duration_s", beat.duration_s),
                    narration=str(item.get("narration", beat.spoken_line)),
                    headline=str(item.get("headline", self._headline(beat.spoken_line))),
                    visual_event=str(item.get("visual_event", beat.visual_event)),
                    scientific_claim=str(item.get("scientific_claim", beat.spoken_line)),
                    time_stage=str(item.get("time_stage", beat.raw.get("time_stage", ""))),
                    attention_goal=str(item.get("attention_goal", beat.visual_event)),
                    desired_change=str(item.get("desired_change", beat.visual_event)),
                    transition=str(item.get("transition", "cut")),
                    raw_llm_output=item,
                )
            )
        board = Storyboard(topic=script.topic, width=1080, height=1920, fps=30, scenes=scenes, raw_llm_output=raw)
        board.estimated_duration_s = round(sum(s.duration_s for s in scenes), 3)
        board.storyboard_hash = hash_value(board.model_dump(exclude={"storyboard_hash", "raw_llm_output"}))
        return board

    @staticmethod
    def _fallback_script(topic: str) -> ScriptPackage:
        subject = re.sub(r"^\s*what\s+(would\s+happen\s+)?if\s+", "", topic.strip().rstrip("?"), flags=re.I)
        lines = [
            (f"What if {subject}?", "hook", "Establish the changed world in one readable image."),
            (
                "First, the system loses the balance that normally resets it.",
                "instant",
                "Show the first broken equilibrium.",
            ),
            (
                "The nearest materials respond before the larger environment catches up.",
                "seconds",
                "Show local physical response.",
            ),
            ("Then the effect spreads through every connected pathway.", "minutes", "Show the causal path widening."),
            (
                "Accumulation turns a temporary disturbance into a persistent state.",
                "hours",
                "Compare storage before and after accumulation.",
            ),
            (
                "Infrastructure and living systems begin failing at different thresholds.",
                "days",
                "Show multiple thresholds inside one environment.",
            ),
            (
                "The final outcome depends on intensity, geography, and how long recovery is denied.",
                "long_term",
                "Show conditional outcomes, not false precision.",
            ),
            (
                "The real danger is not one event—it is a system that never gets time to reset.",
                "payoff",
                "Resolve the causal chain in one final composition.",
            ),
        ]
        beats = [
            Beat(beat_id=f"B{i:02d}", spoken_line=line, visual_event=visual, duration_s=4.6, raw={"time_stage": stage})
            for i, (line, stage, visual) in enumerate(lines, 1)
        ]
        result = ScriptPackage(topic=topic, title=topic, hook=lines[0][0], beats=beats, closing=lines[-1][0])
        result.total_words = sum(len(x[0].split()) for x in lines)
        result.estimated_duration_s = sum(b.duration_s for b in beats)
        result.script_hash = hash_value(result.model_dump(exclude={"script_hash"}))
        return result

    def _fallback_storyboard(self, script: ScriptPackage) -> Storyboard:
        scenes = []
        for i, beat in enumerate(script.beats, 1):
            scenes.append(
                SceneRequest(
                    scene_id=f"SC{i:02d}",
                    beat_id=beat.beat_id,
                    duration_s=beat.duration_s,
                    narration=beat.spoken_line,
                    headline=self._headline(beat.spoken_line),
                    visual_event=beat.visual_event,
                    scientific_claim=beat.spoken_line,
                    time_stage=str(beat.raw.get("time_stage", "")),
                    attention_goal=beat.visual_event,
                    desired_change=beat.visual_event,
                    transition="cut",
                )
            )
        board = Storyboard(topic=script.topic, scenes=scenes)
        board.estimated_duration_s = sum(s.duration_s for s in scenes)
        board.storyboard_hash = hash_value(board.model_dump(exclude={"storyboard_hash"}))
        return board

    @staticmethod
    def _headline(text: str) -> str:
        words = re.findall(r"\b[\w'-]+\b", text)
        return " ".join(words[:6]).upper() if words else "EXPERIMENT"
