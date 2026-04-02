"""Tests for MCP server tool and resource registration."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _set_env(monkeypatch):
    monkeypatch.setenv("CACHE_LAYER_API_URL", "https://test.api.example.com")
    monkeypatch.setenv("CACHE_LAYER_API_KEY", "test-key")


@pytest.fixture
def _reset_client():
    """Reset the module-level client singleton between tests."""
    from bold_cache_layer_mcp import server

    server._client = None
    yield
    server._client = None


class TestToolRegistration:
    def test_all_tools_registered(self):
        from bold_cache_layer_mcp.server import mcp as server

        tool_names = [t.name for t in server._tool_manager.list_tools()]
        expected = [
            "cache_lookup",
            "cache_write",
            "cache_invalidate",
            "cache_purge",
            "cache_stats",
            "cache_config_get",
            "cache_config_update",
        ]
        for name in expected:
            assert name in tool_names, f"Missing tool: {name}"

    def test_all_resources_registered(self):
        from bold_cache_layer_mcp.server import mcp as server

        # Resources with parameters are registered as templates
        resource_templates = server._resource_manager.list_templates()
        template_uris = [str(t.uri_template) for t in resource_templates]
        assert any("stats" in uri for uri in template_uris)
        assert any("config" in uri for uri in template_uris)

        # Resources without parameters are registered directly
        resources = server._resource_manager.list_resources()
        resource_uris = [str(r.uri) for r in resources]
        assert any("health" in uri for uri in resource_uris)


class TestCacheLookupTool:
    @patch("bold_cache_layer_mcp.server._get_client")
    def test_lookup_calls_client(self, mock_get_client, _reset_client):
        from boldsci_cache_layer.types import CacheLookupResponse

        mock_client = MagicMock()
        mock_client.lookup.return_value = CacheLookupResponse(status="hit", source="exact")
        mock_get_client.return_value = mock_client

        from bold_cache_layer_mcp.server import cache_lookup

        result = cache_lookup(workspace_id="ws_1", project_id="proj_1", query="hello")
        assert result["status"] == "hit"
        mock_client.lookup.assert_called_once()


class TestConfigFromEnv:
    def test_missing_api_url_raises(self, monkeypatch):
        monkeypatch.delenv("CACHE_LAYER_API_URL", raising=False)
        from bold_cache_layer_mcp.config import get_api_url

        with pytest.raises(RuntimeError, match="CACHE_LAYER_API_URL"):
            get_api_url()

    def test_missing_api_key_raises(self, monkeypatch):
        monkeypatch.delenv("CACHE_LAYER_API_KEY", raising=False)
        from bold_cache_layer_mcp.config import get_api_key

        with pytest.raises(RuntimeError, match="CACHE_LAYER_API_KEY"):
            get_api_key()
