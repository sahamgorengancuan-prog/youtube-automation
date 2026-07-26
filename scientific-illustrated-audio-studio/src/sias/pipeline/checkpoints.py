"""Checkpointed stage execution: manifest per stage, hash-validated reuse,
failed manifests on error — never a fake success artifact."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..cache import stage_is_reusable
from ..filesystem import input_hash, load_json
from ..manifests import now_iso, read_manifest, write_manifest
from ..schemas import StageManifest
from .stages import PARENTS


def run_stage(
    manifest_dir: str | Path,
    stage_id: str,
    inputs: Any,
    producer: Callable[[], str],
    provider: str = "",
    model: str = "",
    force: bool = False,
) -> StageManifest:
    """Execute a stage with checkpointing. `producer` returns the artifact path.
    On exception a FAILED manifest is written and the error re-raised."""
    current_hash = input_hash(inputs)
    existing = read_manifest(manifest_dir, stage_id)
    if not force:
        reusable, _reason = stage_is_reusable(existing, current_hash)
        if reusable:
            return existing  # type: ignore[return-value]

    manifest = StageManifest(
        stage_id=stage_id,
        status="RUNNING",
        input_hash=current_hash,
        provider=provider,
        model=model,
        started_at=now_iso(),
        parent_stage_ids=PARENTS.get(stage_id, []),
    )
    try:
        artifact = producer()
        manifest.artifact_path = str(artifact)
        manifest.status = "PASS"
        write_manifest(manifest_dir, manifest)
        return manifest
    except Exception as exc:
        manifest.status = "FAILED"
        manifest.errors.append(f"{type(exc).__name__}: {exc}")
        write_manifest(manifest_dir, manifest)
        raise


def read_stage_artifact(manifest_dir: str | Path, stage_id: str, default: Any = None) -> Any:
    manifest = read_manifest(manifest_dir, stage_id)
    if manifest is None or manifest.status != "PASS":
        return default
    return load_json(manifest.artifact_path, default)
