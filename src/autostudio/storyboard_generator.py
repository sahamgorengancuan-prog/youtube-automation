from __future__ import annotations

import json
from pathlib import Path

from .config import StudioConfig
from .hashing import atomic_write_json, hash_value, read_json
from .llm import LLMClient
from .logging_utils import configure_logging
from .schemas import (
    AnimationPlaceholder, AssetRequirement, CameraPlan, DashboardSpec,
    ResearchBundle, Scene, SceneObject, ScriptPackage, Storyboard,
)

# The LLM may only request assets from this whitelist. This is what keeps the
# asset catalogue small and reusable — no "generate thousands of SVGs".
ALLOWED_ASSET_TYPES = {
    "planet", "moon", "sun", "human", "cloud", "building", "arrow", "dashboard",
    "wave", "tree", "animal", "rock", "star", "satellite", "black_hole", "atom",
    "cell", "molecule", "volcano", "mountain", "tornado", "rocket", "battery",
    "robot", "computer", "clock", "thermometer", "dna", "car", "airplane",
    "shield", "generic_object",
}


class StoryboardGenerator:
    """Turns each narration beat into one visual scene. LLM picks semantic assets only."""

    SYSTEM_PROMPT = (
        "You direct a flat-vector scientific simulation channel. Translate every narration "
        "beat into one clear visual consequence. Use only allowed asset types and a small "
        "reusable set. Never request raster, textures, gradients, or arbitrary SVG code. "
        "Return JSON only."
    )

    def __init__(self, config: StudioConfig, llm: LLMClient, cache_dir: Path):
        self.config = config
        self.llm = llm
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.logger = configure_logging("autostudio.storyboard")

    def _keyword_assets(self, text: str) -> list[str]:
        lowered = text.lower()
        mapping = {
            "earth": "planet", "planet": "planet", "moon": "moon", "sun": "sun",
            "human": "human", "people": "human", "person": "human",
            "wind": "arrow", "atmosphere": "cloud", "cloud": "cloud", "ocean": "wave",
            "water": "wave", "city": "building", "building": "building", "tree": "tree",
            "animal": "animal", "rock": "rock", "asteroid": "rock", "star": "star",
            "satellite": "satellite", "black hole": "black_hole", "atom": "atom",
            "cell": "cell", "molecule": "molecule", "volcano": "volcano",
            "mountain": "mountain", "tornado": "tornado", "rocket": "rocket",
            "battery": "battery", "robot": "robot", "computer": "computer",
            "clock": "clock", "temperature": "thermometer", "dna": "dna",
            "car": "car", "airplane": "airplane",
        }
        assets = []
        for keyword, asset_type in mapping.items():
            if keyword in lowered and asset_type not in assets:
                assets.append(asset_type)
        return assets[:4] or ["generic_object"]

    def _fallback(self, script: ScriptPackage) -> Storyboard:
        scenes = []
        for index, beat in enumerate(script.beats, start=1):
            requirements = [AssetRequirement(
                asset_id=f"{asset_type}-{index}-{asset_index}", asset_type=asset_type,
                label=asset_type.replace("_", " ").title(), source_prompt=beat.narration,
                tags=[script.topic],
            ) for asset_index, asset_type in enumerate(self._keyword_assets(beat.narration), start=1)]
            objects = [SceneObject(
                asset_id=req.asset_id, x=0.12 + 0.28 * (asset_index % 3),
                y=0.30 + 0.18 * (asset_index // 3), width=0.28, height=0.28,
                z_index=asset_index,
            ) for asset_index, req in enumerate(requirements)]
            scenes.append(Scene(
                scene_id=f"scene{index:02d}", duration_s=max(2.5, beat.estimated_duration_s),
                narration=beat.narration, title=beat.purpose.upper(), objects=objects,
                camera=CameraPlan(shot="wide", framing="center"),
                animations=[AnimationPlaceholder(target_asset_id=requirements[0].asset_id, kind="scale_in", duration_s=0.8)],
                dashboard=DashboardSpec(experiment_id=f"EXPERIMENT #{index:03d}", status_value="Running", metric_label="PHASE", metric_value=f"{index}/{len(script.beats)}"),
                text=[beat.purpose.upper()], background="paper", motion="hold", transition="cut",
                asset_requirements=requirements,
            ))
        return Storyboard(
            topic=script.topic, canvas_width=self.config.canvas.width, canvas_height=self.config.canvas.height,
            style_name=self.config.style.name, scenes=scenes, estimated_duration_s=script.estimated_duration_s,
        )

    def generate(self, script: ScriptPackage, research: ResearchBundle, force_refresh: bool = False) -> Storyboard:
        key = hash_value({
            "script": script.script_hash, "research": research.research_hash,
            "canvas": self.config.canvas.model_dump(mode="json"), "style": self.config.style.model_dump(mode="json"),
        })
        path = self.cache_dir / f"{key}.json"
        if path.exists() and not force_refresh:
            return Storyboard.model_validate(read_json(path))
        prompt = f"""
Topic: {script.topic}
Script:
{json.dumps(script.model_dump(mode='json'), ensure_ascii=False, indent=2)}
Visual ideas:
{json.dumps(research.visual_ideas, ensure_ascii=False)}
Allowed asset types: {sorted(ALLOWED_ASSET_TYPES)}

Create one scene per beat. Coordinates/sizes are normalized 0-1. Use <=6 assets per scene and reuse IDs for recurring objects.
Visual language: scientific experiment dashboard, flat geometry, strong headline, white/dark panels, blue accents, status cards, arrows, measurable consequences.
Return Storyboard JSON with scene_id, duration_s, narration, title, objects, camera, animations, dashboard, text, icons, background, motion, transition, and asset_requirements.
""".strip()
        try:
            raw = self.llm.generate_json(self.SYSTEM_PROMPT, prompt, cache_namespace="storyboard", max_new_tokens=3400, force_refresh=force_refresh)
            raw.update({
                "topic": script.topic, "canvas_width": self.config.canvas.width,
                "canvas_height": self.config.canvas.height, "style_name": self.config.style.name,
                "estimated_duration_s": script.estimated_duration_s,
            })
            storyboard = Storyboard.model_validate(raw)
        except Exception as exc:
            self.logger.warning("Storyboard fallback: %s", exc)
            storyboard = self._fallback(script)
        storyboard = storyboard.model_copy(update={"storyboard_hash": hash_value(storyboard.model_dump(exclude={"storyboard_hash"}))})
        atomic_write_json(path, storyboard)
        return storyboard
