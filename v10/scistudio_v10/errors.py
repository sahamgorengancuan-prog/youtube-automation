"""Structured exception hierarchy for Scientific Motion Studio V10.

Every provider, cache, job and security failure raises a subclass of
:class:`StudioError` so callers can distinguish configuration mistakes,
retryable provider incidents and hard security violations without string
matching. Messages are pre-redacted at the boundary that raises them.
"""

from __future__ import annotations


class StudioError(RuntimeError):
    """Base class for all first-party errors raised by the studio."""


class ConfigurationError(StudioError):
    """The supplied configuration is invalid or violates the execution mode."""


class ProviderError(StudioError):
    """A provider call failed.

    Attributes:
        provider: short provider identifier (``openai``, ``bfl``...).
        retryable: whether the failure class is worth retrying.
        status_code: HTTP-ish status when one exists.
        request_id: provider request id when one was returned.
    """

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        retryable: bool = False,
        status_code: int | None = None,
        request_id: str = "",
    ):
        super().__init__(message)
        self.provider = provider
        self.retryable = retryable
        self.status_code = status_code
        self.request_id = request_id


class ProviderUnavailableError(ProviderError):
    """No authorized provider could satisfy the request.

    In ``production`` mode this is raised instead of any silent fallback.
    """


class ProviderLockViolationError(ConfigurationError):
    """A configuration or runtime injection attempted to bypass the
    production provider boundary (OpenAI for reasoning, BFL FLUX Kontext
    for image generation)."""


class DownloadPolicyError(ProviderError):
    """A provider-supplied URL or payload violated the download policy
    (scheme, host allowlist, content type, size bound or image validity)."""


class JobConcurrencyError(StudioError):
    """Another live worker already holds the job lock."""


class JobStateError(StudioError):
    """An invalid job/stage status transition was attempted."""


class StageRetryExhaustedError(StudioError):
    """A stage exceeded its configured attempt budget."""


class UnsafeCommandError(StudioError):
    """A temporal/external command configuration was rejected as unsafe."""
