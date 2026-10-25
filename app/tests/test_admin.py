import pytest
from httpx import AsyncClient

from app.tests.conftest import get_auth_token


@pytest.mark.asyncio
async def test_ec09_1_teacher_cannot_access_admin_routes(async_client: AsyncClient):
    """
    EC-09.1: Teacher / Non-Admin Access to Admin Routes
    Teacher calls GET /api/v1/admin/reports/storage.
    Expected: HTTP 403 Forbidden.
    """
    token_teacher = await get_auth_token(async_client, "teacher@amu.edu")
    headers_teacher = {"Authorization": f"Bearer {token_teacher}"}

    res = await async_client.get("/api/v1/admin/reports/storage", headers=headers_teacher)
    assert res.status_code == 403
    assert "administrator" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_ec09_2_audit_logging_and_pii_masking(async_client: AsyncClient):
    """
    EC-09.2: Audit Logging & PII Masking
    Admin updates a student's quota. An immutable audit event is emitted.
    Audit log list endpoint masks actor email (e.g., a***n@amu.edu).
    """
    token_admin = await get_auth_token(async_client, "admin@amu.edu")
    headers_admin = {"Authorization": f"Bearer {token_admin}"}

    # Fetch student profile to get user_id
    token_student = await get_auth_token(async_client, "student@amu.edu")
    student_profile = (
        await async_client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {token_student}"},
        )
    ).json()
    student_id = student_profile["user_id"]

    original_quota = 1_073_741_824  # 1 GiB
    try:
        # Admin updates student quota to 6 GB
        new_quota = 6 * 1024 * 1024 * 1024
        patch_res = await async_client.patch(
            f"/api/v1/admin/users/{student_id}/quota",
            headers=headers_admin,
            json={"new_quota_bytes": new_quota},
        )
        assert patch_res.status_code == 200
        assert patch_res.json()["new_quota_bytes"] == new_quota

        # Query audit logs
        audit_res = await async_client.get(
            "/api/v1/admin/audit-logs",
            headers=headers_admin,
            params={"action": "UPDATE_USER_QUOTA"},
        )
        assert audit_res.status_code == 200
        logs = audit_res.json()
        assert len(logs) >= 1

        latest_event = logs[0]
        assert latest_event["action"] == "UPDATE_USER_QUOTA"
        assert latest_event["entity_type"] == "STORAGE_QUOTA"
        assert latest_event["entity_id"] == student_id
        # Rule 02: Verify email is masked
        assert latest_event["actor_email"] is not None
        assert "***" in latest_event["actor_email"]
    finally:
        # Restore baseline student quota
        await async_client.patch(
            f"/api/v1/admin/users/{student_id}/quota",
            headers=headers_admin,
            json={"new_quota_bytes": original_quota},
        )


@pytest.mark.asyncio
async def test_ec09_3_quota_reduction_below_used_rejected(async_client: AsyncClient):
    """
    EC-09.3: Quota Reduction Below Used Bytes
    Admin attempts to set user quota below current committed usage.
    Expected: HTTP 400 Bad Request.
    """
    token_admin = await get_auth_token(async_client, "admin@amu.edu")
    headers_admin = {"Authorization": f"Bearer {token_admin}"}

    token_student = await get_auth_token(async_client, "student@amu.edu")
    headers_student = {"Authorization": f"Bearer {token_student}"}

    student_profile = (await async_client.get("/api/v1/users/me", headers=headers_student)).json()
    student_id = student_profile["user_id"]

    # 1. Student reserves 50 MB
    init_res = await async_client.post(
        "/api/v1/uploads/initiate",
        headers=headers_student,
        json={
            "filename": "large_project.zip",
            "size_bytes": 50 * 1024 * 1024,
            "content_type": "application/zip",
        },
    )
    assert init_res.status_code == 201
    reservation_id = init_res.json()["reservation_id"]

    try:
        # 2. Admin attempts to reduce quota to 20 MB (below 50 MB committed usage)
        invalid_quota = 20 * 1024 * 1024
        res = await async_client.patch(
            f"/api/v1/admin/users/{student_id}/quota",
            headers=headers_admin,
            json={"new_quota_bytes": invalid_quota},
        )
        assert res.status_code == 400
        assert "cannot reduce" in res.json()["detail"].lower()
    finally:
        # Clean up reservation
        await async_client.delete(
            f"/api/v1/uploads/reservations/{reservation_id}",
            headers=headers_student,
        )


@pytest.mark.asyncio
async def test_get_storage_report(async_client: AsyncClient):
    """Test GET /api/v1/admin/reports/storage returns aggregated metrics."""
    token_admin = await get_auth_token(async_client, "admin@amu.edu")
    headers_admin = {"Authorization": f"Bearer {token_admin}"}

    res = await async_client.get("/api/v1/admin/reports/storage", headers=headers_admin)
    assert res.status_code == 200
    report = res.json()
    assert report["total_allocated_bytes"] > 0
    assert report["overall_utilization_percentage"] >= 0.0
    assert len(report["department_breakdown"]) >= 1

    dept_names = [d["department_name"] for d in report["department_breakdown"]]
    assert "Computer Science" in dept_names
