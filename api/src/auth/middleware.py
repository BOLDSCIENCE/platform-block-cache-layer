"""Auth middleware for the Cache Layer API.

Uses boldsci-auth SDK Lambda Authorizer for authentication.
"""

from boldsci.auth import AuthContext, require_scope
from boldsci.auth import get_auth_context as sdk_get_auth_context
from fastapi import HTTPException, Request

from src.auth.context import set_auth_context


async def auth_middleware(request: Request) -> AuthContext:
    """Authenticate a request using the Lambda Authorizer context.

    Extracts auth context from the AWS API Gateway event injected by
    the Lambda Authorizer via Mangum.
    """
    event = request.scope.get("aws.event", {})
    authorizer = event.get("requestContext", {}).get("authorizer", {})
    if "lambda" in authorizer and "client_id" not in authorizer:
        event["requestContext"]["authorizer"] = authorizer["lambda"]
    try:
        context = sdk_get_auth_context(event)
    except ValueError:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": "No auth context found"},
        )

    request.state.auth = context
    set_auth_context(context)
    return context


def require_read(auth: AuthContext) -> AuthContext:
    """Require cache:read scope."""
    require_scope(auth, "cache:read")
    return auth


def require_write(auth: AuthContext) -> AuthContext:
    """Require cache:write scope."""
    require_scope(auth, "cache:write")
    return auth
