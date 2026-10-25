import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import require_role
from app.core.security import create_access_token, get_password_hash
from app.main import app
from app.models.user import User


@pytest.mark.asyncio
async def test_login_success_student(async_client: AsyncClient):
    """Test successful student login issuing JWT tokens."""
    res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "student@amu.edu", "password": "amu_demo_2026!"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_invalid_password(async_client: AsyncClient):
    """Test login failure on incorrect password."""
    res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "student@amu.edu", "password": "wrong_password"},
    )
    assert res.status_code == 401
    assert "Invalid email or password" in res.json()["detail"]


@pytest.mark.asyncio
async def test_login_unknown_email(async_client: AsyncClient):
    """Test login failure on non-existent account."""
    res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "nonexistent@amu.edu", "password": "amu_demo_2026!"},
    )
    assert res.status_code == 401
    assert "Invalid email or password" in res.json()["detail"]


@pytest.mark.asyncio
async def test_get_my_profile_with_quota(async_client: AsyncClient):
    """Test /api/v1/auth/me returns profile and quota."""
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "student@amu.edu", "password": "amu_demo_2026!"},
    )
    token = login_res.json()["access_token"]

    res = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["email"] == "student@amu.edu"
    assert data["role_name"] == "Student"
    assert data["quota"] is not None
    assert data["quota"]["quota_bytes"] == 1_073_741_824  # 1 GiB
    assert (
        data["quota"]["available_bytes"]
        == data["quota"]["quota_bytes"]
        - data["quota"]["used_bytes"]
        - data["quota"]["reserved_bytes"]
    )


@pytest.mark.asyncio
async def test_refresh_token_rotation(async_client: AsyncClient):
    """Test issuing a new access token via refresh token."""
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "teacher@amu.edu", "password": "amu_demo_2026!"},
    )
    refresh_token = login_res.json()["refresh_token"]

    res = await async_client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert "refresh_token" in data


@pytest.mark.asyncio
async def test_ec03_1_refresh_token_rejected_on_access_endpoint(
    async_client: AsyncClient,
):
    """EC-03.1: Verify refresh token cannot be used directly as an access token."""
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "student@amu.edu", "password": "amu_demo_2026!"},
    )
    refresh_token = login_res.json()["refresh_token"]

    res = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {refresh_token}"},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_ec03_2_expired_token_rejected(async_client: AsyncClient):
    """EC-03.2: Verify expired access token is rejected."""
    expired_token = create_access_token(
        subject=uuid.uuid4(),
        role="Student",
        expires_delta=timedelta(minutes=-5),
    )
    res = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_ec03_3_rbac_role_enforcement(async_client: AsyncClient):
    """EC-03.3: Verify role guard blocks student from accessing teacher/admin routes."""

    # Register temporary guarded route on app for testing role guard
    @app.get(
        "/api/v1/test/teacher-only",
        dependencies=[
            pytest.importorskip("fastapi").Depends(require_role(["Teacher", "Administrator"]))
        ],
    )
    async def teacher_only_endpoint():
        return {"authorized": True}

    # Student login
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "student@amu.edu", "password": "amu_demo_2026!"},
    )
    student_token = login_res.json()["access_token"]

    res = await async_client.get(
        "/api/v1/test/teacher-only",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert res.status_code == 403
    assert "Operation not permitted" in res.json()["detail"]

    # Teacher login
    teacher_login = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "teacher@amu.edu", "password": "amu_demo_2026!"},
    )
    teacher_token = teacher_login.json()["access_token"]

    res_teacher = await async_client.get(
        "/api/v1/test/teacher-only",
        headers={"Authorization": f"Bearer {teacher_token}"},
    )
    assert res_teacher.status_code == 200
    assert res_teacher.json()["authorized"] is True


@pytest.mark.asyncio
async def test_ec03_4_suspended_user_rejected(async_client: AsyncClient, db_session: AsyncSession):
    """EC-03.4: Verify suspended user cannot login and their active tokens are rejected."""
    # Create suspended user
    suspended_user = User(
        email=f"suspended_{uuid.uuid4().hex[:6]}@amu.edu",
        full_name="Suspended Student",
        password_hash=get_password_hash("amu_demo_2026!"),
        role_id=3,
        status="SUSPENDED",
    )
    db_session.add(suspended_user)
    await db_session.flush()

    # Attempt login
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": suspended_user.email, "password": "amu_demo_2026!"},
    )
    assert login_res.status_code == 403
    assert "suspended or inactive" in login_res.json()["detail"]

    # Forged valid token for suspended user
    token = create_access_token(subject=suspended_user.user_id, role="Student")
    res = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_update_user_profile(async_client: AsyncClient):
    """Test PATCH /api/v1/users/me updates name."""
    login_res = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "student@amu.edu", "password": "amu_demo_2026!"},
    )
    token = login_res.json()["access_token"]

    update_res = await async_client.patch(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"full_name": "Fatima Khan (Updated)"},
    )
    assert update_res.status_code == 200
    assert update_res.json()["full_name"] == "Fatima Khan (Updated)"
