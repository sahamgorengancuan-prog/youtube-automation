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


class PreflightError(StudioError):
    """A preflight check with FAIL status blocked the requested action."""


class ProviderAuthenticationError(ProviderError):
    """The provider rejected the credentials (401/403); never retried."""


class ProviderRateLimitError(ProviderError):
    """The provider rate-limited the request (429) beyond the retry budget."""


class ProviderTimeoutError(ProviderError):
    """The provider did not answer within the configured deadline."""


class ReferenceVideoError(StudioError):
    """The reference video is missing, unreadable or has an unsupported format."""


class RenderDependencyError(PreflightError):
    """A rendering dependency (Node/npm/npx/FFmpeg) required by the selected
    backend is unavailable."""


class PipelineStageError(StudioError):
    """A pipeline stage failed; carries the failing stage id."""

    def __init__(self, message: str, *, stage_id: str = ""):
        super().__init__(message)
        self.stage_id = stage_id


class PublishingError(StudioError):
    """Publishing the final artifact failed."""


class JobCancelledError(StudioError):
    """The run was cancelled by the user; it stopped after the last safe
    stage and can be resumed with the same job id."""


def classify_provider_error(exc: BaseException) -> BaseException:
    """Translate a generic ProviderError into a more specific class for
    user-facing display, based on its status code. Returns the original
    exception when no better classification exists."""
    status = getattr(exc, "status_code", None)
    provider = getattr(exc, "provider", "")
    if status in (401, 403):
        return ProviderAuthenticationError(str(exc), provider=provider, status_code=status)
    if status == 429:
        return ProviderRateLimitError(str(exc), provider=provider, retryable=True, status_code=status)
    if status == 408 or "timed out" in str(exc).lower():
        return ProviderTimeoutError(str(exc), provider=provider, retryable=True, status_code=status)
    return exc
