"""Studio facade — the single object the Colab notebook drives.

Wraps the `sias` engine orchestrator + the agentic supervisor with episode
state, BudgetGuardian, and paid-call protection. Importing/creating a Studio
performs no network access."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Any

from sias.filesystem import slugify
from sias.pipeline.orchestrator import Orchestrator
from sias.schemas import RunBudget

from .agents.registry import build_free_agents, paid_agent_stubs
from .budget import BudgetGuardian, assert_paid_call_allowed
from .config import StudioConfig
from .state import EpisodeState, load_or_create, save_state
from .supervisor import Supervisor

SECRET_NAMES = ("BFL_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY", "HF_KEY", "HF_API_KEY", "HF_API_SECRET")

MODE_AGENTS: dict[str, set[str] | None] = {
    "plan": {
        "source_auditor", "research_planner", "fact_verifier", "hook_tournament",
        "story_architect", "retention_critic", "visual_director", "style_canon_guardian",
        "reference_pack_selector", "prompt_compiler", "timeline_planner", "story_qc",
    },
    "canary": None,       # canary is its own bounded routine (providers.canary)
    "style_lock": None,
    "pilot": None,
    "production": None,
    "repair": None,
    "render_only": None,
}


class Studio:
    def __init__(self, cfg: StudioConfig, workspace: str | Path, adapters: dict[str, Any] | None = None):
        self.cfg = cfg
        self.workspace = Path(workspace)
        self.adapters = adapters or {}
        topic = cfg.engine.project.topic or cfg.engine.project.title
        self.episode_id = slugify(topic)
        self.state: EpisodeState = load_or_create(self.workspace, self.episode_id, topic)
        self.budget = BudgetGuardian(
            RunBudget(
                max_image_calls=cfg.engine.budgets.max_image_calls,
                max_vision_calls=cfg.engine.budgets.max_vision_calls,
                max_tts_characters=cfg.engine.budgets.max_tts_characters,
            ),
            hard_stop=cfg.engine.budgets.hard_stop_on_budget_exceeded,
            max_higgsfield_calls=cfg.colab.higgsfield.max_calls,
        )
        self.engine = Orchestrator(cfg.engine, self.workspace / "episodes", adapters=self.adapters,
                                   episode_id=self.episode_id)

    # ---- secrets ---------------------------------------------------------
    def secrets_present(self) -> dict[str, bool]:
        return {name: bool(os.environ.get(name)) for name in SECRET_NAMES}

    # ---- offline plan (free) ---------------------------------------------
    def plan(self, force: bool = False) -> dict[str, Any]:
        supervisor = Supervisor(self.workspace, self.state, self.budget,
                                arm_paid_calls=False, allowed_agents=MODE_AGENTS["plan"])
        for agent in build_free_agents(self.cfg.engine):
            supervisor.register(agent)
        for agent in paid_agent_stubs():
            supervisor.register(agent)
        context = supervisor.run(sorted(MODE_AGENTS["plan"]), {"llm": self.adapters.get("openai_text")})
        # Materialize compiled prompts + scene files through the proven engine too.
        engine_out = self.engine.plan(force=force)
        self.state.mark("plan", "PASS")
        save_state(self.workspace, self.state)
        return {"agents": {k: v for k, v in context.items() if isinstance(v, dict)},
                "engine": engine_out, "budget": self.budget.snapshot()}

    # ---- paid-stage guard --------------------------------------------------
    def guard_paid(self, stage: str, secret: str, previous_gate: str | None,
                   user_action_confirmed: bool, budget_kind: str = "image", amount: float = 1) -> None:
        assert_paid_call_allowed(
            stage,
            arm_paid_calls=self.cfg.colab.arm_paid_calls,
            secret_present=bool(os.environ.get(secret)),
            mode_allows_stage=self.cfg.run_mode in (stage.split(":")[0], "production", stage),
            budget_ok=self.budget.can_afford(budget_kind, amount),
            previous_gate_passed=(previous_gate is None) or self.state.gate_passed(previous_gate),
            user_action_confirmed=user_action_confirmed,
        )

    # ---- render-only (free) ------------------------------------------------
    def render_only(self, scenes, timings, approved_images, audio_path, narration_duration_s,
                    out_name: str = "final.mp4") -> dict[str, Any]:
        result = self.engine.render_only(scenes, timings, approved_images, audio_path,
                                         narration_duration_s, out_name)
        self.state.mark("final_render", "PASS")
        save_state(self.workspace, self.state)
        return result

    # ---- export -------------------------------------------------------------
    def export_package(self) -> Path:
        episode_dir = self.workspace / "episodes" / self.episode_id
        out_zip = episode_dir / "package" / f"{self.episode_id}.zip"
        out_zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(episode_dir.rglob("*")):
                if path.is_file() and "package" not in path.parts:
                    zf.write(path, path.relative_to(episode_dir))
        self.state.mark("export", "PASS")
        save_state(self.workspace, self.state)
        return out_zip
