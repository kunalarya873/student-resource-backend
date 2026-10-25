import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
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
from app.services.folder_service import FolderService

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
