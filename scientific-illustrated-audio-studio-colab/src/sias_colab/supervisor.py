"""Supervisor-driven agent DAG — no giant do-everything prompt.

Each agent: single responsibility, validated structured input/output, explicit
dependencies, artifact path, success criteria, retry policy, cost class, and a
manifest record. The supervisor topologically executes the subset a run mode
allows, refuses paid agents unless armed, and records everything to state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from sias.filesystem import atomic_write_json, input_hash
from sias.manifests import now_iso, write_manifest
from sias.schemas import StageManifest

from .budget import BudgetGuardian
from .exceptions import PaidCallBlockedError
from .state import EpisodeState, save_state


@dataclass
class Agent:
    agent_id: str
    description: str
    deps: list[str]
    run: Callable[[dict[str, Any]], dict[str, Any]]  # context -> output dict
    cost_class: str = "free"  # free | paid
    retries: int = 0
    success: Callable[[dict[str, Any]], bool] = field(default=lambda out: bool(out))


class Supervisor:
    def __init__(
        self,
        workspace: str | Path,
        state: EpisodeState,
        budget: BudgetGuardian,
        arm_paid_calls: bool = False,
        allowed_agents: set[str] | None = None,
    ):
        self.workspace = Path(workspace)
        self.state = state
        self.budget = budget
        self.arm_paid_calls = arm_paid_calls
        self.allowed_agents = allowed_agents
        self.agents: dict[str, Agent] = {}
        self.outputs: dict[str, dict[str, Any]] = {}
        self.manifest_dir = self.workspace / "episodes" / state.episode_id / "manifests"

    def register(self, agent: Agent) -> None:
        self.agents[agent.agent_id] = agent

    def _order(self, targets: list[str]) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()

        def visit(aid: str, chain: tuple[str, ...]) -> None:
            if aid in seen:
                return
            if aid in chain:
                raise ValueError(f"agent dependency cycle: {' -> '.join(chain + (aid,))}")
            agent = self.agents[aid]
            for dep in agent.deps:
                visit(dep, chain + (aid,))
            seen.add(aid)
            ordered.append(aid)

        for t in targets:
            visit(t, ())
        return ordered

    def run(self, targets: list[str], context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = dict(context or {})
        for aid in self._order(targets):
            agent = self.agents[aid]
            if self.allowed_agents is not None and aid not in self.allowed_agents:
                raise PaidCallBlockedError(
                    f"agent {aid!r} is not allowed in the current run mode", stage=aid
                )
            if agent.cost_class == "paid" and not self.arm_paid_calls:
                raise PaidCallBlockedError(
                    f"agent {aid!r} is paid and ARM_PAID_CALLS is false", stage=aid
                )
            manifest = StageManifest(
                stage_id=f"agent_{aid}",
                status="RUNNING",
                input_hash=input_hash({k: str(v)[:200] for k, v in context.items()}),
                started_at=now_iso(),
                parent_stage_ids=[f"agent_{d}" for d in agent.deps],
            )
            attempts = 0
            while True:
                try:
                    output = agent.run(context)
                    if not agent.success(output):
                        raise RuntimeError(f"agent {aid} output failed its success criteria")
                    break
                except PaidCallBlockedError:
                    raise
                except Exception as exc:
                    attempts += 1
                    if attempts > agent.retries:
                        manifest.status = "FAILED"
                        manifest.errors.append(f"{type(exc).__name__}: {exc}")
                        write_manifest(self.manifest_dir, manifest)
                        raise
            self.outputs[aid] = output
            context[aid] = output
            artifact = self.manifest_dir / f"agent_{aid}.output.json"
            atomic_write_json(artifact, output)
            manifest.artifact_path = str(artifact)
            manifest.status = "PASS"
            write_manifest(self.manifest_dir, manifest)
        save_state(self.workspace, self.state)
        return context
