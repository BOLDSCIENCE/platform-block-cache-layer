"""Circuit breaker pattern for graceful degradation of external dependencies."""

import enum
import time
from collections.abc import Callable
from typing import TypeVar

import structlog

logger = structlog.get_logger()

T = TypeVar("T")


class CircuitState(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Simple circuit breaker state machine.

    - CLOSED: Requests pass through; consecutive failures are tracked.
    - OPEN: Returns None immediately; transitions to HALF_OPEN after recovery_timeout.
    - HALF_OPEN: Allows one probe request; success → CLOSED, failure → re-OPEN.
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float = 0.0

    @property
    def state(self) -> CircuitState:
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                self._state = CircuitState.HALF_OPEN
                logger.info("circuit_breaker.half_open", name=self.name)
        return self._state

    def call(self, func: Callable[..., T], *args, **kwargs) -> T | None:
        """Execute func through the circuit breaker.

        Returns None when the circuit is open or on failure.
        """
        current_state = self.state

        if current_state == CircuitState.OPEN:
            logger.debug("circuit_breaker.open_skip", name=self.name)
            return None

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure(exc)
            return None

    def _on_success(self) -> None:
        if self._state in (CircuitState.HALF_OPEN, CircuitState.CLOSED):
            self._failure_count = 0
            if self._state == CircuitState.HALF_OPEN:
                logger.info("circuit_breaker.closed", name=self.name)
            self._state = CircuitState.CLOSED

    def _on_failure(self, exc: Exception) -> None:
        self._failure_count += 1
        self._last_failure_time = time.monotonic()
        logger.warning(
            "circuit_breaker.failure",
            name=self.name,
            failure_count=self._failure_count,
            error=str(exc),
        )

        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.OPEN
            logger.info("circuit_breaker.reopened", name=self.name)
        elif self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            logger.info("circuit_breaker.opened", name=self.name)

    def reset(self) -> None:
        """Reset the circuit breaker to closed state (useful for testing)."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
