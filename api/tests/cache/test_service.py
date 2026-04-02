"""Unit tests for CacheService with mocked repository."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from src.cache.models import CacheEntryModel
from src.cache.normalizer import compute_query_hash, normalize_query
from src.cache.schemas import (
    CachedResponse,
    CacheLookupRequest,
    CacheWriteRequest,
    LookupConfig,
)
from src.cache.service import CacheService
from src.common.exceptions import CacheEntryNotFoundError


@pytest.fixture
def mock_repo():
    repo = MagicMock()
    repo.application_id = "test-app"
    repo.client_id = "test-client"
    return repo


@pytest.fixture
def service(mock_repo):
    return CacheService(mock_repo)


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
