import logging
import math
import uuid

from fastapi import BackgroundTasks, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.folder import Folder, FolderAccess
from app.models.resource import Resource, ResourceAccess
from app.models.user import User
from app.repositories.folder_repository import folder_repository
from app.repositories.resource_repository import resource_repository
from app.repositories.sharing_repository import sharing_repository
from app.repositories.user_repository import user_repository
from app.schemas.sharing import (
    FolderShareGrantRead,
    PaginatedFolderShareGrantResponse,
    PaginatedShareGrantResponse,
    ShareGrantRead,
)
from app.services.email_service import EmailService

logger = logging.getLogger("amu_sharing")


class SharingService:
    """Service governing resource sharing grants, peer access, and authorization resolution."""

    @classmethod
    async def can_access_resource(
        cls,
        session: AsyncSession,
        user: User,
        resource: Resource,
        required_permission: str = "VIEW",
    ) -> bool:
        """
        Evaluate access permission in constant time:
        1. Owner has full access
        2. Administrator has full oversight
        3. Approved departmental or public resources
        4. Explicit ResourceAccess grant
        """
        # 1. Owner always has full access
        if resource.owner_id == user.user_id:
            return True

        # 2. Administrator has full access
        if user.role and user.role.name == "Administrator":
            return True

        # 3. Approved institutional resource (for VIEW permission)
        if resource.status == "APPROVED" and required_permission == "VIEW":
            if resource.visibility == "PUBLIC":
                return True
            if (
                resource.visibility == "DEPARTMENT"
                and resource.owner
                and resource.owner.department_id == user.department_id
            ):
                return True

        # 4. Explicit ResourceAccess grant via sharing_repository
        grant = await sharing_repository.get_active_grant(
            session, resource.resource_id, user.user_id
        )
        if grant:
            if required_permission == "VIEW":
                return True
            if required_permission == "EDIT" and grant.permission == "EDIT":
                return True

        # 5. Inherited access: the resource lives inside a folder (or subtree)
        #    that has been shared with this user.
        if resource.folder_id:
            tree_perm = await folder_repository.get_tree_permission(
                session, resource.folder_id, user.user_id
            )
            if tree_perm:
                if required_permission == "VIEW":
                    return True
                if required_permission == "EDIT" and tree_perm == "EDIT":
                    return True

        return False

    @classmethod
    async def grant_access(
        cls,
        session: AsyncSession,
        actor: User,
        resource_id: uuid.UUID,
        grantee_email: str,
        permission: str = "VIEW",
        background_tasks: BackgroundTasks | None = None,
    ) -> ResourceAccess:
        """
        Grants VIEW or EDIT access to a user identified by email.
        Enforces EC-07.1 (owner/admin only) and EC-07.2 (cannot self-share).
        Sends an email notification to the recipient in background.
        """
        clean_permission = permission.upper().strip()
        if clean_permission not in ("VIEW", "EDIT"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid permission. Must be 'VIEW' or 'EDIT'.",
            )

        resource = await resource_repository.get_by_id(session, resource_id)
        if not resource:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Resource not found.",
            )

        # EC-07.1: Only owner or admin can share
        if resource.owner_id != actor.user_id and actor.role.name != "Administrator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the resource owner or an administrator can grant access.",
            )

        # EC-07.4: Find grantee user via user_repository
        grantee = await user_repository.get_by_email(session, grantee_email)
        if not grantee:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Target user with email '{grantee_email}' was not found.",
            )

        # EC-07.2: Self-sharing check
        if grantee.user_id == actor.user_id or grantee.user_id == resource.owner_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot share a resource with yourself.",
            )

        # Check existing grant via sharing_repository (upsert behavior)
        existing = await sharing_repository.get_active_grant(session, resource_id, grantee.user_id)
        if existing:
            existing.permission = clean_permission
            await session.commit()
            existing.user = grantee
            target_grant = existing
        else:
            grant = ResourceAccess(
                resource_id=resource_id,
                user_id=grantee.user_id,
                permission=clean_permission,
                granted_by=actor.user_id,
            )
            await sharing_repository.create(session, grant)
            await session.commit()
            grant.user = grantee
            target_grant = grant

        # Send notification email to the receiver
        if background_tasks is not None:
            background_tasks.add_task(
                EmailService.send_share_notification_email,
                email=grantee.email,
                grantee_name=grantee.full_name,
                sharer_name=actor.full_name,
                resource_title=resource.title,
                permission=clean_permission,
                resource_id=resource.resource_id,
            )
        else:
            await EmailService.send_share_notification_email(
                email=grantee.email,
                grantee_name=grantee.full_name,
                sharer_name=actor.full_name,
                resource_title=resource.title,
                permission=clean_permission,
                resource_id=resource.resource_id,
            )

        return target_grant

    @classmethod
    async def revoke_access(
        cls,
        session: AsyncSession,
        actor: User,
        resource_id: uuid.UUID,
        access_id: uuid.UUID,
    ):
        """
        Revokes a granted permission immediately.
        Enforces EC-07.1: Only owner or admin can revoke.
        """
        resource = await resource_repository.get_by_id(session, resource_id)
        if not resource:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Resource not found.",
            )

        if resource.owner_id != actor.user_id and actor.role.name != "Administrator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the resource owner or an administrator can revoke access.",
            )

        grant = await sharing_repository.get_by_id(session, access_id)
        if not grant or grant.resource_id != resource_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Share grant not found for this resource.",
            )

        await session.delete(grant)
        await session.commit()

    @classmethod
    async def list_grants(
        cls,
        session: AsyncSession,
        actor: User,
        resource_id: uuid.UUID,
        page: int = 1,
        limit: int = 50,
    ) -> PaginatedShareGrantResponse:
        """List all active share grants on a resource with pagination (Owner/Admin only)."""
        resource = await resource_repository.get_by_id(session, resource_id)
        if not resource:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Resource not found.",
            )

        if resource.owner_id != actor.user_id and actor.role.name != "Administrator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the resource owner or an administrator can view share grants.",
            )

        grants, total = await sharing_repository.list_grants_for_resource(
            session, resource_id, page=page, limit=limit
        )
        total_pages = math.ceil(total / limit) if total > 0 else 0

        items = [
            ShareGrantRead(
                access_id=g.access_id,
                resource_id=g.resource_id,
                user_id=g.user_id,
                grantee_email=g.user.email if g.user else "",
                grantee_name=g.user.full_name if g.user else "",
                permission=g.permission,
                granted_by=g.granted_by,
                created_at=g.created_at,
            )
            for g in grants
        ]

        return PaginatedShareGrantResponse(
            items=items,
            page=page,
            limit=limit,
            total=total,
            total_pages=total_pages,
        )

    @classmethod
    async def list_shared_with_me(
        cls,
        session: AsyncSession,
        user: User,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Resource]:
        """List resources that have been explicitly shared with the current user."""
        stmt = (
            select(Resource)
            .join(ResourceAccess, ResourceAccess.resource_id == Resource.resource_id)
            .options(
                selectinload(Resource.versions),
                selectinload(Resource.owner),
            )
            .where(ResourceAccess.user_id == user.user_id)
            .order_by(ResourceAccess.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        resources = (await session.scalars(stmt)).all()
        return list(resources)

    # ------------------------------------------------------------------
    # Folder sharing
    # ------------------------------------------------------------------

    @classmethod
    async def can_access_folder(
        cls,
        session: AsyncSession,
        user: User,
        folder: Folder,
        required_permission: str = "VIEW",
    ) -> bool:
        """Owner and admins always pass; otherwise a grant on the folder or any
        ancestor folder confers access (EDIT grant also satisfies VIEW)."""
        if folder.owner_id == user.user_id:
            return True
        if user.role and user.role.name == "Administrator":
            return True

        tree_perm = await folder_repository.get_tree_permission(
            session, folder.folder_id, user.user_id
        )
        if not tree_perm:
            return False
        if required_permission == "VIEW":
            return True
        return required_permission == "EDIT" and tree_perm == "EDIT"

    @classmethod
    async def grant_folder_access(
        cls,
        session: AsyncSession,
        actor: User,
        folder_id: uuid.UUID,
        grantee_email: str,
        permission: str = "VIEW",
        background_tasks: BackgroundTasks | None = None,
    ) -> FolderAccess:
        """Grant VIEW or EDIT access to a folder (and, by cascade, everything in
        its subtree). Only the folder owner or an administrator may share."""
        clean_permission = permission.upper().strip()
        if clean_permission not in ("VIEW", "EDIT"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid permission. Must be 'VIEW' or 'EDIT'.",
            )

        folder = await folder_repository.get_by_id(session, folder_id)
        if not folder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Folder not found.",
            )

        if folder.owner_id != actor.user_id and actor.role.name != "Administrator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the folder owner or an administrator can grant access.",
            )

        grantee = await user_repository.get_by_email(session, grantee_email)
        if not grantee:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Target user with email '{grantee_email}' was not found.",
            )

        if grantee.user_id == actor.user_id or grantee.user_id == folder.owner_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot share a folder with yourself.",
            )

        existing = await folder_repository.get_active_folder_grant(
            session, folder_id, grantee.user_id
        )
        if existing:
            existing.permission = clean_permission
            await session.commit()
            existing.user = grantee
            target_grant = existing
        else:
            grant = FolderAccess(
                folder_id=folder_id,
                user_id=grantee.user_id,
                permission=clean_permission,
                granted_by=actor.user_id,
            )
            session.add(grant)
            await session.commit()
            grant.user = grantee
            target_grant = grant

        if background_tasks is not None:
            background_tasks.add_task(
                EmailService.send_share_notification_email,
                email=grantee.email,
                grantee_name=grantee.full_name,
                sharer_name=actor.full_name,
                resource_title=f"📁 {folder.name}",
                permission=clean_permission,
                resource_id=folder.folder_id,
            )

        return target_grant

    @classmethod
    async def revoke_folder_access(
        cls,
        session: AsyncSession,
        actor: User,
        folder_id: uuid.UUID,
        access_id: uuid.UUID,
    ) -> None:
        """Revoke a folder share grant. Only the owner or an administrator may revoke."""
        folder = await folder_repository.get_by_id(session, folder_id)
        if not folder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Folder not found.",
            )

        if folder.owner_id != actor.user_id and actor.role.name != "Administrator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the folder owner or an administrator can revoke access.",
            )

        grant = await folder_repository.get_folder_grant_by_id(session, access_id)
        if not grant or grant.folder_id != folder_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Share grant not found for this folder.",
            )

        await session.delete(grant)
        await session.commit()

    @classmethod
    async def list_folder_grants(
        cls,
        session: AsyncSession,
        actor: User,
        folder_id: uuid.UUID,
        page: int = 1,
        limit: int = 50,
    ) -> PaginatedFolderShareGrantResponse:
        """List active share grants on a folder with pagination (Owner/Admin only)."""
        folder = await folder_repository.get_by_id(session, folder_id)
        if not folder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Folder not found.",
            )

        if folder.owner_id != actor.user_id and actor.role.name != "Administrator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only the folder owner or an administrator can view share grants.",
            )

        grants, total = await folder_repository.list_folder_grants(
            session, folder_id, page=page, limit=limit
        )
        total_pages = math.ceil(total / limit) if total > 0 else 0

        items = [
            FolderShareGrantRead(
                access_id=g.access_id,
                folder_id=g.folder_id,
                user_id=g.user_id,
                grantee_email=g.user.email if g.user else "",
                grantee_name=g.user.full_name if g.user else "",
                permission=g.permission,
                granted_by=g.granted_by,
                created_at=g.created_at,
            )
            for g in grants
        ]

        return PaginatedFolderShareGrantResponse(
            items=items,
            page=page,
            limit=limit,
            total=total,
            total_pages=total_pages,
        )

    @classmethod
    async def list_folders_shared_with_me(
        cls,
        session: AsyncSession,
        user: User,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Folder]:
        """List folders that have been explicitly shared with the current user."""
        folders = await folder_repository.list_folders_shared_with_user(
            session, user.user_id, limit=limit, offset=offset
        )
        return list(folders)

    @classmethod
    async def list_shared_by_me(
        cls,
        session: AsyncSession,
        user: User,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Resource]:
        """List the current user's resources that have been shared with at least one other user."""
        stmt = (
            select(Resource)
            .join(ResourceAccess, ResourceAccess.resource_id == Resource.resource_id)
            .options(
                selectinload(Resource.versions),
                selectinload(Resource.owner),
            )
            .where(ResourceAccess.granted_by == user.user_id)
            .order_by(Resource.updated_at.desc())
            .distinct()
            .limit(limit)
            .offset(offset)
        )
        resources = (await session.scalars(stmt)).all()
        return list(resources)
