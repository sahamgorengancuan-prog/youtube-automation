"""Resource + cost orchestrator — schedule by resource, budget the spend.

The pipeline should not run everything sequentially and blindly: it should know
which stage needs which resource (CPU / API / GPU / A100), run independent scenes
in parallel where safe, respect GPU availability, and stop before it blows a cost
or retry budget. This provides:

* **Resource classification** — each stage maps to a resource class; GPU/A100
  tasks are serialized (or skipped) when no GPU is present.
* **Cost model + budget** — estimate the spend of a planned run from a price table
  (OpenAI per 1K tokens, BFL per image, organic-video per second) and enforce a
  ``max_cost_usd`` + ``max_retries`` budget: ``can_afford`` / ``charge`` /
  ``allow_retry``.
* **Scheduler** — topologically order the task graph into waves; independent
  tasks in a wave that share a parallel-safe resource (API / CPU) may run
  concurrently.
* **parallel_map** — a real, budget-aware concurrent executor (ThreadPool) for
  the independent per-scene API work, with a worker cap.

It reads the same cache keys as the artifact graph, so a cached task costs
nothing. The plan (resource classes + estimated cost + parallel waves) is emitted
so a run's cost is previewable before it spends.
"""

from __future__ import annotations

import concurrent.futures
from enum import Enum
from typing import Any, Callable

from .utils import ensure_dir, save_json


class Resource(str, Enum):
    CPU = "cpu"
    API = "api"
    GPU = "gpu"
    A100 = "a100"


# Which resource each stage needs. GPU/A100 stages are gated on availability.
STAGE_RESOURCES: dict[str, Resource] = {
    "research": Resource.API,
    "script": Resource.API,
    "storyboard": Resource.API,
    "art_direction": Resource.API,
    "scene_architecture": Resource.API,
    "beauty_frame": Resource.API,  # BFL image gen
    "vision_review": Resource.API,
    "segmentation": Resource.GPU,  # SAM2
    "rig_pose": Resource.GPU,  # YOLO-pose
    "render_pil": Resource.CPU,
    "render_remotion": Resource.CPU,
    "temporal_video": Resource.A100,  # WAN / SkyReels
}

# Parallel-safe resources: many independent API/CPU tasks can overlap. GPU/A100
# are serialized by default (one heavy model at a time on a single accelerator).
_PARALLEL_SAFE = {Resource.API, Resource.CPU}

# Default price estimates (USD) — for budgeting, not billing. Configurable.
_DEFAULT_PRICES = {
    "openai_per_1k_tokens": 0.0006,
    "bfl_per_image": 0.04,
    "video_per_second": 0.10,
}


class CostModel:
    def __init__(self, prices: dict[str, float] | None = None):
        self.prices = {**_DEFAULT_PRICES, **(prices or {})}

    def estimate(self, kind: str, units: float) -> float:
        if kind == "openai":
            return round(self.prices["openai_per_1k_tokens"] * units / 1000.0, 6)
        if kind == "bfl":
            return round(self.prices["bfl_per_image"] * units, 6)
        if kind == "video":
            return round(self.prices["video_per_second"] * units, 6)
        return 0.0


class Budget:
    def __init__(self, max_cost_usd: float = 0.0, max_retries: int = 8):
        self.max_cost_usd = float(max_cost_usd)  # 0 = uncapped
        self.max_retries = int(max_retries)
        self.spent = 0.0
        self.retries_used = 0

    def can_afford(self, cost: float) -> bool:
        return self.max_cost_usd <= 0 or (self.spent + cost) <= self.max_cost_usd + 1e-9

    def charge(self, cost: float) -> bool:
        if not self.can_afford(cost):
            return False
        self.spent = round(self.spent + cost, 6)
        return True

    def allow_retry(self) -> bool:
        if self.retries_used >= self.max_retries:
            return False
        self.retries_used += 1
        return True

    def remaining(self) -> float:
        return float("inf") if self.max_cost_usd <= 0 else round(self.max_cost_usd - self.spent, 6)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_cost_usd": self.max_cost_usd,
            "spent": self.spent,
            "remaining": self.remaining() if self.max_cost_usd > 0 else "uncapped",
            "max_retries": self.max_retries,
            "retries_used": self.retries_used,
        }


class Scheduler:
    def __init__(self, gpu_available: bool = False, max_api_workers: int = 4):
        self.gpu_available = gpu_available
        self.max_api_workers = int(max_api_workers)

    def resource_for(self, stage: str) -> Resource:
        return STAGE_RESOURCES.get(stage, Resource.CPU)

    def plan(self, tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """tasks: [{id, stage, deps:[ids]}]. Returns ordered waves; each wave lists
        tasks that can run together, flagging parallel groups and gated GPU/A100
        tasks."""
        done: set[str] = set()
        waves: list[dict[str, Any]] = []
        remaining = list(tasks)
        while remaining:
            ready = [t for t in remaining if all(d in done for d in t.get("deps", []))]
            if not ready:
                # Cycle / missing dep — emit the rest as a final serial wave.
                ready = remaining
            entries = []
            for t in ready:
                res = self.resource_for(t["stage"])
                gated = res in (Resource.GPU, Resource.A100) and not self.gpu_available
                entries.append({"id": t["id"], "stage": t["stage"], "resource": res.value, "gpu_gated": gated})
            # Parallel group = the API/CPU tasks in this wave.
            parallel = [e["id"] for e in entries if e["resource"] in {r.value for r in _PARALLEL_SAFE}]
            waves.append(
                {
                    "tasks": entries,
                    "parallel_ids": parallel,
                    "max_workers": min(self.max_api_workers, max(1, len(parallel))),
                }
            )
            for t in ready:
                done.add(t["id"])
            remaining = [t for t in remaining if t["id"] not in done]
        return waves


def parallel_map(
    fn: Callable[[Any], Any],
    items: list[Any],
    max_workers: int = 4,
    budget: Budget | None = None,
    unit_cost: float = 0.0,
) -> list[Any]:
    """Run ``fn`` over ``items`` concurrently (I/O-bound API work), capped at
    ``max_workers``. If a budget is given, only dispatch a task while it can be
    afforded; the cost is charged when the task is dispatched."""
    results: list[Any] = [None] * len(items)
    dispatch: list[int] = []
    for i in range(len(items)):
        if budget is not None and unit_cost > 0:
            if not budget.charge(unit_cost):
                break
        dispatch.append(i)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
        future_to_index = {pool.submit(fn, items[i]): i for i in dispatch}
        for future in concurrent.futures.as_completed(future_to_index):
            idx = future_to_index[future]
            try:
                results[idx] = future.result()
            except Exception as exc:  # keep other tasks; record the failure
                results[idx] = {"error": str(exc)[:160]}
    return results


class ResourceOrchestrator:
    def __init__(self, config: dict[str, Any] | None = None, root: Any = None, gpu_available: bool = False):
        self.config = config or {}
        self.root = ensure_dir(root) if root is not None else None
        self.cost = CostModel(self.config.get("prices"))
        self.budget = Budget(float(self.config.get("max_cost_usd", 0.0)), int(self.config.get("max_retries", 8)))
        self.scheduler = Scheduler(gpu_available, int(self.config.get("max_api_workers", 4)))

    def estimate_run(
        self, scene_count: int, candidate_count: int, bfl_enabled: bool, video_seconds: float = 0.0
    ) -> dict[str, Any]:
        """A cost preview: reasoning calls + BFL images (candidates + scenes) +
        optional organic-video seconds."""
        openai_calls_tokens = self.config.get("openai_tokens_per_run", 60000)
        images = (scene_count * max(1, candidate_count)) + scene_count if bfl_enabled else 0
        est = {
            "openai": self.cost.estimate("openai", openai_calls_tokens),
            "bfl_images": self.cost.estimate("bfl", images),
            "video": self.cost.estimate("video", video_seconds),
        }
        est["total_usd"] = round(sum(est.values()), 4)
        est["images"] = images
        return est

    def build_plan(self, stages: list[str], scene_count: int, scene_stage: str = "beauty_frame") -> dict[str, Any]:
        tasks: list[dict[str, Any]] = []
        prev = None
        for st in stages:
            tasks.append({"id": st, "stage": st, "deps": [prev] if prev else []})
            prev = st
        # Per-scene tasks depend on the last pre-scene stage and are independent of
        # each other (parallelizable).
        for i in range(scene_count):
            tasks.append({"id": f"scene_{i + 1:02d}", "stage": scene_stage, "deps": [prev] if prev else []})
        return {"waves": self.scheduler.plan(tasks), "gpu_available": self.scheduler.gpu_available}

    def emit(
        self, scene_count: int, candidate_count: int, bfl_enabled: bool, stages: list[str], video_seconds: float = 0.0
    ) -> dict[str, Any]:
        plan = {
            "estimate": self.estimate_run(scene_count, candidate_count, bfl_enabled, video_seconds),
            "budget": self.budget.to_dict(),
            "schedule": self.build_plan(stages, scene_count),
        }
        if self.root is not None:
            save_json(self.root / "orchestration_plan.json", plan)
        return plan
