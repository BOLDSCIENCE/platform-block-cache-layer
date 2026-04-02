"""Base HTTP client with envelope unwrapping, error parsing, and retry logic."""

from __future__ import annotations

import random
import time
from typing import Any

import httpx

from boldsci_cache_layer.exceptions import (
    STATUS_EXCEPTION_MAP,
    APIError,
    CacheLayerError,
    NetworkError,
)

_RETRYABLE_STATUSES = {429, 502, 503, 504}


def _parse_error(response: httpx.Response) -> CacheLayerError:
    """Parse an error response into a typed exception."""
    try:
        body = response.json()
        error = body.get("error", {})
        code = error.get("code", "UNKNOWN")
        message = error.get("message", response.text)
        details = error.get("details", {})
    except Exception:
        code = "UNKNOWN"
        message = response.text
        details = {}

    exc_class = STATUS_EXCEPTION_MAP.get(response.status_code)
    if exc_class is None:
        exc_class = APIError if response.status_code >= 500 else CacheLayerError
    return exc_class(message=message, code=code, details=details)


def _calculate_backoff(attempt: int, base: float = 0.5, cap: float = 8.0) -> float:
    """Exponential backoff with jitter."""
    delay = min(base * (2**attempt), cap)
    jitter = random.uniform(0.75, 1.25)
    return delay * jitter


def _unwrap(response: httpx.Response) -> Any:
    """Unwrap the {data, meta} response envelope."""
    if response.status_code == 204:
        return None
    body = response.json()
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


class BaseClient:
    """Synchronous base HTTP client."""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        max_retries: int = 2,
        timeout: float = 30.0,
        _transport: httpx.BaseTransport | None = None,
    ):
        self._api_url = api_url.rstrip("/")
        self._api_key = api_key
        self._max_retries = max_retries
        kwargs: dict[str, Any] = {
            "base_url": self._api_url,
            "headers": {"X-API-Key": api_key},
            "timeout": timeout,
        }
        if _transport is not None:
            kwargs["transport"] = _transport
        self._http = httpx.Client(**kwargs)

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> Any:
        """Make an HTTP request with envelope unwrapping, error parsing, and retries."""
        last_exc: Exception | None = None
        can_retry = self._max_retries > 0

        for attempt in range(self._max_retries + 1):
            try:
                response = self._http.request(method, path, json=json, params=params)
            except httpx.TimeoutException as exc:
                last_exc = NetworkError(message=str(exc), code="TIMEOUT")
                if can_retry and attempt < self._max_retries:
                    time.sleep(_calculate_backoff(attempt))
                    continue
                raise last_exc from exc
            except httpx.HTTPError as exc:
                last_exc = NetworkError(message=str(exc), code="NETWORK_ERROR")
                if can_retry and attempt < self._max_retries:
                    time.sleep(_calculate_backoff(attempt))
                    continue
                raise last_exc from exc

            if response.status_code < 400:
                return _unwrap(response)

            if (
                can_retry
                and attempt < self._max_retries
                and response.status_code in _RETRYABLE_STATUSES
            ):
                retry_after = response.headers.get("Retry-After")
                if retry_after:
                    try:
                        time.sleep(float(retry_after))
                    except ValueError:
                        time.sleep(_calculate_backoff(attempt))
                else:
                    time.sleep(_calculate_backoff(attempt))
                continue

            raise _parse_error(response)

        if last_exc:
            raise last_exc
        raise CacheLayerError("Request failed after retries")
