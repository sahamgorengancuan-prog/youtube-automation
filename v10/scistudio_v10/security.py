"""Secret redaction utilities.

Redaction happens in three layers:

1. **Exact-value scrubbing** — every secret the process actually loaded is
   registered with :func:`register_secret`; any occurrence of the exact value
   in any string is replaced. This catches secrets embedded in URLs,
   tracebacks and provider error bodies regardless of format.
2. **Pattern scrubbing** — realistic secret shapes (OpenAI ``sk-`` keys,
   bearer tokens, ``x-key`` headers, UUID-style keys after a key-like word,
   long random tokens after ``key/token/secret/password`` assignments).
3. **Key-name scrubbing** — dictionary entries whose key names look
   secret-bearing are replaced wholesale.

``redact_secrets`` is safe to call on nested dict/list structures.
"""

from __future__ import annotations

import re
import threading
from typing import Any

REDACTED = "[REDACTED]"

_SECRET_KEY_MARKERS = ("key", "token", "secret", "password", "credential", "authorization")

_SECRET_PATTERNS = [
    # OpenAI-style keys (sk-..., sk-proj-...): long, no spaces.
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    # Bearer tokens.
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    # x-key / api-key style headers: `x-key: value` or `"x-key": "value"`.
    re.compile(r"(?i)(['\"]?x-?key['\"]?\s*[:=]\s*['\"]?)([^\s,'\"}]+)"),
    # key/token/secret/password assignments followed by the value.
    re.compile(
        r"(?i)\b((?:api[_-]?key|access[_-]?key|secret[_-]?key|auth[_-]?token|refresh[_-]?token|token|secret|password|passwd|authorization)\s*[:=]\s*['\"]?)([^\s,'\"}]{6,})"
    ),
    # UUID-shaped values directly after a key-like word (BFL keys are UUIDs).
    re.compile(r"(?i)\b(key\S{0,12}\s*[:=]\s*['\"]?)([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"),
]

_registry_lock = threading.Lock()
_registered_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Register a live secret value for exact-match scrubbing everywhere."""
    if value and isinstance(value, str) and len(value) >= 6:
        with _registry_lock:
            _registered_secrets.add(value)


def clear_registered_secrets() -> None:
    """Testing hook: forget all registered secret values."""
    with _registry_lock:
        _registered_secrets.clear()


def _scrub_text(text: str) -> str:
    with _registry_lock:
        known = list(_registered_secrets)
    for secret in known:
        if secret in text:
            text = text.replace(secret, REDACTED)

    def _sub(match: re.Match) -> str:
        if match.lastindex and match.lastindex >= 2:
            return match.group(1) + REDACTED
        return REDACTED

    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(_sub, text)
    return text


def redact_secrets(value: Any) -> Any:
    """Recursively redact secrets from strings, dicts and lists.

    Secret-named dictionary keys are redacted wholesale only when their value
    is a string — numeric metadata such as ``input_tokens``/``total_tokens``
    usage counts is never a credential and must survive for observability.
    """
    if isinstance(value, dict):
        return {
            key: (
                REDACTED
                if isinstance(item, str) and any(marker in str(key).lower() for marker in _SECRET_KEY_MARKERS)
                else redact_secrets(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_secrets(item) for item in value]
    if isinstance(value, str):
        return _scrub_text(value)
    return value


def redacted_exception_text(exc: BaseException, limit: int = 2000) -> str:
    """A single-line, redacted, length-capped rendering of an exception."""
    text = f"{type(exc).__name__}: {exc}"
    return _scrub_text(text)[:limit]
