"""Request processing middleware.

RequestIdMiddleware: Generates UUID, stores on request.state, returns X-Request-Id header,
    binds to structlog contextvars.
ResponseEnvelopeMiddleware: Wraps 2xx JSON responses (not 204) in {"data": ..., "meta": {...}}.
"""

import json
import uuid
from collections.abc import Callable

import structlog
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.common.base_models import build_meta
from src.common.tracing import get_current_trace_id


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Generate UUID for each request and bind to structlog context."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            service="cache-layer-api",
        )

        trace_id = get_current_trace_id()
        if trace_id:
            structlog.contextvars.bind_contextvars(trace_id=trace_id)

        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response


class ResponseEnvelopeMiddleware(BaseHTTPMiddleware):
    """Wrap successful JSON responses in standard envelope."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)

        content_type = response.headers.get("content-type", "")
        if (
            200 <= response.status_code < 400
            and response.status_code != 204
            and content_type.startswith("application/json")
        ):
            body = b""
            async for chunk in response.body_iterator:
                body += chunk

            try:
                data = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                return response

            wrapped = {
                "data": data,
                "meta": build_meta(request),
            }

            preserved_headers = dict(response.headers)
            preserved_headers.pop("content-length", None)

            return JSONResponse(
                content=wrapped,
                status_code=response.status_code,
                headers=preserved_headers,
            )

        return response
