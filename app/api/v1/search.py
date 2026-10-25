from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import get_current_user
from app.models.user import User
from app.schemas.search import SearchResponse
from app.services.search_service import SearchService

router = APIRouter(prefix="/search", tags=["Search & Discovery"])


@router.get(
    "",
    response_model=SearchResponse,
    status_code=status.HTTP_200_OK,
    summary="Full-text search & faceted resource discovery",
)
async def search_resources(
    session: Annotated[AsyncSession, Depends(get_db_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    q: Annotated[
        str | None,
        Query(
            description="Full-text search query across titles and descriptions",
        ),
    ] = None,
    subject_id: Annotated[int | None, Query(description="Filter by subject ID")] = None,
    department_id: Annotated[int | None, Query(description="Filter by department ID")] = None,
    academic_year_id: Annotated[int | None, Query(description="Filter by academic year ID")] = None,
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    limit: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 20,
) -> SearchResponse:
    """
    Search institutional resources using PostgreSQL Full-Text Search.
    Strictly enforces zero-leakage security predicate (Rule 02).
    """
    return await SearchService.search_resources(
        session=session,
        user=current_user,
        query=q,
        subject_id=subject_id,
        department_id=department_id,
        academic_year_id=academic_year_id,
        page=page,
        limit=limit,
    )
