"""Cache API router — lookup, write, delete, invalidate, purge, config endpoints."""

from fastapi import APIRouter, Query

from src.auth.dependencies import Auth
from src.auth.middleware import require_admin, require_read, require_write
from src.cache.dependencies import CacheServiceDep
from src.cache.schemas import (
    CacheConfigRequest,
    CacheConfigResponse,
    CacheDeleteResponse,
    CacheInvalidateRequest,
    CacheInvalidateResponse,
    CacheLookupRequest,
    CacheLookupResponse,
    CachePurgeRequest,
    CachePurgeResponse,
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


@router.post("/invalidate", response_model=CacheInvalidateResponse)
def cache_invalidate(
    body: CacheInvalidateRequest,
    auth: Auth,
    service: CacheServiceDep,
) -> CacheInvalidateResponse:
    """Bulk invalidate cache entries matching criteria."""
    require_write(auth)
    return service.invalidate(body)


@router.post("/purge", response_model=CachePurgeResponse)
def cache_purge(
    body: CachePurgeRequest,
    auth: Auth,
    service: CacheServiceDep,
) -> CachePurgeResponse:
    """Purge all cache entries for a scope. Requires confirm: true."""
    require_admin(auth)
    return service.purge(body)


@router.get("/config", response_model=CacheConfigResponse)
def cache_config_get(
    auth: Auth,
    service: CacheServiceDep,
    workspace_id: str = Query(..., description="Workspace ID"),
    project_id: str = Query(..., description="Project ID"),
) -> CacheConfigResponse:
    """Get cache configuration for a project."""
    require_read(auth)
    return service.get_config(workspace_id, project_id)


@router.put("/config", response_model=CacheConfigResponse)
def cache_config_put(
    body: CacheConfigRequest,
    auth: Auth,
    service: CacheServiceDep,
) -> CacheConfigResponse:
    """Update cache configuration for a project."""
    require_admin(auth)
    return service.put_config(body, user_id=auth.key_id)
