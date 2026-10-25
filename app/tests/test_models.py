import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resource import Resource, ResourceVersion
from app.models.storage import StorageQuota
from app.models.user import Department, Role, User


@pytest.mark.asyncio
async def test_seeded_data_exists(db_session: AsyncSession):
    """Verify that roles, departments, academic years, and users are seeded correctly."""
    role_count = await db_session.scalar(select(func.count(Role.role_id)))
    assert role_count is not None and role_count >= 3

    dept_count = await db_session.scalar(select(func.count(Department.department_id)))
    assert dept_count is not None and dept_count >= 2

    user_count = await db_session.scalar(select(func.count(User.user_id)))
    assert user_count is not None and user_count >= 3

    # Verify quotas
    student = await db_session.scalar(select(User).where(User.email == "student@amu.edu"))
    assert student is not None
    student_quota = await db_session.get(StorageQuota, student.user_id)
    assert student_quota is not None
    assert student_quota.quota_bytes == 1_073_741_824  # 1 GiB

    teacher = await db_session.scalar(select(User).where(User.email == "teacher@amu.edu"))
    assert teacher is not None
    teacher_quota = await db_session.get(StorageQuota, teacher.user_id)
    assert teacher_quota is not None
    assert teacher_quota.quota_bytes == 16_106_127_360  # 15 GiB


@pytest.mark.asyncio
async def test_storage_quota_check_constraint_enforces_limit(db_session: AsyncSession):
    """EC-02.1: Verify check constraint prevents used_bytes + reserved_bytes exceeding quota."""
    # Create temporary user for constraint test
    test_user = User(
        email=f"constraint_test_{uuid.uuid4().hex[:8]}@amu.edu",
        full_name="Quota Tester",
        password_hash="fakehash",
        role_id=3,
        status="ACTIVE",
    )
    db_session.add(test_user)
    await db_session.flush()

    # Quota limit: 100 bytes. Attempting to set used=60 and reserved=50 (110 > 100)
    invalid_quota = StorageQuota(
        user_id=test_user.user_id,
        quota_bytes=100,
        used_bytes=60,
        reserved_bytes=50,
    )
    db_session.add(invalid_quota)

    with pytest.raises(IntegrityError):
        await db_session.commit()

    await db_session.rollback()


@pytest.mark.asyncio
async def test_role_restrict_foreign_key_constraint(db_session: AsyncSession):
    """EC-02.2: Verify deleting a role assigned to users is blocked by RESTRICT."""
    student_role = await db_session.scalar(select(Role).where(Role.name == "Student"))
    assert student_role is not None

    await db_session.delete(student_role)
    with pytest.raises(IntegrityError):
        await db_session.commit()

    await db_session.rollback()


@pytest.mark.asyncio
async def test_resource_version_unique_constraint(db_session: AsyncSession):
    """EC-02.4: Verify (resource_id, version_number) unique constraint."""
    student = await db_session.scalar(select(User).where(User.email == "student@amu.edu"))
    assert student is not None

    res = Resource(
        owner_id=student.user_id,
        title="Version Test Resource",
        status="PRIVATE",
        visibility="PRIVATE",
    )
    db_session.add(res)
    await db_session.flush()

    v1 = ResourceVersion(
        resource_id=res.resource_id,
        uploaded_by=student.user_id,
        version_number=1,
        content_type="text/plain",
        size_bytes=10,
    )
    db_session.add(v1)
    await db_session.flush()

    # Attempt duplicate version 1 for same resource
    v1_dup = ResourceVersion(
        resource_id=res.resource_id,
        uploaded_by=student.user_id,
        version_number=1,
        content_type="text/plain",
        size_bytes=20,
    )
    db_session.add(v1_dup)

    with pytest.raises(IntegrityError):
        await db_session.commit()

    await db_session.rollback()
