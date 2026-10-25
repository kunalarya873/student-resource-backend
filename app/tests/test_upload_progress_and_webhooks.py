import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.resource import Resource
from app.models.storage import StorageObject, StorageQuota, UploadReservation
from app.models.user import User
from app.tests.conftest import get_auth_token


@pytest.mark.asyncio
async def test_upload_progress_tracking_lifecycle(
    async_client: AsyncClient, db_session: AsyncSession
):
    """
    Test Phase 1 & in-flight progress reporting:
    1. Initiate upload reservation (status=PENDING, progress=0%).
    2. Check status via GET /uploads/{id}/status.
    3. Update progress to 45% and 80% via PATCH /uploads/{id}/progress.
    4. Verify live percentage, byte counts, and status=UPLOADING.
    """
    student_token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {student_token}"}

    # 1. Initiate upload reservation
    init_res = await async_client.post(
        "/api/v1/uploads/initiate",
        headers=headers,
        json={
            "filename": "thesis_chapter_1.pdf",
            "content_type": "application/pdf",
            "size_bytes": 1_000_000,
        },
    )
    assert init_res.status_code == 201
    init_data = init_res.json()
    reservation_id = init_data["reservation_id"]

    # 2. Check initial status
    stat_res = await async_client.get(
        f"/api/v1/uploads/{reservation_id}/status",
        headers=headers,
    )
    assert stat_res.status_code == 200
    stat_data = stat_res.json()
    assert stat_data["status"] == "PENDING"
    assert stat_data["progress_percent"] == 0
    assert stat_data["total_bytes"] == 1_000_000
    assert stat_data["resource_id"] is None

    # 3. Report intermediate progress (e.g. 45%)
    prog_res1 = await async_client.patch(
        f"/api/v1/uploads/{reservation_id}/progress",
        headers=headers,
        json={"progress_percent": 45, "bytes_uploaded": 450_000},
    )
    assert prog_res1.status_code == 200
    prog_data1 = prog_res1.json()
    assert prog_data1["status"] == "UPLOADING"
    assert prog_data1["progress_percent"] == 45
    assert prog_data1["bytes_uploaded"] == 450_000

    # 4. Report advanced progress (85%)
    prog_res2 = await async_client.patch(
        f"/api/v1/uploads/{reservation_id}/progress",
        headers=headers,
        json={"progress_percent": 85, "bytes_uploaded": 850_000},
    )
    assert prog_res2.status_code == 200
    assert prog_res2.json()["progress_percent"] == 85
    assert prog_res2.json()["bytes_uploaded"] == 850_000


@pytest.mark.asyncio
async def test_upload_status_access_control(async_client: AsyncClient, db_session: AsyncSession):
    """
    Ensure users cannot inspect or modify another user's upload progress.
    """
    student_token = await get_auth_token(async_client, "student@amu.edu")
    teacher_token = await get_auth_token(async_client, "teacher@amu.edu")

    headers_student = {"Authorization": f"Bearer {student_token}"}
    headers_teacher = {"Authorization": f"Bearer {teacher_token}"}

    # Student creates upload reservation
    init_res = await async_client.post(
        "/api/v1/uploads/initiate",
        headers=headers_student,
        json={
            "filename": "private_student_log.pdf",
            "content_type": "application/pdf",
            "size_bytes": 500_000,
        },
    )
    assert init_res.status_code == 201
    res_id = init_res.json()["reservation_id"]

    # Teacher (non-owner) tries to view status -> 403 Forbidden
    forbidden_stat = await async_client.get(
        f"/api/v1/uploads/{res_id}/status",
        headers=headers_teacher,
    )
    assert forbidden_stat.status_code == 403

    # Teacher tries to update progress -> 403 Forbidden
    forbidden_patch = await async_client.patch(
        f"/api/v1/uploads/{res_id}/progress",
        headers=headers_teacher,
        json={"progress_percent": 50},
    )
    assert forbidden_patch.status_code == 403


@pytest.mark.asyncio
async def test_supabase_storage_webhook_authentication(
    async_client: AsyncClient, db_session: AsyncSession
):
    """
    Verify security controls on the Supabase Webhook endpoint:
    - Missing secret -> 401 Unauthorized
    - Wrong secret -> 401 Unauthorized
    - Valid secret -> 200 OK
    """
    valid_secret = settings.SUPABASE_WEBHOOK_SECRET

    payload = {
        "type": "INSERT",
        "table": "objects",
        "schema": "storage",
        "record": {
            "name": "users/dummy/test.pdf",
            "bucket_id": "resources",
            "metadata": {"size": 100, "mimetype": "application/pdf"},
        },
    }

    # 1. Missing secret
    res_missing = await async_client.post(
        "/api/v1/webhooks/supabase/storage",
        json=payload,
    )
    assert res_missing.status_code == 401

    # 2. Invalid secret
    res_invalid = await async_client.post(
        "/api/v1/webhooks/supabase/storage",
        headers={"X-Webhook-Secret": "wrong_secret_token"},
        json=payload,
    )
    assert res_invalid.status_code == 401

    # 3. Valid secret (X-Webhook-Secret header)
    res_valid = await async_client.post(
        "/api/v1/webhooks/supabase/storage",
        headers={"X-Webhook-Secret": valid_secret},
        json=payload,
    )
    assert res_valid.status_code == 200

    # 4. Valid secret (Authorization: Bearer header)
    res_bearer = await async_client.post(
        "/api/v1/webhooks/supabase/storage",
        headers={"Authorization": f"Bearer {valid_secret}"},
        json=payload,
    )
    assert res_bearer.status_code == 200


@pytest.mark.asyncio
async def test_supabase_storage_webhook_finalizes_reservation_and_preserves_privacy(
    async_client: AsyncClient, db_session: AsyncSession
):
    """
    Full end-to-end webhook integration:
    1. Student initiates an upload reservation for Supabase.
    2. Supabase Storage triggers the webhook with object metadata.
    3. Webhook atomically verifies secret, transitions status to COMPLETED (100%),
       commits storage quota, creates StorageObject, and provisions private Resource.
    4. Frontend polling /status confirms COMPLETED status and receives resource_id.
    5. Privacy check: Resource is status=PRIVATE and visibility=PRIVATE.
    """
    student_token = await get_auth_token(async_client, "student@amu.edu")
    headers_student = {"Authorization": f"Bearer {student_token}"}
    student = await db_session.scalar(select(User).where(User.email == "student@amu.edu"))
    assert student is not None

    file_size = 750_000
    unique_key = f"users/{student.user_id}/Assignments/{uuid.uuid4().hex[:8]}_research_paper.pdf"

    # Step 1: Initiate reservation
    init_res = await async_client.post(
        "/api/v1/uploads/initiate",
        headers=headers_student,
        json={
            "filename": "research_paper.pdf",
            "content_type": "application/pdf",
            "size_bytes": file_size,
        },
    )
    assert init_res.status_code == 201
    res_id = uuid.UUID(init_res.json()["reservation_id"])

    # Manually associate the unique Supabase object key on the reservation
    reservation = await db_session.get(UploadReservation, res_id)
    assert reservation is not None
    reservation.object_key = unique_key
    await db_session.commit()
    await db_session.refresh(reservation)

    # Get quota before webhook
    quota_before = await db_session.scalar(
        select(StorageQuota).where(StorageQuota.user_id == student.user_id)
    )
    assert quota_before is not None
    reserved_before = quota_before.reserved_bytes
    used_before = quota_before.used_bytes

    # Step 2: Simulate Supabase Storage Webhook firing
    webhook_payload = {
        "type": "INSERT",
        "table": "objects",
        "schema": "storage",
        "record": {
            "name": unique_key,
            "bucket_id": "resources",
            "metadata": {
                "size": file_size,
                "mimetype": "application/pdf",
                "eTag": '"etag-hash-998877"',
            },
        },
    }

    webhook_res = await async_client.post(
        "/api/v1/webhooks/supabase/storage",
        headers={"X-Webhook-Secret": settings.SUPABASE_WEBHOOK_SECRET},
        json=webhook_payload,
    )
    assert webhook_res.status_code == 200
    webhook_data = webhook_res.json()
    assert webhook_data["status"] == "success"
    assert webhook_data["object_key"] == unique_key
    created_resource_id = webhook_data["resource_id"]
    assert created_resource_id is not None

    # Step 3: Frontend checks status via GET /uploads/{id}/status
    stat_res = await async_client.get(
        f"/api/v1/uploads/{res_id}/status",
        headers=headers_student,
    )
    assert stat_res.status_code == 200
    status_info = stat_res.json()
    assert status_info["status"] == "COMPLETED"
    assert status_info["progress_percent"] == 100
    assert status_info["resource_id"] == created_resource_id
    assert status_info["bytes_uploaded"] == file_size

    # Step 4: Quota committed atomically
    await db_session.refresh(quota_before)
    assert quota_before.reserved_bytes == max(0, reserved_before - file_size)
    assert quota_before.used_bytes == used_before + file_size

    # Step 5: Privacy check on created Resource
    res_obj = await db_session.get(Resource, uuid.UUID(created_resource_id))
    assert res_obj is not None
    assert res_obj.owner_id == student.user_id
    assert res_obj.status == "PRIVATE"  # Default privacy strictly maintained
    assert res_obj.visibility == "PRIVATE"

    # Step 6: Verify StorageObject registration
    storage_obj = await db_session.scalar(
        select(StorageObject).where(StorageObject.object_key == unique_key)
    )
    assert storage_obj is not None
    assert storage_obj.storage_provider == "SUPABASE"
    assert storage_obj.verification_status == "VERIFIED"
    assert storage_obj.byte_size == file_size

    # Step 7: Idempotency - duplicate webhook does not fail or duplicate quota
    webhook_dup = await async_client.post(
        "/api/v1/webhooks/supabase/storage",
        headers={"X-Webhook-Secret": settings.SUPABASE_WEBHOOK_SECRET},
        json=webhook_payload,
    )
    assert webhook_dup.status_code == 200
    assert "already" in webhook_dup.json()["message"].lower()


@pytest.mark.asyncio
async def test_upload_sse_stream(async_client: AsyncClient, db_session: AsyncSession):
    """
    Verify Server-Sent Events (SSE) streaming endpoint:
    GET /api/v1/uploads/{id}/events returns text/event-stream.
    """
    student_token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {student_token}"}

    # Initiate upload
    init_res = await async_client.post(
        "/api/v1/uploads/initiate",
        headers=headers,
        json={
            "filename": "stream_test.pdf",
            "content_type": "application/pdf",
            "size_bytes": 100_000,
        },
    )
    assert init_res.status_code == 201
    res_id = init_res.json()["reservation_id"]

    # Call SSE stream endpoint
    async with async_client.stream(
        "GET", f"/api/v1/uploads/{res_id}/events?max_events=1", headers=headers
    ) as stream:
        assert stream.status_code == 200
        assert "text/event-stream" in stream.headers.get("content-type", "")

        # Read first event chunk
        async for line in stream.aiter_lines():
            if line.startswith("data:"):
                assert res_id in line
                break
