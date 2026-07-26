"""Stage manifests — every stage writes one; resume trusts hashes, not
file existence."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from .filesystem import atomic_write_json, load_json, sha256_file
from .schemas import StageManifest


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def manifest_path(manifest_dir: str | Path, stage_id: str) -> Path:
    return Path(manifest_dir) / f"{stage_id}.manifest.json"


def write_manifest(manifest_dir: str | Path, manifest: StageManifest) -> Path:
    if manifest.artifact_path and Path(manifest.artifact_path).exists():
        manifest.artifact_sha256 = sha256_file(manifest.artifact_path)
    if not manifest.finished_at:
        manifest.finished_at = now_iso()
    path = manifest_path(manifest_dir, manifest.stage_id)
    atomic_write_json(path, manifest.model_dump())
    return path


def read_manifest(manifest_dir: str | Path, stage_id: str) -> StageManifest | None:
    data = load_json(manifest_path(manifest_dir, stage_id))
    return StageManifest.model_validate(data) if data else None
