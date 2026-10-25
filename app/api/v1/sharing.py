import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_user
from app.models.user import User
from app.schemas.common import StandardMessageResponse
from app.schemas.resource import ResourceRead
from app.schemas.sharing import PaginatedShareGrantResponse, ShareGrantCreate, ShareGrantRead
from app.services.resource_service import ResourceService
from app.services.sharing_service import SharingService

router = APIRouter(tags=["Sharing & Access Grants"])


@router.get(
    "/resources/shared-with-me",
    response_model=list[ResourceRead],
    status_code=status.HTTP_200_OK,
    summary="List all resources that have been explicitly shared with current user",
)
async def list_resources_shared_with_me(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ResourceRead]:
    """Retrieves all academic resources explicitly shared with the authenticated user."""
    resources = await SharingService.list_shared_with_me(
        session=session,
        user=current_user,
        limit=limit,
        offset=offset,
    )
    return [ResourceService.serialize_resource(r) for r in resources]


@router.get(
    "/resources/shared-by-me",
    response_model=list[ResourceRead],
    status_code=status.HTTP_200_OK,
    summary="List all resources the current user has shared with other users",
)
async def list_resources_shared_by_me(
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ResourceRead]:
    """Retrieves resources owned by the user that have at least one active share grant."""
    resources = await SharingService.list_shared_by_me(
        session=session,
        user=current_user,
        limit=limit,
        offset=offset,
    )
    return [ResourceService.serialize_resource(r) for r in resources]


@router.post(
    "/resources/{resource_id}/share",
    response_model=ShareGrantRead,
    status_code=status.HTTP_201_CREATED,
    summary="Grant VIEW or EDIT access to a user by email",
)
async def share_resource(
    resource_id: uuid.UUID,
    payload: ShareGrantCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    background_tasks: BackgroundTasks,
) -> ShareGrantRead:
    """
    Grants access to a target student or teacher:
    - Enforces EC-07.1: Only resource owner or administrator can share.
    - Enforces EC-07.2: Cannot share a resource with yourself.
    - Enforces EC-07.4: Grantee must be an active registered user.
    - Asynchronously dispatches a notification email to the recipient.
    """
    grant = await SharingService.grant_access(
        session=session,
        actor=current_user,
        resource_id=resource_id,
        grantee_email=payload.grantee_email,
        permission=payload.permission,
        background_tasks=background_tasks,
    )
    # Refresh to load user relation
    return ShareGrantRead(
        access_id=grant.access_id,
        resource_id=grant.resource_id,
        user_id=grant.user_id,
        grantee_email=grant.user.email if grant.user else payload.grantee_email,
        grantee_name=grant.user.full_name if grant.user else "",
        permission=grant.permission,
        granted_by=grant.granted_by,
        created_at=grant.created_at,
    )


@router.get(
    "/resources/{resource_id}/share",
    response_model=PaginatedShareGrantResponse,
    status_code=status.HTTP_200_OK,
    summary="List all active share grants on a resource",
)
async def list_resource_share_grants(
    resource_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    limit: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 50,
) -> PaginatedShareGrantResponse:
    """Lists users who have been granted access to this resource (Owner/Admin only) with pagination."""
    return await SharingService.list_grants(
        session=session,
        actor=current_user,
        resource_id=resource_id,
        page=page,
        limit=limit,
    )


@router.delete(
    "/resources/{resource_id}/share/{access_id}",
    response_model=StandardMessageResponse,
    status_code=status.HTTP_200_OK,
    summary="Revoke an existing share grant",
)
async def revoke_resource_share_grant(
    resource_id: uuid.UUID,
    access_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> StandardMessageResponse:
    """
    Revokes access immediately (EC-07.3). Subsequent download or read attempts
    by the revoked user will immediately be rejected with HTTP 403 Forbidden.
    """
    await SharingService.revoke_access(
        session=session,
        actor=current_user,
        resource_id=resource_id,
        access_id=access_id,
    )
    return StandardMessageResponse(message="Share grant revoked successfully.")
