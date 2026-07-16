"""Artifact provenance manifest.

Connects every stage of a run — topic → research → script → storyboard →
architecture → references → prompts → FLUX outputs → approvals → semantic
layers → animation → render — with content hashes so any published video can
be traced back to the exact inputs that produced it.
"""

from __future__ import annotations

from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from .utils import hash_value, save_json, sha256_file

PROVENANCE_SCHEMA_VERSION = "1.0"


def _artifact_entry(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {"path": "", "exists": False, "sha256": ""}
    path = Path(path)
    if not path.exists() or not path.is_file():
        return {"path": str(path), "exists": False, "sha256": ""}
    return {"path": str(path), "exists": True, "sha256": sha256_file(path)}


def build_provenance_manifest(
    *,
    job_id: str,
    topic: str,
    execution_mode: str,
    config_hash: str,
    artifacts: dict[str, str | Path | None],
    providers: dict[str, str],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Assemble the provenance manifest for one pipeline run.

    ``artifacts`` maps stage names (script, storyboard, beauty_frames, video,
    ...) to file paths; each entry is recorded with existence and SHA-256.
    """
    entries = {name: _artifact_entry(path) for name, path in artifacts.items()}
    manifest = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "job_id": job_id,
        "topic": topic,
        "execution_mode": execution_mode,
        "config_hash": config_hash,
        "created_at": datetime.now(UTC).isoformat(),
        "providers": providers,
        "artifacts": entries,
        "warnings": list(warnings or []),
    }
    manifest["provenance_hash"] = hash_value({k: v for k, v in manifest.items() if k != "provenance_hash"}, 32)
    return manifest


def write_provenance_manifest(run_dir: str | Path, manifest: dict[str, Any]) -> Path:
    path = Path(run_dir) / "provenance_manifest.json"
    save_json(path, manifest)
    return path
