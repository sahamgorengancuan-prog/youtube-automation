"""SIAS Diamond — routed, multi-backend production layer.

Primary stack (per operator direction): OpenAI (story/TTS/transcription),
BFL (images), OpenRouter Qwen VL 32B (structural QC) + Gemini 2.5 Flash
(editorial QC). Open-source backends are FALLBACK / SECOND-OPINION /
OBJECTIVE-MEASURE tiers — integrated where they add accuracy, never replacing
the defaults, never enabled while their weight license is unaudited.

The "SIAS Diamond Editorial Standard" is an internal production threshold,
not a claim about audience outcomes.
"""

from .registry import BackendDescriptor, ServiceRegistry, build_default_registry  # noqa: F401
from .standard import DiamondGate  # noqa: F401
