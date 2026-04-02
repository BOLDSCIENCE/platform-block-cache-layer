"""Integration tests for cache router using TestClient + moto."""

from tests.conftest import unwrap


class TestCacheLookup:
    def test_miss_returns_200(self, client):
        response = client.post(
            "/v1/cache/lookup",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "query": "How do I reset my password?",
            },
        )
        assert response.status_code == 200
        data = unwrap(response)
        assert data["status"] == "miss"

    def test_write_then_lookup_round_trip(self, client):
        # Write
        write_resp = client.post(
            "/v1/cache/write",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "query": "How do I reset my password?",
                "response": {
                    "content": "Click forgot password link.",
                    "model": "anthropic.claude-sonnet-4-5-20250929",
                    "tokens_used": {"input": 100, "output": 50},
                },
            },
        )
        assert write_resp.status_code == 200
        write_data = unwrap(write_resp)
        assert write_data["status"] == "written"
        entry_id = write_data["cacheEntryId"]

        # Lookup
        lookup_resp = client.post(
            "/v1/cache/lookup",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "query": "How do I reset my password?",
            },
        )
        assert lookup_resp.status_code == 200
        lookup_data = unwrap(lookup_resp)
        assert lookup_data["status"] == "hit"
        assert lookup_data["source"] == "exact"
        assert lookup_data["cacheEntryId"] == entry_id
        assert lookup_data["response"]["content"] == "Click forgot password link."

    def test_hit_includes_cache_metadata(self, client):
        # Write first
        client.post(
            "/v1/cache/write",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "query": "test metadata query?",
                "response": {"content": "answer"},
            },
        )

        # Lookup
        resp = client.post(
            "/v1/cache/lookup",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "query": "test metadata query?",
            },
        )
        data = unwrap(resp)
        assert data["status"] == "hit"
        assert "cacheMetadata" in data
        assert data["cacheMetadata"]["hitCount"] == 1
        assert data["cacheMetadata"]["createdAt"] is not None

    def test_normalized_query_matches(self, client):
        # Write with normal casing
        client.post(
            "/v1/cache/write",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "query": "How do I reset my password?",
                "response": {"content": "answer"},
            },
        )

        # Lookup with different casing/whitespace
        resp = client.post(
            "/v1/cache/lookup",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "query": "  how do i  reset my password??  ",
            },
        )
        data = unwrap(resp)
        assert data["status"] == "hit"


class TestCacheWrite:
    def test_write_returns_entry_id(self, client):
        resp = client.post(
            "/v1/cache/write",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "query": "test?",
                "response": {"content": "answer"},
            },
        )
        assert resp.status_code == 200
        data = unwrap(resp)
        assert data["cacheEntryId"].startswith("ce_")
        assert data["status"] == "written"
        assert data["stores"]["dynamodb"] == "ok"

    def test_write_returns_camel_case(self, client):
        resp = client.post(
            "/v1/cache/write",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "query": "test?",
                "response": {"content": "answer"},
            },
        )
        data = unwrap(resp)
        assert "cacheEntryId" in data
        assert "expiresAt" in data
        assert "createdAt" in data


class TestCacheDelete:
    def test_delete_after_write(self, client):
        # Write
        write_resp = client.post(
            "/v1/cache/write",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "query": "test delete?",
                "response": {"content": "answer"},
            },
        )
        entry_id = unwrap(write_resp)["cacheEntryId"]

        # Delete
        delete_resp = client.delete(
            f"/v1/cache/entries/{entry_id}",
            params={"workspace_id": "ws_01", "project_id": "proj_01"},
        )
        assert delete_resp.status_code == 200
        data = unwrap(delete_resp)
        assert data["status"] == "invalidated"
        assert data["cacheEntryId"] == entry_id

        # Lookup should miss
        lookup_resp = client.post(
            "/v1/cache/lookup",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "query": "test delete?",
            },
        )
        assert unwrap(lookup_resp)["status"] == "miss"

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete(
            "/v1/cache/entries/nonexistent_id",
            params={"workspace_id": "ws_01", "project_id": "proj_01"},
        )
        assert resp.status_code == 404


class TestAuth:
    def test_lookup_requires_auth(self, unauth_client):
        resp = unauth_client.post(
            "/v1/cache/lookup",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "query": "test?",
            },
        )
        assert resp.status_code == 401

    def test_write_requires_write_scope(self, read_client):
        resp = read_client.post(
            "/v1/cache/write",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "query": "test?",
                "response": {"content": "answer"},
            },
        )
        assert resp.status_code == 403

    def test_delete_requires_write_scope(self, read_client):
        resp = read_client.delete(
            "/v1/cache/entries/some_id",
            params={"workspace_id": "ws_01", "project_id": "proj_01"},
        )
        assert resp.status_code == 403


class TestCacheInvalidate:
    def test_invalidate_with_criteria(self, client):
        # Write some entries
        for q in ["password reset help", "password change", "weather forecast"]:
            client.post(
                "/v1/cache/write",
                json={
                    "workspace_id": "ws_01",
                    "project_id": "proj_01",
                    "query": q,
                    "response": {"content": f"answer for {q}"},
                },
            )

        # Invalidate entries matching "password"
        resp = client.post(
            "/v1/cache/invalidate",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "invalidation_criteria": {"query_contains": "password"},
            },
        )
        assert resp.status_code == 200
        data = unwrap(resp)
        assert data["entriesInvalidated"] == 2

        # Weather should still be a hit
        lookup_resp = client.post(
            "/v1/cache/lookup",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "query": "weather forecast",
            },
        )
        assert unwrap(lookup_resp)["status"] == "hit"

        # Password should be a miss
        lookup_resp2 = client.post(
            "/v1/cache/lookup",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "query": "password reset help",
            },
        )
        assert unwrap(lookup_resp2)["status"] == "miss"

    def test_invalidate_requires_write_scope(self, read_client):
        resp = read_client.post(
            "/v1/cache/invalidate",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "invalidation_criteria": {},
            },
        )
        assert resp.status_code == 403


class TestCachePurge:
    def test_purge_with_confirm(self, client):
        # Write entries
        for i in range(3):
            client.post(
                "/v1/cache/write",
                json={
                    "workspace_id": "ws_01",
                    "project_id": "proj_01",
                    "query": f"purge test {i}?",
                    "response": {"content": f"answer {i}"},
                },
            )

        resp = client.post(
            "/v1/cache/purge",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "confirm": True,
            },
        )
        assert resp.status_code == 200
        data = unwrap(resp)
        assert data["entriesPurged"] == 3

    def test_purge_reject_without_confirm(self, client):
        resp = client.post(
            "/v1/cache/purge",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "confirm": False,
            },
        )
        assert resp.status_code == 400

    def test_purge_requires_admin_scope(self, write_client):
        resp = write_client.post(
            "/v1/cache/purge",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "confirm": True,
            },
        )
        assert resp.status_code == 403


class TestCacheConfig:
    def test_get_config_returns_defaults(self, client):
        resp = client.get(
            "/v1/cache/config",
            params={"workspace_id": "ws_01", "project_id": "proj_01"},
        )
        assert resp.status_code == 200
        data = unwrap(resp)
        assert data["config"]["enabled"] is True
        assert data["config"]["defaultTtlSeconds"] == 86400

    def test_put_config(self, client):
        resp = client.put(
            "/v1/cache/config",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "config": {
                    "enabled": False,
                    "default_ttl_seconds": 3600,
                },
            },
        )
        assert resp.status_code == 200
        data = unwrap(resp)
        assert data["config"]["enabled"] is False
        assert data["config"]["defaultTtlSeconds"] == 3600

        # GET should return updated config
        get_resp = client.get(
            "/v1/cache/config",
            params={"workspace_id": "ws_01", "project_id": "proj_01"},
        )
        get_data = unwrap(get_resp)
        assert get_data["config"]["enabled"] is False

    def test_put_config_requires_admin_scope(self, write_client):
        resp = write_client.put(
            "/v1/cache/config",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "config": {"enabled": True},
            },
        )
        assert resp.status_code == 403


class TestResponseEnvelope:
    def test_response_has_data_and_meta(self, client):
        resp = client.post(
            "/v1/cache/lookup",
            json={
                "workspace_id": "ws_01",
                "project_id": "proj_01",
                "query": "test?",
            },
        )
        body = resp.json()
        assert "data" in body
        assert "meta" in body
        assert "timestamp" in body["meta"]
        assert "requestId" in body["meta"]
