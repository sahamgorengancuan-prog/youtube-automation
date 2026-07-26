"""Run modes (§9): what each mode may execute, whether it may spend, and the
pilot-first production lock."""

from __future__ import annotations

from ..exceptions import ConfigurationError

MODES: dict[str, dict] = {
    "plan": {
        "paid_calls": False,
        "allowed": {"source_audit", "schemas", "research_plan", "hooks", "story_beats", "spoken_script_draft", "scene_specs"},
    },
    "style_lock": {
        "paid_calls": True,
        "allowed": {"style_board", "character_sheet", "prop_sheet", "visual_review"},
    },
    "pilot": {
        "paid_calls": True,
        "allowed": {
            "source_audit", "schemas", "research_plan", "hooks", "story_beats", "spoken_script_draft", "scene_specs",
            "style_board", "character_sheet", "prop_sheet", "visual_review",
            "pilot_scenes", "pilot_tts", "alignment", "pilot_render", "pilot_qc",
        },
    },
    "production": {"paid_calls": True, "allowed": {"complete_pipeline"}},
    "repair": {"paid_calls": True, "allowed": {"selected_scene_repairs", "rerender", "final_qc"}},
    "render_only": {
        "paid_calls": False,
        "allowed": {"timeline", "captions", "ffmpeg_render", "local_qc"},
    },
}


def mode_allows(mode: str, stage: str) -> bool:
    spec = MODES.get(mode)
    if spec is None:
        raise ConfigurationError(f"unknown run mode {mode!r}", stage="modes")
    return stage in spec["allowed"] or "complete_pipeline" in spec["allowed"]


def mode_may_spend(mode: str) -> bool:
    spec = MODES.get(mode)
    if spec is None:
        raise ConfigurationError(f"unknown run mode {mode!r}", stage="modes")
    return bool(spec["paid_calls"])


def check_production_unlock(
    pilot_qc_status: str | None,
    allow_production_without_pilot: bool = False,
) -> list[str]:
    """Returns warnings; raises when production is locked and not overridden."""
    if pilot_qc_status == "PASS":
        return []
    if allow_production_without_pilot:
        return [
            "PRODUCTION OVERRIDE: pilot QC status is "
            f"{pilot_qc_status!r}; production was explicitly unlocked without a passing pilot."
        ]
    raise ConfigurationError(
        f"production is locked until pilot_qc.status == PASS (current: {pilot_qc_status!r}); "
        "set allow_production_without_pilot=true to override (recorded as a warning)",
        stage="production_unlock",
    )
