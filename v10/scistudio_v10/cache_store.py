"""Content-addressed artifact cache.

* Keys are derived from the full input payload hash (never filename+size).
* All writes are atomic (temp file + rename) — a killed process cannot leave
  a partial cache artifact.
* Corrupt JSON entries are quarantined and treated as cache misses.
* Fallback entries live in a dedicated namespace so degraded results can
  never masquerade as live provider output.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import atomic_copy, ensure_dir, hash_value, load_json, save_json

FALLBACK_PREFIX = "fallback-"


class ArtifactCache:
    """Content-addressed cache for expensive LLM, FLUX and render stages."""

    def __init__(self, root: str | Path):
        self.root = ensure_dir(root)

    def key(self, namespace: str, payload: Any) -> str:
        """Cache key: namespace + SHA-256 over the canonical payload JSON."""
        return f"{namespace}-{hash_value(payload, 32)}"

    def fallback_key(self, namespace: str, payload: Any) -> str:
        """Separate namespace for degraded/fallback results."""
        return f"{FALLBACK_PREFIX}{self.key(namespace, payload)}"

    def json_path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def file_path(self, key: str, suffix: str) -> Path:
        return self.root / key[:2] / f"{key}{suffix}"

    def get_json(self, key: str) -> Any | None:
        """Read a cached JSON value; corrupt entries are quarantined and
        reported as misses instead of raising."""
        path = self.json_path(key)
        if not path.exists():
            return None
        return load_json(path, None)

    def put_json(self, key: str, value: Any) -> str:
        path = self.json_path(key)
        ensure_dir(path.parent)
        save_json(path, value)
        return str(path)

    def get_file(self, key: str, suffix: str) -> str | None:
        path = self.file_path(key, suffix)
        return str(path) if path.exists() and path.stat().st_size > 0 else None

    def put_file(self, key: str, source: str | Path, suffix: str | None = None) -> str:
        source_path = Path(source)
        extension = suffix or source_path.suffix
        destination = self.file_path(key, extension)
        ensure_dir(destination.parent)
        atomic_copy(source_path, destination)
        return str(destination)
