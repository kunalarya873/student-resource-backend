import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_user
from app.models.user import User
from app.schemas.common import StandardMessageResponse
from app.schemas.folder import (
    FolderContentsResponse,
    FolderCreate,
    FolderRead,
    FolderUpdate,
    PaginatedFolderResponse,
)
from app.schemas.public_share import PublicLinkResponse, PublicLinkToggleRequest
from app.schemas.sharing import (
    FolderShareGrantRead,
    PaginatedFolderShareGrantResponse,
    ShareGrantCreate,
)
from app.services.folder_service import FolderService
from app.services.public_share_service import PublicShareService
from app.services.sharing_service import SharingService

router = APIRouter(prefix="/folders", tags=["Folders"])


@router.post(
    "",
    response_model=FolderRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new folder or subfolder",
)
async def create_folder(
    payload: FolderCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> FolderRead:
    folder = await FolderService.create_folder(
        session=session,
        user=current_user,
        name=payload.name,
        parent_folder_id=payload.parent_folder_id,
    )
    return FolderRead.model_validate(folder)


@router.get(
    "",
    response_model=PaginatedFolderResponse,
    status_code=status.HTTP_200_OK,
    summary="List root folders or subfolders of a given parent with search, pagination, and sorting",
)
async def list_folders(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    parent_id: Annotated[uuid.UUID | None, Query()] = None,
    q: Annotated[str | None, Query(description="Search query to filter folders by name")] = None,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    limit: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 50,
    sort_by: Annotated[str, Query(description="Sort by: name, created_at, updated_at")] = "name",
    order: Annotated[str, Query(description="Sort order: asc, desc")] = "asc",
) -> PaginatedFolderResponse:
    folders, total, total_pages = await FolderService.list_folders(
        session=session,
        user=current_user,
        parent_id=parent_id,
        q=q,
        page=page,
        limit=limit,
        sort_by=sort_by,
        order=order,
    )
    return PaginatedFolderResponse(
        items=[FolderRead.model_validate(f) for f in folders],
        page=page,
        limit=limit,
        total=total,
        total_pages=total_pages,
    )


@router.get(
    "/shared-with-me",
    response_model=list[FolderRead],
    status_code=status.HTTP_200_OK,
    summary="List folders that have been shared with the current user",
)
async def list_folders_shared_with_me(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[FolderRead]:
    """Retrieves folders explicitly shared with the authenticated user (browsable subtrees)."""
    folders = await SharingService.list_folders_shared_with_me(
        session=session,
        user=current_user,
        limit=limit,
        offset=offset,
    )
    return [FolderRead.model_validate(f) for f in folders]


@router.get(
    "/{folder_id}",
    response_model=FolderRead,
    status_code=status.HTTP_200_OK,
    summary="Get single folder metadata by ID",
)
async def get_folder(
    folder_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> FolderRead:
    folder = await FolderService.get_folder(
        session=session,
        user=current_user,
        folder_id=folder_id,
    )
    return FolderRead.model_validate(folder)


@router.get(
    "/{folder_id}/contents",
    response_model=FolderContentsResponse,
    status_code=status.HTTP_200_OK,
    summary="Get folder contents including breadcrumbs, subfolders, and resources with pagination, search, and sorting",
)
async def get_folder_contents(
    folder_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    q: Annotated[
        str | None, Query(description="Filter subfolders and resources by search term")
    ] = None,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    limit: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 50,
    sort_by: Annotated[str, Query(description="Sort by: name, created_at, updated_at")] = "name",
    order: Annotated[str, Query(description="Sort order: asc, desc")] = "asc",
) -> FolderContentsResponse:
    return await FolderService.get_folder_contents(
        session=session,
        user=current_user,
        folder_id=folder_id,
        q=q,
        page=page,
        limit=limit,
        sort_by=sort_by,
        order=order,
    )


@router.patch(
    "/{folder_id}",
    response_model=FolderRead,
    status_code=status.HTTP_200_OK,
    summary="Rename or move a folder (with DAG cycle prevention)",
)
async def update_folder(
    folder_id: uuid.UUID,
    payload: FolderUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> FolderRead:
    folder = await FolderService.move_or_rename_folder(
        session=session,
        user=current_user,
        folder_id=folder_id,
        new_name=payload.name,
        new_parent_id=payload.parent_folder_id,
        reparent="parent_folder_id" in payload.model_fields_set,
    )
    return FolderRead.model_validate(folder)


@router.delete(
    "/{folder_id}",
    response_model=StandardMessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a folder (safely blocks non-empty folders unless recursive=true)",
)
async def delete_folder(
    folder_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    recursive: Annotated[
        bool,
        Query(
            description="If True, recursively deletes all nested subfolders, resources, and releases storage quota"
        ),
    ] = False,
) -> StandardMessageResponse:
    await FolderService.delete_folder(
        session=session,
        user=current_user,
        folder_id=folder_id,
        recursive=recursive,
    )
    return StandardMessageResponse(message="Folder deleted successfully")


@router.post(
    "/{folder_id}/share",
    response_model=FolderShareGrantRead,
    status_code=status.HTTP_201_CREATED,
    summary="Grant VIEW or EDIT access to a folder (and its entire subtree) by email",
)
async def share_folder(
    folder_id: uuid.UUID,
    payload: ShareGrantCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    background_tasks: BackgroundTasks,
) -> FolderShareGrantRead:
    """Shares a folder and everything inside it with a target user. Owner/Admin only."""
    grant = await SharingService.grant_folder_access(
        session=session,
        actor=current_user,
        folder_id=folder_id,
        grantee_email=payload.grantee_email,
        permission=payload.permission,
        background_tasks=background_tasks,
    )
    return FolderShareGrantRead(
        access_id=grant.access_id,
        folder_id=grant.folder_id,
        user_id=grant.user_id,
        grantee_email=grant.user.email if grant.user else payload.grantee_email,
        grantee_name=grant.user.full_name if grant.user else "",
        permission=grant.permission,
        granted_by=grant.granted_by,
        created_at=grant.created_at,
    )


@router.get(
    "/{folder_id}/share",
    response_model=PaginatedFolderShareGrantResponse,
    status_code=status.HTTP_200_OK,
    summary="List all active share grants on a folder",
)
async def list_folder_share_grants(
    folder_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    limit: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 50,
) -> PaginatedFolderShareGrantResponse:
    """Lists users granted access to this folder (Owner/Admin only) with pagination."""
    return await SharingService.list_folder_grants(
        session=session,
        actor=current_user,
        folder_id=folder_id,
        page=page,
        limit=limit,
    )


@router.delete(
    "/{folder_id}/share/{access_id}",
    response_model=StandardMessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke an existing folder share grant",
)
async def revoke_folder_share_grant(
    folder_id: uuid.UUID,
    access_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StandardMessageResponse:
    """Revokes folder access immediately. The grantee loses access to the subtree."""
    await SharingService.revoke_folder_access(
        session=session,
        actor=current_user,
        folder_id=folder_id,
        access_id=access_id,
    )
    return StandardMessageResponse(message="Folder share grant revoked successfully.")


@router.post(
    "/{folder_id}/public-link",
    response_model=PublicLinkResponse,
    status_code=status.HTTP_200_OK,
    summary="Enable or disable public link sharing for a folder",
)
async def toggle_folder_public_link(
    folder_id: uuid.UUID,
    payload: PublicLinkToggleRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PublicLinkResponse:
    """Enables or disables public link access to this folder. Owner or Administrator only."""
    return await PublicShareService.toggle_folder_public_link(
        session=session,
        actor=current_user,
        folder_id=folder_id,
        is_public=payload.is_public,
    )


@router.get(
    "/{folder_id}/public-link",
    response_model=PublicLinkResponse,
    status_code=status.HTTP_200_OK,
    summary="Get current public link status and URL for a folder",
)
async def get_folder_public_link(
    folder_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PublicLinkResponse:
    """Retrieves current public link status for this folder."""
    return await PublicShareService.get_folder_public_link(
        session=session,
        actor=current_user,
        folder_id=folder_id,
    )
