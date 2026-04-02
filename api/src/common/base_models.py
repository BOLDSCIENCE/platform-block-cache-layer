"""Base models for API request/response schemas.

Provides:
- ApiModel: Base with camelCase serialization
- build_meta: Response metadata builder
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    """Base model for all API request/response schemas.

    Produces camelCase JSON output. Accepts both camelCase and snake_case input.
    """

    model_config = ConfigDict(
        from_attributes=True,
        alias_generator=to_camel,
        populate_by_name=True,
    )


def build_meta(request: Any | None = None) -> dict:
    """Build the meta block for API responses."""
    request_id = str(uuid4())
    if request:
        request_id = getattr(getattr(request, "state", None), "request_id", request_id)
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "requestId": request_id,
    }
