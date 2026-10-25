from typing import Any

from fastapi import APIRouter, status

from app.core.logging_config import ServerLifecycleTracker
from app.core.telemetry import telemetry_tracker

router = APIRouter(prefix="/telemetry", tags=["Telemetry"])


@router.get(
    "/stats",
    status_code=status.HTTP_200_OK,
    summary="Get Real-Time Server Telemetry",
    description="Returns real-time server telemetry collected from FastAPI Request and Response lifecycles.",
)
async def get_telemetry_stats() -> dict[str, Any]:
    """Retrieve comprehensive telemetry metrics snapshot."""
    return telemetry_tracker.get_metrics_snapshot()


@router.get(
    "/lifecycle",
    status_code=status.HTTP_200_OK,
    summary="Get Server Lifecycle Status",
    description="Returns server boot timestamp, current uptime, and health status.",
)
async def get_server_lifecycle() -> dict[str, Any]:
    """Retrieve server lifecycle and uptime metrics."""
    boot_time = ServerLifecycleTracker.get_boot_time()
    return {
        "status": "online",
        "boot_time": boot_time.isoformat() if boot_time else None,
        "uptime_seconds": ServerLifecycleTracker.get_uptime_seconds(),
        "uptime_human": ServerLifecycleTracker.get_uptime_human(),
        "total_requests_processed": telemetry_tracker.total_requests,
        "current_active_requests": telemetry_tracker.active_requests,
    }
