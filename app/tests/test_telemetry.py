import logging
import sys

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.logging_config import ServerLifecycleTracker, setup_server_logging
from app.core.telemetry import (
    fastapi_telemetry_middleware,
    get_logger_provider,
    setup_telemetry,
    shutdown_telemetry,
    telemetry_tracker,
)
from app.main import app


def test_telemetry_lifecycle():
    # Setup
    provider = setup_telemetry()
    assert provider is not None
    assert get_logger_provider() is not None

    # Verify standard library logging bridge works without exceptions
    test_logger = logging.getLogger("test_telemetry")
    test_logger.info("Test log message for OpenTelemetry bridge")

    # Shutdown
    shutdown_telemetry()
    assert get_logger_provider() is None


def test_server_lifecycle_and_crash_logging(caplog):
    """Verify server lifecycle startup, shutdown, and fatal crash logging."""
    setup_server_logging()

    # Verify boot time and uptime
    ServerLifecycleTracker.record_startup(app_name="TestApp", env="testing")
    assert ServerLifecycleTracker.get_boot_time() is not None
    uptime = ServerLifecycleTracker.get_uptime_seconds()
    assert uptime >= 0.0
    uptime_human = ServerLifecycleTracker.get_uptime_human()
    assert isinstance(uptime_human, str)

    # Test crash logging for unhandled exception causing server down
    with caplog.at_level(logging.CRITICAL):
        try:
            raise RuntimeError("Simulated fatal database connection crash")
        except RuntimeError:
            exc_type, exc_val, exc_tb = sys.exc_info()
            ServerLifecycleTracker.record_crash(
                exc_type=exc_type,
                exc_val=exc_val,
                exc_tb=exc_tb,
                origin="test_crash_simulation",
            )

    assert "SERVER FATAL CRASH DETECTED" in caplog.text
    assert "Simulated fatal database connection crash" in caplog.text

    # Test shutdown logging
    ServerLifecycleTracker.record_shutdown(reason="Test Graceful Termination", exit_code=0)


def test_telemetry_tracker_metrics():
    """Verify TelemetryTracker accurately computes traffic, latency, and status distributions."""
    telemetry_tracker.reset()

    assert telemetry_tracker.total_requests == 0
    assert telemetry_tracker.active_requests == 0

    # Record normal 200 OK requests
    telemetry_tracker.record_request(
        method="GET",
        path="/api/v1/resources",
        status_code=200,
        duration_ms=10.5,
        client_ip="127.0.0.1",
        request_id="req-1",
    )
    telemetry_tracker.record_request(
        method="POST",
        path="/api/v1/uploads/reserve",
        status_code=201,
        duration_ms=25.0,
        client_ip="127.0.0.1",
        request_id="req-2",
    )
    # Record 404 Not Found
    telemetry_tracker.record_request(
        method="GET",
        path="/api/v1/non-existent",
        status_code=404,
        duration_ms=3.2,
        client_ip="10.0.0.1",
        request_id="req-3",
    )
    # Record 500 Internal Error
    telemetry_tracker.record_request(
        method="GET",
        path="/api/v1/crash-endpoint",
        status_code=500,
        duration_ms=150.0,
        client_ip="10.0.0.1",
        request_id="req-4",
        error="Unexpected DB Timeout",
    )

    metrics = telemetry_tracker.get_metrics_snapshot()

    assert metrics["traffic"]["total_requests"] == 4
    assert metrics["traffic"]["categories"]["2xx"] == 2
    assert metrics["traffic"]["categories"]["4xx"] == 1
    assert metrics["traffic"]["categories"]["5xx"] == 1
    assert metrics["traffic"]["methods"]["GET"] == 3
    assert metrics["traffic"]["methods"]["POST"] == 1

    # Check latency
    latency = metrics["latency_ms"]
    assert latency["sample_size"] == 4
    assert latency["min"] == 3.2
    assert latency["max"] == 150.0
    assert latency["avg"] > 0

    # Check recent errors
    assert len(metrics["recent_errors"]) == 2
    assert metrics["recent_errors"][-1]["status_code"] == 500


@pytest.mark.asyncio
async def test_fastapi_request_response_telemetry_headers():
    """Verify FastAPI inbuilt request/response monitoring adds X-Request-ID & X-Process-Time."""
    telemetry_tracker.reset()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Request with client-supplied X-Request-ID
        custom_id = "test-client-req-999"
        res = await client.get("/healthz", headers={"X-Request-ID": custom_id})

        assert res.status_code == 200
        assert res.headers.get("X-Request-ID") == custom_id
        assert "X-Process-Time" in res.headers
        assert res.headers["X-Process-Time"].endswith("ms")

        # Telemetry in /healthz
        data = res.json()
        assert "telemetry" in data
        assert data["telemetry"]["total_requests"] >= 1

        # Request with auto-generated X-Request-ID
        res2 = await client.get("/healthz")
        assert res2.status_code == 200
        generated_id = res2.headers.get("X-Request-ID")
        assert generated_id is not None
        assert len(generated_id) > 10


@pytest.mark.asyncio
async def test_telemetry_api_endpoints():
    """Verify /api/v1/telemetry/stats and /api/v1/telemetry/lifecycle endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # GET /api/v1/telemetry/stats
        stats_res = await client.get("/api/v1/telemetry/stats")
        assert stats_res.status_code == 200
        stats = stats_res.json()
        assert "server" in stats
        assert "traffic" in stats
        assert "latency_ms" in stats
        assert stats["server"]["status"] == "healthy"

        # GET /api/v1/telemetry/lifecycle
        lifecycle_res = await client.get("/api/v1/telemetry/lifecycle")
        assert lifecycle_res.status_code == 200
        lifecycle = lifecycle_res.json()
        assert lifecycle["status"] == "online"
        assert "uptime_seconds" in lifecycle
        assert "uptime_human" in lifecycle
        assert "total_requests_processed" in lifecycle


@pytest.mark.asyncio
async def test_telemetry_middleware_unhandled_route_exception(caplog):
    """Verify telemetry middleware catches unhandled exceptions, records 500 telemetry, and returns 500 response."""
    test_app = FastAPI()

    @test_app.middleware("http")
    async def middleware_wrapper(request, call_next):
        return await fastapi_telemetry_middleware(request, call_next)

    @test_app.get("/trigger-crash")
    async def crash_endpoint():
        raise ZeroDivisionError("Simulated unhandled route division by zero")

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with caplog.at_level(logging.CRITICAL):
            res = await client.get("/trigger-crash")

        assert res.status_code == 500
        assert "X-Request-ID" in res.headers
        assert "X-Process-Time" in res.headers
        data = res.json()
        assert data["detail"] == "Internal Server Error"
        assert data["request_id"] == res.headers["X-Request-ID"]

        # Verify critical log was emitted
        assert "SERVER ERROR IN REQUEST" in caplog.text
