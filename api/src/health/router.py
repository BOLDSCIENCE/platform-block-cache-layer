"""Health check router."""

import time
from typing import Any

from fastapi import APIRouter

from src.common.dependencies import get_dynamodb_table
from src.config import get_settings

router = APIRouter(tags=["health"])

_HEALTH_CACHE_TTL = 15  # seconds
_cached_result: dict[str, Any] | None = None
_cached_at: float = 0.0


def _check_health() -> dict[str, Any]:
    """Run health checks against dependencies.

    Results are cached for 15 seconds to avoid excessive
    DynamoDB describe_table calls from ALB health probes.
    """
    global _cached_result, _cached_at

    now = time.monotonic()
    if _cached_result is not None and (now - _cached_at) < _HEALTH_CACHE_TTL:
        return _cached_result

    settings = get_settings()
    db_status = "healthy"
    try:
        table = get_dynamodb_table()
        table.meta.client.describe_table(TableName=settings.dynamodb_table)
    except Exception:
        db_status = "unhealthy"

    status = "healthy" if db_status == "healthy" else "degraded"
    result = {
        "status": status,
        "service": "cache-layer-api",
        "version": "0.1.0",
        "dependencies": {"dynamodb": db_status},
    }

    _cached_result = result
    _cached_at = now
    return result


@router.get("/health")
def health_check() -> dict[str, Any]:
    """Health check endpoint with dependency verification."""
    return _check_health()
