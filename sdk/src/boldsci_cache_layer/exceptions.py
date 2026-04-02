"""Exception hierarchy for the Cache Layer SDK."""

from __future__ import annotations


class CacheLayerError(Exception):
    """Base exception for all SDK errors."""

    def __init__(self, message: str, code: str = "UNKNOWN", details: dict | None = None):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(message)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r}, message={self.message!r})"


class APIError(CacheLayerError):
    """Server error (5xx)."""


class AuthenticationError(CacheLayerError):
    """Authentication failed (401)."""


class ForbiddenError(CacheLayerError):
    """Permission denied (403)."""


class NotFoundError(CacheLayerError):
    """Resource not found (404)."""


class ValidationError(CacheLayerError):
    """Request validation failed (422)."""


class RateLimitError(CacheLayerError):
    """Rate limit exceeded (429)."""


class NetworkError(CacheLayerError):
    """Network or timeout error."""


STATUS_EXCEPTION_MAP: dict[int, type[CacheLayerError]] = {
    401: AuthenticationError,
    403: ForbiddenError,
    404: NotFoundError,
    422: ValidationError,
    429: RateLimitError,
}
