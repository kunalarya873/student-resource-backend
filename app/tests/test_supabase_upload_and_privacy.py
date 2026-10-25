import io
import uuid
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.folder import Folder
from app.models.resource import Resource
from app.models.storage import StorageObject
from app.models.user import User
from app.services.supabase_storage_service import SupabaseStorageService
from app.tests.conftest import get_auth_token


@pytest.mark.asyncio
async def test_direct_resource_upload_segregated_folder_and_privacy(
    async_client: AsyncClient, db_session: AsyncSession
):
    """
    Verify:
    1. Direct file upload places file in user-segregated folder (users/{user_id}/{folder}/...).
    2. Enforces privacy default: status=PRIVATE, visibility=PRIVATE for students.
    3. Storage provider is marked SUPABASE.
    """
    student_token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {student_token}"}

    # Fetch current student user
    student = await db_session.scalar(select(User).where(User.email == "student@amu.edu"))
    assert student is not None

    # Create a user folder
    folder = Folder(owner_id=student.user_id, name="PhysicsNotes")
    db_session.add(folder)
    await db_session.commit()
    await db_session.refresh(folder)

    unique_key = f"users/{student.user_id}/PhysicsNotes/{uuid.uuid4().hex[:8]}_test_lecture.png"
    mock_upload_result = {
        "object_key": unique_key,
        "byte_size": 256,
        "checksum": "abcdef1234567890",
        "content_type": "image/png",
        "storage_provider": "SUPABASE",
    }

    file_content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 248  # 256 bytes

    with patch.object(
        SupabaseStorageService, "upload_file_async", return_value=mock_upload_result
    ) as mock_up:
        res = await async_client.post(
            "/api/v1/resources/upload",
            headers=headers,
            data={
                "title": "Quantum Physics Lecture 1",
                "description": "Handwritten notes on wave functions",
                "folder_id": str(folder.folder_id),
            },
            files={"file": ("lecture1.png", io.BytesIO(file_content), "image/png")},
        )

        assert res.status_code == 201
        data = res.json()
        assert data["title"] == "Quantum Physics Lecture 1"
        assert data["status"] == "PRIVATE"
        assert data["visibility"] == "PRIVATE"
        assert data["folder_id"] == str(folder.folder_id)

        resource_id = uuid.UUID(data["resource_id"])

        # Check DB records
        resource = await db_session.scalar(
            select(Resource).where(Resource.resource_id == resource_id)
        )
        assert resource is not None
        assert resource.owner_id == student.user_id

        # Check StorageObject record
        storage_obj = await db_session.scalar(
            select(StorageObject).where(
                StorageObject.object_key == mock_upload_result["object_key"]
            )
        )
        assert storage_obj is not None
        assert storage_obj.storage_provider == "SUPABASE"
        assert storage_obj.byte_size == 256
        assert "users/" in storage_obj.object_key
        assert str(student.user_id) in storage_obj.object_key

        # Verify Supabase service was invoked with student user_id and folder name
        mock_up.assert_called_once()
        _args, kwargs = mock_up.call_args
        assert kwargs["user_id"] == student.user_id
        assert kwargs["folder_name"] == "PhysicsNotes"
        assert kwargs["filename"] == "lecture1.png"


@pytest.mark.asyncio
async def test_upload_to_another_users_folder_forbidden(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Verify students cannot place resources in another user's folder (EC-05.2)."""
    teacher = await db_session.scalar(select(User).where(User.email == "teacher@amu.edu"))
    student = await db_session.scalar(select(User).where(User.email == "student@amu.edu"))
    assert teacher is not None and student is not None

    # Teacher creates a folder
    teacher_folder = Folder(owner_id=teacher.user_id, name="ExamsArchive")
    db_session.add(teacher_folder)
    await db_session.commit()
    await db_session.refresh(teacher_folder)

    student_token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {student_token}"}

    res = await async_client.post(
        "/api/v1/resources/upload",
        headers=headers,
        data={
            "title": "Unauthorized Folder Insertion",
            "folder_id": str(teacher_folder.folder_id),
        },
        files={"file": ("malicious.pdf", io.BytesIO(b"Data"), "application/pdf")},
    )
    assert res.status_code == 403
    assert "permission" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_privacy_on_download_and_sharing_lifecycle(
    async_client: AsyncClient, db_session: AsyncSession
):
    """
    Verify complete privacy lifecycle:
    1. Student uploads private resource.
    2. Peer cannot download (403 Forbidden).
    3. Student shares resource with peer.
    4. Peer CAN download (receives signed URL).
    5. Student revokes share -> Peer immediately loses download access (403 Forbidden).
    """
    student_token = await get_auth_token(async_client, "student@amu.edu")
    teacher_token = await get_auth_token(async_client, "teacher@amu.edu")

    headers_student = {"Authorization": f"Bearer {student_token}"}
    headers_teacher = {"Authorization": f"Bearer {teacher_token}"}

    student = await db_session.scalar(select(User).where(User.email == "student@amu.edu"))
    teacher = await db_session.scalar(select(User).where(User.email == "teacher@amu.edu"))
    assert student is not None and teacher is not None

    mock_upload_result = {
        "object_key": f"users/{student.user_id}/confidential/{uuid.uuid4().hex[:8]}_private_sheet.pdf",
        "byte_size": 100,
        "checksum": "sha256hash123",
        "content_type": "application/pdf",
        "storage_provider": "SUPABASE",
    }

    # 1. Student uploads private resource
    with patch.object(SupabaseStorageService, "upload_file_async", return_value=mock_upload_result):
        create_res = await async_client.post(
            "/api/v1/resources/upload",
            headers=headers_student,
            data={"title": "Private Research Notes"},
            files={
                "file": (
                    "private_sheet.pdf",
                    io.BytesIO(b"Confidential"),
                    "application/pdf",
                )
            },
        )
        assert create_res.status_code == 201
        resource_id = create_res.json()["resource_id"]

    # 2. Peer (Teacher) tries to download -> 403 Forbidden (Privacy maintained!)
    dl_fail = await async_client.get(
        f"/api/v1/resources/{resource_id}/download",
        headers=headers_teacher,
    )
    assert dl_fail.status_code == 403

    # 3. Owner (Student) downloads -> Success with signed URL
    with patch.object(
        SupabaseStorageService,
        "create_signed_download_url",
        return_value="https://supabase.co/signed-url-private-sheet",
    ):
        dl_owner = await async_client.get(
            f"/api/v1/resources/{resource_id}/download",
            headers=headers_student,
        )
        assert dl_owner.status_code == 200
        assert dl_owner.json()["download_url"] == "https://supabase.co/signed-url-private-sheet"

    # 4. Student shares with teacher
    share_res = await async_client.post(
        f"/api/v1/resources/{resource_id}/share",
        headers=headers_student,
        json={"grantee_email": "teacher@amu.edu", "permission": "VIEW"},
    )
    assert share_res.status_code == 201
    access_id = share_res.json()["access_id"]

    # 5. Teacher can now access and download
    with patch.object(
        SupabaseStorageService,
        "create_signed_download_url",
        return_value="https://supabase.co/signed-url-for-teacher",
    ):
        dl_teacher = await async_client.get(
            f"/api/v1/resources/{resource_id}/download",
            headers=headers_teacher,
        )
        assert dl_teacher.status_code == 200
        assert dl_teacher.json()["download_url"] == "https://supabase.co/signed-url-for-teacher"

    # 6. Student revokes share grant
    revoke_res = await async_client.delete(
        f"/api/v1/resources/{resource_id}/share/{access_id}",
        headers=headers_student,
    )
    assert revoke_res.status_code == 200

    # 7. Teacher is blocked again -> 403 Forbidden (Privacy restored!)
    dl_revoked = await async_client.get(
        f"/api/v1/resources/{resource_id}/download",
        headers=headers_teacher,
    )
    assert dl_revoked.status_code == 403


@pytest.mark.asyncio
async def test_supabase_storage_service_unit():
    """Verify SupabaseStorageService path generation and client methods."""
    with patch.object(SupabaseStorageService, "get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_bucket = MagicMock()
        mock_client.storage.from_.return_value = mock_bucket
        mock_get_client.return_value = mock_client

        mock_bucket.upload.return_value = {"Key": "files/pic.png"}
        mock_bucket.create_signed_url.return_value = {"signedURL": "https://signed.url"}

        user_id = uuid.uuid4()
        result = await SupabaseStorageService.upload_file_async(
            user_id=user_id,
            folder_name="Research Lab",
            filename="document.pdf",
            file_data=b"Sample PDF Content",
            content_type="application/pdf",
        )

        assert f"users/{user_id}/Research_Lab/" in result["object_key"]
        assert "document.pdf" in result["object_key"]
        assert result["storage_provider"] == "SUPABASE"
        assert result["byte_size"] == 18

        signed_url = await SupabaseStorageService.create_signed_download_url(result["object_key"])
        assert signed_url == "https://signed.url"
