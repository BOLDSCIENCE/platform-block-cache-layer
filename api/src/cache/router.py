"""Cache API router — lookup, write, delete endpoints."""

from fastapi import APIRouter, Query

from src.auth.dependencies import Auth
from src.auth.middleware import require_read, require_write
from src.cache.dependencies import CacheServiceDep
from src.cache.schemas import (
    CacheDeleteResponse,
    CacheLookupRequest,
    CacheLookupResponse,
    CacheWriteRequest,
    CacheWriteResponse,
)

router = APIRouter(prefix="/cache", tags=["cache"])


@router.post("/lookup", response_model=CacheLookupResponse)
def cache_lookup(
    body: CacheLookupRequest,
    auth: Auth,
    service: CacheServiceDep,
) -> CacheLookupResponse:
    """Check the cache for a query. Returns a hit or miss."""
    require_read(auth)
    return service.lookup(body)


@router.post("/write", response_model=CacheWriteResponse)
def cache_write(
    body: CacheWriteRequest,
    auth: Auth,
    service: CacheServiceDep,
) -> CacheWriteResponse:
    """Write a response to the cache."""
    require_write(auth)
    return service.write(body)


@router.delete("/entries/{cache_entry_id}", response_model=CacheDeleteResponse)
def cache_delete(
    cache_entry_id: str,
    auth: Auth,
    service: CacheServiceDep,
    workspace_id: str = Query(..., description="Workspace ID for scope"),
    project_id: str = Query(..., description="Project ID for scope"),
) -> CacheDeleteResponse:
    """Invalidate a specific cache entry."""
    require_write(auth)
    return service.delete(cache_entry_id, workspace_id, project_id)
