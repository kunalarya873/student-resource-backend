import logging
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.folder import Folder, FolderAccess
from app.models.resource import Resource, ResourceAccess, ResourceVersion
from app.models.storage import StorageObject
from app.models.user import User
from app.schemas.folder import BreadcrumbItem, FolderRead, FolderResourceItem
from app.schemas.public_share import (
    PublicFolderContentsResponse,
    PublicFolderRead,
    PublicJoinResponse,
    PublicLinkResponse,
    PublicResourceDownloadResponse,
    PublicResourceRead,
    PublicShareItemResponse,
)
from app.services.storage_service import storage_service
from app.services.supabase_storage_service import SupabaseStorageService

logger = logging.getLogger("amu_resources")


class PublicShareService:
    """Service governing public link sharing, unauthenticated guest viewing, and join onboarding."""

    @staticmethod
    def generate_token() -> str:
        """Generate a cryptographically unguessable, URL-safe 22-character token."""
        return secrets.token_urlsafe(16)

    # -------------------------------------------------------------------------
    # Public Link Management (Authenticated Owner / Admin)
    # -------------------------------------------------------------------------

    @classmethod
    async def toggle_folder_public_link(
        cls,
        session: AsyncSession,
        actor: User,
        folder_id: uuid.UUID,
        is_public: bool,
    ) -> PublicLinkResponse:
        folder = await session.get(Folder, folder_id)
        if not folder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Folder not found.",
            )
        if folder.owner_id != actor.user_id and actor.role.name != "Administrator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the folder owner or an administrator can manage public link sharing.",
            )

        if is_public:
            if not folder.share_token:
                folder.share_token = cls.generate_token()
            folder.is_public = True
        else:
            folder.is_public = False

        folder.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(folder)

        share_url = f"/s/{folder.share_token}" if folder.is_public and folder.share_token else None
        return PublicLinkResponse(
            is_public=folder.is_public,
            share_token=folder.share_token,
            share_url=share_url,
            permission="VIEW",
        )

    @classmethod
    async def get_folder_public_link(
        cls,
        session: AsyncSession,
        actor: User,
        folder_id: uuid.UUID,
    ) -> PublicLinkResponse:
        folder = await session.get(Folder, folder_id)
        if not folder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Folder not found.",
            )
        if folder.owner_id != actor.user_id and actor.role.name != "Administrator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the folder owner or an administrator can view public link status.",
            )

        share_url = f"/s/{folder.share_token}" if folder.is_public and folder.share_token else None
        return PublicLinkResponse(
            is_public=folder.is_public,
            share_token=folder.share_token,
            share_url=share_url,
            permission="VIEW",
        )

    @classmethod
    async def toggle_resource_public_link(
        cls,
        session: AsyncSession,
        actor: User,
        resource_id: uuid.UUID,
        is_public: bool,
    ) -> PublicLinkResponse:
        resource = await session.get(Resource, resource_id)
        if not resource:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Resource not found.",
            )
        if resource.owner_id != actor.user_id and actor.role.name != "Administrator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the resource owner or an administrator can manage public link sharing.",
            )

        if is_public:
            if not resource.share_token:
                resource.share_token = cls.generate_token()
            resource.is_public = True
            resource.visibility = "PUBLIC"
        else:
            resource.is_public = False
            resource.visibility = "PRIVATE"

        resource.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(resource)

        share_url = f"/s/{resource.share_token}" if resource.is_public and resource.share_token else None
        return PublicLinkResponse(
            is_public=resource.is_public,
            share_token=resource.share_token,
            share_url=share_url,
            permission="VIEW",
        )

    @classmethod
    async def get_resource_public_link(
        cls,
        session: AsyncSession,
        actor: User,
        resource_id: uuid.UUID,
    ) -> PublicLinkResponse:
        resource = await session.get(Resource, resource_id)
        if not resource:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Resource not found.",
            )
        if resource.owner_id != actor.user_id and actor.role.name != "Administrator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the resource owner or an administrator can view public link status.",
            )

        share_url = f"/s/{resource.share_token}" if resource.is_public and resource.share_token else None
        return PublicLinkResponse(
            is_public=resource.is_public,
            share_token=resource.share_token,
            share_url=share_url,
            permission="VIEW",
        )

    # -------------------------------------------------------------------------
    # Public Guest Resolution & Content Delivery (Unauthenticated)
    # -------------------------------------------------------------------------

    @classmethod
    async def resolve_share_item(
        cls,
        session: AsyncSession,
        share_token: str,
    ) -> PublicShareItemResponse:
        """Resolve whether the share token represents a public Folder or a public Resource."""
        # Check folder
        stmt_folder = select(Folder).options(selectinload(Folder.owner)).where(
            Folder.share_token == share_token,
            Folder.is_public.is_(True),
        )
        folder = await session.scalar(stmt_folder)
        if folder:
            return PublicShareItemResponse(
                kind="folder",
                share_token=share_token,
                name=folder.name,
                owner_name=folder.owner.full_name if folder.owner else "AMU Faculty/Student",
                created_at=folder.created_at,
            )

        # Check resource
        stmt_res = select(Resource).options(
            selectinload(Resource.owner),
            selectinload(Resource.versions),
        ).where(
            Resource.share_token == share_token,
            Resource.is_public.is_(True),
        )
        resource = await session.scalar(stmt_res)
        if resource:
            current_v = max(resource.versions, key=lambda v: v.version_number) if resource.versions else None
            return PublicShareItemResponse(
                kind="resource",
                share_token=share_token,
                name=resource.title,
                owner_name=resource.owner.full_name if resource.owner else "AMU Faculty/Student",
                created_at=resource.created_at,
                size_bytes=current_v.size_bytes if current_v else 0,
                content_type=current_v.content_type if current_v else None,
            )

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Public link is invalid, expired, or access has been restricted by the owner.",
        )

    @classmethod
    async def get_public_folder_contents(
        cls,
        session: AsyncSession,
        share_token: str,
        current_subfolder_id: uuid.UUID | None = None,
        q: str | None = None,
        page: int = 1,
        limit: int = 50,
        sort_by: str = "name",
        order: str = "asc",
    ) -> PublicFolderContentsResponse:
        """Retrieve public folder details and browsable child subfolders / resources."""
        # 1. Fetch public root folder
        stmt_root = select(Folder).options(selectinload(Folder.owner)).where(
            Folder.share_token == share_token,
            Folder.is_public.is_(True),
        )
        root_folder = await session.scalar(stmt_root)
        if not root_folder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Public folder not found or link has been restricted.",
            )

        target_folder = root_folder
        breadcrumbs: list[BreadcrumbItem] = [
            BreadcrumbItem(folder_id=root_folder.folder_id, name=root_folder.name)
        ]

        # 2. If navigating inside a public subfolder, ensure it is within the root subtree
        if current_subfolder_id and current_subfolder_id != root_folder.folder_id:
            sub = await session.get(Folder, current_subfolder_id)
            if not sub:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Subfolder not found.",
                )

            # Validate that sub is a descendant of root_folder
            curr = sub
            trail = [BreadcrumbItem(folder_id=curr.folder_id, name=curr.name)]
            is_descendant = False
            for _ in range(25):  # safe depth limit
                if curr.parent_folder_id == root_folder.folder_id:
                    is_descendant = True
                    break
                if not curr.parent_folder_id:
                    break
                curr = await session.get(Folder, curr.parent_folder_id)
                if not curr:
                    break
                trail.insert(0, BreadcrumbItem(folder_id=curr.folder_id, name=curr.name))

            if not is_descendant:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Access denied: requested folder is outside this public share.",
                )

            target_folder = sub
            breadcrumbs.extend(trail)

        page = max(1, page)
        limit = max(1, min(limit, 100))
        offset = (page - 1) * limit
        clean_q = q.strip() if q and q.strip() else None

        # 3. Subfolders
        sub_count_stmt = select(func.count(Folder.folder_id)).where(
            Folder.parent_folder_id == target_folder.folder_id
        )
        subfolders_stmt = select(Folder).where(Folder.parent_folder_id == target_folder.folder_id)
        if clean_q:
            sub_count_stmt = sub_count_stmt.where(Folder.name.ilike(f"%{clean_q}%"))
            subfolders_stmt = subfolders_stmt.where(Folder.name.ilike(f"%{clean_q}%"))

        total_subfolders = await session.scalar(sub_count_stmt) or 0
        sub_sort_col = Folder.name
        if sort_by == "created_at":
            sub_sort_col = Folder.created_at
        elif sort_by == "updated_at":
            sub_sort_col = Folder.updated_at

        subfolders_stmt = (
            subfolders_stmt.order_by(sub_sort_col.desc())
            if order == "desc"
            else subfolders_stmt.order_by(sub_sort_col.asc())
        )
        subfolders_stmt = subfolders_stmt.limit(limit).offset(offset)
        subfolders = (await session.scalars(subfolders_stmt)).all()

        # 4. Resources
        res_count_stmt = select(func.count(Resource.resource_id)).where(
            Resource.folder_id == target_folder.folder_id
        )
        resources_stmt = select(Resource).where(Resource.folder_id == target_folder.folder_id)
        if clean_q:
            res_count_stmt = res_count_stmt.where(Resource.title.ilike(f"%{clean_q}%"))
            resources_stmt = resources_stmt.where(Resource.title.ilike(f"%{clean_q}%"))

        total_resources = await session.scalar(res_count_stmt) or 0
        res_sort_col = Resource.title
        if sort_by == "created_at":
            res_sort_col = Resource.created_at
        elif sort_by == "updated_at":
            res_sort_col = Resource.updated_at

        resources_stmt = (
            resources_stmt.order_by(res_sort_col.desc())
            if order == "desc"
            else resources_stmt.order_by(res_sort_col.asc())
        )
        resources_stmt = resources_stmt.limit(limit).offset(offset)
        resources = (await session.scalars(resources_stmt)).all()

        folder_read = PublicFolderRead(
            folder_id=target_folder.folder_id,
            name=target_folder.name,
            owner_name=root_folder.owner.full_name if root_folder.owner else "AMU Faculty/Student",
            created_at=target_folder.created_at,
            share_token=share_token,
            is_public=True,
        )

        return PublicFolderContentsResponse(
            folder=folder_read,
            breadcrumbs=breadcrumbs,
            subfolders=[FolderRead.model_validate(f) for f in subfolders],
            resources=[
                FolderResourceItem(
                    resource_id=r.resource_id,
                    owner_id=r.owner_id,
                    title=r.title,
                    description=r.description,
                    visibility=r.visibility,
                    status=r.status,
                    is_public=r.is_public,
                    share_token=r.share_token,
                    created_at=r.created_at,
                )
                for r in resources
            ],
            page=page,
            limit=limit,
            total_subfolders=total_subfolders,
            total_resources=total_resources,
        )

    @classmethod
    async def get_public_resource(
        cls,
        session: AsyncSession,
        share_token: str,
    ) -> PublicResourceRead:
        """Fetch metadata for a single public resource."""
        stmt = select(Resource).options(
            selectinload(Resource.owner),
            selectinload(Resource.versions),
        ).where(
            Resource.share_token == share_token,
            Resource.is_public.is_(True),
        )
        resource = await session.scalar(stmt)
        if not resource:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Public resource not found or link has been restricted.",
            )

        current_v = max(resource.versions, key=lambda v: v.version_number) if resource.versions else None
        is_external = bool(current_v and current_v.external_url)

        return PublicResourceRead(
            resource_id=resource.resource_id,
            title=resource.title,
            description=resource.description,
            content_type=current_v.content_type if current_v else None,
            size_bytes=current_v.size_bytes if current_v else 0,
            is_external=is_external,
            external_url=current_v.external_url if current_v else None,
            owner_name=resource.owner.full_name if resource.owner else "AMU Faculty/Student",
            created_at=resource.created_at,
            share_token=share_token,
            is_public=True,
        )

    @classmethod
    async def get_public_resource_download(
        cls,
        session: AsyncSession,
        share_token: str,
        inline: bool = False,
    ) -> PublicResourceDownloadResponse:
        """Generate presigned download or preview URL for a public resource."""
        stmt = select(Resource).options(
            selectinload(Resource.versions),
        ).where(
            Resource.share_token == share_token,
            Resource.is_public.is_(True),
        )
        resource = await session.scalar(stmt)
        if not resource:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Public resource not found or link has been restricted.",
            )

        if not resource.versions:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No asset version attached to this resource.",
            )

        target_version = max(resource.versions, key=lambda v: v.version_number)

        # External link
        if target_version.external_url:
            return PublicResourceDownloadResponse(
                download_url=target_version.external_url,
                filename=resource.title,
                content_type=target_version.content_type,
                size_bytes=0,
                is_external=True,
            )

        # File asset from storage
        stmt_storage = select(StorageObject).where(StorageObject.version_id == target_version.version_id)
        storage_obj = await session.scalar(stmt_storage)
        if not storage_obj:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Underlying storage asset not found.",
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

        return PublicResourceDownloadResponse(
            download_url=download_url,
            filename=filename,
            content_type=target_version.content_type,
            size_bytes=target_version.size_bytes,
            is_external=False,
        )

    # -------------------------------------------------------------------------
    # Join Onboarding (Claim into "Shared with Me")
    # -------------------------------------------------------------------------

    @classmethod
    async def join_public_share(
        cls,
        session: AsyncSession,
        current_user: User,
        share_token: str,
    ) -> PublicJoinResponse:
        """
        When a registered user views a public folder or file, clicking 'Join / Save to My Drive'
        grants them explicit VIEW access so the item appears permanently in their 'Shared with Me' list.
        """
        # 1. Try folder
        stmt_f = select(Folder).where(
            Folder.share_token == share_token,
            Folder.is_public.is_(True),
        )
        folder = await session.scalar(stmt_f)
        if folder:
            if folder.owner_id == current_user.user_id:
                return PublicJoinResponse(
                    message="You are the owner of this folder.",
                    kind="folder",
                    item_id=folder.folder_id,
                    already_member=True,
                )

            # Check existing grant
            stmt_grant = select(FolderAccess).where(
                FolderAccess.folder_id == folder.folder_id,
                FolderAccess.user_id == current_user.user_id,
            )
            existing_grant = await session.scalar(stmt_grant)
            if existing_grant:
                return PublicJoinResponse(
                    message="This folder is already in your 'Shared with Me' list.",
                    kind="folder",
                    item_id=folder.folder_id,
                    already_member=True,
                )

            # Create grant
            new_grant = FolderAccess(
                folder_id=folder.folder_id,
                user_id=current_user.user_id,
                permission="VIEW",
                granted_by=folder.owner_id,
            )
            session.add(new_grant)
            await session.commit()
            return PublicJoinResponse(
                message="Folder successfully joined and saved to your 'Shared with Me' drive.",
                kind="folder",
                item_id=folder.folder_id,
                already_member=False,
            )

        # 2. Try resource
        stmt_r = select(Resource).where(
            Resource.share_token == share_token,
            Resource.is_public.is_(True),
        )
        resource = await session.scalar(stmt_r)
        if resource:
            if resource.owner_id == current_user.user_id:
                return PublicJoinResponse(
                    message="You are the owner of this resource.",
                    kind="resource",
                    item_id=resource.resource_id,
                    already_member=True,
                )

            stmt_r_grant = select(ResourceAccess).where(
                ResourceAccess.resource_id == resource.resource_id,
                ResourceAccess.user_id == current_user.user_id,
            )
            existing_r_grant = await session.scalar(stmt_r_grant)
            if existing_r_grant:
                return PublicJoinResponse(
                    message="This resource is already in your 'Shared with Me' list.",
                    kind="resource",
                    item_id=resource.resource_id,
                    already_member=True,
                )

            new_r_grant = ResourceAccess(
                resource_id=resource.resource_id,
                user_id=current_user.user_id,
                permission="VIEW",
                granted_by=resource.owner_id,
            )
            session.add(new_r_grant)
            await session.commit()
            return PublicJoinResponse(
                message="Resource successfully joined and saved to your 'Shared with Me' drive.",
                kind="resource",
                item_id=resource.resource_id,
                already_member=False,
            )

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Public link is invalid or expired.",
        )
