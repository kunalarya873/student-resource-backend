import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.audit import AuditEvent
from app.repositories.base import BaseRepository


class AuditRepository(BaseRepository[AuditEvent]):
    """Data access repository for immutable AuditEvent records."""

    def __init__(self) -> None:
        super().__init__(AuditEvent)

    async def create_event(
        self,
        session: AsyncSession,
        actor_id: uuid.UUID | None,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID | None,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """Create and stage an immutable audit log entry."""
        event = AuditEvent(
            actor_id=actor_id,
            action=action.upper(),
            entity_type=entity_type.upper(),
            entity_id=entity_id,
            details=details or {},
        )
        session.add(event)
        await session.flush()
        return event

    async def list_recent_events(
        self,
        session: AsyncSession,
        *,
        skip: int = 0,
        limit: int = 50,
        action: str | None = None,
        entity_type: str | None = None,
        actor_id: uuid.UUID | None = None,
    ) -> Sequence[AuditEvent]:
        """Query audit log entries with optional filtering and actor preloading."""
        stmt = (
            select(AuditEvent)
            .options(selectinload(AuditEvent.actor))
            .order_by(AuditEvent.created_at.desc())
        )
        if action:
            stmt = stmt.where(AuditEvent.action == action.upper())
        if entity_type:
            stmt = stmt.where(AuditEvent.entity_type == entity_type.upper())
        if actor_id:
            stmt = stmt.where(AuditEvent.actor_id == actor_id)

        stmt = stmt.offset(skip).limit(limit)
        result = await session.scalars(stmt)
        return result.all()


audit_repository = AuditRepository()
