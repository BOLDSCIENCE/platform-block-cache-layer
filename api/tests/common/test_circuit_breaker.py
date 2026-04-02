"""Unit tests for the CircuitBreaker."""

import time
from unittest.mock import patch

import pytest

from src.common.circuit_breaker import CircuitBreaker, CircuitState


@pytest.fixture
def cb():
    return CircuitBreaker("test", failure_threshold=3, recovery_timeout=1.0)


class TestCircuitBreaker:
    def test_closed_passthrough(self, cb):
        """Calls pass through when the circuit is closed."""
        result = cb.call(lambda: 42)
        assert result == 42
        assert cb.state == CircuitState.CLOSED

    def test_failure_counting(self, cb):
        """Failures are counted but circuit stays closed below threshold."""
        cb.call(self._failing_func)
        assert cb._failure_count == 1
        assert cb.state == CircuitState.CLOSED

        cb.call(self._failing_func)
        assert cb._failure_count == 2
        assert cb.state == CircuitState.CLOSED

    def test_opens_at_threshold(self, cb):
        """Circuit opens when failure count reaches threshold."""
        for _ in range(3):
            cb.call(self._failing_func)

        assert cb.state == CircuitState.OPEN

    def test_open_returns_none(self, cb):
        """Open circuit returns None without calling the function."""
        for _ in range(3):
            cb.call(self._failing_func)

        call_count = 0

        def tracked():
            nonlocal call_count
            call_count += 1
            return 99

        result = cb.call(tracked)
        assert result is None
        assert call_count == 0

    def test_half_open_after_timeout(self, cb):
        """Circuit transitions to half-open after recovery timeout."""
        for _ in range(3):
            cb.call(self._failing_func)

        assert cb.state == CircuitState.OPEN

        with patch.object(time, "monotonic", return_value=time.monotonic() + 2.0):
            assert cb.state == CircuitState.HALF_OPEN

    def test_half_open_success_closes(self, cb):
        """Successful call in half-open state closes the circuit."""
        for _ in range(3):
            cb.call(self._failing_func)

        # Force half-open
        cb._state = CircuitState.HALF_OPEN
        result = cb.call(lambda: "ok")
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0

    def test_half_open_failure_reopens(self, cb):
        """Failed call in half-open state reopens the circuit."""
        cb._state = CircuitState.HALF_OPEN
        result = cb.call(self._failing_func)
        assert result is None
        assert cb._state == CircuitState.OPEN

    @staticmethod
    def _failing_func():
        raise RuntimeError("boom")
