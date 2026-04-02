"""Common dependency injection factories.

Provides DynamoDB table dependency used by per-domain repositories.
"""

from functools import lru_cache

import boto3

from src.config import get_settings


@lru_cache
def _get_dynamodb_resource():
    """Get cached boto3 DynamoDB resource singleton."""
    settings = get_settings()
    kwargs = {"region_name": settings.aws_region}
    if settings.dynamodb_endpoint_url:
        kwargs["endpoint_url"] = settings.dynamodb_endpoint_url
    return boto3.resource("dynamodb", **kwargs)


def get_dynamodb_table():
    """Get the main DynamoDB table resource."""
    settings = get_settings()
    return _get_dynamodb_resource().Table(settings.dynamodb_table)
