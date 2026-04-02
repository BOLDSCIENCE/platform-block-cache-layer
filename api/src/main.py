"""FastAPI application entry point."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mangum import Mangum

from src.common.base_models import build_meta
from src.common.exceptions import EXCEPTION_STATUS_MAP, AppError, AuthorizationError
from src.common.middleware import RequestIdMiddleware, ResponseEnvelopeMiddleware
from src.common.tracing import instrument_app
from src.config import get_settings

settings = get_settings()

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
        if settings.environment == "development"
        else structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(
        structlog.get_level_from_name(settings.log_level.upper())
        if hasattr(structlog, "get_level_from_name")
        else 0
    ),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

from src.cache.router import router as cache_router  # noqa: E402
from src.health.router import router as health_router  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager."""
    logger.info("Starting Cache Layer API")
    yield
    logger.info("Shutting down Cache Layer API")


app = FastAPI(
    title="Bold Cache Layer API",
    description="Intelligent response caching for AI applications. "
    "Provides exact match and semantic similarity caching with "
    "lookup-or-exec cache-aside pattern, statistics, and cost savings tracking.",
    version="0.5.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Middleware — added in reverse order (last added = outermost = runs first)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-API-Key",
        "X-Forwarded-Client-Id",
    ],
    expose_headers=[
        "X-Request-Id",
        "X-Cache-Status",
    ],
)
app.add_middleware(ResponseEnvelopeMiddleware)
app.add_middleware(RequestIdMiddleware)


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Handle AppError hierarchy using EXCEPTION_STATUS_MAP."""
    status_code = EXCEPTION_STATUS_MAP.get(type(exc), 500)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
            "meta": build_meta(request),
        },
    )


@app.exception_handler(PermissionError)
async def permission_error_handler(request: Request, exc: PermissionError) -> JSONResponse:
    """Handle PermissionError raised by boldsci-auth require_scope."""
    wrapped = AuthorizationError(str(exc))
    status_code = EXCEPTION_STATUS_MAP.get(AuthorizationError, 403)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": wrapped.code,
                "message": wrapped.message,
                "details": {},
            },
            "meta": build_meta(request),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handle FastAPI request validation errors."""
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": exc.errors(),
            },
            "meta": build_meta(request),
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Global exception handler for unhandled exceptions."""
    logger.exception("Unhandled exception", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred",
                "details": {},
            },
            "meta": build_meta(request),
        },
    )


@app.get("/")
def get_root() -> dict[str, str]:
    """Root endpoint."""
    return {
        "service": settings.service_name,
        "version": "0.5.0",
    }


@app.get("/health")
def health_check():
    """Health check endpoint (root level, delegates to deep check)."""
    from src.health.router import _check_health

    return _check_health()


# Include routers
app.include_router(health_router, prefix="/v1")
app.include_router(cache_router, prefix="/v1")

# Instrument with OpenTelemetry (no-op if ADOT layer not present)
instrument_app(app)

handler = Mangum(app, lifespan="off")
