import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_user
from app.models.user import User
from app.schemas.common import StandardMessageResponse
from app.schemas.public_share import PublicLinkResponse, PublicLinkToggleRequest
from app.schemas.resource import (
    ResourceCreate,
    ResourceDownloadResponse,
    ResourceRead,
    ResourceUpdate,
    ResourceVersionCreate,
    ResourceVersionRead,
)
from app.services.public_share_service import PublicShareService
from app.services.resource_service import ResourceService

router = APIRouter(prefix="/resources", tags=["Resources"])


@router.post(
    "/upload",
    response_model=ResourceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Direct multipart file upload into user-segregated folder with privacy defaults",
)
async def upload_resource_file(
    file: Annotated[UploadFile, File(description="File asset to upload")],
    title: Annotated[str, Form(description="Resource title")],
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    description: Annotated[str | None, Form(description="Resource description")] = None,
    folder_id: Annotated[uuid.UUID | None, Form(description="Target folder ID")] = None,
    subject_id: Annotated[int | None, Form(description="Associated subject ID")] = None,
    academic_year_id: Annotated[int | None, Form(description="Associated academic year ID")] = None,
) -> ResourceRead:
    """
    Directly uploads file to Supabase storage in user's isolated folder (users/{user_id}/{folder}/...),
    enforces storage quota, sets privacy to PRIVATE by default, and registers the resource.
    """
    file_bytes = await file.read()
    filename = file.filename or "upload.bin"
    content_type = file.content_type or "application/octet-stream"

    res = await ResourceService.upload_resource_direct(
        session=session,
        user=current_user,
        file_data=file_bytes,
        filename=filename,
        content_type=content_type,
        title=title,
        description=description,
        folder_id=folder_id,
        subject_id=subject_id,
        academic_year_id=academic_year_id,
    )
    return ResourceService.serialize_resource(res)


@router.post(
    "",
    response_model=ResourceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new resource (file-backed or link-backed)",
)
async def create_resource(
    payload: ResourceCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ResourceRead:
    """
    Creates a new academic resource:
    - If student: status=PRIVATE, visibility=PRIVATE (EC-06.1)
    - If teacher/admin: status=APPROVED, visibility=DEPARTMENT
    - If link: validates with SSRF guard against internal/private IPs (EC-06.2)
    - If file: attaches completed upload reservation and links storage object.
    """
    res = await ResourceService.create_resource(session, current_user, payload)
    return ResourceService.serialize_resource(res)


@router.get(
    "",
    response_model=list[ResourceRead],
    status_code=status.HTTP_200_OK,
    summary="List accessible resources with pagination, search, sorting, and filters",
)
async def list_resources(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    folder_id: Annotated[uuid.UUID | None, Query(description="Filter by folder ID")] = None,
    subject_id: Annotated[int | None, Query(description="Filter by subject ID")] = None,
    academic_year_id: Annotated[int | None, Query(description="Filter by academic year ID")] = None,
    status: Annotated[
        str | None, Query(description="Filter by status: DRAFT, APPROVED, PRIVATE, ARCHIVED")
    ] = None,
    visibility: Annotated[
        str | None, Query(description="Filter by visibility: PRIVATE, DEPARTMENT, PUBLIC")
    ] = None,
    q: Annotated[str | None, Query(description="Search term in title or description")] = None,
    page: Annotated[int | None, Query(ge=1, description="Page number")] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 50,
    offset: Annotated[int, Query(ge=0, description="Offset index")] = 0,
    sort_by: Annotated[
        str, Query(description="Sort by: created_at, title, status, visibility")
    ] = "created_at",
    order: Annotated[str, Query(description="Sort order: asc, desc")] = "desc",
) -> list[ResourceRead]:
    """Retrieves resources visible to current user (own resources, approved departmental, or public)."""
    resources = await ResourceService.list_resources(
        session=session,
        user=current_user,
        folder_id=folder_id,
        subject_id=subject_id,
        academic_year_id=academic_year_id,
        status=status,
        visibility=visibility,
        q=q,
        limit=limit,
        offset=offset,
        page=page,
        sort_by=sort_by,
        order=order,
    )
    return [ResourceService.serialize_resource(r) for r in resources]


@router.get(
    "/folder/{folder_id}",
    response_model=list[ResourceRead],
    status_code=status.HTTP_200_OK,
    summary="List accessible resources within a specific folder",
)
async def list_resources_in_folder(
    folder_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    page: Annotated[int | None, Query(ge=1, description="Page number")] = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 50,
    offset: Annotated[int, Query(ge=0, description="Offset index")] = 0,
    sort_by: Annotated[
        str, Query(description="Sort by: created_at, title, status, visibility")
    ] = "created_at",
    order: Annotated[str, Query(description="Sort order: asc, desc")] = "desc",
) -> list[ResourceRead]:
    """Convenience endpoint to query resources belonging to a folder ID directly."""
    resources = await ResourceService.list_resources(
        session=session,
        user=current_user,
        folder_id=folder_id,
        limit=limit,
        offset=offset,
        page=page,
        sort_by=sort_by,
        order=order,
    )
    return [ResourceService.serialize_resource(r) for r in resources]


@router.get(
    "/{resource_id}",
    response_model=ResourceRead,
    status_code=status.HTTP_200_OK,
    summary="Get single resource details by ID",
)
async def get_resource(
    resource_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ResourceRead:
    """Retrieves resource with version history. Enforces privacy guard (EC-06.1, EC-06.4)."""
    res = await ResourceService.get_resource(session, current_user, resource_id)
    return ResourceService.serialize_resource(res)


@router.patch(
    "/{resource_id}",
    response_model=ResourceRead,
    status_code=status.HTTP_200_OK,
    summary="Update resource metadata or visibility",
)
async def update_resource(
    resource_id: uuid.UUID,
    payload: ResourceUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ResourceRead:
    """Updates title, description, folder, subject, or visibility (Owner/Admin only)."""
    res = await ResourceService.update_resource(session, current_user, resource_id, payload)
    return ResourceService.serialize_resource(res)


@router.delete(
    "/{resource_id}",
    response_model=StandardMessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a resource and release used storage quota",
)
async def delete_resource(
    resource_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StandardMessageResponse:
    """Deletes resource, releases used quota for stored files, and cascades versions."""
    await ResourceService.delete_resource(session, current_user, resource_id)
    return StandardMessageResponse(message="Resource deleted successfully.")


@router.post(
    "/{resource_id}/versions",
    response_model=ResourceVersionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a new version to an existing resource",
)
async def add_resource_version(
    resource_id: uuid.UUID,
    payload: ResourceVersionCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ResourceVersionRead:
    """
    Creates a new version (version 2, 3...) for an existing resource.
    Preserves all previous versions and file assets (EC-06.3).
    """
    new_v = await ResourceService.add_version(
        session=session,
        user=current_user,
        resource_id=resource_id,
        reservation_id=payload.reservation_id,
        external_url=payload.external_url,
    )
    return ResourceVersionRead.model_validate(new_v)


@router.get(
    "/{resource_id}/download",
    response_model=ResourceDownloadResponse,
    status_code=status.HTTP_200_OK,
    summary="Get download URL or link for a resource asset",
)
async def download_resource(
    resource_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    version_id: Annotated[uuid.UUID | None, Query()] = None,
    inline: Annotated[
        bool, Query(description="Return an inline (preview) URL instead of an attachment")
    ] = False,
) -> ResourceDownloadResponse:
    """
    Generates secure presigned download URL for file or returns safe external URL.
    Enforces authorization check (EC-06.4).
    """
    return await ResourceService.get_download_info(
        session=session,
        user=current_user,
        resource_id=resource_id,
        version_id=version_id,
        inline=inline,
    )


@router.post(
    "/{resource_id}/public-link",
    response_model=PublicLinkResponse,
    status_code=status.HTTP_200_OK,
    summary="Enable or disable public link sharing for a resource",
)
async def toggle_resource_public_link(
    resource_id: uuid.UUID,
    payload: PublicLinkToggleRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PublicLinkResponse:
    """Enables or disables public link access to this resource. Owner or Administrator only."""
    return await PublicShareService.toggle_resource_public_link(
        session=session,
        actor=current_user,
        resource_id=resource_id,
        is_public=payload.is_public,
    )


@router.get(
    "/{resource_id}/public-link",
    response_model=PublicLinkResponse,
    status_code=status.HTTP_200_OK,
    summary="Get current public link status and URL for a resource",
)
async def get_resource_public_link(
    resource_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> PublicLinkResponse:
    """Retrieves current public link status for this resource."""
    return await PublicShareService.get_resource_public_link(
        session=session,
        actor=current_user,
        resource_id=resource_id,
    )
