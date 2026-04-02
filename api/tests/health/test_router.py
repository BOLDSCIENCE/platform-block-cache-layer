"""Tests for the health check endpoint."""

from tests.conftest import unwrap


def test_health_returns_200(client):
    response = client.get("/health")
    assert response.status_code == 200
    data = unwrap(response)
    assert data["status"] == "healthy"
    assert data["service"] == "cache-layer-api"


def test_health_includes_dependencies(client):
    response = client.get("/health")
    data = unwrap(response)
    assert "dependencies" in data
    assert data["dependencies"]["dynamodb"] == "healthy"


def test_health_v1_returns_200(client):
    response = client.get("/v1/health")
    assert response.status_code == 200
    data = unwrap(response)
    assert data["status"] == "healthy"
