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
