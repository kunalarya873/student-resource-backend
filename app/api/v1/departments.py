from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.models.user import Department
from app.schemas.user import DepartmentRead

router = APIRouter(prefix="/departments", tags=["Departments"])


@router.get(
    "",
    response_model=list[DepartmentRead],
    status_code=status.HTTP_200_OK,
    summary="List all departments (for signup and profile selection)",
)
async def list_departments(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[DepartmentRead]:
    result = await session.scalars(select(Department).order_by(Department.name))
    return [DepartmentRead.model_validate(dept) for dept in result.all()]
