from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.services.email_service import EmailService
from app.tests.conftest import get_auth_token


@pytest.mark.asyncio
async def test_ec07_1_non_owner_cannot_share_resource(async_client: AsyncClient):
    """
    EC-07.1: Non-Owner Shares Resource
    Student B (recipient of a VIEW grant) attempts to share Student A's resource with Student C.
    Expected: HTTP 403 Forbidden.
    """
    token_a = await get_auth_token(async_client, "student@amu.edu")
    headers_a = {"Authorization": f"Bearer {token_a}"}

    # 1. Student A creates private resource
    res_create = await async_client.post(
        "/api/v1/resources",
        headers=headers_a,
        json={
            "title": "Quantum Mechanics Notes",
            "external_url": "https://amu.ac.in/physics/qm.html",
        },
    )
    assert res_create.status_code == 201
    resource_id = res_create.json()["resource_id"]

    # 2. Student A shares with Teacher
    share_res = await async_client.post(
        f"/api/v1/resources/{resource_id}/share",
        headers=headers_a,
        json={"grantee_email": "teacher@amu.edu", "permission": "VIEW"},
    )
    assert share_res.status_code == 201

    # 3. Teacher attempts to share Student A's resource with Admin
    token_teacher = await get_auth_token(async_client, "teacher@amu.edu")
    headers_teacher = {"Authorization": f"Bearer {token_teacher}"}

    illegal_share = await async_client.post(
        f"/api/v1/resources/{resource_id}/share",
        headers=headers_teacher,
        json={"grantee_email": "admin@amu.edu", "permission": "VIEW"},
    )
    assert illegal_share.status_code == 403
    assert "owner" in illegal_share.json()["detail"].lower()


@pytest.mark.asyncio
async def test_ec07_2_self_sharing_rejected(async_client: AsyncClient):
    """
    EC-07.2: Self-Sharing Attempt
    User attempts to share resource with their own email.
    Expected: HTTP 400 Bad Request.
    """
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    res_create = await async_client.post(
        "/api/v1/resources",
        headers=headers,
        json={
            "title": "Self Study Guide",
            "external_url": "https://amu.ac.in/guide.html",
        },
    )
    resource_id = res_create.json()["resource_id"]

    self_share = await async_client.post(
        f"/api/v1/resources/{resource_id}/share",
        headers=headers,
        json={"grantee_email": "student@amu.edu", "permission": "VIEW"},
    )
    assert self_share.status_code == 400
    assert "yourself" in self_share.json()["detail"].lower()


@pytest.mark.asyncio
async def test_ec07_3_immediate_revocation_effect(async_client: AsyncClient):
    """
    EC-07.3: Immediate Revocation Effect
    Granting access allows view & download; revoking access immediately terminates rights (HTTP 403).
    """
    token_owner = await get_auth_token(async_client, "student@amu.edu")
    headers_owner = {"Authorization": f"Bearer {token_owner}"}

    token_peer = await get_auth_token(async_client, "teacher@amu.edu")
    headers_peer = {"Authorization": f"Bearer {token_peer}"}

    # 1. Owner creates private resource
    res = (
        await async_client.post(
            "/api/v1/resources",
            headers=headers_owner,
            json={
                "title": "Confidential Research Draft",
                "external_url": "https://amu.ac.in/research/draft.html",
            },
        )
    ).json()
    resource_id = res["resource_id"]

    # 2. Verify Peer is initially blocked
    peek_before = await async_client.get(f"/api/v1/resources/{resource_id}", headers=headers_peer)
    assert peek_before.status_code == 403

    # 3. Owner grants VIEW access to Peer
    grant_res = await async_client.post(
        f"/api/v1/resources/{resource_id}/share",
        headers=headers_owner,
        json={"grantee_email": "teacher@amu.edu", "permission": "VIEW"},
    )
    assert grant_res.status_code == 201
    grant_id = grant_res.json()["access_id"]

    # 4. Peer can now view and download
    peek_during = await async_client.get(f"/api/v1/resources/{resource_id}", headers=headers_peer)
    assert peek_during.status_code == 200
    assert peek_during.json()["title"] == "Confidential Research Draft"

    dl_during = await async_client.get(
        f"/api/v1/resources/{resource_id}/download", headers=headers_peer
    )
    assert dl_during.status_code == 200

    # 5. Owner revokes access
    revoke_res = await async_client.delete(
        f"/api/v1/resources/{resource_id}/share/{grant_id}",
        headers=headers_owner,
    )
    assert revoke_res.status_code == 200

    # 6. Peer is immediately blocked again (EC-07.3)
    peek_after = await async_client.get(f"/api/v1/resources/{resource_id}", headers=headers_peer)
    assert peek_after.status_code == 403

    dl_after = await async_client.get(
        f"/api/v1/resources/{resource_id}/download", headers=headers_peer
    )
    assert dl_after.status_code == 403


@pytest.mark.asyncio
async def test_ec07_4_non_existent_grantee_rejected(async_client: AsyncClient):
    """
    EC-07.4: Non-Existent Grantee Email
    Sharing with an unverified or phantom email returns HTTP 404 User not found.
    """
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    res = (
        await async_client.post(
            "/api/v1/resources",
            headers=headers,
            json={
                "title": "Algorithms Cheat Sheet",
                "external_url": "https://amu.ac.in/cs/algo.html",
            },
        )
    ).json()

    phantom_share = await async_client.post(
        f"/api/v1/resources/{res['resource_id']}/share",
        headers=headers,
        json={"grantee_email": "phantom_nobody_404@amu.edu", "permission": "VIEW"},
    )
    assert phantom_share.status_code == 404
    assert "not found" in phantom_share.json()["detail"].lower()


@pytest.mark.asyncio
async def test_list_shared_with_me_and_list_grants(async_client: AsyncClient):
    """Test GET /resources/shared-with-me and GET /resources/{id}/share."""
    token_owner = await get_auth_token(async_client, "student@amu.edu")
    headers_owner = {"Authorization": f"Bearer {token_owner}"}

    token_peer = await get_auth_token(async_client, "teacher@amu.edu")
    headers_peer = {"Authorization": f"Bearer {token_peer}"}

    # 1. Owner creates resource and shares with Teacher
    res = (
        await async_client.post(
            "/api/v1/resources",
            headers=headers_owner,
            json={
                "title": "Collaborative Study Document",
                "external_url": "https://amu.ac.in/collab.html",
            },
        )
    ).json()

    await async_client.post(
        f"/api/v1/resources/{res['resource_id']}/share",
        headers=headers_owner,
        json={"grantee_email": "teacher@amu.edu", "permission": "EDIT"},
    )

    # 2. Owner lists active grants on this resource
    grants_res = await async_client.get(
        f"/api/v1/resources/{res['resource_id']}/share",
        headers=headers_owner,
    )
    assert grants_res.status_code == 200
    grants_data = grants_res.json()
    assert "items" in grants_data
    assert "total" in grants_data
    assert "page" in grants_data
    assert "limit" in grants_data
    assert "total_pages" in grants_data
    grants = grants_data["items"]
    assert len(grants) >= 1
    grantee_emails = [g["grantee_email"] for g in grants]
    assert "teacher@amu.edu" in grantee_emails

    # 3. Peer queries /resources/shared-with-me
    shared_res = await async_client.get("/api/v1/resources/shared-with-me", headers=headers_peer)
    assert shared_res.status_code == 200
    shared_items = shared_res.json()
    shared_ids = [item["resource_id"] for item in shared_items]
    assert res["resource_id"] in shared_ids


@pytest.mark.asyncio
async def test_share_notification_email_and_pagination(async_client: AsyncClient):
    """Verify that sharing a resource triggers an email notification and share grants endpoint paginates correctly."""
    token_owner = await get_auth_token(async_client, "student@amu.edu")
    headers_owner = {"Authorization": f"Bearer {token_owner}"}

    # 1. Create a resource
    res_create = await async_client.post(
        "/api/v1/resources",
        headers=headers_owner,
        json={
            "title": "Database Optimization Guide",
            "external_url": "https://amu.ac.in/cs/db-opt.html",
        },
    )
    assert res_create.status_code == 201
    resource_id = res_create.json()["resource_id"]

    # 2. Share resource and assert background notification email was dispatched
    notification_called = False
    captured_recipient = None

    async def fake_share_email(
        email, grantee_name, sharer_name, resource_title, permission, resource_id
    ):
        nonlocal notification_called, captured_recipient
        notification_called = True
        captured_recipient = email
        return True

    with patch.object(EmailService, "send_share_notification_email", side_effect=fake_share_email):
        share_res = await async_client.post(
            f"/api/v1/resources/{resource_id}/share",
            headers=headers_owner,
            json={"grantee_email": "teacher@amu.edu", "permission": "VIEW"},
        )
        assert share_res.status_code == 201

    assert notification_called is True
    assert captured_recipient == "teacher@amu.edu"

    # 3. Query paginated share grants with limit=1, page=1
    page1_res = await async_client.get(
        f"/api/v1/resources/{resource_id}/share?page=1&limit=1",
        headers=headers_owner,
    )
    assert page1_res.status_code == 200
    page1_data = page1_res.json()
    assert page1_data["page"] == 1
    assert page1_data["limit"] == 1
    assert page1_data["total"] == 1
    assert page1_data["total_pages"] == 1
    assert len(page1_data["items"]) == 1
    assert page1_data["items"][0]["grantee_email"] == "teacher@amu.edu"
