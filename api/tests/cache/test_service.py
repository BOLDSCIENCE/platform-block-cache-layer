"""Unit tests for CacheService with mocked repository."""

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.cache.models import CacheEntryModel
from src.cache.normalizer import compute_query_hash, normalize_query
from src.cache.schemas import (
    CacheConfig,
    CacheConfigRequest,
    CachedResponse,
    CacheInvalidateRequest,
    CacheLookupRequest,
    CachePurgeRequest,
    CacheWriteRequest,
    InvalidationCriteria,
    LookupConfig,
)
from src.cache.service import CacheService
from src.common.exceptions import CacheEntryNotFoundError, PurgeRequiresConfirmError


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.application_id = "test-app"
    repo.client_id = "test-client"
    repo.query_all_by_project.return_value = []
    repo.query_all_by_workspace.return_value = []
    repo.batch_invalidate.return_value = 0
    repo.get_config.return_value = None
    repo.query_by_citation.return_value = []
    return repo


@pytest.fixture
def mock_embedding_service():
    svc = MagicMock()
    svc.generate_embedding.return_value = [0.1] * 1024
    return svc


@pytest.fixture
def mock_opensearch_repo():
    repo = MagicMock()
    repo.index_embedding.return_value = True
    repo.search_similar.return_value = None
    repo.delete_entry.return_value = True
    return repo


@pytest.fixture
def service(mock_repo):
    return CacheService(mock_repo)


@pytest.fixture
def semantic_service(mock_repo, mock_opensearch_repo, mock_embedding_service):
    return CacheService(mock_repo, mock_opensearch_repo, mock_embedding_service)


def _make_entry(**overrides) -> CacheEntryModel:
    defaults = {
        "cache_entry_id": "ce_01TEST",
        "application_id": "test-app",
        "client_id": "test-client",
        "workspace_id": "ws_01",
        "project_id": "proj_01",
        "query_normalized": "how do i reset my password?",
        "query_hash": compute_query_hash("how do i reset my password?"),
        "response": {"content": "Click forgot password."},
        "model": "anthropic.claude-sonnet-4-5-20250929",
        "tokens_used": {"input": 100, "output": 50},
        "citations": [],
        "hit_count": 5,
        "last_hit_at": "2026-01-01T00:00:00+00:00",
        "created_at": datetime.now(UTC).isoformat(),
        "status": "active",
        "ttl": 9999999999,
    }
    defaults.update(overrides)
    return CacheEntryModel(**defaults)


class TestLookup:
    def test_lookup_hit(self, service, mock_repo):
        entry = _make_entry()
        mock_repo.get_by_hash.return_value = entry

        req = CacheLookupRequest(
            workspace_id="ws_01", project_id="proj_01", query="How do I reset my password?"
        )
        result = service.lookup(req)

        assert result.status == "hit"
        assert result.source == "exact"
        assert result.cache_entry_id == "ce_01TEST"
        assert result.response is not None
        assert result.response.content == "Click forgot password."
        assert result.cache_metadata is not None
        assert result.cache_metadata.hit_count == 6  # 5 + 1

    def test_lookup_miss(self, service, mock_repo):
        mock_repo.get_by_hash.return_value = None

        req = CacheLookupRequest(
            workspace_id="ws_01", project_id="proj_01", query="Some unknown query?"
        )
        result = service.lookup(req)

        assert result.status == "miss"
        assert result.source is None
        assert result.response is None

    def test_lookup_normalizes_before_hash(self, service, mock_repo):
        mock_repo.get_by_hash.return_value = None

        req = CacheLookupRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="  How Do I  Reset My Password??  ",
        )
        service.lookup(req)

        expected_hash = compute_query_hash(normalize_query("  How Do I  Reset My Password??  "))
        mock_repo.get_by_hash.assert_called_once_with("ws_01", "proj_01", expected_hash, None)

    def test_lookup_increments_hit_count(self, service, mock_repo):
        entry = _make_entry()
        mock_repo.get_by_hash.return_value = entry

        req = CacheLookupRequest(
            workspace_id="ws_01", project_id="proj_01", query="How do I reset my password?"
        )
        service.lookup(req)

        mock_repo.increment_hit_count.assert_called_once()

    def test_lookup_returns_latency(self, service, mock_repo):
        mock_repo.get_by_hash.return_value = None

        req = CacheLookupRequest(workspace_id="ws_01", project_id="proj_01", query="test?")
        result = service.lookup(req)

        assert result.lookup_latency_ms >= 0

    def test_lookup_exact_match_disabled(self, service, mock_repo):
        req = CacheLookupRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="test?",
            lookup_config=LookupConfig(enable_exact_match=False),
        )
        result = service.lookup(req)

        assert result.status == "miss"
        mock_repo.get_by_hash.assert_not_called()

    def test_lookup_request_id_forwarded(self, service, mock_repo):
        mock_repo.get_by_hash.return_value = None

        req = CacheLookupRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="test?",
            request_id="req-abc",
        )
        result = service.lookup(req)

        assert result.request_id == "req-abc"


class TestSemanticLookup:
    def test_semantic_hit_after_exact_miss(
        self, semantic_service, mock_repo, mock_opensearch_repo, mock_embedding_service
    ):
        """Semantic match is returned when exact match misses."""
        mock_repo.get_by_hash.return_value = None
        entry = _make_entry(cache_entry_id="ce_SEMANTIC")
        mock_repo.get_by_id.return_value = entry
        mock_opensearch_repo.search_similar.return_value = {
            "cache_entry_id": "ce_SEMANTIC",
            "score": 0.95,
            "query_normalized": "how do i reset my password?",
        }

        req = CacheLookupRequest(
            workspace_id="ws_01", project_id="proj_01", query="How can I change my password?"
        )
        result = semantic_service.lookup(req)

        assert result.status == "hit"
        assert result.source == "semantic"
        assert result.cache_entry_id == "ce_SEMANTIC"
        assert result.similarity_score == 0.95
        assert result.matched_query == "how do i reset my password?"

    def test_semantic_miss(
        self, semantic_service, mock_repo, mock_opensearch_repo, mock_embedding_service
    ):
        """Returns miss when semantic search finds nothing."""
        mock_repo.get_by_hash.return_value = None
        mock_opensearch_repo.search_similar.return_value = None

        req = CacheLookupRequest(
            workspace_id="ws_01", project_id="proj_01", query="completely different question?"
        )
        result = semantic_service.lookup(req)

        assert result.status == "miss"

    def test_semantic_disabled(
        self, semantic_service, mock_repo, mock_opensearch_repo, mock_embedding_service
    ):
        """Semantic search is skipped when disabled in config."""
        mock_repo.get_by_hash.return_value = None

        req = CacheLookupRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="test?",
            lookup_config=LookupConfig(enable_semantic=False),
        )
        result = semantic_service.lookup(req)

        assert result.status == "miss"
        mock_embedding_service.generate_embedding.assert_not_called()

    def test_semantic_skipped_without_services(self, service, mock_repo):
        """Semantic search is skipped when services are not injected."""
        mock_repo.get_by_hash.return_value = None

        req = CacheLookupRequest(workspace_id="ws_01", project_id="proj_01", query="test?")
        result = service.lookup(req)

        assert result.status == "miss"

    def test_semantic_graceful_embedding_failure(
        self, semantic_service, mock_repo, mock_opensearch_repo, mock_embedding_service
    ):
        """Returns miss when embedding generation fails."""
        mock_repo.get_by_hash.return_value = None
        mock_embedding_service.generate_embedding.return_value = None

        req = CacheLookupRequest(workspace_id="ws_01", project_id="proj_01", query="test?")
        result = semantic_service.lookup(req)

        assert result.status == "miss"
        mock_opensearch_repo.search_similar.assert_not_called()

    def test_semantic_graceful_opensearch_failure(
        self, semantic_service, mock_repo, mock_opensearch_repo, mock_embedding_service
    ):
        """Returns miss when OpenSearch search fails (returns None via circuit breaker)."""
        mock_repo.get_by_hash.return_value = None
        mock_opensearch_repo.search_similar.return_value = None

        req = CacheLookupRequest(workspace_id="ws_01", project_id="proj_01", query="test?")
        result = semantic_service.lookup(req)

        assert result.status == "miss"

    def test_semantic_respects_max_age(
        self, semantic_service, mock_repo, mock_opensearch_repo, mock_embedding_service
    ):
        """Semantic hit respects max_age_seconds filter."""
        mock_repo.get_by_hash.return_value = None
        old_entry = _make_entry(
            cache_entry_id="ce_OLD",
            created_at=(datetime.now(UTC) - timedelta(hours=2)).isoformat(),
        )
        mock_repo.get_by_id.return_value = old_entry
        mock_opensearch_repo.search_similar.return_value = {
            "cache_entry_id": "ce_OLD",
            "score": 0.95,
            "query_normalized": "old query",
        }

        req = CacheLookupRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="test?",
            lookup_config=LookupConfig(max_age_seconds=3600),  # 1 hour
        )
        result = semantic_service.lookup(req)

        assert result.status == "miss"

    def test_semantic_timing_stages(
        self, semantic_service, mock_repo, mock_opensearch_repo, mock_embedding_service
    ):
        """Timing stages include embedding and semantic match durations."""
        mock_repo.get_by_hash.return_value = None
        mock_opensearch_repo.search_similar.return_value = None

        req = CacheLookupRequest(workspace_id="ws_01", project_id="proj_01", query="test?")
        result = semantic_service.lookup(req)

        assert result.stages is not None
        assert result.stages.exact_match_ms is not None
        assert result.stages.embedding_ms is not None
        assert result.stages.semantic_match_ms is not None

    def test_semantic_hydration_miss(
        self, semantic_service, mock_repo, mock_opensearch_repo, mock_embedding_service
    ):
        """Returns miss when OpenSearch match can't be hydrated from DynamoDB."""
        mock_repo.get_by_hash.return_value = None
        mock_repo.get_by_id.return_value = None
        mock_opensearch_repo.search_similar.return_value = {
            "cache_entry_id": "ce_GONE",
            "score": 0.95,
            "query_normalized": "deleted entry",
        }

        req = CacheLookupRequest(workspace_id="ws_01", project_id="proj_01", query="test?")
        result = semantic_service.lookup(req)

        assert result.status == "miss"


class TestWrite:
    def test_write_creates_entry(self, service, mock_repo):
        req = CacheWriteRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="How do I reset my password?",
            response=CachedResponse(
                content="Click forgot password.",
                model="anthropic.claude-sonnet-4-5-20250929",
                tokens_used={"input": 100, "output": 50},
            ),
        )
        result = service.write(req)

        assert result.status == "written"
        assert result.cache_entry_id.startswith("ce_")
        assert result.stores == {"dynamodb": "ok"}
        mock_repo.put.assert_called_once()

    def test_write_generates_ulid(self, service, mock_repo):
        req = CacheWriteRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="test?",
            response=CachedResponse(content="answer"),
        )
        result1 = service.write(req)
        result2 = service.write(req)

        assert result1.cache_entry_id != result2.cache_entry_id

    def test_write_sets_ttl(self, service, mock_repo):
        req = CacheWriteRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="test?",
            response=CachedResponse(content="answer"),
        )
        result = service.write(req)

        assert result.expires_at is not None
        # The put call should have a CacheEntryModel with a ttl
        put_call_entry = mock_repo.put.call_args[0][0]
        assert put_call_entry.ttl > 0

    def test_write_request_id_forwarded(self, service, mock_repo):
        req = CacheWriteRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="test?",
            response=CachedResponse(content="answer"),
            request_id="req-xyz",
        )
        result = service.write(req)

        assert result.request_id == "req-xyz"

    def test_write_stores_embedding(
        self, semantic_service, mock_repo, mock_opensearch_repo, mock_embedding_service
    ):
        """Write indexes embedding in OpenSearch when services are available."""
        req = CacheWriteRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="test?",
            response=CachedResponse(content="answer"),
        )
        result = semantic_service.write(req)

        assert result.stores["dynamodb"] == "ok"
        assert result.stores["opensearch"] == "ok"
        mock_embedding_service.generate_embedding.assert_called_once()
        mock_opensearch_repo.index_embedding.assert_called_once()

    def test_write_opensearch_failure_doesnt_fail_write(
        self, semantic_service, mock_repo, mock_opensearch_repo, mock_embedding_service
    ):
        """OpenSearch failure is reported in stores but write succeeds."""
        mock_opensearch_repo.index_embedding.return_value = False

        req = CacheWriteRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="test?",
            response=CachedResponse(content="answer"),
        )
        result = semantic_service.write(req)

        assert result.status == "written"
        assert result.stores["dynamodb"] == "ok"
        assert result.stores["opensearch"] == "failed"

    def test_write_without_semantic_services(self, service, mock_repo):
        """Write succeeds with only DynamoDB when semantic services unavailable."""
        req = CacheWriteRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="test?",
            response=CachedResponse(content="answer"),
        )
        result = service.write(req)

        assert result.stores == {"dynamodb": "ok"}

    def test_write_embedding_failure_in_stores(
        self, semantic_service, mock_repo, mock_opensearch_repo, mock_embedding_service
    ):
        """Embedding failure is reported as 'embedding_failed' in stores."""
        mock_embedding_service.generate_embedding.return_value = None

        req = CacheWriteRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="test?",
            response=CachedResponse(content="answer"),
        )
        result = semantic_service.write(req)

        assert result.status == "written"
        assert result.stores["dynamodb"] == "ok"
        assert result.stores["opensearch"] == "embedding_failed"


class TestDelete:
    def test_delete_returns_invalidated(self, service, mock_repo):
        result = service.delete("ce_01TEST", "ws_01", "proj_01")

        assert result.status == "invalidated"
        assert result.cache_entry_id == "ce_01TEST"
        mock_repo.delete.assert_called_once_with("ce_01TEST", "ws_01", "proj_01")

    def test_delete_raises_on_missing(self, service, mock_repo):
        mock_repo.delete.side_effect = CacheEntryNotFoundError("not found")

        with pytest.raises(CacheEntryNotFoundError):
            service.delete("nonexistent", "ws_01", "proj_01")

    def test_delete_also_removes_from_opensearch(
        self, semantic_service, mock_repo, mock_opensearch_repo
    ):
        """Delete also calls OpenSearch delete when available."""
        result = semantic_service.delete("ce_01TEST", "ws_01", "proj_01")

        assert result.status == "invalidated"
        mock_opensearch_repo.delete_entry.assert_called_once_with("ce_01TEST")


class TestInvalidate:
    def test_invalidate_by_query_contains(self, service, mock_repo):
        entries = [
            _make_entry(cache_entry_id="ce_A", query_normalized="how do i reset my password?"),
            _make_entry(cache_entry_id="ce_B", query_normalized="what is the weather?"),
        ]
        mock_repo.query_all_by_project.return_value = entries
        mock_repo.batch_invalidate.return_value = 1

        req = CacheInvalidateRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            invalidation_criteria=InvalidationCriteria(query_contains="password"),
        )
        result = service.invalidate(req)

        assert result.entries_invalidated == 1
        # Only the password entry should be passed to batch_invalidate
        call_entries = mock_repo.batch_invalidate.call_args[0][0]
        assert len(call_entries) == 1
        assert call_entries[0].cache_entry_id == "ce_A"

    def test_invalidate_by_created_before(self, service, mock_repo):
        old = (datetime.now(UTC) - timedelta(days=10)).isoformat()
        new = datetime.now(UTC).isoformat()
        entries = [
            _make_entry(cache_entry_id="ce_OLD", created_at=old),
            _make_entry(cache_entry_id="ce_NEW", created_at=new),
        ]
        mock_repo.query_all_by_project.return_value = entries
        mock_repo.batch_invalidate.return_value = 1

        cutoff = (datetime.now(UTC) - timedelta(days=5)).isoformat()
        req = CacheInvalidateRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            invalidation_criteria=InvalidationCriteria(created_before=cutoff),
        )
        service.invalidate(req)

        call_entries = mock_repo.batch_invalidate.call_args[0][0]
        assert len(call_entries) == 1
        assert call_entries[0].cache_entry_id == "ce_OLD"

    def test_invalidate_by_cited_document_ids(self, service, mock_repo):
        mock_repo.query_by_citation.return_value = ["ce_CITED"]
        entry = _make_entry(cache_entry_id="ce_CITED")
        mock_repo.get_by_id.return_value = entry
        mock_repo.batch_invalidate.return_value = 1

        req = CacheInvalidateRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            invalidation_criteria=InvalidationCriteria(cited_document_ids=["doc_X"]),
        )
        result = service.invalidate(req)

        assert result.entries_invalidated == 1
        mock_repo.query_by_citation.assert_called_once_with("doc_X")

    def test_invalidate_empty_result(self, service, mock_repo):
        mock_repo.query_all_by_project.return_value = []
        mock_repo.batch_invalidate.return_value = 0

        req = CacheInvalidateRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            invalidation_criteria=InvalidationCriteria(query_contains="nonexistent"),
        )
        result = service.invalidate(req)

        assert result.entries_invalidated == 0

    def test_invalidate_records_audit_event(self, service, mock_repo):
        mock_repo.query_all_by_project.return_value = []
        mock_repo.batch_invalidate.return_value = 0

        req = CacheInvalidateRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            invalidation_criteria=InvalidationCriteria(query_contains="test"),
        )
        service.invalidate(req)

        mock_repo.record_invalidation_event.assert_called_once()

    def test_invalidate_opensearch_cleanup(self, semantic_service, mock_repo, mock_opensearch_repo):
        entries = [_make_entry(cache_entry_id="ce_OS")]
        mock_repo.query_all_by_project.return_value = entries
        mock_repo.batch_invalidate.return_value = 1

        req = CacheInvalidateRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            invalidation_criteria=InvalidationCriteria(),
        )
        semantic_service.invalidate(req)

        mock_opensearch_repo.delete_entry.assert_called_once_with("ce_OS")


class TestPurge:
    def test_purge_project(self, service, mock_repo):
        entries = [_make_entry(cache_entry_id="ce_P1"), _make_entry(cache_entry_id="ce_P2")]
        mock_repo.query_all_by_project.return_value = entries
        mock_repo.batch_invalidate.return_value = 2

        req = CachePurgeRequest(workspace_id="ws_01", project_id="proj_01", confirm=True)
        result = service.purge(req)

        assert result.entries_purged == 2
        assert result.scope["workspace_id"] == "ws_01"
        assert result.scope["project_id"] == "proj_01"

    def test_purge_workspace(self, service, mock_repo):
        entries = [_make_entry(cache_entry_id="ce_W1")]
        mock_repo.query_all_by_workspace.return_value = entries
        mock_repo.batch_invalidate.return_value = 1

        req = CachePurgeRequest(workspace_id="ws_01", confirm=True)
        result = service.purge(req)

        assert result.entries_purged == 1
        mock_repo.query_all_by_workspace.assert_called_once_with("ws_01")

    def test_purge_requires_confirm(self, service, mock_repo):
        req = CachePurgeRequest(workspace_id="ws_01", project_id="proj_01", confirm=False)
        with pytest.raises(PurgeRequiresConfirmError):
            service.purge(req)

    def test_purge_records_audit_event(self, service, mock_repo):
        mock_repo.query_all_by_project.return_value = []
        mock_repo.batch_invalidate.return_value = 0

        req = CachePurgeRequest(workspace_id="ws_01", project_id="proj_01", confirm=True)
        service.purge(req)

        mock_repo.record_invalidation_event.assert_called_once()
        event = mock_repo.record_invalidation_event.call_args[0][0]
        assert event.source == "purge"


class TestGetConfig:
    def test_get_config_returns_defaults(self, service, mock_repo):
        mock_repo.get_config.return_value = None

        result = service.get_config("ws_01", "proj_01")

        assert result.workspace_id == "ws_01"
        assert result.project_id == "proj_01"
        assert result.config.enabled is True
        assert result.config.default_ttl_seconds == 86400

    def test_get_config_returns_stored(self, service, mock_repo):
        from src.cache.models import CacheConfigModel

        stored = CacheConfigModel(
            workspace_id="ws_01",
            project_id="proj_01",
            enabled=False,
            default_ttl_seconds=3600,
            similarity_threshold=0.85,
            updated_at="2026-01-01T00:00:00+00:00",
            updated_by="key_admin",
        )
        mock_repo.get_config.return_value = stored

        result = service.get_config("ws_01", "proj_01")

        assert result.config.enabled is False
        assert result.config.default_ttl_seconds == 3600
        assert result.config.similarity_threshold == 0.85
        assert result.updated_by == "key_admin"


class TestPutConfig:
    def test_put_config(self, service, mock_repo):
        req = CacheConfigRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            config=CacheConfig(enabled=True, default_ttl_seconds=7200),
        )
        result = service.put_config(req, user_id="key_admin")

        assert result.workspace_id == "ws_01"
        assert result.config.default_ttl_seconds == 7200
        assert result.updated_by == "key_admin"
        mock_repo.put_config.assert_called_once()

    def test_put_config_sets_updated_at(self, service, mock_repo):
        req = CacheConfigRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            config=CacheConfig(),
        )
        result = service.put_config(req, user_id="key_admin")

        assert result.updated_at != ""


class TestWriteCitationLinks:
    def test_write_stores_citation_links(self, service, mock_repo):
        req = CacheWriteRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="test?",
            response=CachedResponse(
                content="answer",
                citations=[{"document_id": "doc_A"}, {"document_id": "doc_B"}],
            ),
        )
        service.write(req)

        mock_repo.put_citation_links.assert_called_once()
        # positional args: (cache_entry_id, workspace_id, project_id, doc_ids)
        call_args = mock_repo.put_citation_links.call_args[0]
        doc_ids = call_args[3]
        assert "doc_A" in doc_ids
        assert "doc_B" in doc_ids

    def test_write_no_citations_skips_links(self, service, mock_repo):
        req = CacheWriteRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="test?",
            response=CachedResponse(content="answer"),
        )
        service.write(req)

        mock_repo.put_citation_links.assert_not_called()


class TestContextAwareLookup:
    """Tests for context-aware caching via context_hash."""

    def test_same_query_different_context_are_separate(self, cache_service, cache_repo):
        """Two writes with same query but different context_hash produce separate entries."""
        from src.cache.schemas import CachedResponse, CacheLookupRequest, CacheWriteRequest

        base_write = {
            "workspace_id": "ws_01",
            "project_id": "proj_01",
            "query": "How do I reset my password?",
            "response": CachedResponse(
                content="Answer A", model="m", tokens_used={"input": 10, "output": 20}
            ),
        }

        # Write with context_hash "ctx_A"
        req_a = CacheWriteRequest(**base_write, context_hash="ctx_A")
        cache_service.write(req_a)

        # Write with context_hash "ctx_B"
        req_b = CacheWriteRequest(
            **{
                **base_write,
                "response": CachedResponse(
                    content="Answer B", model="m", tokens_used={"input": 10, "output": 20}
                ),
            },
            context_hash="ctx_B",
        )
        cache_service.write(req_b)

        # Lookup with ctx_A should get Answer A
        lookup_a = CacheLookupRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="How do I reset my password?",
            context_hash="ctx_A",
        )
        result_a = cache_service.lookup(lookup_a)
        assert result_a.status == "hit"
        assert result_a.response.content == "Answer A"

        # Lookup with ctx_B should get Answer B
        lookup_b = CacheLookupRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="How do I reset my password?",
            context_hash="ctx_B",
        )
        result_b = cache_service.lookup(lookup_b)
        assert result_b.status == "hit"
        assert result_b.response.content == "Answer B"

    def test_no_context_hash_backward_compatible(self, cache_service):
        """Lookup without context_hash works the same as before."""
        from src.cache.schemas import CachedResponse, CacheLookupRequest, CacheWriteRequest

        req = CacheWriteRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="test query?",
            response=CachedResponse(content="answer", model="m", tokens_used={}),
        )
        cache_service.write(req)

        lookup = CacheLookupRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="test query?",
        )
        result = cache_service.lookup(lookup)
        assert result.status == "hit"
        assert result.response.content == "answer"


class TestGetStats:
    """Tests for GET /v1/cache/stats."""

    def test_get_stats_returns_defaults_when_no_data(self, cache_service):
        """Returns zeroed stats when no data exists."""
        result = cache_service.get_stats("ws_01", "proj_01", "24h")
        assert result.period == "24h"
        assert result.stats.total_lookups == 0
        assert result.stats.hit_rate == 0.0
        assert result.stats.estimated_cost_saved_usd == 0.0

    def test_get_stats_returns_aggregated_data(self, cache_service):
        """Returns pre-aggregated stats when data exists."""
        from src.cache.models import StatsPeriodModel

        period = StatsPeriodModel(
            workspace_id="ws_01",
            project_id="proj_01",
            period="24h",
            timestamp="2026-04-01T14:00",
            exact_hits=100,
            semantic_hits=30,
            misses=50,
            total_lookups=180,
            hit_rate=0.722,
            exact_hit_rate=0.556,
            semantic_hit_rate=0.167,
            tokens_saved_input=50000,
            tokens_saved_output=30000,
            estimated_cost_saved_usd=1.23,
            total_entries=42,
            ttl=9999999999,
        )
        cache_service.repository.put_stats_period(period)

        result = cache_service.get_stats("ws_01", "proj_01", "24h")
        assert result.stats.total_lookups == 180
        assert result.stats.hit_rate == 0.722
        assert result.stats.estimated_cost_saved_usd == 1.23
        assert result.stats.estimated_tokens_saved.input == 50000


class TestStatsIncrement:
    """Tests for stats counter increment during lookup."""

    def test_exact_hit_increments_stats(self, cache_service):
        """An exact hit increments the exact_hits counter."""
        from src.cache.schemas import CachedResponse, CacheLookupRequest, CacheWriteRequest

        # Write an entry
        write_req = CacheWriteRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="stats test query?",
            response=CachedResponse(
                content="answer",
                model="test-model",
                tokens_used={"input": 100, "output": 50},
            ),
        )
        cache_service.write(write_req)

        # Lookup triggers stats increment
        lookup_req = CacheLookupRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="stats test query?",
        )
        result = cache_service.lookup(lookup_req)
        assert result.status == "hit"

        # Verify stats bucket was incremented
        buckets = cache_service.repository.query_stats_live_buckets("ws_01", "proj_01")
        assert len(buckets) >= 1
        bucket = buckets[0]
        assert int(bucket.get("exact_hits", 0)) == 1
        assert int(bucket.get("tokens_saved_input", 0)) == 100
        assert int(bucket.get("tokens_saved_output", 0)) == 50

    def test_miss_increments_stats(self, cache_service):
        """A miss increments the misses counter."""
        from src.cache.schemas import CacheLookupRequest

        lookup_req = CacheLookupRequest(
            workspace_id="ws_01",
            project_id="proj_01",
            query="nonexistent query?",
        )
        result = cache_service.lookup(lookup_req)
        assert result.status == "miss"

        buckets = cache_service.repository.query_stats_live_buckets("ws_01", "proj_01")
        assert len(buckets) >= 1
        bucket = buckets[0]
        assert int(bucket.get("misses", 0)) == 1
