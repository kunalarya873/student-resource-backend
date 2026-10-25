import logging
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone

try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

from app.core.config import settings
from app.core.logging_config import ServerLifecycleTracker

logger = logging.getLogger("amu_backend.telemetry")
http_logger = logging.getLogger("amu_backend.http")

_logger_provider: LoggerProvider | None = None
_otel_logging_handler: LoggingHandler | None = None


# ---------------------------------------------------------------------------
# OpenTelemetry Provider Setup & Teardown
# ---------------------------------------------------------------------------

def setup_telemetry() -> LoggerProvider | None:
    """
    Configure and initialize OpenTelemetry logging provider with PostHog OTLP exporter,
    and bridge standard library logging to OTel.
    """
    global _logger_provider, _otel_logging_handler

    if not settings.OTEL_ENABLED:
        logger.info("OpenTelemetry logging is disabled (OTEL_ENABLED=false).")
        return None

    if _logger_provider is not None:
        return _logger_provider

    try:
        resource = Resource(attributes={"service.name": settings.OTEL_SERVICE_NAME})
        _logger_provider = LoggerProvider(resource=resource)
        set_logger_provider(_logger_provider)

        headers: dict[str, str] = {}
        if settings.POSTHOG_API_KEY:
            headers["Authorization"] = f"Bearer {settings.POSTHOG_API_KEY}"

        exporter = OTLPLogExporter(
            endpoint=settings.POSTHOG_LOGS_ENDPOINT,
            headers=headers,
        )

        _logger_provider.add_log_record_processor(BatchLogRecordProcessor(exporter))

        _otel_logging_handler = LoggingHandler(logger_provider=_logger_provider)
        root_logger = logging.getLogger()
        root_logger.addHandler(_otel_logging_handler)
        root_logger.setLevel(logging.INFO if not settings.DEBUG else logging.DEBUG)

        logger.info(
            f"OpenTelemetry logging initialized for service '{settings.OTEL_SERVICE_NAME}' "
            f"exporting to '{settings.POSTHOG_LOGS_ENDPOINT}'."
        )
        return _logger_provider
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Failed to initialize OpenTelemetry logging: {exc}")
        return None


def get_logger_provider() -> LoggerProvider | None:
    """Return the currently configured LoggerProvider instance if any."""
    return _logger_provider


def shutdown_telemetry() -> None:
    """Flush and shutdown the LoggerProvider and remove logging handler."""
    global _logger_provider, _otel_logging_handler

    if _otel_logging_handler is not None:
        try:
            logging.getLogger().removeHandler(_otel_logging_handler)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"Failed to remove OTel logging handler: {exc}")
        _otel_logging_handler = None

    if _logger_provider is not None:
        try:
            _logger_provider.shutdown()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Error during OpenTelemetry LoggerProvider shutdown: {exc}")
        finally:
            _logger_provider = None


# ---------------------------------------------------------------------------
# Inbuilt FastAPI Request/Response Telemetry Tracker
# ---------------------------------------------------------------------------

class TelemetryTracker:
    """
    Monitors and aggregates real-time telemetry metrics using FastAPI's inbuilt
    Request and Response lifecycle:
    - Active vs. Total request volumes
    - HTTP status distributions (2xx, 3xx, 4xx, 5xx)
    - Latency distribution (average, min, max, p50, p95, p99)
    - Top endpoint traffic
    - Slow request profiling
    - Error and failure tracking
    """

    def __init__(self, max_history: int = 1000) -> None:
        self._lock = threading.Lock()
        self.max_history = max_history
        self.total_requests: int = 0
        self.active_requests: int = 0
        self.status_codes: dict[str, int] = {}
        self.status_categories: dict[str, int] = {
            "2xx": 0,
            "3xx": 0,
            "4xx": 0,
            "5xx": 0,
            "other": 0,
        }
        self.method_counts: dict[str, int] = {}
        self.endpoint_counts: dict[str, int] = {}
        self.latencies_ms: deque[float] = deque(maxlen=max_history)
        self.slow_requests: list[dict[str, Any]] = []
        self.recent_errors: list[dict[str, Any]] = []

    def increment_active(self) -> None:
        with self._lock:
            self.active_requests += 1

    def decrement_active(self) -> None:
        with self._lock:
            if self.active_requests > 0:
                self.active_requests -= 1

    @property
    def total_incoming_requests(self) -> int:
        with self._lock:
            return self.total_requests + self.active_requests

    def record_request(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
        client_ip: str,
        request_id: str,
        user_agent: str = "",
        error: str | None = None,
    ) -> None:
        """Record telemetry data from FastAPI Request and Response."""
        with self._lock:
            self.total_requests += 1

            # Method stats
            self.method_counts[method] = self.method_counts.get(method, 0) + 1

            # Endpoint stats (coalesce query params / IDs where appropriate)
            self.endpoint_counts[path] = self.endpoint_counts.get(path, 0) + 1

            # Status code stats
            status_str = str(status_code)
            self.status_codes[status_str] = self.status_codes.get(status_str, 0) + 1

            # Status categories
            if 200 <= status_code < 300:
                self.status_categories["2xx"] += 1
            elif 300 <= status_code < 400:
                self.status_categories["3xx"] += 1
            elif 400 <= status_code < 500:
                self.status_categories["4xx"] += 1
            elif 500 <= status_code < 600:
                self.status_categories["5xx"] += 1
            else:
                self.status_categories["other"] += 1

            # Latency
            self.latencies_ms.append(duration_ms)

            now_iso = datetime.now(UTC).isoformat()

            # Track slow requests (top slowest or latency > 500ms)
            if duration_ms >= 500.0 or len(self.slow_requests) < 20:
                record = {
                    "timestamp": now_iso,
                    "method": method,
                    "path": path,
                    "status_code": status_code,
                    "duration_ms": round(duration_ms, 2),
                    "client_ip": client_ip,
                    "request_id": request_id,
                }
                self.slow_requests.append(record)
                # Keep top 20 sorted by duration descending
                self.slow_requests.sort(key=lambda r: r["duration_ms"], reverse=True)
                if len(self.slow_requests) > 20:
                    self.slow_requests.pop()

            # Track errors (4xx / 5xx / exceptions)
            if status_code >= 400 or error:
                error_record = {
                    "timestamp": now_iso,
                    "method": method,
                    "path": path,
                    "status_code": status_code,
                    "duration_ms": round(duration_ms, 2),
                    "client_ip": client_ip,
                    "request_id": request_id,
                    "error": error or f"HTTP {status_code}",
                }
                self.recent_errors.append(error_record)
                if len(self.recent_errors) > 50:
                    self.recent_errors.pop(0)

    def get_metrics_snapshot(self) -> dict[str, Any]:
        """Generate a complete telemetry metrics snapshot."""
        with self._lock:
            latencies = list(self.latencies_ms)
            count = len(latencies)

            if count > 0:
                sorted_lat = sorted(latencies)
                min_ms = round(sorted_lat[0], 2)
                max_ms = round(sorted_lat[-1], 2)
                avg_ms = round(sum(latencies) / count, 2)
                p50_ms = round(sorted_lat[int(count * 0.50)], 2)
                p95_ms = round(sorted_lat[min(int(count * 0.95), count - 1)], 2)
                p99_ms = round(sorted_lat[min(int(count * 0.99), count - 1)], 2)
            else:
                min_ms = max_ms = avg_ms = p50_ms = p95_ms = p99_ms = 0.0

            error_count = self.status_categories["4xx"] + self.status_categories["5xx"]
            error_rate = round((error_count / self.total_requests * 100), 2) if self.total_requests > 0 else 0.0

            boot_time = ServerLifecycleTracker.get_boot_time()
            uptime_seconds = ServerLifecycleTracker.get_uptime_seconds()
            uptime_human = ServerLifecycleTracker.get_uptime_human()

            # Top 10 endpoints by traffic
            top_endpoints = sorted(
                self.endpoint_counts.items(), key=lambda item: item[1], reverse=True
            )[:10]

            return {
                "server": {
                    "status": "healthy",
                    "boot_time": boot_time.isoformat() if boot_time else None,
                    "uptime_seconds": uptime_seconds,
                    "uptime_human": uptime_human,
                    "environment": settings.APP_ENV,
                },
                "traffic": {
                    "total_requests": self.total_requests,
                    "active_requests": self.active_requests,
                    "error_rate_pct": error_rate,
                    "categories": dict(self.status_categories),
                    "status_codes": dict(self.status_codes),
                    "methods": dict(self.method_counts),
                    "top_endpoints": dict(top_endpoints),
                },
                "latency_ms": {
                    "sample_size": count,
                    "avg": avg_ms,
                    "min": min_ms,
                    "max": max_ms,
                    "p50": p50_ms,
                    "p95": p95_ms,
                    "p99": p99_ms,
                },
                "slow_requests": list(self.slow_requests),
                "recent_errors": list(self.recent_errors[-10:]),
            }

    def reset(self) -> None:
        """Reset internal metrics counters (useful for unit tests)."""
        with self._lock:
            self.total_requests = 0
            self.active_requests = 0
            self.status_codes.clear()
            self.status_categories = {"2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0, "other": 0}
            self.method_counts.clear()
            self.endpoint_counts.clear()
            self.latencies_ms.clear()
            self.slow_requests.clear()
            self.recent_errors.clear()


# Global in-memory telemetry tracker
telemetry_tracker = TelemetryTracker()


# ---------------------------------------------------------------------------
# FastAPI Request/Response Telemetry Middleware
# ---------------------------------------------------------------------------

async def fastapi_telemetry_middleware(request: Request, call_next: Any) -> Response:
    """
    Inbuilt FastAPI middleware monitoring HTTP Request & Response:
    1. Extracts / Generates correlation 'X-Request-ID'.
    2. Times exact request execution duration.
    3. Injects 'X-Request-ID' and 'X-Process-Time' headers into the Response.
    4. Records telemetry metrics (status codes, latencies, errors).
    5. Logs structured telemetry.
    6. Traps unhandled route exceptions, logs fatal error with stack trace,
       and returns a traceable 500 JSONResponse.
    """
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id

    client_ip = request.client.host if request.client else "unknown"
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()

    start_perf = time.perf_counter()
    telemetry_tracker.increment_active()

    try:
        response: Response = await call_next(request)
        duration_ms = (time.perf_counter() - start_perf) * 1000

        # Inject diagnostic headers to response
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = f"{duration_ms:.2f}ms"

        # Record metrics
        telemetry_tracker.record_request(
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            client_ip=client_ip,
            request_id=request_id,
            user_agent=request.headers.get("user-agent", ""),
        )

        # In production, skip logging routine 200 OK health checks to keep logs clean and reduce cloud ingest cost
        is_routine_probe = request.url.path in ("/healthz", f"{settings.API_V1_PREFIX}/telemetry/lifecycle")
        if not (settings.is_production and is_routine_probe and response.status_code == 200):
            log_msg = (
                f"[HTTP TELEMETRY] {request.method} {request.url.path} "
                f"-> {response.status_code} | {duration_ms:.2f}ms "
                f"| Client: {client_ip} | Req-ID: {request_id}"
            )
            if response.status_code >= 500:
                http_logger.error(log_msg)
            elif response.status_code >= 400:
                http_logger.warning(log_msg)
            else:
                http_logger.info(log_msg)

        return response

    except Exception as exc:
        duration_ms = (time.perf_counter() - start_perf) * 1000

        # Log server failure
        http_logger.critical(
            f"🚨 [SERVER ERROR IN REQUEST] Unhandled exception during {request.method} {request.url.path} "
            f"after {duration_ms:.2f}ms | Client: {client_ip} | Req-ID: {request_id}: {exc}",
            exc_info=True,
        )

        # Record in telemetry metrics
        telemetry_tracker.record_request(
            method=request.method,
            path=request.url.path,
            status_code=500,
            duration_ms=duration_ms,
            client_ip=client_ip,
            request_id=request_id,
            user_agent=request.headers.get("user-agent", ""),
            error=str(exc),
        )

        # Return standardized error response with trace headers preserved
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Internal Server Error",
                "request_id": request_id,
            },
            headers={
                "X-Request-ID": request_id,
                "X-Process-Time": f"{duration_ms:.2f}ms",
            },
        )
    finally:
        telemetry_tracker.decrement_active()
