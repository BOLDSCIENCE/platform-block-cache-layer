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
        mock_repo.get_by_hash.assert_called_once_with("ws_01", "proj_01", expected_hash)

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
