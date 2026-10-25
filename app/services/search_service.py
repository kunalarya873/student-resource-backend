import logging
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.search import SearchResponse, SearchResultItem

logger = logging.getLogger("amu_backend")


class SearchService:
    @classmethod
    async def search_resources(
        cls,
        session: AsyncSession,
        user: User,
        query: str | None = None,
        subject_id: int | None = None,
        department_id: int | None = None,
        academic_year_id: int | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> SearchResponse:
        """
        PostgreSQL Full-Text Search with GIN indexing, prefix token expansion,
        wildcard fallback, and zero-leakage security predicate.
        Filters by query, subject, department, academic year, and pagination.
        """
        page = max(1, page)
        limit = max(1, min(limit, 100))
        offset = (page - 1) * limit

        clean_q = query.strip() if query and query.strip() else None
        q_is_null = clean_q is None
        q_param = clean_q if clean_q is not None else ""
        like_q = f"%{clean_q}%" if clean_q is not None else ""

        # Extract alphanumeric word tokens for prefix matching (e.g. 'pi' -> 'pi:*')
        tokens = re.findall(r"\w+", clean_q) if clean_q else []
        if tokens:
            prefix_q = " & ".join(f"{t}:*" for t in tokens)
            has_prefix_q = True
        else:
            prefix_q = ""
            has_prefix_q = False

        is_admin = bool(user.role and user.role.name == "Administrator")

        sql = """
            SELECT
                r.resource_id,
                r.title,
                r.description,
                s.code as subject_code,
                s.name as subject_name,
                u.full_name as owner_name,
                r.visibility,
                r.status,
                CASE
                    WHEN CAST(:q_is_null AS BOOLEAN) = FALSE THEN (
                        ts_rank(to_tsvector('english', r.title || ' ' || coalesce(r.description, '')), plainto_tsquery('english', CAST(:q AS TEXT))) * 2.0
                        + CASE WHEN CAST(:has_prefix_q AS BOOLEAN) = TRUE AND to_tsvector('english', r.title || ' ' || coalesce(r.description, '')) @@ to_tsquery('english', CAST(:prefix_q AS TEXT)) THEN 1.0 ELSE 0.0 END
                        + CASE WHEN r.title ILIKE CAST(:like_q AS TEXT) THEN 1.5 ELSE 0.0 END
                    )
                    ELSE 0.0
                END as rank,
                r.created_at,
                count(*) OVER() AS total_count
            FROM resources r
            LEFT JOIN subjects s ON r.subject_id = s.subject_id
            JOIN users u ON r.owner_id = u.user_id
            WHERE
                (
                    CAST(:q_is_null AS BOOLEAN) = TRUE
                    OR to_tsvector('english', r.title || ' ' || coalesce(r.description, '')) @@ plainto_tsquery('english', CAST(:q AS TEXT))
                    OR (CAST(:has_prefix_q AS BOOLEAN) = TRUE AND to_tsvector('english', r.title || ' ' || coalesce(r.description, '')) @@ to_tsquery('english', CAST(:prefix_q AS TEXT)))
                    OR r.title ILIKE CAST(:like_q AS TEXT)
                    OR coalesce(r.description, '') ILIKE CAST(:like_q AS TEXT)
                )
                AND (CAST(:subject_id AS INTEGER) IS NULL OR r.subject_id = CAST(:subject_id AS INTEGER))
                AND (CAST(:department_id AS INTEGER) IS NULL OR s.department_id = CAST(:department_id AS INTEGER))
                AND (CAST(:academic_year_id AS INTEGER) IS NULL OR r.academic_year_id = CAST(:academic_year_id AS INTEGER))
                -- Mandatory Security Predicate (Rule 02: Zero-Leakage Policy)
                AND (
                    -- 1. Caller owns the resource (can see own private & draft items)
                    r.owner_id = CAST(:user_id AS UUID)
                    -- 2. System Administrator has full oversight
                    OR CAST(:is_admin AS BOOLEAN) = TRUE
                    -- 3. Approved institutional content: PUBLIC or within caller's department
                    OR (
                        r.status = 'APPROVED'
                        AND (
                            r.visibility = 'PUBLIC'
                            OR (r.visibility = 'DEPARTMENT' AND (s.department_id = CAST(:user_dept_id AS INTEGER) OR u.department_id = CAST(:user_dept_id AS INTEGER)))
                        )
                    )
                    -- 4. Explicitly shared with caller via granular ResourceAccess
                    OR r.resource_id IN (
                        SELECT resource_id FROM resource_access WHERE user_id = CAST(:user_id AS UUID)
                    )
                )
            ORDER BY rank DESC, r.created_at DESC
            LIMIT CAST(:limit AS INTEGER) OFFSET CAST(:offset AS INTEGER);
        """

        params = {
            "q": q_param,
            "q_is_null": q_is_null,
            "prefix_q": prefix_q,
            "has_prefix_q": has_prefix_q,
            "like_q": like_q,
            "subject_id": subject_id,
            "department_id": department_id,
            "academic_year_id": academic_year_id,
            "user_id": user.user_id,
            "user_dept_id": user.department_id,
            "is_admin": is_admin,
            "limit": limit,
            "offset": offset,
        }

        result = await session.execute(text(sql), params)
        rows = result.fetchall()

        items: list[SearchResultItem] = []
        total = 0
        for r in rows:
            total = r[10]
            items.append(
                SearchResultItem(
                    resource_id=r[0],
                    title=r[1],
                    description=r[2],
                    subject_code=r[3],
                    subject_name=r[4],
                    owner_name=r[5],
                    visibility=r[6],
                    status=r[7],
                    rank=float(r[8] or 0.0),
                    created_at=r[9],
                )
            )

        return SearchResponse(
            total_count=total,
            page=page,
            limit=limit,
            results=items,
        )
