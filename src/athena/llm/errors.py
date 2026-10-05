from __future__ import annotations

from athena.contracts import AthenaError


class ModelNotAllowed(AthenaError):
    """A model id is outside the provider's spending policy (for example a paid OpenRouter model)."""


class SpendCapExceeded(AthenaError):
    """A request would push a provider's cumulative spend past its hard cap, or the spend record cannot be trusted."""


class ProviderError(AthenaError):
    """A provider call failed. `status` is the HTTP status (None for network errors); `retryable` says whether
    trying the same model again could help (rate limits, server errors, timeouts)."""

    def __init__(self, message: str, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable
