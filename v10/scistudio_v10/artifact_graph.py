"""Artifact DAG — dependency-aware, deterministic recovery.

Stage-level resume (``job_runtime``) already prevents re-running completed stages
on a clean resume. This adds the finer artifact graph the operator asked for:

* **ArtifactNode** — per-output metadata: an ``artifact_key`` derived from
  ``(stage, input_hash, prompt_hash, model, model_version, config_hash)``, the
  provider + attempt, the dependency list, a node state machine and an attempt
  log.
* **Hash-based validity** — ``is_valid(node, key)`` answers "is this output still
  valid?" without re-calling a provider: same key + artifact present + checksum
  matches.
* **DAG invalidation** — changing an upstream node marks only its transitive
  descendants stale; unrelated branches stay cached. That is the difference
  between checkpoint resume and dependency-aware recovery.
* **Failure classification → repair routing** — an exception is classified
  (TRANSIENT / MODERATION / QUALITY_FAILURE / INVALID_INPUT / PROVIDER_FAILURE /
  FATAL) and mapped to a repair strategy, so recovery repairs the *right* node
  (rewrite the prompt vs. repair an upstream artifact vs. fall back a provider).
* **Lineage** — nodes.json + edges.json + per-node attempt records, so a final
  asset can be traced back through its attempts to the failure that caused each.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from .utils import ensure_dir, hash_value, load_json, save_json, sha256_file


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    GENERATED = "generated"
    VALIDATING = "validating"
    VALID = "valid"
    FAILED = "failed"
    REPAIRING = "repairing"
    RETRYING = "retrying"
    STALE = "stale"
    SKIPPED = "skipped"


class FailureClass(str, Enum):
    TRANSIENT = "transient"
    MODERATION = "moderation"
    QUALITY_FAILURE = "quality_failure"
    INVALID_INPUT = "invalid_input"
    PROVIDER_FAILURE = "provider_failure"
    FATAL = "fatal"


# Each failure class routes to the node/strategy that must change to recover.
REPAIR_STRATEGY: dict[FailureClass, dict[str, str]] = {
    FailureClass.TRANSIENT: {"strategy": "retry_same_input", "target": "self"},
    FailureClass.MODERATION: {"strategy": "prompt_safety_rewrite", "target": "self"},
    FailureClass.QUALITY_FAILURE: {"strategy": "regenerate_with_variation", "target": "self"},
    FailureClass.INVALID_INPUT: {"strategy": "repair_upstream_artifact", "target": "parent"},
    FailureClass.PROVIDER_FAILURE: {"strategy": "fallback_provider", "target": "self"},
    FailureClass.FATAL: {"strategy": "human_review", "target": "self"},
}


def classify_failure(exc: Exception) -> FailureClass:
    """Classify an exception so recovery knows which node/strategy to apply."""
    try:
        from .prompt_safety import is_moderation_error

        if is_moderation_error(exc):
            return FailureClass.MODERATION
    except Exception:
        if "moderat" in str(exc).lower():
            return FailureClass.MODERATION
    name = type(exc).__name__
    msg = str(exc).lower()
    if getattr(exc, "retryable", False) or any(
        t in msg for t in ("timeout", "rate limit", "429", "temporarily", "503", "502")
    ):
        return FailureClass.TRANSIENT
    if "QualityError" in name or "quality" in msg or "qc failed" in msg:
        return FailureClass.QUALITY_FAILURE
    if "Auth" in name or "unauthor" in msg or "ProviderUnavailable" in name or "401" in msg or "403" in msg:
        return FailureClass.PROVIDER_FAILURE
    if name in {"ValueError", "ValidationError", "KeyError"} or "invalid" in msg or "required" in msg:
        return FailureClass.INVALID_INPUT
    return FailureClass.FATAL


class ArtifactNode:
    def __init__(
        self,
        node_id: str,
        artifact_type: str,
        dependencies: list[str] | None = None,
        stage: str = "",
    ):
        self.node_id = node_id
        self.artifact_type = artifact_type
        self.stage = stage or artifact_type
        self.dependencies = list(dependencies or [])
        self.status = NodeStatus.PENDING
        self.inputs: dict[str, str] = {}
        self.provider: dict[str, Any] = {}
        self.artifact_key = ""
        self.output_path = ""
        self.checksum = ""
        self.attempts: list[dict[str, Any]] = []
        self.failure: dict[str, Any] | None = None
        self.repair: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "artifact_type": self.artifact_type,
            "stage": self.stage,
            "dependencies": self.dependencies,
            "status": self.status.value,
            "inputs": self.inputs,
            "provider": self.provider,
            "artifact_key": self.artifact_key,
            "output_path": self.output_path,
            "checksum": self.checksum,
            "attempts": self.attempts,
            "failure": self.failure,
            "repair": self.repair,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactNode:
        node = cls(data["node_id"], data.get("artifact_type", ""), data.get("dependencies", []), data.get("stage", ""))
        node.status = NodeStatus(data.get("status", "pending"))
        node.inputs = data.get("inputs", {})
        node.provider = data.get("provider", {})
        node.artifact_key = data.get("artifact_key", "")
        node.output_path = data.get("output_path", "")
        node.checksum = data.get("checksum", "")
        node.attempts = data.get("attempts", [])
        node.failure = data.get("failure")
        node.repair = data.get("repair")
        return node


class ArtifactGraph:
    """A persisted DAG of artifact nodes with dependency-aware validity."""

    def __init__(self, root: str | Path):
        self.root = ensure_dir(root)
        self.graph_dir = ensure_dir(self.root / "graph")
        self.nodes: dict[str, ArtifactNode] = {}
        self.load()

    # -- construction -------------------------------------------------------
    def add_node(
        self,
        node_id: str,
        artifact_type: str,
        dependencies: list[str] | None = None,
        stage: str = "",
    ) -> ArtifactNode:
        node = self.nodes.get(node_id)
        if node is None:
            node = ArtifactNode(node_id, artifact_type, dependencies, stage)
            self.nodes[node_id] = node
        else:
            if dependencies is not None:
                node.dependencies = list(dependencies)
        return node

    @staticmethod
    def artifact_key(
        stage: str,
        input_hash: str,
        prompt_hash: str = "",
        model: str = "",
        model_version: str = "",
        config_hash: str = "",
    ) -> str:
        return hash_value(
            {
                "stage": stage,
                "input_hash": input_hash,
                "prompt_hash": prompt_hash,
                "model": model,
                "model_version": model_version,
                "config_hash": config_hash,
            },
            24,
        )

    # -- validity -----------------------------------------------------------
    def is_valid(self, node_id: str, expected_key: str) -> bool:
        """True if the node is VALID, its key matches, and its artifact is present
        and checksum-consistent. This is the "is this output still valid?" gate."""
        node = self.nodes.get(node_id)
        if node is None or node.status != NodeStatus.VALID:
            return False
        if node.artifact_key != expected_key:
            return False
        if node.output_path:
            path = Path(node.output_path)
            if not path.exists():
                return False
            if node.checksum and sha256_file(path) != node.checksum:
                return False
        return True

    # -- transitions --------------------------------------------------------
    def begin(
        self,
        node_id: str,
        artifact_key: str,
        provider: dict[str, Any] | None = None,
        inputs: dict[str, str] | None = None,
    ) -> ArtifactNode:
        node = self.add_node(node_id, self.nodes[node_id].artifact_type if node_id in self.nodes else node_id)
        node.artifact_key = artifact_key
        if provider is not None:
            node.provider = provider
        if inputs is not None:
            node.inputs = inputs
        node.status = NodeStatus.RUNNING
        return node

    def mark_valid(self, node_id: str, output_path: str = "") -> None:
        node = self.nodes[node_id]
        node.status = NodeStatus.VALID
        node.failure = None
        node.repair = None
        if output_path:
            node.output_path = str(output_path)
            if Path(output_path).exists():
                node.checksum = sha256_file(Path(output_path))

    def record_failure(self, node_id: str, exc: Exception, provider_name: str = "") -> dict[str, Any]:
        """Classify a failure, set the node to REPAIRING with a repair plan, and
        append an attempt record. Returns the repair plan."""
        node = self.nodes[node_id]
        fclass = classify_failure(exc)
        plan = dict(REPAIR_STRATEGY[fclass])
        node.status = NodeStatus.REPAIRING
        node.failure = {
            "type": fclass.value,
            "provider": provider_name or node.provider.get("name", ""),
            "message": str(exc)[:200],
        }
        node.repair = {"strategy": plan["strategy"], "target": plan["target"], "next_action": "retry_generation"}
        node.attempts.append(
            {
                "attempt": len(node.attempts) + 1,
                "status": "failed",
                "failure_type": fclass.value,
                "strategy": plan["strategy"],
            }
        )
        return {"failure_class": fclass, **plan}

    def record_attempt_success(self, node_id: str, provider: dict[str, Any] | None = None) -> None:
        node = self.nodes[node_id]
        node.attempts.append(
            {"attempt": len(node.attempts) + 1, "status": "succeeded", **({"provider": provider} if provider else {})}
        )

    # -- DAG operations -----------------------------------------------------
    def children_of(self, node_id: str) -> list[str]:
        return [nid for nid, n in self.nodes.items() if node_id in n.dependencies]

    def descendants_of(self, node_id: str) -> list[str]:
        seen: set[str] = set()
        stack = [node_id]
        while stack:
            cur = stack.pop()
            for child in self.children_of(cur):
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        return sorted(seen)

    def invalidate_downstream(self, node_id: str) -> list[str]:
        """Mark the transitive descendants of ``node_id`` stale (dependency-aware).
        Unrelated branches are untouched. Returns the invalidated node ids."""
        invalidated = self.descendants_of(node_id)
        for nid in invalidated:
            node = self.nodes[nid]
            if node.status in {NodeStatus.VALID, NodeStatus.GENERATED}:
                node.status = NodeStatus.STALE
        return invalidated

    # -- persistence --------------------------------------------------------
    def save(self) -> None:
        save_json(self.graph_dir / "nodes.json", {nid: n.to_dict() for nid, n in self.nodes.items()})
        edges = [{"from": dep, "to": nid} for nid, n in self.nodes.items() for dep in n.dependencies]
        save_json(self.graph_dir / "edges.json", edges)

    def load(self) -> None:
        data = load_json(self.graph_dir / "nodes.json")
        if isinstance(data, dict):
            self.nodes = {nid: ArtifactNode.from_dict(nd) for nid, nd in data.items()}

    def attempt_dir(self, node_id: str, attempt: int) -> Path:
        return ensure_dir(self.root / "attempts" / node_id / f"attempt_{attempt:03d}")
