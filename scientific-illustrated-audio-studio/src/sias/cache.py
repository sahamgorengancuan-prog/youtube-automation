"""Cache reuse rules. A cached stage is reusable only when status is PASS,
the input hash matches, the artifact exists AND its SHA-256 matches.
File existence alone is never sufficient."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .filesystem import sha256_file
from .schemas import StageManifest


def stage_is_reusable(
    manifest: StageManifest | None,
    current_input_hash: str,
    integrity_check: Callable[[str], bool] | None = None,
) -> tuple[bool, str]:
    """Return (reusable, reason). The reason names the first failed condition."""
    if manifest is None:
        return False, "no manifest"
    if manifest.status != "PASS":
        return False, f"status is {manifest.status}, not PASS"
    if manifest.input_hash != current_input_hash:
        return False, "input hash changed"
    if not manifest.artifact_path:
        return False, "manifest has no artifact path"
    artifact = Path(manifest.artifact_path)
    if not artifact.exists():
        return False, "artifact missing on disk"
    if not manifest.artifact_sha256:
        return False, "manifest lacks artifact sha256"
    if sha256_file(artifact) != manifest.artifact_sha256:
        return False, "artifact sha256 mismatch (corrupt or replaced)"
    if integrity_check is not None and not integrity_check(str(artifact)):
        return False, "artifact failed integrity validation"
    return True, "reusable"
