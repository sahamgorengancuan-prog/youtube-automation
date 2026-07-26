"""OpenAI text adapter: structured story generation and script revision.
Injectable transport; strict JSON validation; offline calls fail explicitly."""

from __future__ import annotations

import json
from typing import Any, Callable

from ..exceptions import ProviderRequestError, ProviderSchemaError

Transport = Callable[..., tuple[int, Any]]
BASE_URL = "https://api.openai.com/v1"


class OpenAITextAdapter:
    def __init__(self, api_key: str = "", transport: Transport | None = None, model: str = "gpt-5-mini"):
        self.api_key = api_key
        self.transport = transport
        self.model = model

    def structured_json(self, system: str, prompt: str, namespace: str = "") -> dict[str, Any]:
        if self.transport is None:
            raise ProviderRequestError(
                "OpenAI text transport not configured (offline mode performs no paid calls)",
                stage=f"openai_text.{namespace}",
            )
        if not self.api_key:
            raise ProviderRequestError("OPENAI_API_KEY missing", stage=f"openai_text.{namespace}")
        status, body = self.transport(
            "POST",
            f"{BASE_URL}/chat/completions",
            {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "response_format": {"type": "json_object"},
            },
        )
        if status != 200 or not isinstance(body, dict):
            raise ProviderRequestError(f"OpenAI text call failed (status {status})", stage=f"openai_text.{namespace}")
        try:
            content = body["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderSchemaError(
                f"OpenAI response is not the expected JSON shape: {exc}",
                stage=f"openai_text.{namespace}",
            ) from exc
        if not isinstance(parsed, dict):
            raise ProviderSchemaError("OpenAI JSON content is not an object", stage=f"openai_text.{namespace}")
        return parsed
