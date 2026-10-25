import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_user, require_role
from app.models.user import User
from app.repositories.storage_repository import storage_repository
from app.schemas.common import StandardMessageResponse
from app.schemas.storage import DownloadUrlResponse, StorageQuotaResponse
from app.services.quota_service import QuotaService
from app.services.storage_service import storage_service

router = APIRouter(prefix="/storage", tags=["Storage & Quota"])


@router.get(
    "/quota",
    response_model=StorageQuotaResponse,
    status_code=status.HTTP_200_OK,
    summary="Get current user's storage quota breakdown",
)
async def get_my_storage_quota(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StorageQuotaResponse:
    """Returns total, used, reserved, and available storage bytes with usage percentage."""
    return await QuotaService.get_quota_summary(session, current_user)


@router.get(
    "/objects/{storage_object_id}/download-url",
    response_model=DownloadUrlResponse,
    status_code=status.HTTP_200_OK,
    summary="Generate a presigned GET download URL for a stored object",
)
async def get_object_download_url(
    storage_object_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DownloadUrlResponse:
    """Returns a short-lived presigned URL with Content-Disposition headers for downloading."""
    storage_obj = await storage_repository.get_storage_object_by_id(session, storage_object_id)
    if not storage_obj:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Storage object not found.",
        )

    # Filename extraction from key
    filename = storage_obj.object_key.split("/")[-1] or "download"

    # If linked to a resource version, verify permission
    if storage_obj.version:
        res = storage_obj.version.resource
        if (
            res
            and res.owner_id != current_user.user_id
            and current_user.role.name != "Administrator"
            and res.visibility == "PRIVATE"
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this resource object.",
            )

    download_url = await storage_service.generate_presigned_download_url(
        object_key=storage_obj.object_key,
        filename=filename,
        expires_in=300,
    )
    return DownloadUrlResponse(
        download_url=download_url,
        object_key=storage_obj.object_key,
        filename=filename,
        expires_in_seconds=300,
    )


@router.post(
    "/cleanup-expired",
    response_model=StandardMessageResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_role(["Administrator"]))],
    summary="Admin sweeper: Clean up expired reservations and release reserved quota",
)
async def cleanup_expired_reservations(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StandardMessageResponse:
    """Sweeps abandoned upload reservations older than 30 minutes (EC-05.4)."""
    count = await QuotaService.cleanup_expired_reservations(session)
    return StandardMessageResponse(message=f"Cleaned up {count} expired upload reservations.")
