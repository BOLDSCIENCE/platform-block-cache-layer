"""Application exception hierarchy.

All exceptions live here in a single file, matching the model-gateway pattern.
"""


class AppError(Exception):
    """Base exception for all application-level errors."""

    def __init__(self, message: str, code: str = "INTERNAL_ERROR", details: dict | None = None):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(message)


class NotFoundError(AppError):
    """Resource not found (404)."""

    def __init__(self, message: str):
        super().__init__(message, code="NOT_FOUND")


class ConflictError(AppError):
    """Resource conflict / duplicate (409)."""

    def __init__(self, message: str):
        super().__init__(message, code="CONFLICT")


class ValidationError(AppError):
    """Request validation failure (400)."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message, code="VALIDATION_ERROR", details=details)


class AuthorizationError(AppError):
    """Permission denied (403)."""

    def __init__(self, message: str):
        super().__init__(message, code="PERMISSION_DENIED")


class CacheEntryNotFoundError(AppError):
    """Cache entry not found (404)."""

    def __init__(self, message: str = "Cache entry not found"):
        super().__init__(message, code="CACHE_ENTRY_NOT_FOUND")


class CacheWriteFailedError(AppError):
    """Cache write failed (500)."""

    def __init__(self, message: str = "Failed to write cache entry"):
        super().__init__(message, code="CACHE_WRITE_FAILED")


class PurgeRequiresConfirmError(AppError):
    """Purge requires confirm: true (400)."""

    def __init__(self):
        super().__init__("Purge requires confirm: true", code="PURGE_REQUIRES_CONFIRM")


EXCEPTION_STATUS_MAP: dict[type[AppError], int] = {
    NotFoundError: 404,
    ConflictError: 409,
    ValidationError: 400,
    AuthorizationError: 403,
    CacheEntryNotFoundError: 404,
    CacheWriteFailedError: 500,
    PurgeRequiresConfirmError: 400,
}
