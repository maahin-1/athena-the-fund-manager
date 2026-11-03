from __future__ import annotations

from athena.contracts import AthenaError


class ProviderError(AthenaError):
    """A provider call failed. `status` is the HTTP status (None for network errors); `retryable` says whether
    trying the same model again could help (rate limits, server errors, timeouts)."""

    def __init__(self, message: str, status: int | None = None, retryable: bool = False, timed_out: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.timed_out = timed_out  # the provider did not answer in time (every retry of the call timed out)
