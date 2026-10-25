from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_user
from app.models.user import Department, User
from app.repositories.user_repository import user_repository
from app.schemas.user import UserRead, UserUpdate

router = APIRouter(prefix="/users", tags=["Users"])


@router.get(
    "/me",
    response_model=UserRead,
    status_code=status.HTTP_200_OK,
    summary="Retrieve current user account details",
)
async def get_current_user_profile(
    current_user: Annotated[User, Depends(get_current_user)],
) -> UserRead:
    return UserRead(
        user_id=current_user.user_id,
        email=current_user.email,
        full_name=current_user.full_name,
        role_id=current_user.role_id,
        role_name=current_user.role.name,
        department_id=current_user.department_id,
        department_name=current_user.department.name if current_user.department else None,
        status=current_user.status,
        created_at=current_user.created_at,
    )


@router.patch(
    "/me",
    response_model=UserRead,
    status_code=status.HTTP_200_OK,
    summary="Update current user account details",
)
async def update_current_user_profile(
    payload: UserUpdate,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserRead:
    updates: dict[str, object] = {}

    if payload.full_name is not None and payload.full_name.strip():
        updates["full_name"] = payload.full_name.strip()

    # Resolve the department for the response; only touch it if the client sent it.
    department = current_user.department
    if "department_id" in payload.model_fields_set:
        if payload.department_id is not None:
            department = await session.get(Department, payload.department_id)
            if department is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid department",
                )
            updates["department_id"] = payload.department_id
        else:
            department = None
            updates["department_id"] = None

    if updates:
        await user_repository.update(session, current_user, updates)
        await session.commit()

    return UserRead(
        user_id=current_user.user_id,
        email=current_user.email,
        full_name=current_user.full_name,
        role_id=current_user.role_id,
        role_name=current_user.role.name,
        department_id=department.department_id if department else None,
        department_name=department.name if department else None,
        status=current_user.status,
        created_at=current_user.created_at,
    )
