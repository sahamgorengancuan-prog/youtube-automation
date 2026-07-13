from __future__ import annotations

import logging
import os
from pathlib import Path


def configure_logging(name: str = "autostudio") -> logging.Logger:
    """Idempotent logger factory. Streams to stdout and appends to logs/studio.log."""
    root = Path(os.environ.get("AUTOSTUDIO_ROOT", ".")).resolve()
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    file_handler = logging.FileHandler(log_dir / "studio.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(stream)
    logger.addHandler(file_handler)
    logger.propagate = False
    return logger
