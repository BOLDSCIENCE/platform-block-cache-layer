"""Tests for SDK exception hierarchy."""

from boldsci_cache_layer.exceptions import (
    STATUS_EXCEPTION_MAP,
    APIError,
    AuthenticationError,
    CacheLayerError,
    ForbiddenError,
    NetworkError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)


class TestExceptionHierarchy:
    def test_base_error_stores_fields(self):
        err = CacheLayerError(message="boom", code="TEST", details={"key": "val"})
        assert err.message == "boom"
        assert err.code == "TEST"
        assert err.details == {"key": "val"}
        assert str(err) == "boom"

    def test_base_error_defaults(self):
        err = CacheLayerError(message="boom")
        assert err.code == "UNKNOWN"
        assert err.details == {}

    def test_repr(self):
        err = CacheLayerError(message="boom", code="TEST")
        assert "CacheLayerError" in repr(err)
        assert "TEST" in repr(err)

    def test_all_subclasses_inherit(self):
        for cls in [APIError, AuthenticationError, ForbiddenError, NotFoundError, ValidationError, RateLimitError, NetworkError]:
            err = cls(message="test")
            assert isinstance(err, CacheLayerError)

    def test_status_map_covers_common_codes(self):
        assert STATUS_EXCEPTION_MAP[401] is AuthenticationError
        assert STATUS_EXCEPTION_MAP[403] is ForbiddenError
        assert STATUS_EXCEPTION_MAP[404] is NotFoundError
        assert STATUS_EXCEPTION_MAP[422] is ValidationError
        assert STATUS_EXCEPTION_MAP[429] is RateLimitError
