"""Timeline: scene timings + approved images → render scenes with policy-
compliant motion and transitions."""

from __future__ import annotations

from ..exceptions import RenderError
from ..schemas import RenderScene, SceneSpec, SceneTiming
from .transitions import transition_for

_ROLE_MOTION = {
    "cold_open": "slow_push_in",
    "fact_1": "hold",
    "fact_2": "pan_right",
    "fact_3": "hold",
    "explanation": "label_reveal",
    "scale_example": "slow_pull_out",
    "gasp_reveal": "slow_push_in",
    "payoff": "hold",
}


def build_render_scenes(
    scenes: list[SceneSpec],
    timings: list[SceneTiming],
    approved_images: dict[str, str],
    allowed_motion: list[str],
) -> list[RenderScene]:
    by_id = {t.scene_id: t for t in timings}
    out: list[RenderScene] = []
    prev_role: str | None = None
    for scene in scenes:
        timing = by_id.get(scene.scene_id)
        if timing is None:
            continue  # merged into a neighbour during alignment
        image = approved_images.get(scene.scene_id)
        if not image:
            raise RenderError(f"no approved image for scene {scene.scene_id}", stage="timeline")
        motion = _ROLE_MOTION.get(scene.beat_role, "hold")
        if motion not in allowed_motion:
            motion = "hold"
        out.append(
            RenderScene(
                scene_id=scene.scene_id,
                image_path=image,
                start_s=timing.start_s,
                end_s=timing.end_s,
                motion=motion,
                transition_in=transition_for(prev_role, scene.beat_role),
            )
        )
        prev_role = scene.beat_role
    if not out:
        raise RenderError("timeline produced no render scenes", stage="timeline")
    return out
