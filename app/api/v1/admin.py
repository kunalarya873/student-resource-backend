import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.dependencies import require_role
from app.models.storage import StorageQuota
from app.models.user import Department, User
from app.repositories.storage_repository import storage_repository
from app.schemas.admin import (
    AuditEventRead,
    DepartmentStorageSummary,
    SystemStorageReport,
    UserQuotaUpdate,
)
from app.services.audit_service import AuditService

router = APIRouter(prefix="/admin", tags=["Administration & Governance"])

# Enforce Administrator RBAC strictly on all routes (EC-09.1)
AdminUser = Annotated[User, Depends(require_role(["Administrator"]))]


@router.get(
    "/reports/storage",
    response_model=SystemStorageReport,
    status_code=status.HTTP_200_OK,
    summary="System-wide storage utilization report",
)
async def get_storage_report(
    admin: AdminUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SystemStorageReport:
    """
    Aggregates institutional storage consumption and generates departmental breakdown.
    Strictly protected by Administrator RBAC (Rule 02).
    """
    total_quota = (
        await session.scalar(select(func.coalesce(func.sum(StorageQuota.quota_bytes), 0)))
    ) or 0
    total_used = (
        await session.scalar(select(func.coalesce(func.sum(StorageQuota.used_bytes), 0)))
    ) or 0

    pct = round((total_used / total_quota * 100), 2) if total_quota > 0 else 0.0

    dept_stmt = (
        select(
            Department.department_id,
            Department.name.label("department_name"),
            func.count(User.user_id).label("total_users"),
            func.coalesce(func.sum(StorageQuota.quota_bytes), 0).label("total_quota_bytes"),
            func.coalesce(func.sum(StorageQuota.used_bytes), 0).label("total_used_bytes"),
        )
        .join(User, Department.department_id == User.department_id, isouter=True)
        .join(StorageQuota, User.user_id == StorageQuota.user_id, isouter=True)
        .group_by(Department.department_id, Department.name)
        .order_by(Department.name.asc())
    )
    dept_rows = (await session.execute(dept_stmt)).all()

    department_breakdown: list[DepartmentStorageSummary] = []
    for row in dept_rows:
        d_id = row[0]
        d_name = row[1]
        u_count = row[2]
        d_quota = row[3]
        d_used = row[4]
        d_pct = round((d_used / d_quota * 100), 2) if d_quota > 0 else 0.0

        department_breakdown.append(
            DepartmentStorageSummary(
                department_id=d_id,
                department_name=d_name,
                total_users=u_count,
                total_quota_bytes=d_quota,
                total_used_bytes=d_used,
                utilization_percentage=d_pct,
            )
        )

    return SystemStorageReport(
        total_allocated_bytes=total_quota,
        total_used_bytes=total_used,
        overall_utilization_percentage=pct,
        department_breakdown=department_breakdown,
    )


@router.patch(
    "/users/{user_id}/quota",
    status_code=status.HTTP_200_OK,
    summary="Update individual user storage quota",
)
async def update_user_quota(
    user_id: uuid.UUID,
    payload: UserQuotaUpdate,
    admin: AdminUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
):
    """
    Modifies an individual user's storage quota with boundary enforcement (EC-09.3)
    and immutable audit event tracking.
    """
    quota = await storage_repository.get_quota_by_user_id(session, user_id)
    if not quota:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Storage quota record not found for user.",
        )

    # EC-09.3: Quota Reduction Below Used Bytes check
    current_committed = quota.used_bytes + quota.reserved_bytes
    if payload.new_quota_bytes < current_committed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot reduce quota to {payload.new_quota_bytes} bytes: "
                f"user currently has {quota.used_bytes} bytes used and "
                f"{quota.reserved_bytes} bytes reserved (total: {current_committed} bytes)."
            ),
        )

    old_quota = quota.quota_bytes
    quota.quota_bytes = payload.new_quota_bytes

    # Log immutable audit event
    await AuditService.log_event(
        session=session,
        actor=admin,
        action="UPDATE_USER_QUOTA",
        entity_type="STORAGE_QUOTA",
        entity_id=user_id,
        details={
            "old_quota_bytes": old_quota,
            "new_quota_bytes": payload.new_quota_bytes,
        },
    )

    await session.commit()
    return {
        "message": "User storage quota updated successfully.",
        "user_id": str(user_id),
        "new_quota_bytes": quota.quota_bytes,
    }


@router.get(
    "/audit-logs",
    response_model=list[AuditEventRead],
    status_code=status.HTTP_200_OK,
    summary="List immutable system audit logs",
)
async def list_audit_logs(
    admin: AdminUser,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    action: Annotated[str | None, Query(description="Filter by action name")] = None,
    entity_type: Annotated[str | None, Query(description="Filter by entity type")] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Page limit")] = 50,
    offset: Annotated[int, Query(ge=0, description="Page offset")] = 0,
) -> list[AuditEventRead]:
    """
    Returns immutable audit logs with masked PII in compliance with Rule 02.
    """
    return await AuditService.list_audit_logs(
        session=session,
        action=action,
        entity_type=entity_type,
        limit=limit,
        offset=offset,
    )
