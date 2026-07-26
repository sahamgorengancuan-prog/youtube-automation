"""Typed exceptions. No broad `except Exception: pass` anywhere in SIAS —
every failure carries stage context and never fabricates a success artifact."""

from __future__ import annotations


class SIASError(Exception):
    """Base class; carries optional stage context."""

    def __init__(self, message: str, stage: str = ""):
        self.stage = stage
        super().__init__(f"[{stage}] {message}" if stage else message)


class ConfigurationError(SIASError):
    pass


class BudgetExceededError(SIASError):
    pass


class ProviderRequestError(SIASError):
    pass


class ProviderSchemaError(SIASError):
    pass


class AssetIntegrityError(SIASError):
    pass


class StyleLockError(SIASError):
    pass


class VisionHardFailError(SIASError):
    pass


class RepairLimitError(SIASError):
    pass


class SilentAudioError(SIASError):
    pass


class AlignmentError(SIASError):
    pass


class RenderError(SIASError):
    pass


class FinalQCError(SIASError):
    pass
