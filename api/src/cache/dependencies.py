"""Cache domain dependency injection wiring."""

from typing import Annotated

from boldsci.auth import AuthContext
from fastapi import Depends

from src.auth.middleware import auth_middleware
from src.cache.repository import CacheRepository
from src.cache.service import CacheService
from src.common.dependencies import get_dynamodb_table


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
    return CacheService(repo)


CacheServiceDep = Annotated[CacheService, Depends(get_cache_service)]
