"""Folder sharing: granting VIEW/EDIT on a folder cascades access to its whole
subtree (subfolders + resources), is listed under /folders/shared-with-me, and is
revocable immediately. Mirrors the resource-sharing edge-case suite (EC-07.x).

NOTE: requires Alembic migration 0007 (folder_access table) to be applied to the
configured database before running.
"""

import pytest
from httpx import AsyncClient

from app.tests.conftest import get_auth_token


async def _make_folder(client: AsyncClient, headers: dict, name: str) -> str:
    res = await client.post("/api/v1/folders", headers=headers, json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()["folder_id"]


async def _make_resource_in(client: AsyncClient, headers: dict, folder_id: str, title: str) -> str:
    res = await client.post(
        "/api/v1/resources",
        headers=headers,
        json={
            "title": title,
            "folder_id": folder_id,
            "external_url": "https://amu.ac.in/notes/shared.html",
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["resource_id"]


@pytest.mark.asyncio
async def test_folder_share_cascades_access_to_subtree(async_client: AsyncClient):
    """Sharing a folder grants the grantee access to the folder contents and the
    resources inside it (view + download); revoking terminates access at once."""
    token_owner = await get_auth_token(async_client, "student@amu.edu")
    headers_owner = {"Authorization": f"Bearer {token_owner}"}
    token_peer = await get_auth_token(async_client, "teacher@amu.edu")
    headers_peer = {"Authorization": f"Bearer {token_peer}"}

    # 1. Owner creates a folder with a private resource inside it.
    folder_id = await _make_folder(async_client, headers_owner, "Semester Notes")
    resource_id = await _make_resource_in(
        async_client, headers_owner, folder_id, "Unit 1 - Thermodynamics"
    )

    # 2. Peer is initially blocked from the folder and the resource.
    assert (
        await async_client.get(f"/api/v1/folders/{folder_id}/contents", headers=headers_peer)
    ).status_code == 403
    assert (
        await async_client.get(f"/api/v1/resources/{resource_id}", headers=headers_peer)
    ).status_code == 403

    # 3. Owner shares the folder with the peer (VIEW).
    grant_res = await async_client.post(
        f"/api/v1/folders/{folder_id}/share",
        headers=headers_owner,
        json={"grantee_email": "teacher@amu.edu", "permission": "VIEW"},
    )
    assert grant_res.status_code == 201, grant_res.text
    grant = grant_res.json()
    assert grant["folder_id"] == folder_id
    assert grant["grantee_email"] == "teacher@amu.edu"
    grant_id = grant["access_id"]

    # 4. Peer can now browse the folder and view + download the resource inside it.
    contents = await async_client.get(
        f"/api/v1/folders/{folder_id}/contents", headers=headers_peer
    )
    assert contents.status_code == 200
    assert any(r["resource_id"] == resource_id for r in contents.json()["resources"])

    assert (
        await async_client.get(f"/api/v1/resources/{resource_id}", headers=headers_peer)
    ).status_code == 200
    assert (
        await async_client.get(f"/api/v1/resources/{resource_id}/download", headers=headers_peer)
    ).status_code == 200

    # 5. The folder shows under the peer's /folders/shared-with-me.
    shared = await async_client.get("/api/v1/folders/shared-with-me", headers=headers_peer)
    assert shared.status_code == 200
    assert folder_id in [f["folder_id"] for f in shared.json()]

    # 6. Revoking the grant blocks the peer again immediately (EC-07.3 parity).
    revoke = await async_client.delete(
        f"/api/v1/folders/{folder_id}/share/{grant_id}", headers=headers_owner
    )
    assert revoke.status_code == 200
    assert (
        await async_client.get(f"/api/v1/folders/{folder_id}/contents", headers=headers_peer)
    ).status_code == 403
    assert (
        await async_client.get(f"/api/v1/resources/{resource_id}", headers=headers_peer)
    ).status_code == 403


@pytest.mark.asyncio
async def test_folder_share_cascades_to_nested_subfolder(async_client: AsyncClient):
    """A grant on an ancestor folder also covers resources in its descendant folders."""
    token_owner = await get_auth_token(async_client, "student@amu.edu")
    headers_owner = {"Authorization": f"Bearer {token_owner}"}
    token_peer = await get_auth_token(async_client, "teacher@amu.edu")
    headers_peer = {"Authorization": f"Bearer {token_peer}"}

    parent_id = await _make_folder(async_client, headers_owner, "Course Root")
    child_res = await async_client.post(
        "/api/v1/folders",
        headers=headers_owner,
        json={"name": "Week 2", "parent_folder_id": parent_id},
    )
    assert child_res.status_code == 201
    child_id = child_res.json()["folder_id"]
    nested_resource_id = await _make_resource_in(
        async_client, headers_owner, child_id, "Nested Lecture Slides"
    )

    # Share only the PARENT; access must reach the nested resource.
    grant = await async_client.post(
        f"/api/v1/folders/{parent_id}/share",
        headers=headers_owner,
        json={"grantee_email": "teacher@amu.edu", "permission": "VIEW"},
    )
    assert grant.status_code == 201

    assert (
        await async_client.get(f"/api/v1/folders/{child_id}/contents", headers=headers_peer)
    ).status_code == 200
    assert (
        await async_client.get(f"/api/v1/resources/{nested_resource_id}", headers=headers_peer)
    ).status_code == 200


@pytest.mark.asyncio
async def test_non_owner_cannot_share_folder(async_client: AsyncClient):
    """EC-07.1 parity: a grantee cannot re-share the owner's folder."""
    token_owner = await get_auth_token(async_client, "student@amu.edu")
    headers_owner = {"Authorization": f"Bearer {token_owner}"}
    token_peer = await get_auth_token(async_client, "teacher@amu.edu")
    headers_peer = {"Authorization": f"Bearer {token_peer}"}

    folder_id = await _make_folder(async_client, headers_owner, "Restricted Folder")
    await async_client.post(
        f"/api/v1/folders/{folder_id}/share",
        headers=headers_owner,
        json={"grantee_email": "teacher@amu.edu", "permission": "VIEW"},
    )

    illegal = await async_client.post(
        f"/api/v1/folders/{folder_id}/share",
        headers=headers_peer,
        json={"grantee_email": "admin@amu.edu", "permission": "VIEW"},
    )
    assert illegal.status_code == 403
    assert "owner" in illegal.json()["detail"].lower()


@pytest.mark.asyncio
async def test_folder_self_share_rejected(async_client: AsyncClient):
    """EC-07.2 parity: cannot share a folder with yourself."""
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    folder_id = await _make_folder(async_client, headers, "My Own Folder")
    res = await async_client.post(
        f"/api/v1/folders/{folder_id}/share",
        headers=headers,
        json={"grantee_email": "student@amu.edu", "permission": "VIEW"},
    )
    assert res.status_code == 400
    assert "yourself" in res.json()["detail"].lower()


@pytest.mark.asyncio
async def test_list_folder_grants(async_client: AsyncClient):
    """GET /folders/{id}/share returns a paginated grant list for the owner."""
    token_owner = await get_auth_token(async_client, "student@amu.edu")
    headers_owner = {"Authorization": f"Bearer {token_owner}"}

    folder_id = await _make_folder(async_client, headers_owner, "Shared Listing Folder")
    await async_client.post(
        f"/api/v1/folders/{folder_id}/share",
        headers=headers_owner,
        json={"grantee_email": "teacher@amu.edu", "permission": "EDIT"},
    )

    grants = await async_client.get(f"/api/v1/folders/{folder_id}/share", headers=headers_owner)
    assert grants.status_code == 200
    data = grants.json()
    assert {"items", "page", "limit", "total", "total_pages"} <= set(data.keys())
    assert "teacher@amu.edu" in [g["grantee_email"] for g in data["items"]]
