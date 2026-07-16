"""Publishing adapters.

``UploadPostPublisher`` only ever posts to an https endpoint whose host is in
the explicitly configured allowlist — a bearer token is never sent to an
arbitrary or plain-http destination.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from .http_safety import validate_url
from .security import redacted_exception_text, register_secret
from .utils import atomic_copy, ensure_dir, save_json


class Publisher(Protocol):
    publisher_id: str

    def publish(self, video_path: str, metadata: dict[str, Any]) -> dict[str, Any]: ...


class LocalArchivePublisher:
    publisher_id = "local-archive"

    def __init__(self, root: str | Path):
        self.root = ensure_dir(root)

    def publish(self, video_path: str, metadata: dict[str, Any]) -> dict[str, Any]:
        source = Path(video_path)
        if not source.exists():
            raise FileNotFoundError(source)
        destination = self.root / source.name
        atomic_copy(source, destination)
        manifest = {
            "publisher": self.publisher_id,
            "video": str(destination),
            "metadata": metadata,
            "status": "archived",
        }
        save_json(destination.with_suffix(".publish.json"), manifest)
        return manifest


class UploadPostPublisher:
    """Minimal optional Upload-Post compatible adapter.

    The endpoint must be https and its host must appear in *allowed_hosts*;
    both come from configuration. Not invoked unless publishing is enabled.
    """

    publisher_id = "upload-post"

    def __init__(self, endpoint: str, token: str, allowed_hosts: list[str] | None = None):
        self.endpoint = endpoint
        self.token = token
        self.allowed_hosts = list(allowed_hosts or [])
        register_secret(token)

    def publish(self, video_path: str, metadata: dict[str, Any]) -> dict[str, Any]:
        if not self.endpoint or not self.token:
            raise RuntimeError("Upload-Post endpoint/token are required")
        validate_url(self.endpoint, allowed_hosts=self.allowed_hosts, purpose="publishing endpoint")
        payload = json.dumps({"video_path": video_path, "metadata": metadata}).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 - https+allowlist enforced above
                return json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise RuntimeError(f"Upload-Post publish failed: {redacted_exception_text(exc, 300)}") from exc
