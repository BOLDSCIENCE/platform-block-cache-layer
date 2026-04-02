"""Tests for the base HTTP client."""

import httpx
import respx

from boldsci_cache_layer._base import BaseClient
from boldsci_cache_layer.exceptions import (
    APIError,
    AuthenticationError,
    NetworkError,
    NotFoundError,
)

API_URL = "https://cache-layer.test.boldscience.io"
API_KEY = "test-key"


class TestEnvelopeUnwrap:
    @respx.mock
    def test_unwraps_data_envelope(self):
        respx.get(f"{API_URL}/v1/health").mock(
            return_value=httpx.Response(200, json={"data": {"status": "healthy"}, "meta": {}})
        )
        client = BaseClient(api_url=API_URL, api_key=API_KEY, max_retries=0)
        result = client._request("GET", "/v1/health")
        assert result == {"status": "healthy"}
        client.close()

    @respx.mock
    def test_returns_body_without_envelope(self):
        respx.get(f"{API_URL}/v1/raw").mock(return_value=httpx.Response(200, json={"raw": True}))
        client = BaseClient(api_url=API_URL, api_key=API_KEY, max_retries=0)
        result = client._request("GET", "/v1/raw")
        assert result == {"raw": True}
        client.close()

    @respx.mock
    def test_handles_204_no_content(self):
        respx.delete(f"{API_URL}/v1/cache/entries/x").mock(return_value=httpx.Response(204))
        client = BaseClient(api_url=API_URL, api_key=API_KEY, max_retries=0)
        result = client._request("DELETE", "/v1/cache/entries/x")
        assert result is None
        client.close()


class TestErrorParsing:
    @respx.mock
    def test_401_raises_authentication_error(self):
        respx.post(f"{API_URL}/v1/cache/lookup").mock(
            return_value=httpx.Response(
                401,
                json={"error": {"code": "UNAUTHORIZED", "message": "Bad key", "details": {}}},
            )
        )
        client = BaseClient(api_url=API_URL, api_key=API_KEY, max_retries=0)
        try:
            client._request("POST", "/v1/cache/lookup", json={})
            assert False, "Should have raised"
        except AuthenticationError as e:
            assert e.code == "UNAUTHORIZED"
            assert e.message == "Bad key"
        client.close()

    @respx.mock
    def test_404_raises_not_found(self):
        respx.get(f"{API_URL}/v1/cache/config").mock(
            return_value=httpx.Response(
                404,
                json={"error": {"code": "NOT_FOUND", "message": "Not found"}},
            )
        )
        client = BaseClient(api_url=API_URL, api_key=API_KEY, max_retries=0)
        try:
            client._request("GET", "/v1/cache/config")
            assert False, "Should have raised"
        except NotFoundError as e:
            assert e.code == "NOT_FOUND"
        client.close()

    @respx.mock
    def test_500_raises_api_error(self):
        respx.get(f"{API_URL}/v1/health").mock(
            return_value=httpx.Response(
                500, json={"error": {"code": "INTERNAL", "message": "Boom"}}
            )
        )
        client = BaseClient(api_url=API_URL, api_key=API_KEY, max_retries=0)
        try:
            client._request("GET", "/v1/health")
            assert False, "Should have raised"
        except APIError:
            pass
        client.close()


class TestRetries:
    @respx.mock
    def test_retries_on_503(self):
        route = respx.get(f"{API_URL}/v1/health")
        route.side_effect = [
            httpx.Response(503, json={"error": {"code": "UNAVAILABLE", "message": "Down"}}),
            httpx.Response(200, json={"data": {"status": "healthy"}, "meta": {}}),
        ]
        client = BaseClient(api_url=API_URL, api_key=API_KEY, max_retries=1)
        result = client._request("GET", "/v1/health")
        assert result == {"status": "healthy"}
        assert route.call_count == 2
        client.close()

    @respx.mock
    def test_network_error_raises(self):
        respx.get(f"{API_URL}/v1/health").mock(side_effect=httpx.ConnectError("refused"))
        client = BaseClient(api_url=API_URL, api_key=API_KEY, max_retries=0)
        try:
            client._request("GET", "/v1/health")
            assert False, "Should have raised"
        except NetworkError:
            pass
        client.close()


class TestAuthHeader:
    @respx.mock
    def test_sends_api_key_header(self):
        route = respx.get(f"{API_URL}/v1/health").mock(
            return_value=httpx.Response(200, json={"data": {}, "meta": {}})
        )
        client = BaseClient(api_url=API_URL, api_key="my-secret-key", max_retries=0)
        client._request("GET", "/v1/health")
        assert route.calls[0].request.headers["X-API-Key"] == "my-secret-key"
        client.close()
