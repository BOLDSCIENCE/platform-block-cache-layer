"""Internal cache entry model (not API-facing)."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CacheEntryModel:
    """Internal representation of a cache entry stored in DynamoDB."""

    cache_entry_id: str
    application_id: str
    client_id: str
    workspace_id: str
    project_id: str
    query_normalized: str
    query_hash: str
    response: dict[str, Any]
    model: str = ""
    tokens_used: dict[str, int] = field(default_factory=dict)
    citations: list[dict[str, Any]] = field(default_factory=list)
    guardrail_policy_version: str | None = None
    hit_count: int = 0
    last_hit_at: str | None = None
    created_at: str = ""
    created_by_user: str | None = None
    original_request_id: str | None = None
    status: str = "active"
    ttl: int = 0
    query_embedding: list[float] | None = field(default=None)
    embedding_model: str | None = None
    context_hash: str | None = None


@dataclass
class CacheConfigModel:
    """Internal representation of a cache config entry stored in DynamoDB."""

    workspace_id: str
    project_id: str
    enabled: bool = True
    default_ttl_seconds: int = 86400
    semantic_ttl_seconds: int = 3600
    similarity_threshold: float = 0.92
    max_entry_size_bytes: int = 102400
    event_driven_invalidation: bool = True
    invalidation_events: list[str] = field(default_factory=list)
    updated_at: str = ""
    updated_by: str | None = None


@dataclass
class InvalidationEventModel:
    """Audit record for an invalidation or purge operation."""

    event_id: str
    workspace_id: str
    project_id: str
    source: str  # "manual" | "event" | "purge"
    criteria: dict[str, Any] = field(default_factory=dict)
    entries_affected: int = 0
    triggered_by: str = ""
    created_at: str = ""
    ttl: int = 0  # 90-day retention
