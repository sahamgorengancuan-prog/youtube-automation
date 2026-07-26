"""Structured logging with secret redaction. API keys never reach logs."""

from __future__ import annotations

import json
import logging
import re
import sys

_SECRETS: set[str] = set()
_KEY_PATTERNS = [
    re.compile(r"(sk-[A-Za-z0-9_\-]{8,})"),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", re.IGNORECASE),
    re.compile(r"([A-Za-z0-9_\-]{24,})"),
]


def register_secret(value: str) -> None:
    if value and len(value) >= 8:
        _SECRETS.add(value)


def redact(text: str) -> str:
    out = text
    for s in _SECRETS:
        out = out.replace(s, "[REDACTED]")
    out = _KEY_PATTERNS[0].sub("[REDACTED-KEY]", out)
    out = _KEY_PATTERNS[1].sub(r"\1[REDACTED]", out)
    return out


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        if record.exc_info and record.exc_info[1] is not None:
            payload["error"] = redact(str(record.exc_info[1]))
        return json.dumps(payload, ensure_ascii=False)


def get_logger(name: str = "sias", structured: bool = True, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            _JsonFormatter() if structured else logging.Formatter("%(levelname)s %(name)s: %(message)s")
        )
        logger.addHandler(handler)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logger
