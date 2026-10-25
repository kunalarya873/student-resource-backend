import logging
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.validators import validate_safe_url
from app.models.folder import Folder
from app.models.resource import Resource, ResourceAccess, ResourceVersion
from app.models.storage import StorageObject, UploadReservation
from app.models.user import User
from app.repositories.folder_repository import folder_repository
from app.repositories.resource_repository import resource_repository
from app.repositories.sharing_repository import sharing_repository
from app.schemas.resource import (
    ResourceCreate,
    ResourceDownloadResponse,
    ResourceRead,
    ResourceUpdate,
    ResourceVersionRead,
)
from app.services.quota_service import QuotaService
from app.services.storage_service import storage_service
from app.services.supabase_storage_service import SupabaseStorageService

logger = logging.getLogger("amu_resources")


class ResourceService:
    """Service governing resource lifecycle, publishing policies, SSRF guards, and versioning."""

    @classmethod
    async def create_resource(
        cls,
        session: AsyncSession,
        user: User,
        payload: ResourceCreate,
    ) -> Resource:
        """
        Creates a new resource and attaches version 1 (file or external link).
        Enforces role-based publishing:
        - Students: status=PRIVATE, visibility=PRIVATE
        - Teachers/Admins: status=APPROVED, visibility=DEPARTMENT
        """
        # Validate folder ownership if folder_id is provided
        if payload.folder_id:
            folder = await session.get(Folder, payload.folder_id)
            if not folder:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Parent folder not found.",
                )
            if folder.owner_id != user.user_id and user.role.name != "Administrator":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not have permission to place resources in this folder.",
                )

        # Enforce role-based status and visibility
        if user.role.name in ("Teacher", "Administrator"):
            status_val = "APPROVED"
            visibility_val = "DEPARTMENT"
        else:
            status_val = "PRIVATE"
            visibility_val = "PRIVATE"

        resource = Resource(
            owner_id=user.user_id,
            folder_id=payload.folder_id,
            subject_id=payload.subject_id,
            academic_year_id=payload.academic_year_id,
            title=payload.title.strip(),
            description=payload.description.strip() if payload.description else None,
            status=status_val,
            visibility=visibility_val,
        )
        session.add(resource)
        await session.flush()

        # Handle version 1 creation
        if payload.external_url:
            safe_url = await validate_safe_url(payload.external_url)
            version = ResourceVersion(
                resource_id=resource.resource_id,
                uploaded_by=user.user_id,
                version_number=1,
                content_type="text/html",
                external_url=safe_url,
                size_bytes=0,
                checksum=None,
            )
            session.add(version)
        elif payload.reservation_id:
            reservation = await session.get(UploadReservation, payload.reservation_id)
            if not reservation or reservation.status != "COMPLETED":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Upload reservation must be completed before attaching to a resource.",
                )
            if reservation.user_id != user.user_id and user.role.name != "Administrator":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not own this upload reservation.",
                )

            version = ResourceVersion(
                resource_id=resource.resource_id,
                uploaded_by=user.user_id,
                version_number=1,
                content_type=reservation.content_type or "application/octet-stream",
                external_url=None,
                size_bytes=reservation.actual_bytes or reservation.reserved_bytes,
                checksum=reservation.checksum,
            )
            session.add(version)
            await session.flush()

            # Link storage object to this version
            stmt = select(StorageObject).where(StorageObject.object_key == reservation.object_key)
            storage_obj = await session.scalar(stmt)
            if storage_obj:
                storage_obj.version_id = version.version_id

        await session.commit()
        return await cls.get_resource(session, user, resource.resource_id)

    @classmethod
    async def upload_resource_direct(
        cls,
        session: AsyncSession,
        user: User,
        file_data: bytes,
        filename: str,
        content_type: str,
        title: str,
        description: str | None = None,
        folder_id: uuid.UUID | None = None,
        subject_id: int | None = None,
        academic_year_id: int | None = None,
    ) -> Resource:
        """
        Uploads resource file directly into user's isolated folder in Supabase storage,
        checks quota boundaries, registers the resource with privacy by default,
        and links version and storage objects.
        """
        if not file_data or len(file_data) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploaded file cannot be empty.",
            )

        file_size = len(file_data)

        # 1. Enforce quota boundary atomically
        quota = await QuotaService.get_or_create_quota(session, user, for_update=True)
        available_bytes = quota.quota_bytes - (quota.used_bytes + quota.reserved_bytes)
        if file_size > available_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Storage quota exceeded. Available: {available_bytes} bytes, Requested: {file_size} bytes.",
            )

        # 2. Folder resolution & isolation
        folder_name = "general"
        if folder_id:
            folder = await session.get(Folder, folder_id)
            if not folder:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Specified folder not found.",
                )
            if folder.owner_id != user.user_id and user.role.name != "Administrator":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not have permission to place resources in this folder.",
                )
            folder_name = folder.name

        # 3. Upload to Supabase storage in user-segregated folder
        storage_meta = await SupabaseStorageService.upload_file_async(
            user_id=user.user_id,
            folder_name=folder_name,
            filename=filename,
            file_data=file_data,
            content_type=content_type,
        )

        # 4. Enforce privacy defaults
        if user.role.name in ("Teacher", "Administrator"):
            status_val = "APPROVED"
            visibility_val = "DEPARTMENT"
        else:
            status_val = "PRIVATE"
            visibility_val = "PRIVATE"

        resource = Resource(
            owner_id=user.user_id,
            folder_id=folder_id,
            subject_id=subject_id,
            academic_year_id=academic_year_id,
            title=title.strip(),
            description=description.strip() if description else None,
            status=status_val,
            visibility=visibility_val,
        )
        session.add(resource)
        await session.flush()

        # 5. Attach ResourceVersion
        version = ResourceVersion(
            resource_id=resource.resource_id,
            uploaded_by=user.user_id,
            version_number=1,
            content_type=content_type,
            size_bytes=file_size,
            checksum=storage_meta["checksum"],
        )
        session.add(version)
        await session.flush()

        # 6. Create StorageObject
        storage_obj = StorageObject(
            version_id=version.version_id,
            object_key=storage_meta["object_key"],
            storage_provider="SUPABASE",
            verification_status="VERIFIED",
            byte_size=file_size,
            checksum=storage_meta["checksum"],
        )
        session.add(storage_obj)

        # 7. Atomically commit quota consumption
        quota.used_bytes += file_size
        await session.commit()

        return await cls.get_resource(session, user, resource.resource_id)

    @classmethod
    async def add_version(
        cls,
        session: AsyncSession,
        user: User,
        resource_id: uuid.UUID,
        reservation_id: uuid.UUID | None = None,
        external_url: str | None = None,
    ) -> ResourceVersion:
        """
        Creates a new version for an existing resource (increments version_number).
        Preserves all prior versions and historical file storage.
        """
        resource = await resource_repository.get_by_id(session, resource_id)
        if not resource:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Resource not found.",
            )
        if resource.owner_id != user.user_id and user.role.name != "Administrator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to add versions to this resource.",
            )

        # Atomic calculation of next version number
        stmt = select(func.coalesce(func.max(ResourceVersion.version_number), 0)).where(
            ResourceVersion.resource_id == resource_id
        )
        max_v = await session.scalar(stmt)
        next_version = (max_v or 0) + 1

        if external_url:
            safe_url = await validate_safe_url(external_url)
            new_version = ResourceVersion(
                resource_id=resource_id,
                uploaded_by=user.user_id,
                version_number=next_version,
                content_type="text/html",
                external_url=safe_url,
                size_bytes=0,
                checksum=None,
            )
            session.add(new_version)
        elif reservation_id:
            reservation = await session.get(UploadReservation, reservation_id)
            if not reservation or reservation.status != "COMPLETED":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Upload reservation must be completed before attaching as a version.",
                )
            if reservation.user_id != user.user_id and user.role.name != "Administrator":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not own this upload reservation.",
                )

            new_version = ResourceVersion(
                resource_id=resource_id,
                uploaded_by=user.user_id,
                version_number=next_version,
                content_type=reservation.content_type or "application/octet-stream",
                external_url=None,
                size_bytes=reservation.actual_bytes or reservation.reserved_bytes,
                checksum=reservation.checksum,
            )
            session.add(new_version)
            await session.flush()

            stmt = select(StorageObject).where(StorageObject.object_key == reservation.object_key)
            storage_obj = await session.scalar(stmt)
            if storage_obj:
                storage_obj.version_id = new_version.version_id
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either external_url or reservation_id must be provided to create a version.",
            )

        await session.commit()
        await session.refresh(new_version)
        return new_version

    @classmethod
    async def get_resource(
        cls,
        session: AsyncSession,
        user: User,
        resource_id: uuid.UUID,
    ) -> Resource:
        """Retrieve resource details and enforce authorization guards (EC-06.1, EC-06.4)."""
        resource = await resource_repository.get_by_id_with_details(session, resource_id)
        if not resource:
            folder = await folder_repository.get_by_id(session, resource_id)
            if folder:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=(
                        f"Resource not found. The specified ID '{resource_id}' is a Folder ('{folder.name}'). "
                        f"To view contents of this folder, use GET /api/v1/folders/{resource_id}/contents or "
                        f"GET /api/v1/resources?folder_id={resource_id}."
                    ),
                )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Resource not found.",
            )

        # Authorization check
        await cls.verify_read_access(session, resource, user)
        return resource

    @classmethod
    async def verify_read_access(
        cls, session: AsyncSession, resource: Resource, user: User
    ) -> None:
        """Enforce access rules: Owner & Admin have full access; explicit share grant or approved visibility."""
        if (user.role and user.role.name == "Administrator") or resource.owner_id == user.user_id:
            return

        # Check explicit sharing grant via sharing_repository (Phase 7)
        grant = await sharing_repository.get_active_grant(
            session, resource.resource_id, user.user_id
        )
        if grant:
            return

        if resource.status == "PRIVATE" or resource.visibility == "PRIVATE":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: this resource is private to its owner.",
            )

        if (
            resource.visibility == "DEPARTMENT"
            and resource.owner.department_id != user.department_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: resource is restricted to department members.",
            )

    @classmethod
    async def list_resources(
        cls,
        session: AsyncSession,
        user: User,
        folder_id: uuid.UUID | None = None,
        subject_id: int | None = None,
        academic_year_id: int | None = None,
        status: str | None = None,
        visibility: str | None = None,
        q: str | None = None,
        sort_by: str = "created_at",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
        page: int | None = None,
    ) -> list[Resource]:
        """List resources accessible to the user with optional filters, search, sorting, and pagination."""
        if page is not None:
            page = max(1, page)
            limit = max(1, min(limit, 100))
            offset = (page - 1) * limit

        stmt = select(Resource).options(
            selectinload(Resource.versions),
            selectinload(Resource.owner),
        )

        if user.role.name != "Administrator":
            # User sees their own resources OR shared resources OR approved department/public resources
            stmt = stmt.where(
                (Resource.owner_id == user.user_id)
                | (
                    Resource.resource_id.in_(
                        select(ResourceAccess.resource_id).where(
                            ResourceAccess.user_id == user.user_id
                        )
                    )
                )
                | (
                    (Resource.status == "APPROVED")
                    & (
                        (Resource.visibility == "PUBLIC")
                        | (
                            (Resource.visibility == "DEPARTMENT")
                            & (Resource.owner.has(department_id=user.department_id))
                        )
                    )
                )
            )

        if folder_id is not None:
            stmt = stmt.where(Resource.folder_id == folder_id)
        if subject_id is not None:
            stmt = stmt.where(Resource.subject_id == subject_id)
        if academic_year_id is not None:
            stmt = stmt.where(Resource.academic_year_id == academic_year_id)
        if status is not None:
            stmt = stmt.where(Resource.status == status)
        if visibility is not None:
            stmt = stmt.where(Resource.visibility == visibility)
        if q and q.strip():
            clean_q = q.strip()
            stmt = stmt.where(
                or_(
                    Resource.title.ilike(f"%{clean_q}%"),
                    Resource.description.ilike(f"%{clean_q}%"),
                )
            )

        # Dynamic sorting
        sort_col = Resource.created_at
        if sort_by == "title":
            sort_col = Resource.title
        elif sort_by == "status":
            sort_col = Resource.status
        elif sort_by == "visibility":
            sort_col = Resource.visibility

        stmt = stmt.order_by(sort_col.desc()) if order == "desc" else stmt.order_by(sort_col.asc())
        stmt = stmt.limit(limit).offset(offset)

        resources = (await session.scalars(stmt)).all()
        return list(resources)

    @classmethod
    async def update_resource(
        cls,
        session: AsyncSession,
        user: User,
        resource_id: uuid.UUID,
        payload: ResourceUpdate,
    ) -> Resource:
        """Update resource metadata or visibility (Owner or Administrator only)."""
        resource = await cls.get_resource(session, user, resource_id)
        if resource.owner_id != user.user_id and user.role.name != "Administrator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to edit this resource.",
            )

        if payload.title is not None:
            resource.title = payload.title.strip()
        if payload.description is not None:
            resource.description = payload.description.strip()
        # Explicitly provided folder_id (including null) moves the resource,
        # so dragging a file back to the drive root (null) persists.
        if "folder_id" in payload.model_fields_set:
            resource.folder_id = payload.folder_id
        if payload.subject_id is not None:
            resource.subject_id = payload.subject_id
        if payload.academic_year_id is not None:
            resource.academic_year_id = payload.academic_year_id
        if payload.visibility is not None:
            resource.visibility = payload.visibility

        await session.commit()
        await session.refresh(resource)
        return resource

    @classmethod
    async def delete_resource(
        cls,
        session: AsyncSession,
        user: User,
        resource_id: uuid.UUID,
    ) -> None:
        """Deletes a resource, frees owner quota for file-backed versions, and cascades."""
        resource = await cls.get_resource(session, user, resource_id)
        if resource.owner_id != user.user_id and user.role.name != "Administrator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to delete this resource.",
            )

        # Calculate bytes to release from owner quota
        total_file_bytes = 0
        for v in resource.versions:
            if v.size_bytes > 0:
                total_file_bytes += v.size_bytes

        if total_file_bytes > 0:
            await QuotaService.release_quota(session, resource.owner_id, total_file_bytes)

        await session.delete(resource)
        await session.commit()

    @classmethod
    async def get_download_info(
        cls,
        session: AsyncSession,
        user: User,
        resource_id: uuid.UUID,
        version_id: uuid.UUID | None = None,
        inline: bool = False,
    ) -> ResourceDownloadResponse:
        """Generates secure download URL or returns safe external URL for resource asset."""
        resource = await cls.get_resource(session, user, resource_id)

        target_version: ResourceVersion | None = None
        if version_id:
            for v in resource.versions:
                if v.version_id == version_id:
                    target_version = v
                    break
            if not target_version:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Requested resource version not found.",
                )
        else:
            # Use latest version
            if not resource.versions:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="No versions exist for this resource.",
                )
            target_version = max(resource.versions, key=lambda v: v.version_number)

        # Check if external URL
        if target_version.external_url:
            return ResourceDownloadResponse(
                resource_id=resource.resource_id,
                version_id=target_version.version_id,
                version_number=target_version.version_number,
                is_external=True,
                download_url=target_version.external_url,
                filename=f"{resource.title}.html",
                content_type="text/html",
                size_bytes=0,
            )

        # File-backed: fetch StorageObject
        stmt = select(StorageObject).where(StorageObject.version_id == target_version.version_id)
        storage_obj = await session.scalar(stmt)
        if not storage_obj:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Underlying storage object not found for this version.",
            )

        filename = storage_obj.object_key.split("/")[-1] or f"{resource.title}.bin"
        if storage_obj.storage_provider == "SUPABASE":
            download_url = await SupabaseStorageService.create_signed_download_url(
                storage_path=storage_obj.object_key,
                expires_in=900,
            )
        else:
            download_url = await storage_service.generate_presigned_download_url(
                object_key=storage_obj.object_key,
                filename=filename,
                expires_in=300,
                disposition="inline" if inline else "attachment",
                content_type=target_version.content_type,
            )

        return ResourceDownloadResponse(
            resource_id=resource.resource_id,
            version_id=target_version.version_id,
            version_number=target_version.version_number,
            is_external=False,
            download_url=download_url,
            filename=filename,
            content_type=target_version.content_type,
            size_bytes=target_version.size_bytes,
        )

    @classmethod
    def serialize_resource(cls, res: Resource) -> ResourceRead:
        """Map ORM resource and versions into clean Pydantic response."""
        version_reads = [
            ResourceVersionRead(
                version_id=v.version_id,
                resource_id=v.resource_id,
                uploaded_by=v.uploaded_by,
                version_number=v.version_number,
                content_type=v.content_type,
                external_url=v.external_url,
                size_bytes=v.size_bytes,
                checksum=v.checksum,
                created_at=v.created_at,
            )
            for v in res.versions
        ]
        current_v = max(version_reads, key=lambda v: v.version_number) if version_reads else None

        return ResourceRead(
            resource_id=res.resource_id,
            owner_id=res.owner_id,
            folder_id=res.folder_id,
            subject_id=res.subject_id,
            academic_year_id=res.academic_year_id,
            title=res.title,
            description=res.description,
            visibility=res.visibility,
            status=res.status,
            created_at=res.created_at,
            updated_at=res.updated_at,
            current_version=current_v,
            versions=version_reads,
        )
