from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1.router import api_v1_router
from app.constants import API_DESCRIPTION, TAGS_METADATA
from app.core.config import settings
from app.core.database import engine
from app.core.logging_config import ServerLifecycleTracker, setup_server_logging
from app.core.telemetry import (
    fastapi_telemetry_middleware,
    setup_telemetry,
    shutdown_telemetry,
    telemetry_tracker,
)

# Initialize enterprise structured logging (console + persistent rotating files + crash hooks)
setup_server_logging()

# Initialize OpenTelemetry logging bridge early
setup_telemetry()

logger = logging.getLogger("amu_backend")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Verify DB connectivity on startup
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        logger.info("Database connection successfully established.")
    except (SQLAlchemyError, OSError) as exc:
        logger.warning(f"Database connection deferred or pending startup: {exc}")

    # Ensure MinIO/S3 object storage bucket exists
    try:
        from app.services.storage_service import storage_service

        await storage_service.ensure_bucket_exists()
        logger.info("S3 storage bucket verified.")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"S3 storage bucket check deferred: {exc}")

    yield

    # Clean up and log graceful server shutdown
    ServerLifecycleTracker.record_shutdown(reason="Lifespan graceful shutdown")
    logger.info("Disposing database engine connection pool...")
    await engine.dispose()
    shutdown_telemetry()


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description=API_DESCRIPTION,
    openapi_tags=TAGS_METADATA,
    contact={
        "name": "AMU Student Resource Management Engineering Team",
        "email": "support@amu.edu",
    },
    license_info={
        "name": "MIT / Academic License",
        "identifier": "MIT",
    },
    openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
    docs_url=f"{settings.API_V1_PREFIX}/docs",
    redoc_url=f"{settings.API_V1_PREFIX}/redoc",
    lifespan=lifespan,
)

# 1. CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. FastAPI Inbuilt Request/Response Telemetry Middleware
@app.middleware("http")
async def telemetry_middleware(request: Request, call_next):
    return await fastapi_telemetry_middleware(request, call_next)


@app.exception_handler(RequestValidationError)
async def custom_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Sanitizes validation errors to prevent UnicodeDecodeError when binary data is passed in request fields."""
    sanitized_errors = []
    for err in exc.errors():
        clean_err = dict(err)
        # Sanitize binary input payloads
        if "input" in clean_err and isinstance(clean_err["input"], (bytes, bytearray)):
            clean_err["input"] = f"<binary data: {len(clean_err['input'])} bytes>"
        if "ctx" in clean_err and isinstance(clean_err["ctx"], dict):
            clean_ctx = {}
            for k, v in clean_err["ctx"].items():
                if isinstance(v, (bytes, bytearray)):
                    clean_ctx[k] = f"<binary data: {len(v)} bytes>"
                else:
                    clean_ctx[k] = v
            clean_err["ctx"] = clean_ctx
        sanitized_errors.append(clean_err)

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": sanitized_errors},
    )


app.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)


@app.get("/healthz", status_code=status.HTTP_200_OK, tags=["Health"])
async def health_check():
    db_healthy = False
    try:
        async with engine.connect() as conn:
            res = await conn.execute(text("SELECT 1"))
            db_healthy = res.scalar() == 1
    except (SQLAlchemyError, OSError) as exc:
        logger.error(f"Health check database query failed: {exc}")

    return {
        "status": "ok",
        "database": "connected" if db_healthy else "disconnected",
        "environment": settings.APP_ENV,
        "telemetry": {
            "uptime_seconds": ServerLifecycleTracker.get_uptime_seconds(),
            "uptime_human": ServerLifecycleTracker.get_uptime_human(),
            "total_requests": telemetry_tracker.total_incoming_requests,
            "active_requests": telemetry_tracker.active_requests,
        },
    }
