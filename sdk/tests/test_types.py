"""Tests for SDK Pydantic types."""

from boldsci_cache_layer.types import (
    CacheConfig,
    CacheConfigResponse,
    CacheLookupResponse,
    CacheStatsDetail,
    CacheStatsResponse,
    CacheWriteResponse,
    CachedResponse,
    LookupOrExecResponse,
    TokensSaved,
)


class TestCamelCaseAliases:
    def test_lookup_response_from_camel(self):
        data = {
            "status": "hit",
            "source": "exact",
            "cacheEntryId": "entry_123",
            "response": {"content": "Hello", "model": "gpt-4o", "tokensUsed": {"input": 5, "output": 10}},
            "similarityScore": 1.0,
            "matchedQuery": "hello",
            "cacheMetadata": {"createdAt": "2026-01-01T00:00:00Z", "hitCount": 3},
            "lookupLatencyMs": 12.5,
        }
        resp = CacheLookupResponse.model_validate(data)
        assert resp.status == "hit"
        assert resp.cache_entry_id == "entry_123"
        assert resp.response.content == "Hello"
        assert resp.response.tokens_used == {"input": 5, "output": 10}
        assert resp.cache_metadata.hit_count == 3
        assert resp.lookup_latency_ms == 12.5

    def test_write_response_from_camel(self):
        data = {
            "cacheEntryId": "entry_456",
            "status": "written",
            "stores": {"dynamodb": "ok", "opensearch": "ok"},
            "expiresAt": "2026-01-02T00:00:00Z",
            "createdAt": "2026-01-01T00:00:00Z",
        }
        resp = CacheWriteResponse.model_validate(data)
        assert resp.cache_entry_id == "entry_456"
        assert resp.stores["opensearch"] == "ok"

    def test_stats_response_from_camel(self):
        data = {
            "workspaceId": "ws_1",
            "projectId": "proj_1",
            "period": "24h",
            "stats": {
                "totalLookups": 100,
                "exactHits": 60,
                "semanticHits": 20,
                "misses": 20,
                "hitRate": 0.8,
                "exactHitRate": 0.6,
                "semanticHitRate": 0.2,
                "totalEntries": 50,
                "estimatedCostSavedUsd": 1.23,
                "estimatedTokensSaved": {"input": 5000, "output": 3000},
            },
        }
        resp = CacheStatsResponse.model_validate(data)
        assert resp.stats.total_lookups == 100
        assert resp.stats.hit_rate == 0.8
        assert resp.stats.estimated_tokens_saved.input == 5000

    def test_config_response_from_camel(self):
        data = {
            "workspaceId": "ws_1",
            "projectId": "proj_1",
            "config": {
                "enabled": True,
                "defaultTtlSeconds": 86400,
                "semanticTtlSeconds": 3600,
                "similarityThreshold": 0.92,
                "maxEntrySizeBytes": 102400,
                "eventDrivenInvalidation": True,
                "invalidationEvents": [],
            },
            "updatedAt": "2026-01-01T00:00:00Z",
            "updatedBy": "key_abc",
        }
        resp = CacheConfigResponse.model_validate(data)
        assert resp.config.default_ttl_seconds == 86400
        assert resp.updated_by == "key_abc"

    def test_lookup_or_exec_response(self):
        data = {
            "status": "miss_executed",
            "source": "model_gateway",
            "cacheEntryId": "entry_789",
            "response": {"content": "Generated answer", "model": "gpt-4o"},
            "lookupLatencyMs": 250.0,
        }
        resp = LookupOrExecResponse.model_validate(data)
        assert resp.status == "miss_executed"
        assert resp.source == "model_gateway"
