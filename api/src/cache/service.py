"""Cache service — lookup, write, and delete pipeline logic."""

import time
from datetime import UTC, datetime

import structlog
import ulid

from src.cache.models import CacheEntryModel
from src.cache.normalizer import (
    build_cache_sk,
    build_pk,
    compute_query_hash,
    normalize_query,
)
from src.cache.repository import CacheRepository
from src.cache.schemas import (
    CacheDeleteResponse,
    CachedResponse,
    CacheLookupRequest,
    CacheLookupResponse,
    CacheMetadata,
    CacheWriteRequest,
    CacheWriteResponse,
    LookupStages,
)

logger = structlog.get_logger()


class CacheService:
    """Orchestrates cache lookup, write, and delete operations."""

    def __init__(self, repository: CacheRepository):
        self.repository = repository

    def lookup(self, request: CacheLookupRequest) -> CacheLookupResponse:
        """Execute cache lookup pipeline (exact match only in Phase 1)."""
        start = time.monotonic()

        normalized = normalize_query(request.query)
        query_hash = compute_query_hash(normalized)

        exact_start = time.monotonic()
        entry = None

        if request.lookup_config.enable_exact_match:
            entry = self.repository.get_by_hash(
                request.workspace_id, request.project_id, query_hash
            )

        exact_ms = (time.monotonic() - exact_start) * 1000

        if entry is not None:
            # Check max_age if configured
            if request.lookup_config.max_age_seconds is not None:
                created = datetime.fromisoformat(entry.created_at)
                age = (datetime.now(UTC) - created).total_seconds()
                if age > request.lookup_config.max_age_seconds:
                    total_ms = (time.monotonic() - start) * 1000
                    return CacheLookupResponse(
                        request_id=request.request_id,
                        status="miss",
                        lookup_latency_ms=round(total_ms, 2),
                        stages=LookupStages(exact_match_ms=round(exact_ms, 2)),
                    )

            # Hit — increment hit count
            pk = build_pk(self.repository.application_id, self.repository.client_id)
            sk = build_cache_sk(entry.workspace_id, entry.project_id, entry.cache_entry_id)
            now = datetime.now(UTC).isoformat()
            try:
                self.repository.increment_hit_count(pk, sk, now)
            except Exception:
                logger.warning("Failed to increment hit count", entry_id=entry.cache_entry_id)

            total_ms = (time.monotonic() - start) * 1000
            ttl_remaining = None
            if entry.ttl:
                ttl_remaining = max(0, entry.ttl - int(datetime.now(UTC).timestamp()))

            return CacheLookupResponse(
                request_id=request.request_id,
                status="hit",
                source="exact",
                cache_entry_id=entry.cache_entry_id,
                response=CachedResponse(
                    content=entry.response.get("content", ""),
                    model=entry.model,
                    tokens_used=entry.tokens_used,
                    citations=entry.citations,
                ),
                cache_metadata=CacheMetadata(
                    created_at=entry.created_at,
                    hit_count=entry.hit_count + 1,
                    last_hit_at=now,
                    ttl_remaining_seconds=ttl_remaining,
                ),
                lookup_latency_ms=round(total_ms, 2),
            )

        # Miss
        total_ms = (time.monotonic() - start) * 1000
        return CacheLookupResponse(
            request_id=request.request_id,
            status="miss",
            lookup_latency_ms=round(total_ms, 2),
            stages=LookupStages(exact_match_ms=round(exact_ms, 2)),
        )

    def write(self, request: CacheWriteRequest, user_id: str | None = None) -> CacheWriteResponse:
        """Write a response to the cache."""
        normalized = normalize_query(request.query)
        query_hash = compute_query_hash(normalized)
        cache_entry_id = f"ce_{ulid.new().str}"
        now = datetime.now(UTC)
        ttl_epoch = int(now.timestamp()) + request.write_config.ttl_seconds
        expires_at = datetime.fromtimestamp(ttl_epoch, tz=UTC).isoformat()

        entry = CacheEntryModel(
            cache_entry_id=cache_entry_id,
            application_id=self.repository.application_id,
            client_id=self.repository.client_id,
            workspace_id=request.workspace_id,
            project_id=request.project_id,
            query_normalized=normalized,
            query_hash=query_hash,
            response={
                "content": request.response.content,
                "model": request.response.model,
                "tokens_used": request.response.tokens_used,
                "citations": request.response.citations,
            },
            model=request.response.model,
            tokens_used=request.response.tokens_used,
            citations=request.response.citations,
            hit_count=0,
            created_at=now.isoformat(),
            created_by_user=user_id,
            original_request_id=request.request_id,
            status="active",
            ttl=ttl_epoch,
        )

        self.repository.put(entry)

        return CacheWriteResponse(
            cache_entry_id=cache_entry_id,
            request_id=request.request_id,
            status="written",
            stores={"dynamodb": "ok"},
            expires_at=expires_at,
            created_at=now.isoformat(),
        )

    def delete(
        self, cache_entry_id: str, workspace_id: str, project_id: str
    ) -> CacheDeleteResponse:
        """Delete (invalidate) a cache entry."""
        self.repository.delete(cache_entry_id, workspace_id, project_id)
        return CacheDeleteResponse(
            cache_entry_id=cache_entry_id,
            status="invalidated",
        )
