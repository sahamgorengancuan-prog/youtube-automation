"""SIAS Agentic Colab Studio — the Colab product layer over the `sias` engine.

Image-first, never video-first. Audio is the timeline source of truth.
Importing this package performs NO network access and NO paid calls.
"""

from . import bootstrap as _bootstrap

_bootstrap.ensure_engine_importable()

__version__ = "2.0.0"
