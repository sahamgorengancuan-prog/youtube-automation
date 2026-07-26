"""Stage identifiers in dependency order."""

from __future__ import annotations

STAGES = [
    "source_audit",
    "research_plan",
    "hooks",
    "story_beats",
    "spoken_script_draft",
    "scene_specs",
    "style_board",
    "character_sheet",
    "prop_sheet",
    "visual_review",
    "pilot_scenes",
    "pilot_tts",
    "alignment",
    "pilot_render",
    "pilot_qc",
    "production_scenes",
    "full_tts",
    "full_alignment",
    "final_render",
    "final_qc",
    "package",
]

PARENTS: dict[str, list[str]] = {
    "hooks": ["research_plan"],
    "story_beats": ["hooks"],
    "spoken_script_draft": ["story_beats"],
    "scene_specs": ["spoken_script_draft"],
    "style_board": ["scene_specs"],
    "pilot_scenes": ["style_board", "scene_specs"],
    "pilot_tts": ["spoken_script_draft"],
    "alignment": ["pilot_tts"],
    "pilot_render": ["pilot_scenes", "alignment"],
    "pilot_qc": ["pilot_render"],
    "production_scenes": ["pilot_qc"],
    "full_tts": ["pilot_qc"],
    "full_alignment": ["full_tts"],
    "final_render": ["production_scenes", "full_alignment"],
    "final_qc": ["final_render"],
    "package": ["final_qc"],
}
