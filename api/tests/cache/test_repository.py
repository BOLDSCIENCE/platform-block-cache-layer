"""Tests for the CacheRepository (DynamoDB operations)."""

from datetime import UTC, datetime

import pytest

from src.cache.models import CacheEntryModel
from src.cache.repository import CacheRepository
from src.common.exceptions import CacheEntryNotFoundError


@pytest.fixture
def repo(dynamodb_tables):
    """Create a CacheRepository with the test table."""
    return CacheRepository(dynamodb_tables, application_id="test-app", client_id="test-client")


def _make_entry(**overrides) -> CacheEntryModel:
    """Create a CacheEntryModel with sensible defaults."""
    defaults = {
        "cache_entry_id": "ce_01TEST",
        "application_id": "test-app",
        "client_id": "test-client",
        "workspace_id": "ws_01",
        "project_id": "proj_01",
        "query_normalized": "how do i reset my password?",
        "query_hash": "abc123hash",
        "response": {"content": "Reset your password by clicking forgot password."},
        "model": "anthropic.claude-sonnet-4-5-20250929",
        "tokens_used": {"input": 100, "output": 50},
        "citations": [],
        "hit_count": 0,
        "created_at": datetime.now(UTC).isoformat(),
        "status": "active",
        "ttl": 9999999999,
    }
    defaults.update(overrides)
    return CacheEntryModel(**defaults)


class TestPutAndGetByHash:
    def test_put_then_get_by_hash(self, repo):
        entry = _make_entry()
        repo.put(entry)

        result = repo.get_by_hash("ws_01", "proj_01", "abc123hash")
        assert result is not None
        assert result.cache_entry_id == "ce_01TEST"
        assert result.query_normalized == "how do i reset my password?"
        assert result.response["content"] == "Reset your password by clicking forgot password."

    def test_get_miss_returns_none(self, repo):
        result = repo.get_by_hash("ws_01", "proj_01", "nonexistent_hash")
        assert result is None

    def test_get_filters_by_workspace(self, repo):
        entry = _make_entry()
        repo.put(entry)

        # Same hash but different workspace should miss
        result = repo.get_by_hash("ws_other", "proj_01", "abc123hash")
        assert result is None

    def test_get_filters_by_project(self, repo):
        entry = _make_entry()
        repo.put(entry)

        # Same hash but different project should miss
        result = repo.get_by_hash("ws_01", "proj_other", "abc123hash")
        assert result is None

    def test_get_filters_invalidated(self, repo):
        entry = _make_entry(status="invalidated")
        repo.put(entry)

        result = repo.get_by_hash("ws_01", "proj_01", "abc123hash")
        assert result is None


class TestDelete:
    def test_delete_marks_invalidated(self, repo):
        entry = _make_entry()
        repo.put(entry)

        repo.delete("ce_01TEST", "ws_01", "proj_01")

        # Should no longer match as active
        result = repo.get_by_hash("ws_01", "proj_01", "abc123hash")
        assert result is None

    def test_delete_nonexistent_raises(self, repo):
        with pytest.raises(CacheEntryNotFoundError):
            repo.delete("nonexistent_id", "ws_01", "proj_01")


class TestIncrementHitCount:
    def test_increments_hit_count(self, repo):
        entry = _make_entry()
        repo.put(entry)

        from src.cache.normalizer import build_cache_sk, build_pk

        pk = build_pk("test-app", "test-client")
        sk = build_cache_sk("ws_01", "proj_01", "ce_01TEST")
        now = datetime.now(UTC).isoformat()

        repo.increment_hit_count(pk, sk, now)

        result = repo.get_by_hash("ws_01", "proj_01", "abc123hash")
        assert result is not None
        assert result.hit_count == 1
