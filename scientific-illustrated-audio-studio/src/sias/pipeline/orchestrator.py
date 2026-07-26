"""Orchestrator: runs the pipeline honoring run mode, budget and checkpoints.

`plan` and `render_only` are fully executable offline. Paid stages require the
relevant adapter — with none configured they fail explicitly (never a silent
placeholder)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..budget import BudgetLedger
from ..config import SIASConfig
from ..exceptions import ConfigurationError
from ..filesystem import atomic_write_json, init_workspace, slugify
from ..schemas import RunBudget, SceneSpec, SpokenScript, StoryBeat
from ..research.claims import build_research_pack
from ..research.source_guard import validate_pack
from ..story.beats import build_story_beats, validate_beats
from ..story.hooks import generate_hooks, select_hook
from ..story.spoken_script import assemble_script
from ..story.validators import validate_story
from ..style.bible import build_style_bible, save_style_bible
from ..style.prompt_compiler import compile_prompt, persist_prompt
from .checkpoints import run_stage
from .modes import mode_allows, mode_may_spend

PILOT_SCENE_COUNT = 4
_PILOT_ROLES = ["cold_open", "fact_1", "explanation", "gasp_reveal"]


class Orchestrator:
    def __init__(
        self,
        cfg: SIASConfig,
        workspace_base: str | Path,
        adapters: dict[str, Any] | None = None,
        episode_id: str = "",
    ):
        self.cfg = cfg
        self.adapters = adapters or {}
        self.episode_id = episode_id or slugify(cfg.project.topic or cfg.project.title)
        self.workspace = init_workspace(workspace_base, self.episode_id)
        self.manifest_dir = self.workspace / "manifests"
        self.budget = BudgetLedger(
            RunBudget(
                max_image_calls=cfg.budgets.max_image_calls,
                max_vision_calls=cfg.budgets.max_vision_calls,
                max_tts_characters=cfg.budgets.max_tts_characters,
            ),
            hard_stop=cfg.budgets.hard_stop_on_budget_exceeded,
        )

    # ---- guards ----------------------------------------------------------
    def _guard(self, stage: str, paid: bool = False) -> None:
        mode = self.cfg.run_mode
        if not mode_allows(mode, stage):
            raise ConfigurationError(f"stage {stage!r} is not allowed in run mode {mode!r}", stage=stage)
        if paid and not mode_may_spend(mode):
            raise ConfigurationError(f"run mode {mode!r} forbids paid calls (stage {stage!r})", stage=stage)

    # ---- plan (offline-complete) ------------------------------------------
    def plan(self, force: bool = False) -> dict[str, Any]:
        """Topic → research pack → hooks → beats → spoken script → scene specs.
        Fully offline; the LLM adapter upgrades quality when present."""
        cfg = self.cfg
        llm = self.adapters.get("openai_text")
        out: dict[str, Any] = {"episode_id": self.episode_id}

        def _research() -> str:
            pack = build_research_pack(cfg.project.topic, cfg.project.audience, cfg.story.facts_count, llm)
            path = self.workspace / "manifests" / "research_pack.json"
            atomic_write_json(path, pack.model_dump())
            out["research_pack"] = pack.model_dump()
            out["source_guard"] = [c.model_dump() for c in validate_pack(pack)]
            return str(path)

        self._guard("research_plan")
        m = run_stage(self.manifest_dir, "research_plan", {"topic": cfg.project.topic}, _research, force=force)
        if "research_pack" not in out:  # cache hit: hydrate from the artifact
            from ..filesystem import load_json
            from ..schemas import ResearchPack

            out["research_pack"] = load_json(m.artifact_path)
            out["source_guard"] = [
                c.model_dump() for c in validate_pack(ResearchPack.model_validate(out["research_pack"]))
            ]

        def _hooks() -> str:
            from ..schemas import ResearchPack

            pack = ResearchPack.model_validate(out["research_pack"])
            hooks = generate_hooks(pack.topic, llm)
            selected = select_hook(hooks)
            path = self.workspace / "manifests" / "hooks.json"
            atomic_write_json(path, {"hooks": [h.model_dump() for h in hooks], "selected": selected.model_dump()})
            out["hooks"] = [h.model_dump() for h in hooks]
            out["selected_hook"] = selected.model_dump()
            return str(path)

        self._guard("hooks")
        m = run_stage(self.manifest_dir, "hooks", {"topic": cfg.project.topic}, _hooks, force=force)
        if "hooks" not in out:
            from ..filesystem import load_json

            data = load_json(m.artifact_path) or {}
            out["hooks"] = data.get("hooks", [])
            out["selected_hook"] = data.get("selected", {})

        def _beats() -> str:
            from ..schemas import ResearchPack

            pack = ResearchPack.model_validate(out["research_pack"])
            beats = build_story_beats(pack, llm)
            issues = validate_beats(beats, cfg.story.min_scenes, cfg.story.max_scenes)
            if issues:
                raise ConfigurationError("; ".join(issues), stage="story_beats")
            path = self.workspace / "manifests" / "story_beats.json"
            atomic_write_json(path, [b.model_dump() for b in beats])
            out["beats"] = [b.model_dump() for b in beats]
            return str(path)

        self._guard("story_beats")
        m = run_stage(self.manifest_dir, "story_beats", {"topic": cfg.project.topic}, _beats, force=force)
        if "beats" not in out:
            from ..filesystem import load_json

            out["beats"] = load_json(m.artifact_path) or []

        def _script() -> str:
            beats = [StoryBeat.model_validate(b) for b in out["beats"]]
            script = assemble_script(beats, cfg.story.catchphrase, cfg.project.language)
            path = self.workspace / "manifests" / "spoken_script.json"
            atomic_write_json(path, script.model_dump())
            out["script"] = script.model_dump()
            out["story_qc"] = [c.model_dump() for c in validate_story(script, beats, cfg)]
            return str(path)

        self._guard("spoken_script_draft")
        m = run_stage(self.manifest_dir, "spoken_script_draft", {"topic": cfg.project.topic}, _script, force=force)
        if "script" not in out:
            from ..filesystem import load_json

            out["script"] = load_json(m.artifact_path) or {}
            script_obj = SpokenScript.model_validate(out["script"])
            beat_objs = [StoryBeat.model_validate(b) for b in out["beats"]]
            out["story_qc"] = [c.model_dump() for c in validate_story(script_obj, beat_objs, cfg)]

        def _scenes() -> str:
            beats = [StoryBeat.model_validate(b) for b in out["beats"]]
            scenes = build_scene_specs(beats)
            bible = build_style_bible(cfg)
            save_style_bible(bible, self.workspace / "style_lock" / "style_bible.json")
            for scene in scenes:
                compiled = compile_prompt(scene, bible)
                persist_prompt(compiled, self.workspace / "scenes" / scene.scene_id)
            path = self.workspace / "manifests" / "scene_specs.json"
            atomic_write_json(path, [s.model_dump() for s in scenes])
            out["scenes"] = [s.model_dump() for s in scenes]
            return str(path)

        self._guard("scene_specs")
        m = run_stage(self.manifest_dir, "scene_specs", {"topic": cfg.project.topic}, _scenes, force=force)
        if "scenes" not in out:
            from ..filesystem import load_json

            out["scenes"] = load_json(m.artifact_path) or []
        out["budget"] = self.budget.snapshot()
        return out

    # ---- pilot scene subset -------------------------------------------------
    def pilot_scene_specs(self, scenes: list[SceneSpec]) -> list[SceneSpec]:
        chosen: list[SceneSpec] = []
        for role in _PILOT_ROLES:
            match = next((s for s in scenes if s.beat_role == role), None)
            if match:
                chosen.append(match)
        return chosen[:PILOT_SCENE_COUNT]

    # ---- render_only (offline-complete) -------------------------------------
    def render_only(
        self,
        scenes: list[SceneSpec],
        timings: list,
        approved_images: dict[str, str],
        audio_path: str,
        narration_duration_s: float,
        out_name: str = "final.mp4",
    ) -> dict[str, Any]:
        self._guard("ffmpeg_render")
        from ..render.ffmpeg import render_episode
        from ..render.timeline import build_render_scenes
        from ..render.validator import validate_final

        render_scenes = build_render_scenes(scenes, timings, approved_images, self.cfg.render.allowed_motion)
        out_path = self.workspace / "render" / out_name
        render_episode(
            render_scenes,
            audio_path,
            self.workspace / "render" / "work",
            out_path,
            self.cfg.project.width,
            self.cfg.project.height,
            self.cfg.render.fps,
            self.cfg.render.max_zoom_pct,
            self.cfg.render.max_pan_pct,
        )
        report = validate_final(out_path, narration_duration_s, self.cfg.render.duration_tolerance_s)
        return {"video": str(out_path), "validation": report}


def build_scene_specs(beats: list[StoryBeat]) -> list[SceneSpec]:
    scenes: list[SceneSpec] = []
    for i, beat in enumerate(beats):
        reveal = ""
        if beat.role == "gasp_reveal":
            words = [w.strip(".,!?") for w in beat.narration.split() if len(w) > 3]
            reveal = words[-1] if words else ""
        scenes.append(
            SceneSpec(
                scene_id=f"S{i + 1:02d}",
                beat_role=beat.role,
                narration=beat.narration,
                visual_objective=beat.summary or beat.narration,
                composition="single hero subject, clear silhouette, safe caption zone",
                characters=["narrating scientist"] if beat.role in ("explanation", "payoff") else [],
                props=["causal arrows"] if beat.role == "explanation" else [],
                scientific_labels=[],
                reveal_word=reveal,
                continuity_refs=[f"S{i:02d}"] if beat.role == "payoff" and i > 0 else [],
            )
        )
    return scenes
