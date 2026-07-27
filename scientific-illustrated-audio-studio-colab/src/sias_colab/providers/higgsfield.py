"""OPTIONAL Higgsfield adapter — image provider fallback, live model/cost
discovery, and the post-render Virality Predictor (disabled by default).

Written against the documented higgsfield-client/CLI surface (pinned in
repo_sources.lock.json); mock-tested only — mark any live use as unverified
until a real canary run. SIAS works fully without Higgsfield credentials.
Never used for video generation in the core pipeline (image-first invariant).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from sias.filesystem import atomic_write_bytes

from ..exceptions import ProviderRequestError, ProviderSchemaError

Transport = Callable[..., tuple[int, Any]]
BASE_URL = "https://platform.higgsfield.ai/v1"
MIN_IMAGE_BYTES = 4096


def resolve_credentials(env: dict[str, str] | None = None) -> dict[str, str]:
    """Support HF_KEY, or HF_API_KEY + HF_API_SECRET. Values never logged."""
    env = env if env is not None else dict(os.environ)
    if env.get("HF_KEY"):
        return {"scheme": "key", "key": env["HF_KEY"]}
    if env.get("HF_API_KEY") and env.get("HF_API_SECRET"):
        return {"scheme": "key_secret", "key": env["HF_API_KEY"], "secret": env["HF_API_SECRET"]}
    return {}


class HiggsfieldAdapter:
    def __init__(self, credentials: dict[str, str] | None = None, transport: Transport | None = None):
        self.credentials = credentials or {}
        self.transport = transport

    @property
    def available(self) -> bool:
        return bool(self.credentials) and self.transport is not None

    def _headers(self) -> dict[str, str]:
        if self.credentials.get("scheme") == "key_secret":
            return {"hf-api-key": self.credentials["key"], "hf-secret": self.credentials["secret"]}
        return {"Authorization": f"Bearer {self.credentials.get('key', '')}"}

    def _require(self, stage: str) -> Transport:
        if self.transport is None:
            raise ProviderRequestError("Higgsfield transport not configured (optional provider)", stage=stage)
        if not self.credentials:
            raise ProviderRequestError("Higgsfield credentials missing (HF_KEY or HF_API_KEY+HF_API_SECRET)", stage=stage)
        return self.transport

    def list_models(self) -> list[dict[str, Any]]:
        transport = self._require("higgsfield.models")
        status, body = transport("GET", f"{BASE_URL}/models", self._headers(), None)
        if status != 200 or not isinstance(body, (list, dict)):
            raise ProviderSchemaError(f"unexpected model list (status {status})", stage="higgsfield.models")
        return body if isinstance(body, list) else list(body.get("models", body.get("data", [])))

    def estimate_cost(self, model: str, n: int = 1) -> dict[str, Any]:
        transport = self._require("higgsfield.cost")
        status, body = transport("GET", f"{BASE_URL}/models/{model}/cost?n={n}", self._headers(), None)
        if status != 200 or not isinstance(body, dict):
            raise ProviderSchemaError(f"unexpected cost response (status {status})", stage="higgsfield.cost")
        return body

    def generate_image(self, prompt: str, model: str, out_path: str | Path, params: dict[str, Any] | None = None) -> Path:
        transport = self._require("higgsfield.generate")
        status, body = transport(
            "POST", f"{BASE_URL}/generate", self._headers(),
            {"model": model, "prompt": prompt, **(params or {})},
        )
        if status != 200:
            raise ProviderRequestError(f"generation failed (status {status})", stage="higgsfield.generate")
        if isinstance(body, dict):
            url = body.get("url") or (body.get("images") or [{}])[0].get("url", "")
            if not url:
                raise ProviderSchemaError("generate response lacks an image url", stage="higgsfield.generate")
            status2, data = transport("GET", url, {}, None)
            if status2 != 200 or not isinstance(data, (bytes, bytearray)):
                raise ProviderRequestError("image download failed", stage="higgsfield.generate")
            body = data
        if not isinstance(body, (bytes, bytearray)) or len(body) < MIN_IMAGE_BYTES:
            raise ProviderRequestError("image payload invalid or suspiciously small", stage="higgsfield.generate")
        return atomic_write_bytes(out_path, bytes(body))

    def analyze_video(self, video_path: str | Path) -> dict[str, Any]:
        """Virality Predictor — post-render only, opt-in, MAY INCUR COST."""
        transport = self._require("higgsfield.virality")
        status, body = transport(
            "POST", f"{BASE_URL}/analyze", self._headers(), {"video_path": str(video_path)}
        )
        if status != 200 or not isinstance(body, dict):
            raise ProviderSchemaError(f"unexpected analysis response (status {status})", stage="higgsfield.virality")
        for field in ("hook", "attention", "retention"):
            if field not in body:
                raise ProviderSchemaError(f"analysis missing {field!r}", stage="higgsfield.virality")
        return body


def parse_cli_model_list(cli_output: str) -> list[dict[str, Any]]:
    """Parse `higgsfield model list` JSON output — live schema discovery instead
    of hard-coding model schemas."""
    try:
        data = json.loads(cli_output)
    except json.JSONDecodeError as exc:
        raise ProviderSchemaError(f"CLI output is not JSON: {exc}", stage="higgsfield.cli") from exc
    models = data if isinstance(data, list) else data.get("models", [])
    if not isinstance(models, list):
        raise ProviderSchemaError("CLI model list has unexpected shape", stage="higgsfield.cli")
    return models
