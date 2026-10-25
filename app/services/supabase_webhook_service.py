import hmac
import logging
import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.resource import Resource, ResourceVersion
from app.models.storage import StorageObject, StorageQuota, UploadReservation
from app.models.user import User
from app.schemas.storage import SupabaseWebhookResponse
from app.services.quota_service import QuotaService

logger = logging.getLogger("amu_supabase_webhook")


class SupabaseWebhookService:
    """Service to process Supabase Storage events, verify authenticity, and coordinate quotas."""

    @classmethod
    def verify_webhook_secret(cls, secret_header: str | None) -> bool:
        """
        Verify incoming webhook secret against configured SUPABASE_WEBHOOK_SECRET.
        Uses hmac.compare_digest to prevent timing attacks.
        """
        configured_secret = settings.SUPABASE_WEBHOOK_SECRET
        if not configured_secret:
            logger.warning("SUPABASE_WEBHOOK_SECRET is not configured; rejecting webhook request.")
            return False

        if not secret_header:
            return False

        # Support raw token or "Bearer <token>"
        token = secret_header.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()

        return hmac.compare_digest(token, configured_secret)

    @classmethod
    async def process_storage_event(
        cls,
        session: AsyncSession,
        payload: dict[str, Any],
        secret_header: str | None,
    ) -> SupabaseWebhookResponse:
        """
        Process incoming Supabase Storage Webhook payload.
        Idempotent, transactional, and maintains strict user privacy.
        """
        if not cls.verify_webhook_secret(secret_header):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Unauthorized: invalid or missing Supabase webhook secret.",
            )

        # 1. Parse payload structure (handles database triggers or storage event payloads)
        event_type = payload.get("type") or payload.get("event") or "INSERT"
        record = payload.get("record") if isinstance(payload.get("record"), dict) else payload

        object_key = (
            record.get("name")
            or record.get("key")
            or record.get("path")
            or payload.get("key")
            or payload.get("name")
        )
        if not object_key:
            logger.info("Webhook received with no object key; ignoring: %s", payload)
            return SupabaseWebhookResponse(
                status="ignored",
                message="No object key detected in webhook payload.",
                event_type=event_type,
            )

        metadata = record.get("metadata") or {}
        byte_size = int(
            metadata.get("size")
            or record.get("size")
            or record.get("byte_size")
            or payload.get("size")
            or 0
        )
        content_type = (
            metadata.get("mimetype")
            or record.get("content_type")
            or payload.get("contentType")
            or "application/octet-stream"
        )
        checksum = (
            metadata.get("eTag")
            or metadata.get("checksum")
            or record.get("checksum")
            or payload.get("etag")
        )

        logger.info(
            "Processing Supabase Storage event: key=%s, size=%d, type=%s",
            object_key,
            byte_size,
            event_type,
        )

        # 2. Check for matching UploadReservation
        stmt = (
            select(UploadReservation)
            .where(UploadReservation.object_key == object_key)
            .with_for_update()
        )
        reservation = await session.scalar(stmt)

        if reservation:
            # Idempotency check: if already completed, acknowledge immediately
            if reservation.status == "COMPLETED":
                return SupabaseWebhookResponse(
                    status="success",
                    message="Upload reservation already marked COMPLETED.",
                    event_type=event_type,
                    object_key=object_key,
                    resource_id=reservation.resource_id,
                )

            actual_bytes = byte_size if byte_size > 0 else reservation.reserved_bytes
            reservation.actual_bytes = actual_bytes
            reservation.checksum = checksum or reservation.checksum
            reservation.progress_percent = 100
            reservation.status = "COMPLETED"

            # Atomically adjust quota
            quota_stmt = (
                select(StorageQuota)
                .where(StorageQuota.user_id == reservation.user_id)
                .with_for_update()
            )
            quota = await session.scalar(quota_stmt)
            if quota:
                quota.reserved_bytes = max(0, quota.reserved_bytes - reservation.reserved_bytes)
                quota.used_bytes += actual_bytes

            # Register StorageObject
            storage_stmt = select(StorageObject).where(StorageObject.object_key == object_key)
            storage_obj = await session.scalar(storage_stmt)
            if not storage_obj:
                storage_obj = StorageObject(
                    object_key=object_key,
                    storage_provider="SUPABASE",
                    verification_status="VERIFIED",
                    byte_size=actual_bytes,
                    checksum=reservation.checksum,
                )
                session.add(storage_obj)
                await session.flush()

            # Ensure Resource exists and is linked
            if not reservation.resource_id:
                clean_title = reservation.filename or object_key.split("/")[-1]
                resource = Resource(
                    owner_id=reservation.user_id,
                    title=clean_title,
                    description=f"Uploaded via Supabase Storage: {clean_title}",
                    status="PRIVATE",  # Maintain default privacy
                    visibility="PRIVATE",
                )
                session.add(resource)
                await session.flush()

                version = ResourceVersion(
                    resource_id=resource.resource_id,
                    uploaded_by=reservation.user_id,
                    version_number=1,
                    content_type=reservation.content_type or content_type,
                    size_bytes=actual_bytes,
                    checksum=reservation.checksum,
                )
                session.add(version)
                await session.flush()

                storage_obj.version_id = version.version_id
                reservation.resource_id = resource.resource_id

            await session.commit()
            return SupabaseWebhookResponse(
                status="success",
                message="Storage upload confirmed, quota committed, and resource activated.",
                event_type=event_type,
                object_key=object_key,
                resource_id=reservation.resource_id,
            )

        # 3. If direct upload without prior reservation, parse users/{user_id}/... path
        path_parts = object_key.split("/")
        if len(path_parts) >= 2 and path_parts[0] == "users":
            try:
                user_uuid = uuid.UUID(path_parts[1])
            except ValueError:
                user_uuid = None

            if user_uuid:
                user = await session.get(User, user_uuid)
                if user:
                    quota = await QuotaService.get_or_create_quota(session, user, for_update=True)
                    quota.used_bytes += byte_size

                    storage_stmt = select(StorageObject).where(
                        StorageObject.object_key == object_key
                    )
                    storage_obj = await session.scalar(storage_stmt)
                    if not storage_obj:
                        storage_obj = StorageObject(
                            object_key=object_key,
                            storage_provider="SUPABASE",
                            verification_status="VERIFIED",
                            byte_size=byte_size,
                            checksum=checksum,
                        )
                        session.add(storage_obj)
                        await session.flush()

                    filename = path_parts[-1]
                    resource = Resource(
                        owner_id=user.user_id,
                        title=filename,
                        description=f"Direct Supabase upload: {filename}",
                        status="PRIVATE",
                        visibility="PRIVATE",
                    )
                    session.add(resource)
                    await session.flush()

                    version = ResourceVersion(
                        resource_id=resource.resource_id,
                        uploaded_by=user.user_id,
                        version_number=1,
                        content_type=content_type,
                        size_bytes=byte_size,
                        checksum=checksum,
                    )
                    session.add(version)
                    await session.flush()

                    storage_obj.version_id = version.version_id
                    await session.commit()

                    return SupabaseWebhookResponse(
                        status="success",
                        message="Direct Supabase upload registered and resource created.",
                        event_type=event_type,
                        object_key=object_key,
                        resource_id=resource.resource_id,
                    )

        return SupabaseWebhookResponse(
            status="unmatched",
            message=f"Event received for key '{object_key}', but no matching reservation found.",
            event_type=event_type,
            object_key=object_key,
        )
