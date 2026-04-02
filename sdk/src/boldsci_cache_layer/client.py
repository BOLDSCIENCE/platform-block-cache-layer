"""Synchronous Cache Layer SDK client."""

from __future__ import annotations

from typing import Any

import httpx

from boldsci_cache_layer._base import BaseClient
from boldsci_cache_layer.types import (
    CacheConfigResponse,
    CacheDeleteResponse,
    CacheInvalidateResponse,
    CacheLookupResponse,
    CachePurgeResponse,
    CacheStatsResponse,
    CacheWriteResponse,
    HealthStatus,
    LookupOrExecResponse,
)


class CacheLayerClient:
    """Synchronous Cache Layer SDK client.

    Usage:
        client = CacheLayerClient(api_url="https://...", api_key="your-key")
        result = client.lookup(workspace_id="ws_1", project_id="proj_1", query="hello")
    """

    def __init__(
        self,
        api_url: str,
        api_key: str,
        max_retries: int = 2,
        timeout: float = 30.0,
        _transport: httpx.BaseTransport | None = None,
    ):
        self._base = BaseClient(
            api_url=api_url,
            api_key=api_key,
            max_retries=max_retries,
            timeout=timeout,
            _transport=_transport,
        )

    def close(self) -> None:
        self._base.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # --- Lookup ---

    def lookup(
        self,
        *,
        workspace_id: str,
        project_id: str,
        query: str,
        context_hash: str | None = None,
        enable_exact_match: bool = True,
        enable_semantic: bool = True,
        similarity_threshold: float = 0.92,
        max_age_seconds: int | None = None,
    ) -> CacheLookupResponse:
        lookup_config: dict[str, Any] = {
            "enable_exact_match": enable_exact_match,
            "enable_semantic": enable_semantic,
            "similarity_threshold": similarity_threshold,
        }
        if max_age_seconds is not None:
            lookup_config["max_age_seconds"] = max_age_seconds

        body: dict[str, Any] = {
            "workspace_id": workspace_id,
            "project_id": project_id,
            "query": query,
            "lookup_config": lookup_config,
        }
        if context_hash is not None:
            body["context_hash"] = context_hash

        data = self._base._request("POST", "/v1/cache/lookup", json=body)
        return CacheLookupResponse.model_validate(data)

    # --- Write ---

    def write(
        self,
        *,
        workspace_id: str,
        project_id: str,
        query: str,
        content: str,
        model: str = "",
        tokens_used: dict[str, int] | None = None,
        citations: list[dict[str, Any]] | None = None,
        context_hash: str | None = None,
        ttl_seconds: int = 86400,
    ) -> CacheWriteResponse:
        response_payload: dict[str, Any] = {"content": content, "model": model}
        if tokens_used is not None:
            response_payload["tokens_used"] = tokens_used
        if citations is not None:
            response_payload["citations"] = citations

        body: dict[str, Any] = {
            "workspace_id": workspace_id,
            "project_id": project_id,
            "query": query,
            "response": response_payload,
            "write_config": {"ttl_seconds": ttl_seconds},
        }
        if context_hash is not None:
            body["context_hash"] = context_hash

        data = self._base._request("POST", "/v1/cache/write", json=body)
        return CacheWriteResponse.model_validate(data)

    # --- Delete ---

    def delete_entry(
        self,
        *,
        cache_entry_id: str,
        workspace_id: str,
        project_id: str,
    ) -> CacheDeleteResponse:
        data = self._base._request(
            "DELETE",
            f"/v1/cache/entries/{cache_entry_id}",
            params={"workspace_id": workspace_id, "project_id": project_id},
        )
        return CacheDeleteResponse.model_validate(data)

    # --- Invalidate ---

    def invalidate(
        self,
        *,
        workspace_id: str,
        project_id: str,
        query_contains: str | None = None,
        cited_document_ids: list[str] | None = None,
        created_before: str | None = None,
    ) -> CacheInvalidateResponse:
        criteria: dict[str, Any] = {}
        if query_contains is not None:
            criteria["query_contains"] = query_contains
        if cited_document_ids is not None:
            criteria["cited_document_ids"] = cited_document_ids
        if created_before is not None:
            criteria["created_before"] = created_before

        body = {
            "workspace_id": workspace_id,
            "project_id": project_id,
            "invalidation_criteria": criteria,
        }
        data = self._base._request("POST", "/v1/cache/invalidate", json=body)
        return CacheInvalidateResponse.model_validate(data)

    # --- Purge ---

    def purge(
        self,
        *,
        workspace_id: str,
        project_id: str | None = None,
        confirm: bool = True,
    ) -> CachePurgeResponse:
        body: dict[str, Any] = {"workspace_id": workspace_id, "confirm": confirm}
        if project_id is not None:
            body["project_id"] = project_id
        data = self._base._request("POST", "/v1/cache/purge", json=body)
        return CachePurgeResponse.model_validate(data)

    # --- Config ---

    def get_config(
        self,
        *,
        workspace_id: str,
        project_id: str,
    ) -> CacheConfigResponse:
        data = self._base._request(
            "GET",
            "/v1/cache/config",
            params={"workspace_id": workspace_id, "project_id": project_id},
        )
        return CacheConfigResponse.model_validate(data)

    def update_config(
        self,
        *,
        workspace_id: str,
        project_id: str,
        enabled: bool = True,
        default_ttl_seconds: int = 86400,
        semantic_ttl_seconds: int = 3600,
        similarity_threshold: float = 0.92,
        max_entry_size_bytes: int = 102400,
        event_driven_invalidation: bool = True,
        invalidation_events: list[str] | None = None,
    ) -> CacheConfigResponse:
        body = {
            "workspace_id": workspace_id,
            "project_id": project_id,
            "config": {
                "enabled": enabled,
                "default_ttl_seconds": default_ttl_seconds,
                "semantic_ttl_seconds": semantic_ttl_seconds,
                "similarity_threshold": similarity_threshold,
                "max_entry_size_bytes": max_entry_size_bytes,
                "event_driven_invalidation": event_driven_invalidation,
                "invalidation_events": invalidation_events or [],
            },
        }
        data = self._base._request("PUT", "/v1/cache/config", json=body)
        return CacheConfigResponse.model_validate(data)

    # --- Stats ---

    def get_stats(
        self,
        *,
        workspace_id: str,
        project_id: str,
        period: str = "24h",
    ) -> CacheStatsResponse:
        data = self._base._request(
            "GET",
            "/v1/cache/stats",
            params={"workspace_id": workspace_id, "project_id": project_id, "period": period},
        )
        return CacheStatsResponse.model_validate(data)

    # --- Lookup-or-Exec ---

    def lookup_or_exec(
        self,
        *,
        workspace_id: str,
        project_id: str,
        query: str,
        on_miss_model: str,
        on_miss_messages: list[dict[str, str]],
        context_hash: str | None = None,
        enable_exact_match: bool = True,
        enable_semantic: bool = True,
        similarity_threshold: float = 0.92,
        cache_response: bool = True,
        ttl_seconds: int = 86400,
    ) -> LookupOrExecResponse:
        body: dict[str, Any] = {
            "workspace_id": workspace_id,
            "project_id": project_id,
            "query": query,
            "lookup_config": {
                "enable_exact_match": enable_exact_match,
                "enable_semantic": enable_semantic,
                "similarity_threshold": similarity_threshold,
            },
            "on_miss": {
                "model": on_miss_model,
                "messages": on_miss_messages,
                "cache_response": cache_response,
                "ttl_seconds": ttl_seconds,
            },
        }
        if context_hash is not None:
            body["context_hash"] = context_hash

        data = self._base._request("POST", "/v1/cache/lookup-or-exec", json=body)
        return LookupOrExecResponse.model_validate(data)

    # --- Health ---

    def health(self) -> HealthStatus:
        data = self._base._request("GET", "/v1/health")
        return HealthStatus.model_validate(data)
