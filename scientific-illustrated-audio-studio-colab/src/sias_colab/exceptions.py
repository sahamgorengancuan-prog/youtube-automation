"""Typed exceptions: the engine's, plus Colab-layer ones."""

from __future__ import annotations

from sias.exceptions import (  # noqa: F401
    AlignmentError,
    AssetIntegrityError,
    BudgetExceededError,
    ConfigurationError,
    FinalQCError,
    ProviderRequestError,
    ProviderSchemaError,
    RenderError,
    RepairLimitError,
    SIASError,
    SilentAudioError,
    StyleLockError,
    VisionHardFailError,
)


class PaidCallBlockedError(SIASError):
    """A paid operation was attempted without every safety condition met."""


class StateError(SIASError):
    """Episode state is missing, corrupt, or inconsistent."""
