"""Auth context helpers for the Cache Layer API.

Uses boldsci-auth SDK AuthContext directly — no local wrapper.
"""

from boldsci.auth import AuthContext

# Re-export for convenient imports
__all__ = ["AuthContext", "set_auth_context", "get_auth_context", "clear_auth_context"]

_auth_context: AuthContext | None = None


def set_auth_context(context: AuthContext) -> None:
    """Set the auth context for the current request."""
    global _auth_context
    _auth_context = context


def get_auth_context() -> AuthContext:
    """Get the auth context for the current request."""
    if _auth_context is None:
        raise RuntimeError("Auth context not set")
    return _auth_context


def clear_auth_context() -> None:
    """Clear the auth context for the current request."""
    global _auth_context
    _auth_context = None
