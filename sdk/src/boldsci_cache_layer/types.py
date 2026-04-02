"""Pydantic request/response models for the Cache Layer SDK.

All models accept both camelCase (from API) and snake_case (from Python) input.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class _CamelModel(BaseModel):
    """Base model with camelCase alias support."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# --- Shared sub-models ---


class LookupConfig(_CamelModel):
    enable_exact_match: bool = True
    enable_semantic: bool = True
    similarity_threshold: float = 0.92
    max_age_seconds: int | None = None


class CachedResponse(_CamelModel):
    content: str
    model: str = ""
    tokens_used: dict[str, int] = Field(default_factory=dict)
    citations: list[dict[str, Any]] = Field(default_factory=list)


class WriteConfig(_CamelModel):
    ttl_seconds: int = 86400


class CacheMetadata(_CamelModel):
    created_at: str
    hit_count: int
    last_hit_at: str | None = None
    ttl_remaining_seconds: int | None = None


class LookupStages(_CamelModel):
    exact_match_ms: float | None = None
    embedding_ms: float | None = None
    semantic_match_ms: float | None = None


# --- Lookup ---


class CacheLookupResponse(_CamelModel):
    request_id: str | None = None
    status: str
    source: str | None = None
    cache_entry_id: str | None = None
    response: CachedResponse | None = None
    similarity_score: float | None = None
    matched_query: str | None = None
    cache_metadata: CacheMetadata | None = None
    lookup_latency_ms: float = 0
    stages: LookupStages | None = None


# --- Write ---


class CacheWriteResponse(_CamelModel):
    cache_entry_id: str
    request_id: str | None = None
    status: str = "written"
    stores: dict[str, str] = Field(default_factory=dict)
    expires_at: str | None = None
    created_at: str = ""


# --- Delete ---


class CacheDeleteResponse(_CamelModel):
    cache_entry_id: str
    status: str = "invalidated"


# --- Invalidation ---


class InvalidationCriteria(_CamelModel):
    query_contains: str | None = None
    cited_document_ids: list[str] | None = None
    created_before: str | None = None


class CacheInvalidateResponse(_CamelModel):
    request_id: str | None = None
    entries_invalidated: int
    invalidation_criteria: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


# --- Purge ---


class CachePurgeResponse(_CamelModel):
    request_id: str | None = None
    entries_purged: int
    scope: dict[str, str] = Field(default_factory=dict)
    created_at: str = ""


# --- Config ---


class CacheConfig(_CamelModel):
    enabled: bool = True
    default_ttl_seconds: int = 86400
    semantic_ttl_seconds: int = 3600
    similarity_threshold: float = 0.92
    max_entry_size_bytes: int = 102400
    event_driven_invalidation: bool = True
    invalidation_events: list[str] = Field(default_factory=list)


class CacheConfigResponse(_CamelModel):
    workspace_id: str
    project_id: str
    config: CacheConfig
    updated_at: str = ""
    updated_by: str | None = None


# --- Stats ---


class TokensSaved(_CamelModel):
    input: int = 0
    output: int = 0


class CacheStatsDetail(_CamelModel):
    total_lookups: int = 0
    exact_hits: int = 0
    semantic_hits: int = 0
    misses: int = 0
    hit_rate: float = 0.0
    exact_hit_rate: float = 0.0
    semantic_hit_rate: float = 0.0
    total_entries: int = 0
    estimated_cost_saved_usd: float = 0.0
    estimated_tokens_saved: TokensSaved = Field(default_factory=TokensSaved)


class CacheStatsResponse(_CamelModel):
    workspace_id: str
    project_id: str
    period: str
    stats: CacheStatsDetail


# --- Lookup-or-Exec ---


class OnMissConfig(_CamelModel):
    model: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    cache_response: bool = True
    ttl_seconds: int = 86400


class LookupOrExecResponse(_CamelModel):
    request_id: str | None = None
    status: str
    source: str | None = None
    cache_entry_id: str | None = None
    response: CachedResponse | None = None
    similarity_score: float | None = None
    matched_query: str | None = None
    cache_metadata: CacheMetadata | None = None
    lookup_latency_ms: float = 0
    stages: LookupStages | None = None


# --- Health ---


class HealthStatus(_CamelModel):
    status: str
    service: str
    version: str
    dependencies: dict[str, str]
