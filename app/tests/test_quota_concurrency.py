import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.database import get_db_session
from app.main import app
from app.models.storage import StorageQuota, UploadReservation
from app.models.user import User
from app.tests.conftest import get_auth_token


@pytest.mark.asyncio
async def test_get_storage_quota_summary(async_client: AsyncClient):
    """Verify authenticated student can retrieve their 1 GiB quota breakdown."""
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    res = await async_client.get("/api/v1/storage/quota", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["quota_bytes"] == settings.STUDENT_DEFAULT_QUOTA_BYTES  # 1 GiB
    assert data["used_bytes"] >= 0
    assert data["reserved_bytes"] >= 0
    assert data["available_bytes"] <= data["quota_bytes"]
    assert 0.0 <= data["percent_used"] <= 100.0


@pytest.mark.asyncio
async def test_end_to_end_upload_pipeline(async_client: AsyncClient):
    """
    Full upload lifecycle:
    1. Initiate upload reservation (get presigned S3 PUT URL)
    2. Upload payload directly to S3 via presigned URL
    3. Complete upload (verifies S3 object, updates used_bytes, creates StorageObject)
    4. Generate presigned download URL and verify content
    """
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    payload_data = b"Hello AMU Student Resource Management System - Object Storage Pipeline!"
    payload_size = len(payload_data)

    # 1. Initiate upload
    init_res = await async_client.post(
        "/api/v1/uploads/initiate",
        headers=headers,
        json={
            "filename": "e2e_lecture_note.txt",
            "content_type": "text/plain",
            "size_bytes": payload_size,
        },
    )
    assert init_res.status_code == 201
    init_data = init_res.json()
    reservation_id = init_data["reservation_id"]
    upload_url = init_data["upload_url"]
    assert "upload_url" in init_data
    assert init_data["reserved_bytes"] == payload_size

    # 2. Upload payload to MinIO via presigned PUT URL
    async with httpx.AsyncClient() as s3_http:
        put_res = await s3_http.put(
            upload_url,
            content=payload_data,
            headers={"Content-Type": "text/plain"},
        )
        assert put_res.status_code in (200, 204)

    # 3. Complete upload
    complete_res = await async_client.post(
        "/api/v1/uploads/complete",
        headers=headers,
        json={"reservation_id": reservation_id},
    )
    assert complete_res.status_code == 200
    complete_data = complete_res.json()
    assert complete_data["verification_status"] == "VERIFIED"
    assert complete_data["byte_size"] == payload_size
    storage_obj_id = complete_data["storage_object_id"]

    # 4. Generate presigned download URL
    down_res = await async_client.get(
        f"/api/v1/storage/objects/{storage_obj_id}/download-url",
        headers=headers,
    )
    assert down_res.status_code == 200
    down_data = down_res.json()
    assert "download_url" in down_data

    # 5. Fetch content using download URL
    async with httpx.AsyncClient() as s3_http:
        get_res = await s3_http.get(down_data["download_url"])
        assert get_res.status_code == 200
        assert get_res.content == payload_data


@pytest.mark.asyncio
async def test_ec05_1_concurrent_quota_race_attack(db_session: AsyncSession):
    """
    EC-05.1: Concurrent Quota Race Attack
    Student has exactly 200 MB remaining quota.
    Fires 5 simultaneous 100 MB upload requests concurrently.
    Expected: Exactly 2 succeed (reserving 200 MB), 3 fail with HTTP 413.
    Zero bytes over-allocated.
    """
    # 1. Setup isolated user quota
    student = await db_session.scalar(select(User).where(User.email == "student@amu.edu"))
    assert student is not None

    stmt = select(StorageQuota).where(StorageQuota.user_id == student.user_id).with_for_update()
    quota = await db_session.scalar(stmt)
    assert quota is not None

    chunk_100mb = 100 * 1024 * 1024  # 100 MB
    quota.quota_bytes = 200 * 1024 * 1024  # Total quota 200 MB
    quota.used_bytes = 0
    quota.reserved_bytes = 0
    await db_session.commit()

    # 2. Build multi-connection test client for real concurrency
    test_engine = create_async_engine(settings.ASYNC_DATABASE_URI, poolclass=NullPool)
    session_factory = async_sessionmaker(
        bind=test_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def get_test_db():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db_session] = get_test_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        token = await get_auth_token(client, "student@amu.edu")
        headers = {"Authorization": f"Bearer {token}"}

        async def attempt_reservation(idx: int):
            return await client.post(
                "/api/v1/uploads/initiate",
                headers=headers,
                json={
                    "filename": f"race_file_{idx}.bin",
                    "content_type": "application/octet-stream",
                    "size_bytes": chunk_100mb,
                },
            )

        # Fire 5 concurrent reservation requests simultaneously
        responses = await asyncio.gather(*(attempt_reservation(i) for i in range(5)))

    app.dependency_overrides.clear()
    await test_engine.dispose()

    # 3. Analyze responses
    status_codes = [r.status_code for r in responses]
    success_count = status_codes.count(201)
    rejected_count = status_codes.count(413)

    assert success_count == 2, f"Expected exactly 2 successes, got: {status_codes}"
    assert rejected_count == 3, f"Expected exactly 3 rejections, got: {status_codes}"

    # 4. Verify database state
    await db_session.refresh(quota)
    assert quota.reserved_bytes == 2 * chunk_100mb
    assert quota.reserved_bytes <= quota.quota_bytes

    # Clean up reservations created in this test
    res_list = (
        await db_session.scalars(
            select(UploadReservation).where(
                UploadReservation.user_id == student.user_id,
                UploadReservation.status == "PENDING",
            )
        )
    ).all()
    for r in res_list:
        r.status = "CANCELLED"
    quota.reserved_bytes = 0
    quota.quota_bytes = settings.STUDENT_DEFAULT_QUOTA_BYTES
    await db_session.commit()


@pytest.mark.asyncio
async def test_ec05_2_fake_upload_completion_rejected(async_client: AsyncClient):
    """EC-05.2: Calling /uploads/complete without uploading file to S3 must fail with 400."""
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Initiate
    init_res = await async_client.post(
        "/api/v1/uploads/initiate",
        headers=headers,
        json={
            "filename": "ghost_file.bin",
            "content_type": "application/octet-stream",
            "size_bytes": 1024,
        },
    )
    assert init_res.status_code == 201
    res_id = init_res.json()["reservation_id"]

    # 2. Complete directly without S3 upload
    complete_res = await async_client.post(
        "/api/v1/uploads/complete",
        headers=headers,
        json={"reservation_id": res_id},
    )
    assert complete_res.status_code == 400
    assert "not found in storage" in complete_res.json()["detail"]


@pytest.mark.asyncio
async def test_ec05_3_size_discrepancy_attack(async_client: AsyncClient, db_session: AsyncSession):
    """
    EC-05.3: Size Discrepancy Attack
    Client reserves 500 bytes, but uploads a payload exceeding remaining quota.
    Server detects actual > reserved and enforces boundary.
    """
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    student = await db_session.scalar(select(User).where(User.email == "student@amu.edu"))
    assert student is not None
    quota = await db_session.scalar(
        select(StorageQuota).where(StorageQuota.user_id == student.user_id).with_for_update()
    )
    assert quota is not None
    # Set quota so that only 1000 bytes are available
    quota.quota_bytes = 1000
    quota.used_bytes = 0
    quota.reserved_bytes = 0
    await db_session.commit()

    try:
        # Reserve 200 bytes
        init_res = await async_client.post(
            "/api/v1/uploads/initiate",
            headers=headers,
            json={
                "filename": "discrepancy.bin",
                "content_type": "application/octet-stream",
                "size_bytes": 200,
            },
        )
        assert init_res.status_code == 201
        init_data = init_res.json()
        reservation_id = init_data["reservation_id"]
        upload_url = init_data["upload_url"]

        # Upload 2000 bytes (violating the 1000 byte quota)
        large_payload = b"X" * 2000
        async with httpx.AsyncClient() as s3_http:
            await s3_http.put(
                upload_url,
                content=large_payload,
                headers={"Content-Type": "application/octet-stream"},
            )

        # Complete upload - should be rejected with 413
        complete_res = await async_client.post(
            "/api/v1/uploads/complete",
            headers=headers,
            json={"reservation_id": reservation_id},
        )
        assert complete_res.status_code == 413
        assert "exceeds" in complete_res.json()["detail"]
    finally:
        # Restore quota
        quota.quota_bytes = settings.STUDENT_DEFAULT_QUOTA_BYTES
        quota.reserved_bytes = 0
        quota.used_bytes = 0
        await db_session.commit()


@pytest.mark.asyncio
async def test_ec05_4_reservation_abandonment_and_sweeper(
    async_client: AsyncClient, db_session: AsyncSession
):
    """
    EC-05.4: Abandoned upload reservations are swept and reserved_bytes released.
    """
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Initiate 50 MB reservation
    size_50mb = 50 * 1024 * 1024
    init_res = await async_client.post(
        "/api/v1/uploads/initiate",
        headers=headers,
        json={
            "filename": "abandoned.iso",
            "content_type": "application/octet-stream",
            "size_bytes": size_50mb,
        },
    )
    assert init_res.status_code == 201
    res_id = uuid.UUID(init_res.json()["reservation_id"])

    # 2. Simulate expiration by backdating expires_at
    reservation = await db_session.get(UploadReservation, res_id)
    assert reservation is not None
    reservation.expires_at = datetime.now(UTC) - timedelta(minutes=5)
    await db_session.commit()

    # 3. Call admin cleanup sweeper
    admin_token = await get_auth_token(async_client, "admin@amu.edu")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    sweep_res = await async_client.post("/api/v1/storage/cleanup-expired", headers=admin_headers)
    assert sweep_res.status_code == 200
    assert "Cleaned up" in sweep_res.json()["message"]

    # 4. Verify reservation is EXPIRED and quota restored
    await db_session.refresh(reservation)
    assert reservation.status == "EXPIRED"

    student = await db_session.scalar(select(User).where(User.email == "student@amu.edu"))
    assert student is not None
    quota = await db_session.scalar(
        select(StorageQuota).where(StorageQuota.user_id == student.user_id)
    )
    assert quota is not None
    assert quota.reserved_bytes == 0


@pytest.mark.asyncio
async def test_cancel_upload_reservation(async_client: AsyncClient, db_session: AsyncSession):
    """Test manual cancellation of a pending upload reservation."""
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    init_res = await async_client.post(
        "/api/v1/uploads/initiate",
        headers=headers,
        json={
            "filename": "cancel_me.pdf",
            "content_type": "application/pdf",
            "size_bytes": 1024 * 1024,
        },
    )
    assert init_res.status_code == 201
    res_id = init_res.json()["reservation_id"]

    # Cancel
    cancel_res = await async_client.post(f"/api/v1/uploads/{res_id}/cancel", headers=headers)
    assert cancel_res.status_code == 200
    assert "cancelled" in cancel_res.json()["message"]

    # Verify status in DB
    reservation = await db_session.get(UploadReservation, uuid.UUID(res_id))
    assert reservation is not None
    assert reservation.status == "CANCELLED"
