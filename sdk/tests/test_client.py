"""Tests for CacheLayerClient."""

import httpx
import respx

from boldsci_cache_layer import CacheLayerClient
from boldsci_cache_layer.types import (
    CacheLookupResponse,
    CacheStatsResponse,
    CacheWriteResponse,
    HealthStatus,
    LookupOrExecResponse,
)

API_URL = "https://cache-layer.test.boldscience.io"
API_KEY = "test-key"

# --- Reusable response fixtures ---

LOOKUP_HIT = {
    "data": {
        "status": "hit",
        "source": "exact",
        "cacheEntryId": "entry_1",
        "response": {"content": "Answer", "model": "gpt-4o"},
        "lookupLatencyMs": 5.2,
    },
    "meta": {},
}

LOOKUP_MISS = {"data": {"status": "miss", "lookupLatencyMs": 3.1}, "meta": {}}

WRITE_OK = {
    "data": {
        "cacheEntryId": "entry_2",
        "status": "written",
        "stores": {"dynamodb": "ok"},
        "createdAt": "2026-01-01T00:00:00Z",
    },
    "meta": {},
}

STATS_OK = {
    "data": {
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
    },
    "meta": {},
}

CONFIG_OK = {
    "data": {
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
    },
    "meta": {},
}

INVALIDATE_OK = {
    "data": {
        "entriesInvalidated": 5,
        "invalidationCriteria": {"queryContains": "password"},
        "createdAt": "2026-01-01T00:00:00Z",
    },
    "meta": {},
}

PURGE_OK = {
    "data": {
        "entriesPurged": 42,
        "scope": {"workspaceId": "ws_1"},
        "createdAt": "2026-01-01T00:00:00Z",
    },
    "meta": {},
}

HEALTH_OK = {
    "data": {
        "status": "healthy",
        "service": "cache-layer-api",
        "version": "0.4.0",
        "dependencies": {"dynamodb": "healthy"},
    },
    "meta": {},
}

EXEC_OK = {
    "data": {
        "status": "miss_executed",
        "source": "model_gateway",
        "cacheEntryId": "entry_3",
        "response": {"content": "Generated", "model": "gpt-4o"},
        "lookupLatencyMs": 500.0,
    },
    "meta": {},
}


class TestLookup:
    @respx.mock
    def test_lookup_hit(self):
        respx.post(f"{API_URL}/v1/cache/lookup").mock(
            return_value=httpx.Response(200, json=LOOKUP_HIT)
        )
        client = CacheLayerClient(api_url=API_URL, api_key=API_KEY, max_retries=0)
        result = client.lookup(workspace_id="ws_1", project_id="proj_1", query="hello")
        assert isinstance(result, CacheLookupResponse)
        assert result.status == "hit"
        assert result.response.content == "Answer"
        client.close()

    @respx.mock
    def test_lookup_miss(self):
        respx.post(f"{API_URL}/v1/cache/lookup").mock(
            return_value=httpx.Response(200, json=LOOKUP_MISS)
        )
        client = CacheLayerClient(api_url=API_URL, api_key=API_KEY, max_retries=0)
        result = client.lookup(workspace_id="ws_1", project_id="proj_1", query="unknown")
        assert result.status == "miss"
        client.close()

    @respx.mock
    def test_lookup_sends_correct_body(self):
        route = respx.post(f"{API_URL}/v1/cache/lookup").mock(
            return_value=httpx.Response(200, json=LOOKUP_MISS)
        )
        client = CacheLayerClient(api_url=API_URL, api_key=API_KEY, max_retries=0)
        client.lookup(
            workspace_id="ws_1",
            project_id="proj_1",
            query="test query",
            context_hash="ctx_abc",
            enable_exact_match=True,
            enable_semantic=False,
            similarity_threshold=0.95,
        )
        import json

        body = json.loads(route.calls[0].request.content)
        assert body["workspace_id"] == "ws_1"
        assert body["query"] == "test query"
        assert body["context_hash"] == "ctx_abc"
        assert body["lookup_config"]["enable_semantic"] is False
        assert body["lookup_config"]["similarity_threshold"] == 0.95
        client.close()


class TestWrite:
    @respx.mock
    def test_write(self):
        respx.post(f"{API_URL}/v1/cache/write").mock(
            return_value=httpx.Response(200, json=WRITE_OK)
        )
        client = CacheLayerClient(api_url=API_URL, api_key=API_KEY, max_retries=0)
        result = client.write(
            workspace_id="ws_1",
            project_id="proj_1",
            query="hello",
            content="Answer",
            model="gpt-4o",
        )
        assert isinstance(result, CacheWriteResponse)
        assert result.cache_entry_id == "entry_2"
        client.close()


class TestDelete:
    @respx.mock
    def test_delete_entry(self):
        respx.delete(f"{API_URL}/v1/cache/entries/entry_1").mock(
            return_value=httpx.Response(
                200, json={"data": {"cacheEntryId": "entry_1", "status": "invalidated"}, "meta": {}}
            )
        )
        client = CacheLayerClient(api_url=API_URL, api_key=API_KEY, max_retries=0)
        result = client.delete_entry(
            cache_entry_id="entry_1", workspace_id="ws_1", project_id="proj_1"
        )
        assert result.status == "invalidated"
        client.close()


class TestInvalidate:
    @respx.mock
    def test_invalidate(self):
        respx.post(f"{API_URL}/v1/cache/invalidate").mock(
            return_value=httpx.Response(200, json=INVALIDATE_OK)
        )
        client = CacheLayerClient(api_url=API_URL, api_key=API_KEY, max_retries=0)
        result = client.invalidate(
            workspace_id="ws_1", project_id="proj_1", query_contains="password"
        )
        assert result.entries_invalidated == 5
        client.close()


class TestPurge:
    @respx.mock
    def test_purge(self):
        respx.post(f"{API_URL}/v1/cache/purge").mock(
            return_value=httpx.Response(200, json=PURGE_OK)
        )
        client = CacheLayerClient(api_url=API_URL, api_key=API_KEY, max_retries=0)
        result = client.purge(workspace_id="ws_1", confirm=True)
        assert result.entries_purged == 42
        client.close()


class TestConfig:
    @respx.mock
    def test_get_config(self):
        respx.get(f"{API_URL}/v1/cache/config").mock(
            return_value=httpx.Response(200, json=CONFIG_OK)
        )
        client = CacheLayerClient(api_url=API_URL, api_key=API_KEY, max_retries=0)
        result = client.get_config(workspace_id="ws_1", project_id="proj_1")
        assert result.config.default_ttl_seconds == 86400
        client.close()

    @respx.mock
    def test_update_config(self):
        respx.put(f"{API_URL}/v1/cache/config").mock(
            return_value=httpx.Response(200, json=CONFIG_OK)
        )
        client = CacheLayerClient(api_url=API_URL, api_key=API_KEY, max_retries=0)
        result = client.update_config(
            workspace_id="ws_1", project_id="proj_1", enabled=True, default_ttl_seconds=86400
        )
        assert result.config.enabled is True
        client.close()


class TestStats:
    @respx.mock
    def test_get_stats(self):
        respx.get(f"{API_URL}/v1/cache/stats").mock(return_value=httpx.Response(200, json=STATS_OK))
        client = CacheLayerClient(api_url=API_URL, api_key=API_KEY, max_retries=0)
        result = client.get_stats(workspace_id="ws_1", project_id="proj_1", period="24h")
        assert isinstance(result, CacheStatsResponse)
        assert result.stats.total_lookups == 100
        client.close()


class TestLookupOrExec:
    @respx.mock
    def test_lookup_or_exec(self):
        respx.post(f"{API_URL}/v1/cache/lookup-or-exec").mock(
            return_value=httpx.Response(200, json=EXEC_OK)
        )
        client = CacheLayerClient(api_url=API_URL, api_key=API_KEY, max_retries=0)
        result = client.lookup_or_exec(
            workspace_id="ws_1",
            project_id="proj_1",
            query="hello",
            on_miss_model="gpt-4o",
            on_miss_messages=[{"role": "user", "content": "hello"}],
        )
        assert isinstance(result, LookupOrExecResponse)
        assert result.status == "miss_executed"
        client.close()


class TestHealth:
    @respx.mock
    def test_health(self):
        respx.get(f"{API_URL}/v1/health").mock(return_value=httpx.Response(200, json=HEALTH_OK))
        client = CacheLayerClient(api_url=API_URL, api_key=API_KEY, max_retries=0)
        result = client.health()
        assert isinstance(result, HealthStatus)
        assert result.status == "healthy"
        client.close()


class TestContextManager:
    def test_context_manager(self):
        with CacheLayerClient(api_url=API_URL, api_key=API_KEY) as client:
            assert client is not None
