"""Episode state: persisted to episodes/<episode_id>/state.json (Drive when
mounted, local otherwise) so a disconnected runtime resumes from the last
validated stage."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from sias.filesystem import atomic_write_json, load_json

from .exceptions import StateError

STAGE_ORDER = [
    "plan",
    "canary",
    "style_lock",
    "style_lock_human",
    "pilot_scenes",
    "pilot_tts",
    "pilot_alignment",
    "pilot_render",
    "pilot_qc",
    "production_unlock",
    "production_scenes",
    "full_tts",
    "full_alignment",
    "final_render",
    "final_qc",
    "export",
]


class EpisodeState(BaseModel):
    episode_id: str
    topic: str = ""
    stage_status: dict[str, str] = Field(default_factory=dict)  # stage -> PENDING/PASS/FAIL/SKIPPED
    approvals: dict[str, str] = Field(default_factory=dict)  # e.g. style_lock_human: APPROVED
    budget: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    paid_calls_made: int = 0

    def mark(self, stage: str, status: str) -> None:
        if stage not in STAGE_ORDER:
            raise StateError(f"unknown stage {stage!r}", stage="state")
        self.stage_status[stage] = status

    def status(self, stage: str) -> str:
        return self.stage_status.get(stage, "PENDING")

    def next_recommended_action(self) -> str:
        for stage in STAGE_ORDER:
            if self.status(stage) not in ("PASS", "SKIPPED"):
                return stage
        return "done"

    def gate_passed(self, stage: str) -> bool:
        return self.status(stage) == "PASS" or self.approvals.get(stage) == "APPROVED"


def state_path(workspace: str | Path, episode_id: str) -> Path:
    return Path(workspace) / "episodes" / episode_id / "state.json"


def save_state(workspace: str | Path, state: EpisodeState) -> Path:
    return atomic_write_json(state_path(workspace, state.episode_id), state.model_dump())


def load_state(workspace: str | Path, episode_id: str) -> EpisodeState | None:
    data = load_json(state_path(workspace, episode_id))
    return EpisodeState.model_validate(data) if data else None


def load_or_create(workspace: str | Path, episode_id: str, topic: str = "") -> EpisodeState:
    existing = load_state(workspace, episode_id)
    if existing is not None:
        return existing
    state = EpisodeState(episode_id=episode_id, topic=topic)
    save_state(workspace, state)
    return state
