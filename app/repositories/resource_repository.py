import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.resource import Resource, ResourceTag, ResourceVersion, Tag
from app.repositories.base import BaseRepository


class ResourceRepository(BaseRepository[Resource]):
    """Data access repository for Resource and ResourceVersion entities."""

    def __init__(self) -> None:
        super().__init__(Resource)

    async def get_by_id_with_details(
        self,
        session: AsyncSession,
        resource_id: uuid.UUID,
    ) -> Resource | None:
        """Fetch resource by primary key eager-loading versions and owner metadata."""
        stmt = (
            select(Resource)
            .where(Resource.resource_id == resource_id)
            .options(
                selectinload(Resource.versions).selectinload(ResourceVersion.storage_object),
                selectinload(Resource.owner),
            )
        )
        return await session.scalar(stmt)

    async def get_latest_version(
        self,
        session: AsyncSession,
        resource_id: uuid.UUID,
    ) -> ResourceVersion | None:
        """Fetch the most recent version of a resource."""
        stmt = (
            select(ResourceVersion)
            .where(ResourceVersion.resource_id == resource_id)
            .order_by(ResourceVersion.version_number.desc())
            .options(selectinload(ResourceVersion.storage_object))
            .limit(1)
        )
        return await session.scalar(stmt)

    async def get_version_by_number(
        self,
        session: AsyncSession,
        resource_id: uuid.UUID,
        version_number: int,
    ) -> ResourceVersion | None:
        """Fetch a specific version number of a resource."""
        stmt = (
            select(ResourceVersion)
            .where(
                ResourceVersion.resource_id == resource_id,
                ResourceVersion.version_number == version_number,
            )
            .options(selectinload(ResourceVersion.storage_object))
        )
        return await session.scalar(stmt)

    async def list_by_folder(
        self,
        session: AsyncSession,
        folder_id: uuid.UUID | None,
        *,
        skip: int = 0,
        limit: int = 50,
    ) -> Sequence[Resource]:
        """List resources contained within a specific folder."""
        stmt = select(Resource)
        if folder_id is None:
            stmt = stmt.where(Resource.folder_id.is_(None))
        else:
            stmt = stmt.where(Resource.folder_id == folder_id)

        stmt = stmt.offset(skip).limit(limit)
        result = await session.scalars(stmt)
        return result.all()

    async def list_by_owner(
        self,
        session: AsyncSession,
        owner_id: uuid.UUID,
        *,
        skip: int = 0,
        limit: int = 50,
    ) -> Sequence[Resource]:
        """List resources owned by a given user."""
        stmt = select(Resource).where(Resource.owner_id == owner_id).offset(skip).limit(limit)
        result = await session.scalars(stmt)
        return result.all()

    async def get_tags_for_resource(
        self,
        session: AsyncSession,
        resource_id: uuid.UUID,
    ) -> Sequence[Tag]:
        """Fetch all tags associated with a resource."""
        stmt = (
            select(Tag)
            .join(ResourceTag, ResourceTag.tag_id == Tag.tag_id)
            .where(ResourceTag.resource_id == resource_id)
        )
        result = await session.scalars(stmt)
        return result.all()


resource_repository = ResourceRepository()
