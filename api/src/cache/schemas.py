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


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


class CacheLookupRequest(ApiModel):
    """POST /v1/cache/lookup request body."""

    workspace_id: str
    project_id: str
    query: str
    request_id: str | None = None
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
