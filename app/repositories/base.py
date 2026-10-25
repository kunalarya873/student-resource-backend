import uuid
from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """
    Generic Abstract Repository Pattern implementation for SQLAlchemy 2.0.
    Provides standard async CRUD operations and query building.
    """

    def __init__(self, model: type[ModelType]) -> None:
        self.model = model

    async def get_by_id(
        self, session: AsyncSession, id_: uuid.UUID | str | int
    ) -> ModelType | None:
        """Fetch a single record by its primary key identifier."""
        return await session.get(self.model, id_)

    async def list_all(
        self,
        session: AsyncSession,
        *,
        skip: int = 0,
        limit: int = 100,
    ) -> Sequence[ModelType]:
        """Fetch a paginated list of records."""
        stmt = select(self.model).offset(skip).limit(limit)
        result = await session.scalars(stmt)
        return result.all()

    async def count(self, session: AsyncSession) -> int:
        """Return total row count for the model entity."""
        stmt = select(func.count()).select_from(self.model)
        result = await session.scalar(stmt)
        return result or 0

    async def create(self, session: AsyncSession, obj: ModelType) -> ModelType:
        """Stage a new entity in the session and flush."""
        session.add(obj)
        await session.flush()
        return obj

    async def update(
        self,
        session: AsyncSession,
        db_obj: ModelType,
        update_data: dict[str, Any],
    ) -> ModelType:
        """Update attribute values on a tracked entity and flush."""
        for field, value in update_data.items():
            if hasattr(db_obj, field):
                setattr(db_obj, field, value)
        session.add(db_obj)
        await session.flush()
        return db_obj

    async def delete(self, session: AsyncSession, id_: uuid.UUID | str | int) -> bool:
        """Remove a record by primary key."""
        obj = await self.get_by_id(session, id_)
        if obj is not None:
            await session.delete(obj)
            await session.flush()
            return True
        return False
