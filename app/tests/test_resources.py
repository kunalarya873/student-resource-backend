import uuid

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.tests.conftest import get_auth_token


async def create_completed_upload(
    async_client: AsyncClient,
    token: str,
    filename: str = "test_asset.pdf",
    content: bytes = b"PDF Payload Data",
) -> uuid.UUID:
    """Helper to run Phase 5 two-phase upload and return reservation_id."""
    headers = {"Authorization": f"Bearer {token}"}
    init_res = await async_client.post(
        "/api/v1/uploads/initiate",
        headers=headers,
        json={
            "filename": filename,
            "content_type": "application/pdf",
            "size_bytes": len(content),
        },
    )
    assert init_res.status_code == 201
    init_data = init_res.json()
    reservation_id = init_data["reservation_id"]
    upload_url = init_data["upload_url"]

    async with httpx.AsyncClient() as s3_client:
        put_res = await s3_client.put(
            upload_url,
            content=content,
            headers={"Content-Type": "application/pdf"},
        )
        assert put_res.status_code in (200, 204)

    complete_res = await async_client.post(
        "/api/v1/uploads/complete",
        headers=headers,
        json={"reservation_id": reservation_id},
    )
    assert complete_res.status_code == 200
    return uuid.UUID(reservation_id)


@pytest.mark.asyncio
async def test_ec06_1_student_upload_privacy_vs_teacher_auto_approval(
    async_client: AsyncClient,
):
    """
    EC-06.1: Student upload yields status=PRIVATE and visibility=PRIVATE (invisible to others).
    Teacher upload yields status=APPROVED and visibility=DEPARTMENT.
    """
    student_token = await get_auth_token(async_client, "student@amu.edu")
    headers_student = {"Authorization": f"Bearer {student_token}"}

    # 1. Student creates link-backed resource
    s_res = await async_client.post(
        "/api/v1/resources",
        headers=headers_student,
        json={
            "title": "Student Private Study Notes",
            "description": "My personal revision notes",
            "external_url": "https://amu.ac.in/revision.html",
        },
    )
    assert s_res.status_code == 201
    student_resource = s_res.json()
    assert student_resource["status"] == "PRIVATE"
    assert student_resource["visibility"] == "PRIVATE"

    # 2. Another user (teacher or other student) attempts to view Student A's private resource
    teacher_token = await get_auth_token(async_client, "teacher@amu.edu")
    headers_teacher = {"Authorization": f"Bearer {teacher_token}"}

    peek_res = await async_client.get(
        f"/api/v1/resources/{student_resource['resource_id']}",
        headers=headers_teacher,
    )
    assert peek_res.status_code == 403
    assert "private" in peek_res.json()["detail"].lower()

    # 3. Teacher creates a resource
    t_res = await async_client.post(
        "/api/v1/resources",
        headers=headers_teacher,
        json={
            "title": "Department Exam Syllabus",
            "description": "Fall semester official syllabus",
            "external_url": "https://amu.ac.in/syllabus.html",
        },
    )
    assert t_res.status_code == 201
    teacher_resource = t_res.json()
    assert teacher_resource["status"] == "APPROVED"
    assert teacher_resource["visibility"] == "DEPARTMENT"


@pytest.mark.asyncio
async def test_ec06_2_ssrf_link_attack_prevention(async_client: AsyncClient):
    """
    EC-06.2: SSRF Link Attack Prevention
    Validates protocol whitelist (only HTTP/HTTPS) and rejects loopback,
    RFC 1918 private IPs, AWS metadata IPs, and internal hostnames with HTTP 400.
    """
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    adversarial_urls = [
        "http://127.0.0.1:8000/internal-secrets",
        "http://localhost:5432/db",
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.1/admin-router",
        "http://10.0.0.1/private-api",
        "ftp://amu.ac.in/downloads/book.pdf",
    ]

    for bad_url in adversarial_urls:
        res = await async_client.post(
            "/api/v1/resources",
            headers=headers,
            json={
                "title": "Adversarial Link Test",
                "external_url": bad_url,
            },
        )
        assert res.status_code == 400, (
            f"Expected HTTP 400 for bad URL '{bad_url}', got {res.status_code}"
        )


@pytest.mark.asyncio
async def test_ec06_3_multi_versioning_and_retention(async_client: AsyncClient):
    """
    EC-06.3: Adding new versions increments version_number and retains historical versions.
    """
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create file-backed resource with Version 1
    res1_id = await create_completed_upload(
        async_client,
        token,
        filename="lecture_v1.pdf",
        content=b"Version 1 Payload Content",
    )
    create_res = await async_client.post(
        "/api/v1/resources",
        headers=headers,
        json={
            "title": "Distributed Systems Handbook",
            "reservation_id": str(res1_id),
        },
    )
    assert create_res.status_code == 201
    resource_data = create_res.json()
    resource_id = resource_data["resource_id"]
    assert len(resource_data["versions"]) == 1
    assert resource_data["current_version"]["version_number"] == 1

    # 2. Upload file for Version 2 and attach
    res2_id = await create_completed_upload(
        async_client,
        token,
        filename="lecture_v2.pdf",
        content=b"Version 2 Revised Payload Content",
    )
    v2_res = await async_client.post(
        f"/api/v1/resources/{resource_id}/versions",
        headers=headers,
        json={"reservation_id": str(res2_id)},
    )
    assert v2_res.status_code == 201
    v2_data = v2_res.json()
    assert v2_data["version_number"] == 2

    # 3. Fetch resource and verify both versions are retained
    get_res = await async_client.get(f"/api/v1/resources/{resource_id}", headers=headers)
    assert get_res.status_code == 200
    updated_resource = get_res.json()
    assert len(updated_resource["versions"]) == 2
    assert updated_resource["current_version"]["version_number"] == 2
    versions_list = [v["version_number"] for v in updated_resource["versions"]]
    assert versions_list == [1, 2]


@pytest.mark.asyncio
async def test_ec06_4_unauthorized_download_attempt_blocked(async_client: AsyncClient):
    """
    EC-06.4: Unauthorized Download Attempt
    Student B cannot download Student A's private resource file or link.
    """
    # Student A uploads private resource
    token_a = await get_auth_token(async_client, "student@amu.edu")
    res_id = await create_completed_upload(
        async_client,
        token_a,
        filename="private_secret.pdf",
        content=b"Secret content from Student A",
    )
    create_res = await async_client.post(
        "/api/v1/resources",
        headers={"Authorization": f"Bearer {token_a}"},
        json={
            "title": "Private Research Notes",
            "reservation_id": str(res_id),
        },
    )
    assert create_res.status_code == 201
    resource_id = create_res.json()["resource_id"]

    # Student B attempts to download
    token_b = await get_auth_token(async_client, "teacher@amu.edu")  # Different user
    dl_res = await async_client.get(
        f"/api/v1/resources/{resource_id}/download",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert dl_res.status_code == 403

    # Student A downloads successfully
    dl_res_a = await async_client.get(
        f"/api/v1/resources/{resource_id}/download",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert dl_res_a.status_code == 200
    dl_data = dl_res_a.json()
    assert not dl_data["is_external"]
    assert "download_url" in dl_data


@pytest.mark.asyncio
async def test_resource_crud_and_quota_cleanup_on_delete(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Test updating and deleting resource, verifying used quota is released upon deletion."""
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create file-backed resource with 4096 bytes
    test_data = b"X" * 4096
    res_id = await create_completed_upload(
        async_client, token, filename="temp_file.pdf", content=test_data
    )
    create_res = await async_client.post(
        "/api/v1/resources",
        headers=headers,
        json={
            "title": "Temporary Research Paper",
            "description": "Draft 1",
            "reservation_id": str(res_id),
        },
    )
    assert create_res.status_code == 201
    resource_id = create_res.json()["resource_id"]

    # 2. Update resource
    patch_res = await async_client.patch(
        f"/api/v1/resources/{resource_id}",
        headers=headers,
        json={"title": "Updated Research Paper Title", "description": "Final Draft"},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["title"] == "Updated Research Paper Title"
    assert patch_res.json()["description"] == "Final Draft"

    # 3. Check quota used_bytes increased
    quota_res = await async_client.get("/api/v1/storage/quota", headers=headers)
    used_before = quota_res.json()["used_bytes"]
    assert used_before >= 4096

    # 4. Delete resource
    del_res = await async_client.delete(f"/api/v1/resources/{resource_id}", headers=headers)
    assert del_res.status_code == 200
    assert "deleted successfully" in del_res.json()["message"]

    # 5. Check quota used_bytes decreased
    quota_res_after = await async_client.get("/api/v1/storage/quota", headers=headers)
    used_after = quota_res_after.json()["used_bytes"]
    assert used_after == used_before - 4096


@pytest.mark.asyncio
async def test_resource_pagination_query_and_sorting(async_client: AsyncClient):
    """Test pagination, search term q, and sorting on GET /api/v1/resources."""
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    # Create distinct resources
    for title in ["Discrete Math Module A", "Discrete Math Module B", "Linear Algebra"]:
        await async_client.post(
            "/api/v1/resources",
            headers=headers,
            json={
                "title": title,
                "description": f"Syllabus notes for {title}",
                "external_url": "https://amu.ac.in/math.html",
            },
        )

    # 1. Test search filter q="Discrete"
    res_q = await async_client.get(
        "/api/v1/resources",
        headers=headers,
        params={"q": "Discrete"},
    )
    assert res_q.status_code == 200
    data_q = res_q.json()
    assert len(data_q) >= 2
    assert all("Discrete" in r["title"] for r in data_q)

    # 2. Test pagination page=1, limit=1
    res_page = await async_client.get(
        "/api/v1/resources",
        headers=headers,
        params={"page": 1, "limit": 1},
    )
    assert res_page.status_code == 200
    assert len(res_page.json()) == 1

    # 3. Test sorting by title ascending
    res_sort = await async_client.get(
        "/api/v1/resources",
        headers=headers,
        params={"sort_by": "title", "order": "asc"},
    )
    assert res_sort.status_code == 200
    titles = [r["title"] for r in res_sort.json()]
    # Postgres ORDER BY title uses case-insensitive collation; mirror that here
    # rather than Python's case-sensitive default sort.
    assert titles == sorted(titles, key=str.lower)


@pytest.mark.asyncio
async def test_resource_folder_id_helpful_error_and_folder_endpoint(
    async_client: AsyncClient,
):
    """Test calling /resources/{folder_id} returns an informative 404 and /resources/folder/{folder_id} succeeds."""
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create a folder
    f_res = await async_client.post(
        "/api/v1/folders", headers=headers, json={"name": "DSA Algorithms"}
    )
    assert f_res.status_code == 201
    folder_id = f_res.json()["folder_id"]

    # 2. Add resource to this folder
    res_obj = await async_client.post(
        "/api/v1/resources",
        headers=headers,
        json={
            "title": "Binary Trees CheatSheet",
            "folder_id": folder_id,
            "external_url": "https://amu.ac.in/cs/trees.html",
        },
    )
    assert res_obj.status_code == 201

    # 3. Attempt to fetch folder ID via /resources/{id} -> informative 404
    err_res = await async_client.get(
        f"/api/v1/resources/{folder_id}",
        headers=headers,
    )
    assert err_res.status_code == 404
    detail = err_res.json()["detail"]
    assert "is a Folder ('DSA Algorithms')" in detail
    assert "GET /api/v1/folders/" in detail

    # 4. Fetch via dedicated /resources/folder/{folder_id} endpoint -> 200 OK
    folder_res = await async_client.get(
        f"/api/v1/resources/folder/{folder_id}",
        headers=headers,
    )
    assert folder_res.status_code == 200
    items = folder_res.json()
    assert len(items) == 1
    assert items[0]["title"] == "Binary Trees CheatSheet"
