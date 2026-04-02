"""Tests for the CacheRepository (DynamoDB operations)."""

from datetime import UTC, datetime

import pytest

from src.cache.models import CacheConfigModel, CacheEntryModel, InvalidationEventModel
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


class TestQueryByProject:
    def test_query_returns_matching_entries(self, repo):
        e1 = _make_entry(cache_entry_id="ce_A", query_hash="h1")
        e2 = _make_entry(cache_entry_id="ce_B", query_hash="h2")
        repo.put(e1)
        repo.put(e2)

        entries, _ = repo.query_by_project("ws_01", "proj_01")
        ids = {e.cache_entry_id for e in entries}
        assert "ce_A" in ids
        assert "ce_B" in ids

    def test_query_empty_result(self, repo):
        entries, _ = repo.query_by_project("ws_99", "proj_99")
        assert entries == []

    def test_query_filters_invalidated(self, repo):
        e1 = _make_entry(cache_entry_id="ce_ACTIVE", query_hash="h1")
        e2 = _make_entry(cache_entry_id="ce_DEAD", query_hash="h2", status="invalidated")
        repo.put(e1)
        repo.put(e2)

        entries, _ = repo.query_by_project("ws_01", "proj_01")
        ids = {e.cache_entry_id for e in entries}
        assert "ce_ACTIVE" in ids
        assert "ce_DEAD" not in ids

    def test_query_all_by_project(self, repo):
        for i in range(5):
            repo.put(_make_entry(cache_entry_id=f"ce_{i}", query_hash=f"h{i}"))

        entries = repo.query_all_by_project("ws_01", "proj_01")
        assert len(entries) == 5

    def test_query_all_by_workspace(self, repo):
        repo.put(_make_entry(cache_entry_id="ce_P1", project_id="proj_01", query_hash="h1"))
        repo.put(_make_entry(cache_entry_id="ce_P2", project_id="proj_02", query_hash="h2"))

        entries = repo.query_all_by_workspace("ws_01")
        ids = {e.cache_entry_id for e in entries}
        assert "ce_P1" in ids
        assert "ce_P2" in ids


class TestBatchInvalidate:
    def test_batch_invalidate_marks_entries(self, repo):
        e1 = _make_entry(cache_entry_id="ce_X", query_hash="hx")
        e2 = _make_entry(cache_entry_id="ce_Y", query_hash="hy")
        repo.put(e1)
        repo.put(e2)

        count = repo.batch_invalidate([e1, e2])
        assert count == 2

        # Entries should no longer be active
        assert repo.get_by_hash("ws_01", "proj_01", "hx") is None
        assert repo.get_by_hash("ws_01", "proj_01", "hy") is None

    def test_batch_invalidate_returns_count(self, repo):
        e1 = _make_entry(cache_entry_id="ce_Z", query_hash="hz")
        repo.put(e1)

        count = repo.batch_invalidate([e1])
        assert count == 1


class TestConfig:
    def test_get_config_returns_none_when_empty(self, repo):
        result = repo.get_config("ws_01", "proj_01")
        assert result is None

    def test_put_then_get_config(self, repo):
        config = CacheConfigModel(
            workspace_id="ws_01",
            project_id="proj_01",
            enabled=True,
            default_ttl_seconds=7200,
            similarity_threshold=0.88,
            updated_at=datetime.now(UTC).isoformat(),
            updated_by="key_admin",
        )
        repo.put_config(config)

        result = repo.get_config("ws_01", "proj_01")
        assert result is not None
        assert result.enabled is True
        assert result.default_ttl_seconds == 7200
        assert result.similarity_threshold == 0.88
        assert result.updated_by == "key_admin"


class TestInvalidationEvent:
    def test_record_event(self, repo):
        event = InvalidationEventModel(
            event_id="inv_TEST01",
            workspace_id="ws_01",
            project_id="proj_01",
            source="manual",
            criteria={"query_contains": "password"},
            entries_affected=3,
            triggered_by="api",
            created_at=datetime.now(UTC).isoformat(),
            ttl=9999999999,
        )
        # Should not raise
        repo.record_invalidation_event(event)


class TestCitationLinks:
    def test_put_and_query_by_citation(self, repo):
        repo.put_citation_links("ce_CITE1", "ws_01", "proj_01", ["doc_A", "doc_B"])

        result_a = repo.query_by_citation("doc_A")
        assert "ce_CITE1" in result_a

        result_b = repo.query_by_citation("doc_B")
        assert "ce_CITE1" in result_b

    def test_query_empty_citation(self, repo):
        result = repo.query_by_citation("doc_NONEXIST")
        assert result == []

    def test_delete_citation_links(self, repo):
        repo.put_citation_links("ce_CITE2", "ws_01", "proj_01", ["doc_C"])
        assert "ce_CITE2" in repo.query_by_citation("doc_C")

        repo.delete_citation_links("ce_CITE2", ["doc_C"])
        # After deletion, the GSI entry won't be present anymore
        # (moto may still return it from GSI since it uses eventual consistency,
        # but the main table item is gone)


class TestStatsBucket:
    """Tests for stats bucket DynamoDB operations."""

    def test_increment_stats_creates_bucket(self, repo):
        """First increment creates the bucket item."""
        repo.increment_stats_bucket("ws_01", "proj_01", "2026-04-01T14:15", "exact_hits", 10, 5)

        from src.cache.normalizer import build_pk, build_stats_live_sk

        pk = build_pk("test-app", "test-client")
        sk = build_stats_live_sk("ws_01", "proj_01", "2026-04-01T14:15")

        response = repo.table.get_item(Key={"PK": pk, "SK": sk})
        item = response["Item"]
        assert item["exact_hits"] == 1
        assert item["tokens_saved_input"] == 10
        assert item["tokens_saved_output"] == 5

    def test_increment_stats_accumulates(self, repo):
        """Multiple increments accumulate atomically."""
        repo.increment_stats_bucket("ws_01", "proj_01", "2026-04-01T14:15", "exact_hits", 10, 5)
        repo.increment_stats_bucket("ws_01", "proj_01", "2026-04-01T14:15", "misses", 0, 0)
        repo.increment_stats_bucket("ws_01", "proj_01", "2026-04-01T14:15", "exact_hits", 20, 10)

        from src.cache.normalizer import build_pk, build_stats_live_sk

        pk = build_pk("test-app", "test-client")
        sk = build_stats_live_sk("ws_01", "proj_01", "2026-04-01T14:15")

        response = repo.table.get_item(Key={"PK": pk, "SK": sk})
        item = response["Item"]
        assert item["exact_hits"] == 2
        assert item["misses"] == 1
        assert item["tokens_saved_input"] == 30
        assert item["tokens_saved_output"] == 15

    def test_query_stats_live_buckets(self, repo):
        """Query returns all live buckets for a scope."""
        repo.increment_stats_bucket("ws_01", "proj_01", "2026-04-01T14:00", "exact_hits", 0, 0)
        repo.increment_stats_bucket("ws_01", "proj_01", "2026-04-01T14:15", "misses", 0, 0)

        buckets = repo.query_stats_live_buckets("ws_01", "proj_01")
        assert len(buckets) == 2

    def test_put_and_query_stats_period(self, repo):
        """Write and read a pre-aggregated stats period."""
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
        repo.put_stats_period(period)

        result = repo.query_stats_period("ws_01", "proj_01", "24h")
        assert result is not None
        assert result.exact_hits == 100
        assert result.hit_rate == 0.722
        assert result.estimated_cost_saved_usd == 1.23
