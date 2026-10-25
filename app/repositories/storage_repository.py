import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.storage import StorageObject, StorageQuota, UploadReservation
from app.repositories.base import BaseRepository


class StorageRepository:
    """Data access repository for StorageQuota, UploadReservation, and StorageObject entities."""

    def __init__(self) -> None:
        self.quota_repo = BaseRepository(StorageQuota)
        self.reservation_repo = BaseRepository(UploadReservation)
        self.object_repo = BaseRepository(StorageObject)

    async def get_quota_by_user_id(
        self,
        session: AsyncSession,
        user_id: uuid.UUID,
        for_update: bool = False,
    ) -> StorageQuota | None:
        """Fetch the storage quota record for a user, optionally applying pessimistic row lock."""
        stmt = select(StorageQuota).where(StorageQuota.user_id == user_id)
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return await session.scalar(stmt)

    async def get_reservation_by_id(
        self,
        session: AsyncSession,
        reservation_id: uuid.UUID,
    ) -> UploadReservation | None:
        """Fetch upload reservation by ID."""
        return await self.reservation_repo.get_by_id(session, reservation_id)

    async def list_expired_reservations(
        self,
        session: AsyncSession,
        cutoff_time: datetime,
        limit: int = 100,
    ) -> Sequence[UploadReservation]:
        """Fetch pending upload reservations that have expired past cutoff_time."""
        stmt = (
            select(UploadReservation)
            .where(
                UploadReservation.status == "PENDING",
                UploadReservation.expires_at < cutoff_time,
            )
            .order_by(UploadReservation.expires_at.asc())
            .limit(limit)
        )
        result = await session.scalars(stmt)
        return result.all()

    async def get_storage_object_by_key(
        self,
        session: AsyncSession,
        object_key: str,
    ) -> StorageObject | None:
        """Fetch storage object by S3 object key."""
        stmt = select(StorageObject).where(StorageObject.object_key == object_key)
        return await session.scalar(stmt)

    async def get_storage_object_by_id(
        self,
        session: AsyncSession,
        object_id: uuid.UUID,
    ) -> StorageObject | None:
        """Fetch storage object by primary key."""
        return await self.object_repo.get_by_id(session, object_id)


storage_repository = StorageRepository()
