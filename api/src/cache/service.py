"""Cache service — lookup, write, delete, invalidate, purge, config logic."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import structlog
import ulid

from src.cache.models import CacheConfigModel, CacheEntryModel, InvalidationEventModel
from src.cache.normalizer import (
    build_cache_sk,
    build_pk,
    compute_query_hash,
    normalize_query,
)
from src.cache.repository import CacheRepository
from src.cache.schemas import (
    CacheConfig,
    CacheConfigRequest,
    CacheConfigResponse,
    CacheDeleteResponse,
    CachedResponse,
    CacheInvalidateRequest,
    CacheInvalidateResponse,
    CacheLookupRequest,
    CacheLookupResponse,
    CacheMetadata,
    CachePurgeRequest,
    CachePurgeResponse,
    CacheStatsResponse,
    CacheWriteRequest,
    CacheWriteResponse,
    LookupOrExecRequest,
    LookupOrExecResponse,
    LookupStages,
    WriteConfig,
)
from src.common.exceptions import PurgeRequiresConfirmError
from src.config import get_settings

logger = structlog.get_logger()


class CacheService:
    """Orchestrates cache lookup, write, and delete operations."""

    def __init__(
        self,
        repository: CacheRepository,
        opensearch_repo=None,
        embedding_service=None,
        gateway_client=None,
    ):
        self.repository = repository
        self.opensearch_repo = opensearch_repo
        self.embedding_service = embedding_service
        self.gateway_client = gateway_client

    @property
    def _semantic_available(self) -> bool:
        return self.opensearch_repo is not None and self.embedding_service is not None

    def lookup(self, request: CacheLookupRequest) -> CacheLookupResponse:
        """Execute cache lookup pipeline (exact match → semantic similarity)."""
        start = time.monotonic()

        normalized = normalize_query(request.query)
        query_hash = compute_query_hash(normalized)

        # --- Exact match tier ---
        exact_start = time.monotonic()
        entry = None

        if request.lookup_config.enable_exact_match:
            entry = self.repository.get_by_hash(
                request.workspace_id, request.project_id, query_hash, request.context_hash
            )

        exact_ms = (time.monotonic() - exact_start) * 1000

        if entry is not None:
            # Check max_age if configured
            if request.lookup_config.max_age_seconds is not None:
                created = datetime.fromisoformat(entry.created_at)
                age = (datetime.now(UTC) - created).total_seconds()
                if age > request.lookup_config.max_age_seconds:
                    self._increment_stats(request.workspace_id, request.project_id, "misses")
                    total_ms = (time.monotonic() - start) * 1000
                    return CacheLookupResponse(
                        request_id=request.request_id,
                        status="miss",
                        lookup_latency_ms=round(total_ms, 2),
                        stages=LookupStages(exact_match_ms=round(exact_ms, 2)),
                    )

            return self._build_hit_response(entry, "exact", request, start, exact_ms)

        # --- Semantic similarity tier ---
        embedding_ms = None
        semantic_ms = None

        if request.lookup_config.enable_semantic and self._semantic_available:
            embed_start = time.monotonic()
            query_embedding = self.embedding_service.generate_embedding(normalized)
            embedding_ms = (time.monotonic() - embed_start) * 1000

            if query_embedding is not None:
                sem_start = time.monotonic()
                match = self.opensearch_repo.search_similar(
                    query_embedding=query_embedding,
                    application_id=self.repository.application_id,
                    client_id=self.repository.client_id,
                    workspace_id=request.workspace_id,
                    project_id=request.project_id,
                    threshold=request.lookup_config.similarity_threshold,
                )
                semantic_ms = (time.monotonic() - sem_start) * 1000

                if match is not None:
                    # Hydrate full entry from DynamoDB
                    entry = self.repository.get_by_id(
                        match["cache_entry_id"],
                        request.workspace_id,
                        request.project_id,
                    )

                    if entry is not None:
                        # Check max_age on semantic match
                        if request.lookup_config.max_age_seconds is not None:
                            created = datetime.fromisoformat(entry.created_at)
                            age = (datetime.now(UTC) - created).total_seconds()
                            if age > request.lookup_config.max_age_seconds:
                                self._increment_stats(
                                    request.workspace_id, request.project_id, "misses"
                                )
                                total_ms = (time.monotonic() - start) * 1000
                                return CacheLookupResponse(
                                    request_id=request.request_id,
                                    status="miss",
                                    lookup_latency_ms=round(total_ms, 2),
                                    stages=LookupStages(
                                        exact_match_ms=round(exact_ms, 2),
                                        embedding_ms=(
                                            round(embedding_ms, 2) if embedding_ms else None
                                        ),
                                        semantic_match_ms=(
                                            round(semantic_ms, 2) if semantic_ms else None
                                        ),
                                    ),
                                )

                        return self._build_hit_response(
                            entry,
                            "semantic",
                            request,
                            start,
                            exact_ms,
                            embedding_ms=embedding_ms,
                            semantic_ms=semantic_ms,
                            similarity_score=match["score"],
                            matched_query=match["query_normalized"],
                        )

        # --- Miss ---
        self._increment_stats(request.workspace_id, request.project_id, "misses")
        total_ms = (time.monotonic() - start) * 1000
        return CacheLookupResponse(
            request_id=request.request_id,
            status="miss",
            lookup_latency_ms=round(total_ms, 2),
            stages=LookupStages(
                exact_match_ms=round(exact_ms, 2),
                embedding_ms=round(embedding_ms, 2) if embedding_ms else None,
                semantic_match_ms=round(semantic_ms, 2) if semantic_ms else None,
            ),
        )

    def _build_hit_response(
        self,
        entry: CacheEntryModel,
        source: str,
        request: CacheLookupRequest,
        start: float,
        exact_ms: float,
        embedding_ms: float | None = None,
        semantic_ms: float | None = None,
        similarity_score: float | None = None,
        matched_query: str | None = None,
    ) -> CacheLookupResponse:
        """Build a cache hit response and increment hit count."""
        pk = build_pk(self.repository.application_id, self.repository.client_id)
        sk = build_cache_sk(entry.workspace_id, entry.project_id, entry.cache_entry_id)
        now = datetime.now(UTC).isoformat()
        try:
            self.repository.increment_hit_count(pk, sk, now)
        except Exception:
            logger.warning("Failed to increment hit count", entry_id=entry.cache_entry_id)

        self._increment_stats(
            entry.workspace_id,
            entry.project_id,
            f"{source}_hits",
            tokens_input=entry.tokens_used.get("input", 0),
            tokens_output=entry.tokens_used.get("output", 0),
        )

        total_ms = (time.monotonic() - start) * 1000
        ttl_remaining = None
        if entry.ttl:
            ttl_remaining = max(0, entry.ttl - int(datetime.now(UTC).timestamp()))

        return CacheLookupResponse(
            request_id=request.request_id,
            status="hit",
            source=source,
            cache_entry_id=entry.cache_entry_id,
            similarity_score=similarity_score,
            matched_query=matched_query,
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
            stages=LookupStages(
                exact_match_ms=round(exact_ms, 2),
                embedding_ms=round(embedding_ms, 2) if embedding_ms else None,
                semantic_match_ms=round(semantic_ms, 2) if semantic_ms else None,
            ),
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
            context_hash=request.context_hash,
        )

        self.repository.put(entry)

        # Write citation links for document-based invalidation
        doc_ids = self._extract_document_ids(request.response.citations)
        if doc_ids:
            try:
                self.repository.put_citation_links(
                    cache_entry_id, request.workspace_id, request.project_id, doc_ids
                )
            except Exception:
                logger.warning("write.citation_links_failed", entry_id=cache_entry_id)

        stores: dict[str, str] = {"dynamodb": "ok"}

        # Best-effort OpenSearch write
        if self._semantic_available:
            embedding = self.embedding_service.generate_embedding(normalized)
            if embedding is not None:
                settings = get_settings()
                entry.query_embedding = embedding
                entry.embedding_model = settings.embedding_model
                success = self.opensearch_repo.index_embedding(
                    cache_entry_id=cache_entry_id,
                    query_embedding=embedding,
                    query_normalized=normalized,
                    application_id=self.repository.application_id,
                    client_id=self.repository.client_id,
                    workspace_id=request.workspace_id,
                    project_id=request.project_id,
                    expires_at=expires_at,
                    created_at=now.isoformat(),
                )
                stores["opensearch"] = "ok" if success else "failed"
            else:
                stores["opensearch"] = "embedding_failed"

        return CacheWriteResponse(
            cache_entry_id=cache_entry_id,
            request_id=request.request_id,
            status="written",
            stores=stores,
            expires_at=expires_at,
            created_at=now.isoformat(),
        )

    def delete(
        self, cache_entry_id: str, workspace_id: str, project_id: str
    ) -> CacheDeleteResponse:
        """Delete (invalidate) a cache entry."""
        self.repository.delete(cache_entry_id, workspace_id, project_id)

        # Best-effort OpenSearch delete
        if self.opensearch_repo is not None:
            self.opensearch_repo.delete_entry(cache_entry_id)

        return CacheDeleteResponse(
            cache_entry_id=cache_entry_id,
            status="invalidated",
        )

    # -----------------------------------------------------------------
    # Invalidation (Phase 3)
    # -----------------------------------------------------------------

    def invalidate(self, request: CacheInvalidateRequest) -> CacheInvalidateResponse:
        """Invalidate cache entries matching criteria."""
        now = datetime.now(UTC)
        criteria = request.invalidation_criteria

        # Use citation GSI for document-based invalidation
        if criteria.cited_document_ids:
            entries_to_invalidate = self._resolve_by_citation(
                criteria.cited_document_ids, request.workspace_id, request.project_id
            )
        else:
            entries_to_invalidate = self.repository.query_all_by_project(
                request.workspace_id, request.project_id
            )

        # Apply remaining in-memory filters
        filtered = self._apply_invalidation_filters(entries_to_invalidate, criteria)

        count = self.repository.batch_invalidate(filtered)

        # Best-effort OpenSearch cleanup
        if self.opensearch_repo is not None:
            for entry in filtered:
                try:
                    self.opensearch_repo.delete_entry(entry.cache_entry_id)
                except Exception:
                    logger.warning(
                        "invalidate.opensearch_delete_failed",
                        entry_id=entry.cache_entry_id,
                    )

        # Record audit event
        event = InvalidationEventModel(
            event_id=f"inv_{ulid.new().str}",
            workspace_id=request.workspace_id,
            project_id=request.project_id,
            source="manual",
            criteria=criteria.model_dump(exclude_none=True),
            entries_affected=count,
            triggered_by="api",
            created_at=now.isoformat(),
            ttl=int((now + timedelta(days=90)).timestamp()),
        )
        try:
            self.repository.record_invalidation_event(event)
        except Exception:
            logger.warning("invalidate.audit_event_failed", event_id=event.event_id)

        return CacheInvalidateResponse(
            request_id=request.request_id,
            entries_invalidated=count,
            invalidation_criteria=criteria.model_dump(exclude_none=True),
            created_at=now.isoformat(),
        )

    def _resolve_by_citation(
        self, document_ids: list[str], workspace_id: str, project_id: str
    ) -> list[CacheEntryModel]:
        """Resolve cache entries by citation document IDs via GSI3."""
        entry_ids: set[str] = set()
        for doc_id in document_ids:
            entry_ids.update(self.repository.query_by_citation(doc_id))

        entries: list[CacheEntryModel] = []
        for eid in entry_ids:
            entry = self.repository.get_by_id(eid, workspace_id, project_id)
            if entry is not None:
                entries.append(entry)
        return entries

    def _apply_invalidation_filters(
        self, entries: list[CacheEntryModel], criteria
    ) -> list[CacheEntryModel]:
        """Apply in-memory filters based on invalidation criteria."""
        filtered = entries

        if criteria.query_contains:
            term = criteria.query_contains.lower()
            filtered = [e for e in filtered if term in e.query_normalized.lower()]

        if criteria.created_before:
            cutoff = datetime.fromisoformat(criteria.created_before)
            filtered = [e for e in filtered if datetime.fromisoformat(e.created_at) < cutoff]

        return filtered

    @staticmethod
    def _extract_document_ids(citations: list[dict]) -> list[str]:
        """Extract document_id values from citation dicts."""
        doc_ids: list[str] = []
        for c in citations:
            doc_id = c.get("document_id") or c.get("documentId")
            if doc_id:
                doc_ids.append(doc_id)
        return doc_ids

    # -----------------------------------------------------------------
    # Purge (Phase 3)
    # -----------------------------------------------------------------

    def purge(self, request: CachePurgeRequest) -> CachePurgeResponse:
        """Purge cache entries for a scope."""
        if not request.confirm:
            raise PurgeRequiresConfirmError()

        now = datetime.now(UTC)

        if request.project_id:
            entries = self.repository.query_all_by_project(request.workspace_id, request.project_id)
        else:
            entries = self.repository.query_all_by_workspace(request.workspace_id)

        count = self.repository.batch_invalidate(entries)

        # Best-effort OpenSearch cleanup
        if self.opensearch_repo is not None:
            try:
                self.opensearch_repo.delete_by_query(
                    application_id=self.repository.application_id,
                    client_id=self.repository.client_id,
                    workspace_id=request.workspace_id,
                    project_id=request.project_id,
                )
            except Exception:
                logger.warning("purge.opensearch_delete_failed")

        scope: dict[str, str] = {"workspace_id": request.workspace_id}
        if request.project_id:
            scope["project_id"] = request.project_id

        # Record audit event
        event = InvalidationEventModel(
            event_id=f"inv_{ulid.new().str}",
            workspace_id=request.workspace_id,
            project_id=request.project_id or "",
            source="purge",
            criteria=scope,
            entries_affected=count,
            triggered_by="api",
            created_at=now.isoformat(),
            ttl=int((now + timedelta(days=90)).timestamp()),
        )
        try:
            self.repository.record_invalidation_event(event)
        except Exception:
            logger.warning("purge.audit_event_failed", event_id=event.event_id)

        return CachePurgeResponse(
            request_id=request.request_id,
            entries_purged=count,
            scope=scope,
            created_at=now.isoformat(),
        )

    # -----------------------------------------------------------------
    # Config (Phase 3)
    # -----------------------------------------------------------------

    def get_config(self, workspace_id: str, project_id: str) -> CacheConfigResponse:
        """Get config for a project, returning defaults if none exists."""
        model = self.repository.get_config(workspace_id, project_id)
        if model is None:
            return CacheConfigResponse(
                workspace_id=workspace_id,
                project_id=project_id,
                config=CacheConfig(),
            )

        return CacheConfigResponse(
            workspace_id=model.workspace_id,
            project_id=model.project_id,
            config=CacheConfig(
                enabled=model.enabled,
                default_ttl_seconds=model.default_ttl_seconds,
                semantic_ttl_seconds=model.semantic_ttl_seconds,
                similarity_threshold=model.similarity_threshold,
                max_entry_size_bytes=model.max_entry_size_bytes,
                event_driven_invalidation=model.event_driven_invalidation,
                invalidation_events=model.invalidation_events,
            ),
            updated_at=model.updated_at,
            updated_by=model.updated_by,
        )

    def put_config(
        self, request: CacheConfigRequest, user_id: str | None = None
    ) -> CacheConfigResponse:
        """Write config for a project."""
        now = datetime.now(UTC)

        model = CacheConfigModel(
            workspace_id=request.workspace_id,
            project_id=request.project_id,
            enabled=request.config.enabled,
            default_ttl_seconds=request.config.default_ttl_seconds,
            semantic_ttl_seconds=request.config.semantic_ttl_seconds,
            similarity_threshold=request.config.similarity_threshold,
            max_entry_size_bytes=request.config.max_entry_size_bytes,
            event_driven_invalidation=request.config.event_driven_invalidation,
            invalidation_events=request.config.invalidation_events,
            updated_at=now.isoformat(),
            updated_by=user_id,
        )

        self.repository.put_config(model)

        return CacheConfigResponse(
            workspace_id=request.workspace_id,
            project_id=request.project_id,
            config=request.config,
            updated_at=now.isoformat(),
            updated_by=user_id,
        )

    # -----------------------------------------------------------------
    # Stats (Phase 4)
    # -----------------------------------------------------------------

    def get_stats(
        self, workspace_id: str, project_id: str, period: str = "24h"
    ) -> CacheStatsResponse:
        """Get pre-aggregated stats for a scope and period."""
        from src.cache.schemas import CacheStatsDetail, CacheStatsResponse, TokensSaved

        result = self.repository.query_stats_period(workspace_id, project_id, period)

        if result is None:
            return CacheStatsResponse(
                workspace_id=workspace_id,
                project_id=project_id,
                period=period,
                stats=CacheStatsDetail(),
            )

        return CacheStatsResponse(
            workspace_id=workspace_id,
            project_id=project_id,
            period=period,
            stats=CacheStatsDetail(
                total_lookups=result.total_lookups,
                exact_hits=result.exact_hits,
                semantic_hits=result.semantic_hits,
                misses=result.misses,
                hit_rate=result.hit_rate,
                exact_hit_rate=result.exact_hit_rate,
                semantic_hit_rate=result.semantic_hit_rate,
                total_entries=result.total_entries,
                estimated_cost_saved_usd=result.estimated_cost_saved_usd,
                estimated_tokens_saved=TokensSaved(
                    input=result.tokens_saved_input,
                    output=result.tokens_saved_output,
                ),
            ),
        )

    def _increment_stats(
        self,
        workspace_id: str,
        project_id: str,
        hit_type: str,
        tokens_input: int = 0,
        tokens_output: int = 0,
    ) -> None:
        """Best-effort increment of the live stats bucket."""
        now = datetime.now(UTC)
        bucket = now.strftime("%Y-%m-%dT%H:") + f"{(now.minute // 15) * 15:02d}"
        try:
            self.repository.increment_stats_bucket(
                workspace_id, project_id, bucket, hit_type, tokens_input, tokens_output
            )
        except Exception:
            logger.warning("stats.increment_failed", hit_type=hit_type)

    # -----------------------------------------------------------------
    # Lookup-or-Exec (Phase 4)
    # -----------------------------------------------------------------

    def lookup_or_exec(self, request: LookupOrExecRequest) -> LookupOrExecResponse:
        """Cache-aside: lookup first, on miss invoke Model Gateway SDK."""
        from src.common.exceptions import GatewayNotConfiguredError

        # Step 1: Try cache lookup
        lookup_req = CacheLookupRequest(
            workspace_id=request.workspace_id,
            project_id=request.project_id,
            query=request.query,
            request_id=request.request_id,
            context_hash=request.context_hash,
            lookup_config=request.lookup_config,
        )

        lookup_result = self.lookup(lookup_req)

        if lookup_result.status == "hit":
            return LookupOrExecResponse(
                request_id=request.request_id,
                status="hit",
                source=lookup_result.source,
                cache_entry_id=lookup_result.cache_entry_id,
                response=lookup_result.response,
                similarity_score=lookup_result.similarity_score,
                matched_query=lookup_result.matched_query,
                cache_metadata=lookup_result.cache_metadata,
                lookup_latency_ms=lookup_result.lookup_latency_ms,
                stages=lookup_result.stages,
            )

        # Step 2: Cache miss — invoke Model Gateway
        if self.gateway_client is None:
            raise GatewayNotConfiguredError()

        start = time.monotonic()
        gw_response = self.gateway_client.invoke(
            model=request.on_miss.model,
            messages=request.on_miss.messages,
            max_tokens=4096,
        )
        invoke_ms = (time.monotonic() - start) * 1000

        content = gw_response.choices[0].message.content
        input_tokens = gw_response.usage.input_tokens
        output_tokens = gw_response.usage.output_tokens
        model_alias = gw_response.gateway.model_alias

        cached_response = CachedResponse(
            content=content,
            model=model_alias,
            tokens_used={"input": input_tokens, "output": output_tokens},
        )

        # Step 3: Cache the result (if enabled)
        cache_entry_id = None
        if request.on_miss.cache_response:
            write_req = CacheWriteRequest(
                workspace_id=request.workspace_id,
                project_id=request.project_id,
                query=request.query,
                response=cached_response,
                request_id=request.request_id,
                context_hash=request.context_hash,
                write_config=WriteConfig(ttl_seconds=request.on_miss.ttl_seconds),
            )
            write_result = self.write(write_req)
            cache_entry_id = write_result.cache_entry_id

        total_ms = lookup_result.lookup_latency_ms + invoke_ms

        return LookupOrExecResponse(
            request_id=request.request_id,
            status="miss_executed",
            source="model_gateway",
            cache_entry_id=cache_entry_id,
            response=cached_response,
            lookup_latency_ms=round(total_ms, 2),
            stages=lookup_result.stages,
        )
