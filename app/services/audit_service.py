import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditEvent
from app.models.user import User
from app.repositories.audit_repository import audit_repository
from app.schemas.admin import AuditEventRead

logger = logging.getLogger("amu_backend")


def mask_email(email: str | None) -> str:
    """
    Masks email address for audit logging in compliance with Rule 02 and GDPR
    (e.g., k***a@domain.com or s***t@amu.edu).
    """
    if not email:
        return "***"
    parts = email.split("@")
    if len(parts) != 2:
        return "***"
    name, domain = parts
    if len(name) <= 2:
        masked_name = name[0] + "***"
    else:
        masked_name = name[0] + "***" + name[-1]
    return f"{masked_name}@{domain}"


class AuditService:
    @staticmethod
    async def log_event(
        session: AsyncSession,
        actor: User | None,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID | None,
        details: dict[str, Any] | None = None,
    ) -> AuditEvent:
        """
        Emits an immutable audit event to the database via audit_repository.
        Masks any sensitive PII in the details payload.
        """
        clean_details = dict(details or {})
        for key in ["email", "grantee_email", "user_email"]:
            if key in clean_details and isinstance(clean_details[key], str):
                clean_details[key] = mask_email(clean_details[key])

        return await audit_repository.create_event(
            session=session,
            actor_id=actor.user_id if actor else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=clean_details,
        )

    @staticmethod
    async def list_audit_logs(
        session: AsyncSession,
        action: str | None = None,
        entity_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditEventRead]:
        """
        Retrieves audit trail with masked actor emails in compliance with GDPR.
        """
        limit = max(1, min(limit, 200))
        offset = max(0, offset)

        events = await audit_repository.list_recent_events(
            session=session,
            skip=offset,
            limit=limit,
            action=action,
            entity_type=entity_type,
        )

        results: list[AuditEventRead] = []
        for e in events:
            actor_masked = mask_email(e.actor.email) if e.actor else None
            results.append(
                AuditEventRead(
                    event_id=e.event_id,
                    actor_id=e.actor_id,
                    actor_email=actor_masked,
                    action=e.action,
                    entity_type=e.entity_type,
                    entity_id=e.entity_id,
                    details=e.details,
                    created_at=e.created_at,
                )
            )
        return results
