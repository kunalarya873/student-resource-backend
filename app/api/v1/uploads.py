import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_user
from app.models.user import User
from app.schemas.common import StandardMessageResponse
from app.schemas.storage import (
    UploadCompleteRequest,
    UploadCompleteResponse,
    UploadInitiateRequest,
    UploadInitiateResponse,
    UploadProgressUpdateRequest,
    UploadStatusResponse,
)
from app.services.quota_service import QuotaService

router = APIRouter(prefix="/uploads", tags=["Uploads"])


@router.post(
    "/initiate",
    response_model=UploadInitiateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Phase 1: Reserve storage quota and obtain presigned S3 upload URL",
)
async def initiate_upload(
    payload: UploadInitiateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UploadInitiateResponse:
    """
    Atomically locks user quota, checks available bytes, increments reserved_bytes,
    and returns a short-lived presigned PUT URL for direct client-to-storage upload.
    """
    reservation, upload_url = await QuotaService.reserve_quota(
        session=session,
        user=current_user,
        filename=payload.filename,
        content_type=payload.content_type,
        requested_bytes=payload.size_bytes,
    )
    return UploadInitiateResponse(
        reservation_id=reservation.reservation_id,
        upload_url=upload_url,
        object_key=reservation.object_key or "",
        expires_at=reservation.expires_at,
        reserved_bytes=reservation.reserved_bytes,
    )


@router.post(
    "/complete",
    response_model=UploadCompleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Phase 2: Finalize upload, verify in storage, and commit quota",
)
async def complete_upload(
    payload: UploadCompleteRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UploadCompleteResponse:
    """
    Inspects S3 object via HeadObject, validates byte length, deducts from reserved
    quota, adds to used quota, marks reservation COMPLETED, and registers StorageObject.
    """
    storage_obj, reservation = await QuotaService.commit_reservation(
        session=session,
        user=current_user,
        reservation_id=payload.reservation_id,
        checksum_sha256=payload.checksum_sha256,
    )
    return UploadCompleteResponse(
        storage_object_id=storage_obj.storage_object_id,
        reservation_id=reservation.reservation_id,
        object_key=storage_obj.object_key,
        byte_size=storage_obj.byte_size,
        checksum=storage_obj.checksum,
        verification_status=storage_obj.verification_status,
        message="Upload successfully verified and quota committed.",
    )


@router.post(
    "/{reservation_id}/cancel",
    response_model=StandardMessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel a pending upload reservation and release reserved bytes",
)
async def cancel_upload(
    reservation_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StandardMessageResponse:
    await QuotaService.cancel_reservation(
        session=session,
        user=current_user,
        reservation_id=reservation_id,
    )
    return StandardMessageResponse(message="Upload reservation cancelled and quota released.")


@router.get(
    "/{reservation_id}/status",
    response_model=UploadStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Check real-time upload progress, processing state, and resource ID",
)
async def get_upload_status(
    reservation_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UploadStatusResponse:
    """
    Returns the live progress percentage (0-100), current status (PENDING, UPLOADING,
    PROCESSING, COMPLETED, FAILED), transferred byte counts, and linked resource ID.
    """
    return await QuotaService.get_upload_status(
        session=session,
        reservation_id=reservation_id,
        user=current_user,
    )


@router.get(
    "/{reservation_id}/progress",
    response_model=UploadStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Get current upload progress, processing state, and byte counts",
)
async def get_upload_progress(
    reservation_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UploadStatusResponse:
    """Retrieve current in-flight or completed upload progress."""
    return await QuotaService.get_upload_status(
        session=session,
        reservation_id=reservation_id,
        user=current_user,
    )


@router.patch(
    "/{reservation_id}/progress",
    response_model=UploadStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Report in-flight byte transmission progress from the frontend",
)
async def update_upload_progress(
    reservation_id: uuid.UUID,
    payload: UploadProgressUpdateRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UploadStatusResponse:
    """
    Frontend clients uploading directly to storage can report intermediate transfer progress.
    """
    return await QuotaService.update_upload_progress(
        session=session,
        reservation_id=reservation_id,
        user=current_user,
        progress_percent=payload.progress_percent,
        bytes_uploaded=payload.bytes_uploaded,
    )


@router.get(
    "/{reservation_id}/events",
    summary="Server-Sent Events (SSE) stream for real-time upload progress updates",
)
async def stream_upload_progress(
    reservation_id: uuid.UUID,
    request: Request,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    max_events: int = 60,
) -> StreamingResponse:
    """
    Provides an SSE connection (text/event-stream) emitting real-time progress events
    until the upload reaches COMPLETED, FAILED, or CANCELLED status.
    """
    # Verify access first
    await QuotaService.get_upload_status(session, reservation_id, current_user)

    async def event_generator():
        stat = await QuotaService.get_upload_status(session, reservation_id, current_user)
        yield f"data: {stat.model_dump_json()}\n\n"
        if stat.status in ("COMPLETED", "FAILED", "CANCELLED", "EXPIRED") or max_events <= 1:
            return

        for _ in range(max_events - 1):
            if await request.is_disconnected():
                break
            await asyncio.sleep(0.5)
            stat = await QuotaService.get_upload_status(session, reservation_id, current_user)
            yield f"data: {stat.model_dump_json()}\n\n"
            if stat.status in ("COMPLETED", "FAILED", "CANCELLED", "EXPIRED"):
                break

    return StreamingResponse(event_generator(), media_type="text/event-stream")
