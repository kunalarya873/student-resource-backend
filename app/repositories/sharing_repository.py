import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.resource import ResourceAccess
from app.repositories.base import BaseRepository


class SharingRepository(BaseRepository[ResourceAccess]):
    """Data access repository for resource sharing and granular access control grants."""

    def __init__(self) -> None:
        super().__init__(ResourceAccess)

    async def get_active_grant(
        self,
        session: AsyncSession,
        resource_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> ResourceAccess | None:
        """Fetch the active access grant for a specific user on a resource."""
        stmt = (
            select(ResourceAccess)
            .where(
                ResourceAccess.resource_id == resource_id,
                ResourceAccess.user_id == user_id,
            )
            .options(selectinload(ResourceAccess.user))
        )
        return await session.scalar(stmt)

    async def list_grants_for_resource(
        self,
        session: AsyncSession,
        resource_id: uuid.UUID,
        *,
        page: int = 1,
        limit: int = 50,
    ) -> tuple[Sequence[ResourceAccess], int]:
        """List all active access grants for a given resource with pagination."""
        count_stmt = (
            select(func.count())
            .select_from(ResourceAccess)
            .where(ResourceAccess.resource_id == resource_id)
        )
        total = (await session.scalar(count_stmt)) or 0

        offset = max(0, (page - 1) * limit)
        stmt = (
            select(ResourceAccess)
            .where(ResourceAccess.resource_id == resource_id)
            .options(selectinload(ResourceAccess.user))
            .order_by(ResourceAccess.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await session.scalars(stmt)
        return result.all(), total

    async def list_grants_for_user(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        *,
        skip: int = 0,
        limit: int = 50,
    ) -> Sequence[ResourceAccess]:
        """List resources that have been shared with a specific user."""
        stmt = (
            select(ResourceAccess)
            .where(ResourceAccess.user_id == user_id)
            .options(selectinload(ResourceAccess.resource))
            .offset(skip)
            .limit(limit)
        )
        result = await session.scalars(stmt)
        return result.all()


sharing_repository = SharingRepository()
