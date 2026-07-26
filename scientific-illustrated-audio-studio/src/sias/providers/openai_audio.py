"""OpenAI audio adapter: TTS + transcription with word/segment timestamps."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..exceptions import ProviderRequestError, ProviderSchemaError
from ..filesystem import atomic_write_bytes

Transport = Callable[..., tuple[int, Any]]
BASE_URL = "https://api.openai.com/v1"
MIN_AUDIO_BYTES = 2048


class OpenAIAudioAdapter:
    def __init__(self, api_key: str = "", transport: Transport | None = None):
        self.api_key = api_key
        self.transport = transport

    def _require(self, stage: str) -> Transport:
        if self.transport is None:
            raise ProviderRequestError(
                "OpenAI audio transport not configured (offline mode performs no paid calls)",
                stage=stage,
            )
        if not self.api_key:
            raise ProviderRequestError("OPENAI_API_KEY missing", stage=stage)
        return self.transport

    def tts(self, text: str, out_path: str | Path, model: str = "gpt-4o-mini-tts", voice: str = "cedar", fmt: str = "wav") -> Path:
        transport = self._require("openai_audio.tts")
        status, body = transport(
            "POST",
            f"{BASE_URL}/audio/speech",
            {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            {"model": model, "voice": voice, "input": text, "response_format": fmt},
        )
        if status != 200 or not isinstance(body, (bytes, bytearray)):
            raise ProviderRequestError(f"TTS failed (status {status})", stage="openai_audio.tts")
        if len(body) < MIN_AUDIO_BYTES:
            raise ProviderRequestError(
                f"TTS returned suspiciously small audio ({len(body)} bytes)", stage="openai_audio.tts"
            )
        return atomic_write_bytes(out_path, bytes(body))

    def transcribe(self, audio_path: str | Path, model: str = "whisper-1") -> dict[str, Any]:
        transport = self._require("openai_audio.transcribe")
        status, body = transport(
            "POST",
            f"{BASE_URL}/audio/transcriptions",
            {"Authorization": f"Bearer {self.api_key}"},
            {
                "file_path": str(audio_path),
                "model": model,
                "response_format": "verbose_json",
                "timestamp_granularities": ["word", "segment"],
            },
        )
        if status != 200 or not isinstance(body, dict):
            raise ProviderRequestError(f"transcription failed (status {status})", stage="openai_audio.transcribe")
        if "segments" not in body and "words" not in body:
            raise ProviderSchemaError(
                "transcription response lacks segments/words timestamps", stage="openai_audio.transcribe"
            )
        return body
