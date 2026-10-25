import logging
import uuid
from datetime import datetime, timedelta, timezone

try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.validators import sanitize_filename
from app.models.storage import StorageObject, StorageQuota, UploadReservation
from app.models.user import User
from app.repositories.storage_repository import storage_repository
from app.schemas.storage import StorageQuotaResponse, UploadStatusResponse
from app.services.storage_service import storage_service

logger = logging.getLogger("amu_quota")


class QuotaService:
    """Two-Phase Atomic Storage Quota & Upload Reservation Service."""

    @classmethod
    async def get_or_create_quota(
        cls, session: AsyncSession, user: User, for_update: bool = False
    ) -> StorageQuota:
        """Retrieve the user's storage quota record, provisioning one if not yet present."""
        quota = await storage_repository.get_quota_by_user_id(
            session, user.user_id, for_update=for_update
        )
        if not quota:
            # Determine default quota based on user role
            default_quota = settings.STUDENT_DEFAULT_QUOTA_BYTES
            if user.role and user.role.name in ("Teacher", "Administrator"):
                default_quota = settings.TEACHER_DEFAULT_QUOTA_BYTES

            quota = StorageQuota(
                user_id=user.user_id,
                quota_bytes=default_quota,
                used_bytes=0,
                reserved_bytes=0,
            )
            session.add(quota)
            await session.flush()
        return quota

    @classmethod
    async def get_quota_summary(cls, session: AsyncSession, user: User) -> StorageQuotaResponse:
        """Fetch the current quota allocation breakdown for a user."""
        quota = await cls.get_or_create_quota(session, user, for_update=False)
        available_bytes = max(0, quota.quota_bytes - (quota.used_bytes + quota.reserved_bytes))
        percent_used = (
            round((quota.used_bytes / quota.quota_bytes) * 100, 2) if quota.quota_bytes > 0 else 0.0
        )
        return StorageQuotaResponse(
            user_id=quota.user_id,
            quota_bytes=quota.quota_bytes,
            used_bytes=quota.used_bytes,
            reserved_bytes=quota.reserved_bytes,
            available_bytes=available_bytes,
            percent_used=percent_used,
        )

    @classmethod
    async def reserve_quota(
        cls,
        session: AsyncSession,
        user: User,
        filename: str,
        content_type: str,
        requested_bytes: int,
    ) -> tuple[UploadReservation, str]:
        """
        Phase 1 of Upload: Atomically checks available quota under row lock and reserves bytes.
        Returns the created UploadReservation and a presigned S3 PUT URL.
        """
        if requested_bytes <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Requested upload size must be greater than 0 bytes.",
            )

        # 1. Lock quota row to prevent concurrent race condition bypass
        quota = await cls.get_or_create_quota(session, user, for_update=True)

        available_bytes = quota.quota_bytes - (quota.used_bytes + quota.reserved_bytes)
        if requested_bytes > available_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=(
                    f"Storage quota exceeded. Available: {available_bytes} bytes, "
                    f"Requested: {requested_bytes} bytes."
                ),
            )

        # 2. Increment reserved bytes
        quota.reserved_bytes += requested_bytes

        # 3. Formulate reservation record
        reservation_id = uuid.uuid4()
        clean_name = sanitize_filename(filename)
        object_key = f"uploads/{user.user_id}/{reservation_id}/{clean_name}"
        expires_at = datetime.now(UTC) + timedelta(
            minutes=settings.UPLOAD_RESERVATION_TIMEOUT_MINUTES
        )

        reservation = UploadReservation(
            reservation_id=reservation_id,
            user_id=user.user_id,
            reserved_bytes=requested_bytes,
            status="PENDING",
            object_key=object_key,
            filename=clean_name,
            content_type=content_type,
            expires_at=expires_at,
        )
        session.add(reservation)
        await session.commit()
        await session.refresh(reservation)

        # 4. Generate S3 presigned upload URL
        upload_url = await storage_service.generate_presigned_upload_url(
            object_key=object_key,
            content_type=content_type,
            expires_in=settings.UPLOAD_RESERVATION_TIMEOUT_MINUTES * 60,
        )

        return reservation, upload_url

    @classmethod
    async def commit_reservation(
        cls,
        session: AsyncSession,
        user: User,
        reservation_id: uuid.UUID,
        checksum_sha256: str | None = None,
    ) -> tuple[StorageObject, UploadReservation]:
        """
        Phase 2 of Upload: Verifies uploaded object in S3, commits used_bytes,
        releases reserved_bytes, marks reservation COMPLETED, and registers StorageObject.
        """
        # 1. Fetch reservation
        res_stmt = (
            select(UploadReservation)
            .where(UploadReservation.reservation_id == reservation_id)
            .with_for_update()
        )
        reservation = await session.scalar(res_stmt)

        if not reservation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Upload reservation not found.",
            )

        if reservation.user_id != user.user_id and user.role.name != "Administrator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this reservation.",
            )

        if reservation.status != "PENDING":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Reservation is not pending (current status: {reservation.status}).",
            )

        # 2. Check timeout expiration
        now_utc = datetime.now(UTC)
        if reservation.expires_at < now_utc:
            # Release reservation bytes
            quota = await cls.get_or_create_quota(session, user, for_update=True)
            quota.reserved_bytes = max(0, quota.reserved_bytes - reservation.reserved_bytes)
            reservation.status = "EXPIRED"
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Upload reservation has expired. Please initiate a new upload.",
            )

        # 3. Verify object in S3 / MinIO (EC-05.2 check)
        if not reservation.object_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Upload reservation does not have an associated object key.",
            )

        exists, actual_size, etag = await storage_service.verify_uploaded_object(
            reservation.object_key
        )
        if not exists:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Upload verification failed: file not found in storage. Ensure binary payload was uploaded to presigned URL.",
            )

        # 4. Check size discrepancy (EC-05.3 check)
        quota = await cls.get_or_create_quota(session, user, for_update=True)
        size_delta = actual_size - reservation.reserved_bytes
        if size_delta > 0:
            # The client uploaded more bytes than originally reserved
            available_after_reserve = quota.quota_bytes - (quota.used_bytes + quota.reserved_bytes)
            if size_delta > available_after_reserve:
                # Quota exceeded by the discrepancy
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail=(
                        f"Actual uploaded size ({actual_size} bytes) exceeds remaining "
                        f"available storage quota."
                    ),
                )

        # 5. Commit quota atomically
        quota.reserved_bytes = max(0, quota.reserved_bytes - reservation.reserved_bytes)
        quota.used_bytes += actual_size

        # 6. Update reservation record
        effective_checksum = checksum_sha256 or etag
        reservation.actual_bytes = actual_size
        reservation.checksum = effective_checksum
        reservation.status = "COMPLETED"
        reservation.progress_percent = 100

        # 7. Create verified StorageObject
        storage_object = StorageObject(
            storage_object_id=uuid.uuid4(),
            version_id=None,  # Attached to ResourceVersion in Phase 6
            object_key=reservation.object_key,
            storage_provider="MINIO",
            verification_status="VERIFIED",
            byte_size=actual_size,
            checksum=effective_checksum,
        )
        session.add(storage_object)
        await session.commit()
        await session.refresh(storage_object)

        return storage_object, reservation

    @classmethod
    async def cancel_reservation(
        cls, session: AsyncSession, user: User, reservation_id: uuid.UUID
    ) -> UploadReservation:
        """Cancel a pending reservation and immediately release reserved quota bytes."""
        stmt = (
            select(UploadReservation)
            .where(UploadReservation.reservation_id == reservation_id)
            .with_for_update()
        )
        reservation = await session.scalar(stmt)
        if not reservation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Reservation not found.",
            )

        if reservation.user_id != user.user_id and user.role.name != "Administrator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this reservation.",
            )

        if reservation.status != "PENDING":
            return reservation

        quota = await cls.get_or_create_quota(session, user, for_update=True)
        quota.reserved_bytes = max(0, quota.reserved_bytes - reservation.reserved_bytes)
        reservation.status = "CANCELLED"
        await session.commit()
        await session.refresh(reservation)
        return reservation

    @classmethod
    async def cleanup_expired_reservations(cls, session: AsyncSession) -> int:
        """Periodic sweeper (EC-05.4): Expires abandoned reservations and batch-restores reserved_bytes."""
        now_utc = datetime.now(UTC)
        stmt = select(UploadReservation).where(
            UploadReservation.status == "PENDING",
            UploadReservation.expires_at < now_utc,
        )
        expired_reservations = (await session.scalars(stmt)).all()
        if not expired_reservations:
            return 0

        # Group bytes to restore by user_id to eliminate redundant queries
        user_bytes_to_restore: dict[uuid.UUID, int] = {}
        for res in expired_reservations:
            user_bytes_to_restore[res.user_id] = (
                user_bytes_to_restore.get(res.user_id, 0) + res.reserved_bytes
            )
            res.status = "EXPIRED"

        # Apply quota restoration per user under row lock
        for user_id, bytes_to_restore in user_bytes_to_restore.items():
            quota_stmt = (
                select(StorageQuota).where(StorageQuota.user_id == user_id).with_for_update()
            )
            quota = await session.scalar(quota_stmt)
            if quota:
                quota.reserved_bytes = max(0, quota.reserved_bytes - bytes_to_restore)

        await session.commit()
        cleaned_count = len(expired_reservations)
        logger.info(
            "Swept %d expired upload reservations across %d users.",
            cleaned_count,
            len(user_bytes_to_restore),
        )
        return cleaned_count

    @classmethod
    async def release_quota(
        cls, session: AsyncSession, user_id: uuid.UUID, bytes_to_release: int
    ) -> None:
        """Release storage quota when resources or versions are deleted."""
        stmt = select(StorageQuota).where(StorageQuota.user_id == user_id).with_for_update()
        quota = await session.scalar(stmt)
        if quota:
            quota.used_bytes = max(0, quota.used_bytes - bytes_to_release)
            await session.commit()

    @classmethod
    async def get_upload_status(
        cls,
        session: AsyncSession,
        reservation_id: uuid.UUID,
        user: User,
    ) -> UploadStatusResponse:
        """Fetch current upload status and progress for a reservation."""
        stmt = (
            select(UploadReservation)
            .where(UploadReservation.reservation_id == reservation_id)
            .execution_options(populate_existing=True)
        )
        reservation = await session.scalar(stmt)
        if not reservation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Upload reservation not found.",
            )

        if reservation.user_id != user.user_id and user.role and user.role.name != "Administrator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this upload reservation.",
            )

        total_bytes = (
            reservation.actual_bytes
            if (reservation.status == "COMPLETED" and reservation.actual_bytes)
            else reservation.reserved_bytes
        )
        if reservation.status == "COMPLETED":
            bytes_uploaded = total_bytes
            progress_percent = 100
        elif reservation.actual_bytes and reservation.status == "UPLOADING":
            bytes_uploaded = reservation.actual_bytes
            progress_percent = reservation.progress_percent
        else:
            progress_percent = reservation.progress_percent
            bytes_uploaded = int(total_bytes * (reservation.progress_percent / 100.0))

        updated_at = reservation.updated_at or reservation.created_at

        return UploadStatusResponse(
            reservation_id=reservation.reservation_id,
            user_id=reservation.user_id,
            status=reservation.status,
            progress_percent=progress_percent,
            bytes_uploaded=bytes_uploaded,
            total_bytes=total_bytes,
            object_key=reservation.object_key,
            filename=reservation.filename,
            content_type=reservation.content_type,
            resource_id=reservation.resource_id,
            error_message=reservation.error_message,
            created_at=reservation.created_at,
            updated_at=updated_at,
        )

    @classmethod
    async def update_upload_progress(
        cls,
        session: AsyncSession,
        reservation_id: uuid.UUID,
        user: User,
        progress_percent: int,
        bytes_uploaded: int | None = None,
    ) -> UploadStatusResponse:
        """Update live transmission progress reported by the frontend."""
        stmt = (
            select(UploadReservation)
            .where(UploadReservation.reservation_id == reservation_id)
            .with_for_update()
        )
        reservation = await session.scalar(stmt)
        if not reservation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Upload reservation not found.",
            )

        if reservation.user_id != user.user_id and user.role and user.role.name != "Administrator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this upload reservation.",
            )

        if reservation.status in ("PENDING", "UPLOADING"):
            reservation.progress_percent = max(reservation.progress_percent, progress_percent)
            if bytes_uploaded is not None:
                reservation.actual_bytes = bytes_uploaded
            if reservation.progress_percent > 0 and reservation.status == "PENDING":
                reservation.status = "UPLOADING"
            reservation.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(reservation)

        return await cls.get_upload_status(session, reservation_id, user)
