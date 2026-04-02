"""Pydantic request/response models for cache endpoints."""

from typing import Any

from pydantic import Field

from src.common.base_models import ApiModel

# ---------------------------------------------------------------------------
# Shared sub-models
# ---------------------------------------------------------------------------


class LookupConfig(ApiModel):
    """Configuration for a cache lookup request."""

    enable_exact_match: bool = True
    enable_semantic: bool = True
    similarity_threshold: float = 0.92
    max_age_seconds: int | None = None


class CachedResponse(ApiModel):
    """The cached LLM response payload."""

    content: str
    model: str = ""
    tokens_used: dict[str, int] = Field(default_factory=dict)
    citations: list[dict[str, Any]] = Field(default_factory=list)


class WriteConfig(ApiModel):
    """Configuration for a cache write request."""

    ttl_seconds: int = 86400


class CacheMetadata(ApiModel):
    """Metadata about a cache entry returned on a hit."""

    created_at: str
    hit_count: int
    last_hit_at: str | None = None
    ttl_remaining_seconds: int | None = None


class LookupStages(ApiModel):
    """Timing breakdown of lookup pipeline stages."""

    exact_match_ms: float | None = None
    embedding_ms: float | None = None
    semantic_match_ms: float | None = None


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


class CacheLookupRequest(ApiModel):
    """POST /v1/cache/lookup request body."""

    workspace_id: str
    project_id: str
    query: str
    request_id: str | None = None
    context_hash: str | None = None
    lookup_config: LookupConfig = Field(default_factory=LookupConfig)


class CacheLookupResponse(ApiModel):
    """POST /v1/cache/lookup response body."""

    request_id: str | None = None
    status: str  # "hit" or "miss"
    source: str | None = None  # "exact" or "semantic"
    cache_entry_id: str | None = None
    response: CachedResponse | None = None
    similarity_score: float | None = None
    matched_query: str | None = None
    cache_metadata: CacheMetadata | None = None
    lookup_latency_ms: float = 0
    stages: LookupStages | None = None


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


class CacheWriteRequest(ApiModel):
    """POST /v1/cache/write request body."""

    workspace_id: str
    project_id: str
    query: str
    response: CachedResponse
    request_id: str | None = None
    context_hash: str | None = None
    write_config: WriteConfig = Field(default_factory=WriteConfig)


class CacheWriteResponse(ApiModel):
    """POST /v1/cache/write response body."""

    cache_entry_id: str
    request_id: str | None = None
    status: str = "written"
    stores: dict[str, str] = Field(default_factory=dict)
    expires_at: str | None = None
    created_at: str = ""


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class CacheDeleteResponse(ApiModel):
    """DELETE /v1/cache/entries/{id} response body."""

    cache_entry_id: str
    status: str = "invalidated"


# ---------------------------------------------------------------------------
# Invalidation
# ---------------------------------------------------------------------------


class InvalidationCriteria(ApiModel):
    """Criteria for selecting cache entries to invalidate."""

    query_contains: str | None = None
    cited_document_ids: list[str] | None = None
    created_before: str | None = None


class CacheInvalidateRequest(ApiModel):
    """POST /v1/cache/invalidate request body."""

    workspace_id: str
    project_id: str
    invalidation_criteria: InvalidationCriteria
    request_id: str | None = None


class CacheInvalidateResponse(ApiModel):
    """POST /v1/cache/invalidate response body."""

    request_id: str | None = None
    entries_invalidated: int
    invalidation_criteria: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


# ---------------------------------------------------------------------------
# Purge
# ---------------------------------------------------------------------------


class CachePurgeRequest(ApiModel):
    """POST /v1/cache/purge request body."""

    workspace_id: str
    project_id: str | None = None
    confirm: bool = False
    request_id: str | None = None


class CachePurgeResponse(ApiModel):
    """POST /v1/cache/purge response body."""

    request_id: str | None = None
    entries_purged: int
    scope: dict[str, str] = Field(default_factory=dict)
    created_at: str = ""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


class CacheConfig(ApiModel):
    """Cache configuration for a project."""

    enabled: bool = True
    default_ttl_seconds: int = 86400
    semantic_ttl_seconds: int = 3600
    similarity_threshold: float = 0.92
    max_entry_size_bytes: int = 102400
    event_driven_invalidation: bool = True
    invalidation_events: list[str] = Field(default_factory=list)


class CacheConfigRequest(ApiModel):
    """PUT /v1/cache/config request body."""

    workspace_id: str
    project_id: str
    config: CacheConfig


class CacheConfigResponse(ApiModel):
    """GET/PUT /v1/cache/config response body."""

    workspace_id: str
    project_id: str
    config: CacheConfig
    updated_at: str = ""
    updated_by: str | None = None


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


class TokensSaved(ApiModel):
    """Token counts saved by cache hits."""

    input: int = 0
    output: int = 0


class CacheStatsDetail(ApiModel):
    """Detailed cache statistics for a period."""

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


class CacheStatsResponse(ApiModel):
    """GET /v1/cache/stats response body."""

    workspace_id: str
    project_id: str
    period: str
    stats: CacheStatsDetail


# ---------------------------------------------------------------------------
# Lookup-or-Exec
# ---------------------------------------------------------------------------


class OnMissConfig(ApiModel):
    """Configuration for what to do on a cache miss in lookup-or-exec."""

    model: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    cache_response: bool = True
    ttl_seconds: int = 86400


class LookupOrExecRequest(ApiModel):
    """POST /v1/cache/lookup-or-exec request body."""

    workspace_id: str
    project_id: str
    query: str
    request_id: str | None = None
    context_hash: str | None = None
    lookup_config: LookupConfig = Field(default_factory=LookupConfig)
    on_miss: OnMissConfig


class LookupOrExecResponse(ApiModel):
    """POST /v1/cache/lookup-or-exec response body."""

    request_id: str | None = None
    status: str  # "hit" or "miss_executed"
    source: str | None = None  # "exact", "semantic", or "model_gateway"
    cache_entry_id: str | None = None
    response: CachedResponse | None = None
    similarity_score: float | None = None
    matched_query: str | None = None
    cache_metadata: CacheMetadata | None = None
    lookup_latency_ms: float = 0
    stages: LookupStages | None = None
