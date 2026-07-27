"""Live HTTP transports (urllib, no extra deps) + adapter factory.

Only used when the user explicitly runs live; importing this module makes no
network call. Transcription uses real multipart upload; everything else is
JSON-in/JSON-or-bytes-out matching the adapter transport contract."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from sias.providers.bfl import BFLAdapter
from sias.providers.model_resolver import resolve_model
from sias.providers.openai_audio import OpenAIAudioAdapter
from sias.providers.openrouter import OpenRouterAdapter

from ..exceptions import ProviderRequestError


def http_transport(method: str, url: str, headers: dict[str, str], body: Any) -> tuple[int, Any]:
    data = None
    headers = dict(headers or {})
    if body is not None and not isinstance(body, (bytes, bytearray)):
        data = json.dumps(body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    elif isinstance(body, (bytes, bytearray)):
        data = bytes(body)
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:  # noqa: S310 - https provider endpoints
            raw = response.read()
            ctype = response.headers.get("Content-Type", "")
            status = response.status
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()[:2000]
    except urllib.error.URLError as exc:
        raise ProviderRequestError(f"network error: {exc.reason}", stage="live_transport") from exc
    if "application/json" in ctype:
        try:
            return status, json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return status, raw
    return status, raw


class LiveOpenAIAudio(OpenAIAudioAdapter):
    """Real TTS (JSON→bytes) + real multipart transcription."""

    def transcribe(self, audio_path: str | Path, model: str = "whisper-1") -> dict[str, Any]:
        # Goes through self.transport (http_transport in production) rather than
        # calling urllib directly, so the multipart path is injectable and can be
        # exercised against simulated responses.
        transport = self._require("openai_audio.transcribe")
        boundary = uuid.uuid4().hex
        audio = Path(audio_path).read_bytes()
        parts: list[bytes] = []

        def field(name: str, value: str) -> None:
            parts.append(
                (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").encode()
            )

        field("model", model)
        field("response_format", "verbose_json")
        field("timestamp_granularities[]", "word")
        field("timestamp_granularities[]", "segment")
        parts.append(
            (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
             f"filename=\"narration.wav\"\r\nContent-Type: audio/wav\r\n\r\n").encode() + audio + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode())
        body = b"".join(parts)
        status, parsed = transport(
            "POST",
            "https://api.openai.com/v1/audio/transcriptions",
            {"Authorization": f"Bearer {self.api_key}",
             "Content-Type": f"multipart/form-data; boundary={boundary}"},
            body,
        )
        if status != 200 or not isinstance(parsed, dict):
            raise ProviderRequestError(f"transcription failed (status {status})", stage="openai_audio.transcribe")
        if "segments" not in parsed and "words" not in parsed:
            raise ProviderRequestError("transcription lacks timestamps", stage="openai_audio.transcribe")
        return parsed


def build_live_adapters(poll_sleep=time.sleep) -> dict[str, Any]:
    """Adapters for every key present in the environment. Missing keys simply
    yield no adapter — callers decide whether that stage runs."""
    adapters: dict[str, Any] = {}
    if os.environ.get("BFL_API_KEY"):
        adapters["bfl"] = BFLAdapter(api_key=os.environ["BFL_API_KEY"], transport=http_transport)
    if os.environ.get("OPENAI_API_KEY"):
        adapters["openai_audio"] = LiveOpenAIAudio(api_key=os.environ["OPENAI_API_KEY"], transport=http_transport)
    if os.environ.get("OPENROUTER_API_KEY"):
        router = OpenRouterAdapter(api_key=os.environ["OPENROUTER_API_KEY"], transport=http_transport)
        adapters["openrouter"] = router
        try:
            catalog = router.list_models()
            adapters["qwen_model"] = resolve_model(catalog, "qwen/", ["vl"])
            adapters["gemini_model"] = resolve_model(catalog, "google/", ["flash", "vision"])
        except Exception:
            adapters["qwen_model"] = "qwen/qwen2.5-vl-72b-instruct"
            adapters["gemini_model"] = "google/gemini-2.5-flash"
    return adapters
