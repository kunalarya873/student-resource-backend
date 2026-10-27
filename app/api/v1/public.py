import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_user
from app.models.user import User
from app.schemas.public_share import (
    PublicFolderContentsResponse,
    PublicJoinResponse,
    PublicResourceDownloadResponse,
    PublicResourceRead,
    PublicShareItemResponse,
)
from app.services.public_share_service import PublicShareService

router = APIRouter(prefix="/public", tags=["Public Sharing & Guest Access"])


@router.get(
    "/s/{share_token}",
    response_model=PublicShareItemResponse,
    status_code=status.HTTP_200_OK,
    summary="Resolve public link token to determine item kind (folder or resource)",
)
async def resolve_public_share(
    share_token: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PublicShareItemResponse:
    """Allows unauthenticated visitors to resolve what a public link points to."""
    return await PublicShareService.resolve_share_item(session, share_token)


@router.get(
    "/folders/{share_token}",
    response_model=PublicFolderContentsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get public folder metadata and contents for unauthenticated guests",
)
async def get_public_folder_contents(
    share_token: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    subfolder_id: Annotated[
        uuid.UUID | None,
        Query(description="ID of a nested subfolder within this public folder tree"),
    ] = None,
    q: Annotated[
        str | None,
        Query(description="Search term to filter child subfolders and files"),
    ] = None,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    limit: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 50,
    sort_by: Annotated[str, Query(description="Sort by: name, created_at, updated_at")] = "name",
    order: Annotated[str, Query(description="Sort order: asc, desc")] = "asc",
) -> PublicFolderContentsResponse:
    """
    Returns public folder contents including breadcrumbs, subfolders, and resources.
    Accessible without logging in.
    """
    return await PublicShareService.get_public_folder_contents(
        session=session,
        share_token=share_token,
        current_subfolder_id=subfolder_id,
        q=q,
        page=page,
        limit=limit,
        sort_by=sort_by,
        order=order,
    )


@router.get(
    "/resources/{share_token}",
    response_model=PublicResourceRead,
    status_code=status.HTTP_200_OK,
    summary="Get single public resource metadata for unauthenticated guests",
)
async def get_public_resource(
    share_token: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PublicResourceRead:
    """
    Returns public resource details (title, description, MIME type, size, owner).
    Accessible without logging in.
    """
    return await PublicShareService.get_public_resource(
        session=session,
        share_token=share_token,
    )


@router.get(
    "/resources/{share_token}/download",
    response_model=PublicResourceDownloadResponse,
    status_code=status.HTTP_200_OK,
    summary="Get secure download or preview URL for a public resource",
)
async def get_public_resource_download(
    share_token: str,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    inline: Annotated[
        bool,
        Query(description="Return inline preview URL (for PDF/images) instead of attachment"),
    ] = False,
) -> PublicResourceDownloadResponse:
    """
    Generates a secure, time-limited presigned download URL for a publicly shared file.
    Accessible without logging in.
    """
    return await PublicShareService.get_public_resource_download(
        session=session,
        share_token=share_token,
        inline=inline,
    )


@router.post(
    "/s/{share_token}/join",
    response_model=PublicJoinResponse,
    status_code=status.HTTP_200_OK,
    summary="Join and save a public folder or file to the authenticated user's drive",
)
async def join_public_share(
    share_token: str,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PublicJoinResponse:
    """
    Allows a registered/logged-in user viewing a public folder or file to save it
    permanently into their 'Shared with Me' list.
    """
    return await PublicShareService.join_public_share(
        session=session,
        current_user=current_user,
        share_token=share_token,
    )
