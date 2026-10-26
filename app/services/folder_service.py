import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.folder import Folder
from app.models.resource import Resource
from app.models.user import User
from app.repositories.folder_repository import folder_repository
from app.schemas.folder import (
    BreadcrumbItem,
    FolderContentsResponse,
    FolderRead,
    FolderResourceItem,
)
from app.services.quota_service import QuotaService


class FolderService:
    @staticmethod
    async def get_breadcrumbs(session: AsyncSession, folder_id: uuid.UUID) -> list[BreadcrumbItem]:
        """Recursive CTE to construct the path breadcrumbs from root to current folder."""
        rows = await folder_repository.get_breadcrumbs_raw(session, folder_id)
        return [BreadcrumbItem(folder_id=r[0], name=r[1]) for r in rows]

    @staticmethod
    async def is_descendant(
        session: AsyncSession, target_parent_id: uuid.UUID, folder_id: uuid.UUID
    ) -> bool:
        """Checks if target_parent_id is folder_id itself or any of its descendants (preventing DAG cycles)."""
        return await folder_repository.is_descendant(session, target_parent_id, folder_id)

    @classmethod
    async def create_folder(
        cls,
        session: AsyncSession,
        user: User,
        name: str,
        parent_folder_id: uuid.UUID | None = None,
    ) -> Folder:
        if parent_folder_id:
            parent = await folder_repository.get_by_id(session, parent_folder_id)
            if not parent:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Parent folder not found",
                )
            if parent.owner_id != user.user_id and user.role.name != "Administrator":
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Cannot create subfolder in another user's folder",
                )

        folder = Folder(
            name=name.strip(),
            parent_folder_id=parent_folder_id,
            owner_id=user.user_id,
        )
        await folder_repository.create(session, folder)
        await session.commit()
        await session.refresh(folder)
        return folder

    @classmethod
    async def list_folders(
        cls,
        session: AsyncSession,
        user: User,
        parent_id: uuid.UUID | None = None,
        q: str | None = None,
        page: int = 1,
        limit: int = 50,
        sort_by: str = "name",
        order: str = "asc",
    ) -> tuple[list[Folder], int, int]:
        page = max(1, page)
        limit = max(1, min(limit, 100))
        offset = (page - 1) * limit

        count_stmt = select(func.count(Folder.folder_id))
        stmt = select(Folder)
        if user.role.name != "Administrator":
            count_stmt = count_stmt.where(Folder.owner_id == user.user_id)
            stmt = stmt.where(Folder.owner_id == user.user_id)

        if parent_id is None:
            count_stmt = count_stmt.where(Folder.parent_folder_id.is_(None))
            stmt = stmt.where(Folder.parent_folder_id.is_(None))
        else:
            count_stmt = count_stmt.where(Folder.parent_folder_id == parent_id)
            stmt = stmt.where(Folder.parent_folder_id == parent_id)

        if q and q.strip():
            clean_q = q.strip()
            count_stmt = count_stmt.where(Folder.name.ilike(f"%{clean_q}%"))
            stmt = stmt.where(Folder.name.ilike(f"%{clean_q}%"))

        total = await session.scalar(count_stmt) or 0
        total_pages = (total + limit - 1) // limit if total > 0 else 0

        # Sorting
        sort_column = Folder.name
        if sort_by == "created_at":
            sort_column = Folder.created_at
        elif sort_by == "updated_at":
            sort_column = Folder.updated_at

        stmt = (
            stmt.order_by(sort_column.desc())
            if order == "desc"
            else stmt.order_by(sort_column.asc())
        )
        stmt = stmt.limit(limit).offset(offset)

        result = await session.scalars(stmt)
        return list(result.all()), total, total_pages

    @classmethod
    async def get_folder(cls, session: AsyncSession, user: User, folder_id: uuid.UUID) -> Folder:
        folder = await folder_repository.get_by_id(session, folder_id)
        if not folder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Folder not found",
            )
        if folder.owner_id != user.user_id and user.role.name != "Administrator":
            # Access may still be inherited via a share grant on this folder or
            # any of its ancestors (full folder sharing cascades down a subtree).
            tree_perm = await folder_repository.get_tree_permission(
                session, folder_id, user.user_id
            )
            if not tree_perm:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not have access to this folder",
                )
        return folder

    @classmethod
    async def get_folder_contents(
        cls,
        session: AsyncSession,
        user: User,
        folder_id: uuid.UUID,
        q: str | None = None,
        page: int = 1,
        limit: int = 50,
        sort_by: str = "name",
        order: str = "asc",
    ) -> FolderContentsResponse:
        folder = await cls.get_folder(session, user, folder_id)
        breadcrumbs = await cls.get_breadcrumbs(session, folder_id)

        # A grantee should only see the breadcrumb trail from the folder that was
        # shared with them downward — not their sharer's ancestor folder names.
        if folder.owner_id != user.user_id and user.role.name != "Administrator":
            shared = await folder_repository.list_folders_shared_with_user(
                session, user.user_id, limit=1000, offset=0
            )
            shared_ids = {f.folder_id for f in shared}
            for i, crumb in enumerate(breadcrumbs):
                if crumb.folder_id in shared_ids:
                    breadcrumbs = breadcrumbs[i:]
                    break

        page = max(1, page)
        limit = max(1, min(limit, 100))
        offset = (page - 1) * limit
        clean_q = q.strip() if q and q.strip() else None

        # 1. Subfolders query & count
        sub_count_stmt = select(func.count(Folder.folder_id)).where(
            Folder.parent_folder_id == folder_id
        )
        subfolders_stmt = select(Folder).where(Folder.parent_folder_id == folder_id)

        if clean_q:
            sub_count_stmt = sub_count_stmt.where(Folder.name.ilike(f"%{clean_q}%"))
            subfolders_stmt = subfolders_stmt.where(Folder.name.ilike(f"%{clean_q}%"))

        total_subfolders = await session.scalar(sub_count_stmt) or 0

        # Subfolders sorting
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

        # 2. Child resources query & count
        res_count_stmt = select(func.count(Resource.resource_id)).where(
            Resource.folder_id == folder_id
        )
        resources_stmt = select(Resource).where(Resource.folder_id == folder_id)

        if clean_q:
            q_filter = or_(
                Resource.title.ilike(f"%{clean_q}%"),
                Resource.description.ilike(f"%{clean_q}%"),
            )
            res_count_stmt = res_count_stmt.where(q_filter)
            resources_stmt = resources_stmt.where(q_filter)

        total_resources = await session.scalar(res_count_stmt) or 0

        # Resources sorting
        res_sort_col = Resource.created_at
        if sort_by == "name" or sort_by == "title":
            res_sort_col = Resource.title
        elif sort_by == "updated_at":
            res_sort_col = Resource.updated_at

        resources_stmt = (
            resources_stmt.order_by(res_sort_col.desc())
            if order == "desc"
            else resources_stmt.order_by(res_sort_col.asc())
        )
        resources_stmt = resources_stmt.limit(limit).offset(offset)
        resources = (await session.scalars(resources_stmt)).all()

        return FolderContentsResponse(
            folder=FolderRead.model_validate(folder),
            breadcrumbs=breadcrumbs,
            subfolders=[FolderRead.model_validate(f) for f in subfolders],
            resources=[FolderResourceItem.model_validate(r) for r in resources],
            page=page,
            limit=limit,
            total_subfolders=total_subfolders,
            total_resources=total_resources,
        )

    @classmethod
    async def move_or_rename_folder(
        cls,
        session: AsyncSession,
        user: User,
        folder_id: uuid.UUID,
        new_name: str | None = None,
        new_parent_id: uuid.UUID | None = None,
        reparent: bool = False,
    ) -> Folder:
        folder = await folder_repository.get_by_id(session, folder_id)
        if not folder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Folder not found",
            )
        if folder.owner_id != user.user_id and user.role.name != "Administrator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied to modify this folder",
            )

        # Handle moving. `reparent` lets an explicit null move the folder to the
        # root; a non-null target still moves even without the flag (back-compat).
        wants_move = reparent or new_parent_id is not None
        if wants_move and new_parent_id != folder.parent_folder_id:
            if new_parent_id is not None:
                # Prevent DAG cycles
                if await cls.is_descendant(session, new_parent_id, folder_id):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Cannot move folder into itself or one of its descendants (circular hierarchy).",
                    )
                parent = await folder_repository.get_by_id(session, new_parent_id)
                if not parent:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail="Target parent folder not found",
                    )
                if parent.owner_id != user.user_id and user.role.name != "Administrator":
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Target parent folder is owned by another user",
                    )
                folder.parent_folder_id = new_parent_id
            else:
                # Move to the drive root
                folder.parent_folder_id = None

        # Handle renaming
        if new_name is not None and new_name.strip():
            folder.name = new_name.strip()

        await session.commit()
        await session.refresh(folder)
        return folder

    @classmethod
    async def delete_folder(
        cls,
        session: AsyncSession,
        user: User,
        folder_id: uuid.UUID,
        recursive: bool = False,
    ) -> None:
        folder = await folder_repository.get_by_id(session, folder_id)
        if not folder:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Folder not found",
            )
        if folder.owner_id != user.user_id and user.role.name != "Administrator":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied to delete this folder",
            )

        if not recursive:
            # SD-09: Verify folder is empty before deleting
            (
                child_folders,
                child_resources,
            ) = await folder_repository.count_children_and_resources(session, folder_id)

            if child_folders > 0 or child_resources > 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Folder is not empty. Remove all subfolders and resources before deleting.",
                )

            await session.delete(folder)
            await session.commit()
            return

        # Recursive cascade deletion
        descendant_ids = await folder_repository.get_all_descendant_ids(session, folder_id)

        # 1. Fetch all resources in the entire folder tree with their versions
        res_stmt = (
            select(Resource)
            .options(selectinload(Resource.versions))
            .where(Resource.folder_id.in_(descendant_ids))
        )
        resources = (await session.scalars(res_stmt)).all()

        # 2. Release storage quota for all file assets across affected owners
        quota_releases: dict[uuid.UUID, int] = {}
        for r in resources:
            for v in r.versions:
                if v.size_bytes > 0:
                    quota_releases[r.owner_id] = quota_releases.get(r.owner_id, 0) + v.size_bytes

        for owner_id, total_bytes in quota_releases.items():
            await QuotaService.release_quota(session, owner_id, total_bytes)

        # 3. Delete resources
        for r in resources:
            await session.delete(r)

        # 4. Delete folders bottom-up (descendant_ids is ordered by depth DESC)
        for fid in descendant_ids:
            target = await folder_repository.get_by_id(session, fid)
            if target:
                await session.delete(target)

        await session.commit()
