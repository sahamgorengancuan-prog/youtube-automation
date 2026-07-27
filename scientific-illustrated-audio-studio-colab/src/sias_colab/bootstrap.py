"""Make the sibling `sias` engine importable in every source mode.

Modes: standalone (pip-installed), github_repository (repo cloned somewhere),
uploaded_zip (extracted anywhere). We search upward from this file and a few
conventional Colab locations; if `sias` is already importable we do nothing.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_CANDIDATES = [
    # repo layout: <repo>/scientific-illustrated-audio-studio/src
    Path(__file__).resolve().parents[3] / "scientific-illustrated-audio-studio" / "src",
    # zip/flat layout: engine next to this package's src tree
    Path(__file__).resolve().parents[2] / "scientific-illustrated-audio-studio" / "src",
    # common Colab clone locations
    Path("/content/youtube-automation/scientific-illustrated-audio-studio/src"),
    Path("/content/scientific-illustrated-audio-studio/src"),
]


def ensure_engine_importable() -> str:
    if importlib.util.find_spec("sias") is not None:
        return "already-importable"
    for candidate in _CANDIDATES:
        if (candidate / "sias" / "__init__.py").exists():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            return str(candidate)
    raise ImportError(
        "The `sias` engine package was not found. Install it (pip install -e "
        "scientific-illustrated-audio-studio) or keep both directories side by "
        "side (github_repository / uploaded_zip modes)."
    )
