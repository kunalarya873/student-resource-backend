import uuid
from collections.abc import Sequence

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.folder import Folder
from app.models.resource import Resource
from app.repositories.base import BaseRepository


class FolderRepository(BaseRepository[Folder]):
    """Data access repository for Folder hierarchy and DAG operations."""

    def __init__(self) -> None:
        super().__init__(Folder)

    async def get_breadcrumbs_raw(
        self,
        session: AsyncSession,
        folder_id: uuid.UUID,
    ) -> list[tuple[uuid.UUID, str]]:
        """Execute recursive CTE to traverse ancestors up to the root folder."""
        query = text("""
            WITH RECURSIVE folder_path AS (
                SELECT folder_id, name, parent_folder_id, 1 as depth
                FROM folders
                WHERE folder_id = :folder_id
                UNION ALL
                SELECT f.folder_id, f.name, f.parent_folder_id, fp.depth + 1
                FROM folders f
                JOIN folder_path fp ON f.folder_id = fp.parent_folder_id
            )
            SELECT folder_id, name FROM folder_path ORDER BY depth DESC;
        """)
        result = await session.execute(query, {"folder_id": str(folder_id)})
        return [(uuid.UUID(str(r[0])), str(r[1])) for r in result.fetchall()]

    async def is_descendant(
        self,
        session: AsyncSession,
        target_parent_id: uuid.UUID,
        folder_id: uuid.UUID,
    ) -> bool:
        """Check if target_parent_id is folder_id itself or any recursive child."""
        if target_parent_id == folder_id:
            return True

        query = text("""
            WITH RECURSIVE descendants AS (
                SELECT folder_id, parent_folder_id
                FROM folders
                WHERE parent_folder_id = :folder_id
                UNION ALL
                SELECT f.folder_id, f.parent_folder_id
                FROM folders f
                JOIN descendants d ON f.parent_folder_id = d.folder_id
            )
            SELECT 1 FROM descendants WHERE folder_id = :target_parent_id LIMIT 1;
        """)
        result = await session.execute(
            query,
            {"folder_id": str(folder_id), "target_parent_id": str(target_parent_id)},
        )
        return result.scalar() is not None

    async def list_by_owner_and_parent(
        self,
        session: AsyncSession,
        owner_id: uuid.UUID | None,
        parent_id: uuid.UUID | None,
        is_admin: bool = False,
    ) -> Sequence[Folder]:
        """Fetch immediate child folders scoped by parent and owner."""
        stmt = select(Folder)
        if not is_admin and owner_id:
            stmt = stmt.where(Folder.owner_id == owner_id)

        if parent_id is None:
            stmt = stmt.where(Folder.parent_folder_id.is_(None))
        else:
            stmt = stmt.where(Folder.parent_folder_id == parent_id)

        stmt = stmt.order_by(Folder.name.asc())
        result = await session.scalars(stmt)
        return result.all()

    async def count_children_and_resources(
        self,
        session: AsyncSession,
        folder_id: uuid.UUID,
    ) -> tuple[int, int]:
        """Count direct subfolders and direct resources inside a folder."""
        subfolders_stmt = select(func.count(Folder.folder_id)).where(
            Folder.parent_folder_id == folder_id
        )
        subfolders_count = await session.scalar(subfolders_stmt) or 0

        resources_stmt = select(func.count(Resource.resource_id)).where(
            Resource.folder_id == folder_id
        )
        resources_count = await session.scalar(resources_stmt) or 0

        return subfolders_count, resources_count

    async def get_all_descendant_ids(
        self,
        session: AsyncSession,
        folder_id: uuid.UUID,
    ) -> list[uuid.UUID]:
        """Fetch all descendant folder IDs (including target folder_id itself) via recursive CTE ordered bottom-up."""
        query = text("""
            WITH RECURSIVE tree AS (
                SELECT folder_id, 1 as depth
                FROM folders WHERE folder_id = :folder_id
                UNION ALL
                SELECT f.folder_id, t.depth + 1
                FROM folders f JOIN tree t ON f.parent_folder_id = t.folder_id
            )
            SELECT folder_id FROM tree ORDER BY depth DESC;
        """)
        result = await session.execute(query, {"folder_id": str(folder_id)})
        return [uuid.UUID(str(r[0])) for r in result.fetchall()]


folder_repository = FolderRepository()
