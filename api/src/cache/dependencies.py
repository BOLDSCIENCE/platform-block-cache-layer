"""Cache domain dependency injection wiring."""

from functools import lru_cache
from typing import Annotated

import structlog
from boldsci.auth import AuthContext
from fastapi import Depends

from src.auth.middleware import auth_middleware
from src.cache.embedding_service import EmbeddingService
from src.cache.opensearch_repository import OpenSearchRepository
from src.cache.repository import CacheRepository
from src.cache.service import CacheService
from src.common.dependencies import get_dynamodb_table
from src.config import get_settings

logger = structlog.get_logger()


@lru_cache
def _get_opensearch_client():
    """Get cached OpenSearch client singleton, or None if not configured."""
    settings = get_settings()
    if not settings.opensearch_endpoint:
        return None

    import boto3
    from opensearchpy import OpenSearch, RequestsHttpConnection
    from requests_aws4auth import AWS4Auth

    credentials = boto3.Session().get_credentials()
    aws_auth = AWS4Auth(
        credentials.access_key,
        credentials.secret_key,
        settings.aws_region,
        "es",
        session_token=credentials.token,
    )

    return OpenSearch(
        hosts=[{"host": settings.opensearch_endpoint, "port": 443}],
        http_auth=aws_auth,
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
    )


def get_opensearch_repository() -> OpenSearchRepository | None:
    """Build an OpenSearchRepository or None if not configured."""
    client = _get_opensearch_client()
    if client is None:
        return None
    return OpenSearchRepository(client)


@lru_cache
def _get_gateway_client():
    """Get cached Model Gateway client singleton, or None if not configured."""
    settings = get_settings()
    if not settings.model_gateway_api_url or not settings.model_gateway_api_key:
        return None

    from boldsci_model_gateway import GatewayClient

    return GatewayClient(
        api_url=settings.model_gateway_api_url,
        api_key=settings.model_gateway_api_key,
        timeout=10.0,
    )


def get_embedding_service() -> EmbeddingService | None:
    """Build an EmbeddingService or None if not configured."""
    client = _get_gateway_client()
    if client is None:
        return None
    return EmbeddingService(client)


def get_cache_repository(
    table=Depends(get_dynamodb_table),
    auth: AuthContext = Depends(auth_middleware),
) -> CacheRepository:
    """Build a CacheRepository scoped to the authenticated tenant."""
    return CacheRepository(table, auth.application_id, auth.client_id)


def get_cache_service(
    repo: CacheRepository = Depends(get_cache_repository),
) -> CacheService:
    """Build a CacheService with the tenant-scoped repository."""
    return CacheService(
        repository=repo,
        opensearch_repo=get_opensearch_repository(),
        embedding_service=get_embedding_service(),
        gateway_client=_get_gateway_client(),
    )


CacheServiceDep = Annotated[CacheService, Depends(get_cache_service)]
