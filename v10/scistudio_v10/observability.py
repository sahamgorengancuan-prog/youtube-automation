"""Structured observability: JSONL event log + provider-call records.

Every record is redacted before it is written. Prompts are never stored in
events — only prompt hashes; full prompts may be stored by artifact writers
under an explicit debug setting.
"""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Iterator

from .security import redact_secrets
from .utils import ensure_dir


class EventLogger:
    """Append-only JSONL event log for stage latency, retries and failures.

    Appends are serialized with a process-local lock; the log is intended for
    a single worker process per job directory (enforced by the job lock).
    """

    def __init__(self, root: str | Path):
        self.root = ensure_dir(root)
        self.path = self.root / "events.jsonl"
        self._lock = threading.Lock()

    def emit(self, event: str, **payload: Any) -> None:
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            **redact_secrets(payload),
        }
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)

    def record_provider_call(
        self,
        *,
        job_id: str = "",
        stage_id: str = "",
        provider: str = "",
        model: str = "",
        request_id: str = "",
        attempt: int = 1,
        input_hash: str = "",
        output_hash: str = "",
        latency_s: float = 0.0,
        cache: str = "miss",
        status: str = "ok",
        error_class: str = "",
        **extra: Any,
    ) -> None:
        """Standardized provider-call observability record."""
        self.emit(
            "provider.call",
            job_id=job_id,
            stage_id=stage_id,
            provider=provider,
            model=model,
            request_id=request_id,
            attempt=attempt,
            input_hash=input_hash,
            output_hash=output_hash,
            latency_s=round(float(latency_s), 4),
            cache=cache,
            status=status,
            error_class=error_class,
            **extra,
        )

    @contextmanager
    def timed(self, event: str, **payload: Any) -> Iterator[None]:
        start = time.perf_counter()
        self.emit(event + ".started", **payload)
        try:
            yield
        except Exception as exc:
            self.emit(
                event + ".failed",
                duration_s=round(time.perf_counter() - start, 4),
                error_type=type(exc).__name__,
                **payload,
            )
            raise
        else:
            self.emit(event + ".completed", duration_s=round(time.perf_counter() - start, 4), **payload)
