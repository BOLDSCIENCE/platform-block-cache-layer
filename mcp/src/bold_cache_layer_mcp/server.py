"""MCP server for the Bold Science Cache Layer.

Exposes 7 tools and 3 resources wrapping the Cache Layer API via the SDK.
"""

from __future__ import annotations

from typing import Any

from boldsci_cache_layer import CacheLayerClient
from mcp.server.fastmcp import FastMCP

from bold_cache_layer_mcp.config import get_api_key, get_api_url

mcp = FastMCP("bold-cache-layer")

_client: CacheLayerClient | None = None


def _get_client() -> CacheLayerClient:
    """Lazy-initialize the SDK client."""
    global _client
    if _client is None:
        _client = CacheLayerClient(api_url=get_api_url(), api_key=get_api_key())
    return _client


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def cache_lookup(
    workspace_id: str,
    project_id: str,
    query: str,
    enable_exact_match: bool = True,
    enable_semantic: bool = True,
    similarity_threshold: float = 0.92,
    context_hash: str | None = None,
) -> dict[str, Any]:
    """Check the cache for a query. Returns hit/miss status with cached response if found."""
    result = _get_client().lookup(
        workspace_id=workspace_id,
        project_id=project_id,
        query=query,
        enable_exact_match=enable_exact_match,
        enable_semantic=enable_semantic,
        similarity_threshold=similarity_threshold,
        context_hash=context_hash,
    )
    return result.model_dump(by_alias=True, exclude_none=True)


@mcp.tool()
def cache_write(
    workspace_id: str,
    project_id: str,
    query: str,
    content: str,
    model: str = "",
    ttl_seconds: int = 86400,
    context_hash: str | None = None,
) -> dict[str, Any]:
    """Write a response to the cache."""
    result = _get_client().write(
        workspace_id=workspace_id,
        project_id=project_id,
        query=query,
        content=content,
        model=model,
        ttl_seconds=ttl_seconds,
        context_hash=context_hash,
    )
    return result.model_dump(by_alias=True, exclude_none=True)


@mcp.tool()
def cache_invalidate(
    workspace_id: str,
    project_id: str,
    query_contains: str | None = None,
    cited_document_ids: list[str] | None = None,
    created_before: str | None = None,
) -> dict[str, Any]:
    """Bulk invalidate cache entries matching criteria."""
    result = _get_client().invalidate(
        workspace_id=workspace_id,
        project_id=project_id,
        query_contains=query_contains,
        cited_document_ids=cited_document_ids,
        created_before=created_before,
    )
    return result.model_dump(by_alias=True, exclude_none=True)


@mcp.tool()
def cache_purge(
    workspace_id: str,
    project_id: str | None = None,
    confirm: bool = True,
) -> dict[str, Any]:
    """Purge all cache entries for a scope. Requires confirm=True."""
    result = _get_client().purge(
        workspace_id=workspace_id,
        project_id=project_id,
        confirm=confirm,
    )
    return result.model_dump(by_alias=True, exclude_none=True)


@mcp.tool()
def cache_stats(
    workspace_id: str,
    project_id: str,
    period: str = "24h",
) -> dict[str, Any]:
    """Get cache statistics for a project."""
    result = _get_client().get_stats(
        workspace_id=workspace_id,
        project_id=project_id,
        period=period,
    )
    return result.model_dump(by_alias=True, exclude_none=True)


@mcp.tool()
def cache_config_get(
    workspace_id: str,
    project_id: str,
) -> dict[str, Any]:
    """Get cache configuration for a project."""
    result = _get_client().get_config(
        workspace_id=workspace_id,
        project_id=project_id,
    )
    return result.model_dump(by_alias=True, exclude_none=True)


@mcp.tool()
def cache_config_update(
    workspace_id: str,
    project_id: str,
    enabled: bool = True,
    default_ttl_seconds: int = 86400,
    semantic_ttl_seconds: int = 3600,
    similarity_threshold: float = 0.92,
    max_entry_size_bytes: int = 102400,
    event_driven_invalidation: bool = True,
    invalidation_events: list[str] | None = None,
) -> dict[str, Any]:
    """Update cache configuration for a project."""
    result = _get_client().update_config(
        workspace_id=workspace_id,
        project_id=project_id,
        enabled=enabled,
        default_ttl_seconds=default_ttl_seconds,
        semantic_ttl_seconds=semantic_ttl_seconds,
        similarity_threshold=similarity_threshold,
        max_entry_size_bytes=max_entry_size_bytes,
        event_driven_invalidation=event_driven_invalidation,
        invalidation_events=invalidation_events,
    )
    return result.model_dump(by_alias=True, exclude_none=True)


# ---------------------------------------------------------------------------
# Resources
# ---------------------------------------------------------------------------


@mcp.resource("cache://stats/{workspace_id}/{project_id}")
def stats_resource(workspace_id: str, project_id: str) -> str:
    """Cache statistics for a project (24h period)."""
    import json

    result = _get_client().get_stats(
        workspace_id=workspace_id,
        project_id=project_id,
        period="24h",
    )
    return json.dumps(result.model_dump(by_alias=True, exclude_none=True), indent=2)


@mcp.resource("cache://config/{workspace_id}/{project_id}")
def config_resource(workspace_id: str, project_id: str) -> str:
    """Cache configuration for a project."""
    import json

    result = _get_client().get_config(
        workspace_id=workspace_id,
        project_id=project_id,
    )
    return json.dumps(result.model_dump(by_alias=True, exclude_none=True), indent=2)


@mcp.resource("cache://health")
def health_resource() -> str:
    """Cache Layer service health status."""
    import json

    result = _get_client().health()
    return json.dumps(result.model_dump(by_alias=True, exclude_none=True), indent=2)
