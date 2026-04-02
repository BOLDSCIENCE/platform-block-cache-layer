"""Pytest fixtures for the Cache Layer API tests."""

import os

# IMPORTANT: Set environment variables BEFORE any app imports
os.environ["DYNAMODB_TABLE"] = "bold-cache-layer-test"
os.environ["AWS_REGION"] = "us-east-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["OPENSEARCH_ENDPOINT"] = ""
os.environ["MODEL_GATEWAY_API_URL"] = ""

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws


def unwrap(response):
    """Extract data from response envelope.

    The ResponseEnvelopeMiddleware wraps all 2xx JSON responses in
    ``{"data": ..., "meta": {...}}``.  This helper peels that off so
    test assertions can check the inner payload.
    """
    body = response.json()
    if "data" in body:
        return body["data"]
    return body


@pytest.fixture
def dynamodb_tables():
    """Create mock DynamoDB table for testing.

    Creates table with PK/SK + GSI1 (QueryHash) + GSI2 (ProjectEntries) + TTL.
    """
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

        table = dynamodb.create_table(
            TableName="bold-cache-layer-test",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "GSI1PK", "AttributeType": "S"},
                {"AttributeName": "GSI1SK", "AttributeType": "S"},
                {"AttributeName": "GSI2PK", "AttributeType": "S"},
                {"AttributeName": "GSI2SK", "AttributeType": "S"},
                {"AttributeName": "GSI3PK", "AttributeType": "S"},
                {"AttributeName": "GSI3SK", "AttributeType": "S"},
                {"AttributeName": "GSI4PK", "AttributeType": "S"},
                {"AttributeName": "GSI4SK", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "GSI2",
                    "KeySchema": [
                        {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI2SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "GSI3",
                    "KeySchema": [
                        {"AttributeName": "GSI3PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI3SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "KEYS_ONLY"},
                },
                {
                    "IndexName": "GSI4",
                    "KeySchema": [
                        {"AttributeName": "GSI4PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI4SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        yield table


@pytest.fixture
def admin_auth_context():
    """Create an admin-scoped AuthContext for testing (cache:*)."""
    from src.auth.context import AuthContext

    return AuthContext(
        client_id="test-client",
        application_id="test-app",
        scopes=["cache:*"],
        key_id="key_abc123",
        auth_method="api_key",
    )


@pytest.fixture
def read_auth_context():
    """Create a read-only AuthContext for testing (cache:read)."""
    from src.auth.context import AuthContext

    return AuthContext(
        client_id="test-client",
        application_id="test-app",
        scopes=["cache:read"],
        key_id="key_read456",
        auth_method="api_key",
    )


@pytest.fixture
def write_auth_context():
    """Create a write-scoped AuthContext for testing (cache:read + cache:write)."""
    from src.auth.context import AuthContext

    return AuthContext(
        client_id="test-client",
        application_id="test-app",
        scopes=["cache:read", "cache:write"],
        key_id="key_write789",
        auth_method="api_key",
    )


def _make_auth_override(auth_context):
    """Create an auth_middleware override function that returns the given context."""
    from src.auth.context import set_auth_context

    async def _override(request=None):
        set_auth_context(auth_context)
        return auth_context

    return _override


@pytest.fixture
def client(dynamodb_tables, admin_auth_context) -> TestClient:
    """Create a FastAPI TestClient with admin auth patched."""
    from src.auth.middleware import auth_middleware
    from src.main import app

    app.dependency_overrides[auth_middleware] = _make_auth_override(admin_auth_context)

    test_client = TestClient(app)
    yield test_client

    app.dependency_overrides.pop(auth_middleware, None)


@pytest.fixture
def write_client(dynamodb_tables, write_auth_context) -> TestClient:
    """Create a FastAPI TestClient with write auth (no admin)."""
    from src.auth.middleware import auth_middleware
    from src.main import app

    app.dependency_overrides[auth_middleware] = _make_auth_override(write_auth_context)

    test_client = TestClient(app)
    yield test_client

    app.dependency_overrides.pop(auth_middleware, None)


@pytest.fixture
def read_client(dynamodb_tables, read_auth_context) -> TestClient:
    """Create a FastAPI TestClient with read-only auth."""
    from src.auth.middleware import auth_middleware
    from src.main import app

    app.dependency_overrides[auth_middleware] = _make_auth_override(read_auth_context)

    test_client = TestClient(app)
    yield test_client

    app.dependency_overrides.pop(auth_middleware, None)


@pytest.fixture
def unauth_client(dynamodb_tables) -> TestClient:
    """Create a FastAPI TestClient that simulates unauthenticated requests."""
    from fastapi import HTTPException

    from src.auth.middleware import auth_middleware
    from src.main import app

    async def _raise_401(request=None):
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "No auth context found"},
        )

    app.dependency_overrides[auth_middleware] = _raise_401

    test_client = TestClient(app)
    yield test_client

    app.dependency_overrides.pop(auth_middleware, None)


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Reset module-level singletons between tests."""
    yield
    from src.health import router as health_router

    health_router._cached_result = None
    health_router._cached_at = 0.0


@pytest.fixture(autouse=True)
def _reset_circuit_breakers():
    """Reset circuit breakers between tests."""
    yield
    from src.cache.embedding_service import _circuit_breaker as embed_cb
    from src.cache.opensearch_repository import _circuit_breaker as os_cb

    embed_cb.reset()
    os_cb.reset()


@pytest.fixture
def cache_repo(dynamodb_tables):
    """Create a CacheRepository connected to mocked DynamoDB."""
    from src.cache.repository import CacheRepository

    return CacheRepository(dynamodb_tables, application_id="test-app", client_id="test-client")


@pytest.fixture
def cache_service(cache_repo):
    """Create a CacheService with real repository (no semantic features)."""
    from src.cache.service import CacheService

    return CacheService(cache_repo)
