"""OpenRouter adapter for Qwen/Gemini vision review. Data-collection denial is
sent when configured; malformed reviewer JSON is a ProviderSchemaError, never
silently accepted."""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any, Callable

from ..exceptions import ProviderRequestError, ProviderSchemaError

Transport = Callable[..., tuple[int, Any]]
BASE_URL = "https://openrouter.ai/api/v1"

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class OpenRouterAdapter:
    def __init__(self, api_key: str = "", transport: Transport | None = None, deny_data_collection: bool = True):
        self.api_key = api_key
        self.transport = transport
        self.deny_data_collection = deny_data_collection

    def _require(self, stage: str) -> Transport:
        if self.transport is None:
            raise ProviderRequestError(
                "OpenRouter transport not configured (offline mode performs no paid calls)",
                stage=stage,
            )
        if not self.api_key:
            raise ProviderRequestError("OPENROUTER_API_KEY missing", stage=stage)
        return self.transport

    def list_models(self) -> list[dict[str, Any]]:
        transport = self._require("openrouter.models")
        status, body = transport("GET", f"{BASE_URL}/models", {"Authorization": f"Bearer {self.api_key}"}, None)
        if status != 200 or not isinstance(body, dict) or "data" not in body:
            raise ProviderSchemaError(f"unexpected model catalog response (status {status})", stage="openrouter.models")
        return list(body["data"])

    def vision_review(self, model: str, image_path: str | Path, rubric_prompt: str) -> dict[str, Any]:
        transport = self._require("openrouter.vision")
        image_b64 = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": rubric_prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                        },
                    ],
                }
            ],
        }
        if self.deny_data_collection:
            payload["provider"] = {"data_collection": "deny"}
        status, body = transport(
            "POST",
            f"{BASE_URL}/chat/completions",
            {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            payload,
        )
        if status != 200 or not isinstance(body, dict):
            raise ProviderRequestError(f"vision review failed (status {status})", stage="openrouter.vision")
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderSchemaError(f"vision response missing content: {exc}", stage="openrouter.vision") from exc
        match = _JSON_BLOCK.search(content or "")
        if not match:
            raise ProviderSchemaError("vision reviewer returned no JSON object", stage="openrouter.vision")
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ProviderSchemaError(f"vision reviewer JSON malformed: {exc}", stage="openrouter.vision") from exc
        if not isinstance(parsed, dict):
            raise ProviderSchemaError("vision reviewer JSON is not an object", stage="openrouter.vision")
        return parsed
