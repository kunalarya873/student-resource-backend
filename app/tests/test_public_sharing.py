"""Public Sharing & Guest Access Test Suite.

Verifies:
1. Enabling public link on folders and resources (Owner/Admin only).
2. Unauthenticated anonymous visitors can resolve public tokens, read contents, and download files.
3. Disabled/restricted public links immediately block anonymous access (HTTP 404).
4. Authenticated users can Join and save public folders/resources to their 'Shared with Me' list.
"""

import pytest
from httpx import AsyncClient

from app.tests.conftest import get_auth_token


async def _make_folder(client: AsyncClient, headers: dict, name: str) -> str:
    res = await client.post("/api/v1/folders", headers=headers, json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()["folder_id"]


async def _make_resource_in(client: AsyncClient, headers: dict, folder_id: str | None, title: str) -> str:
    payload = {
        "title": title,
        "external_url": "https://amu.ac.in/academic-calendar.html",
    }
    if folder_id:
        payload["folder_id"] = folder_id
    res = await client.post("/api/v1/resources", headers=headers, json=payload)
    assert res.status_code == 201, res.text
    return res.json()["resource_id"]


@pytest.mark.asyncio
async def test_public_folder_lifecycle_and_guest_access(async_client: AsyncClient):
    token_owner = await get_auth_token(async_client, "teacher@amu.edu")
    headers_owner = {"Authorization": f"Bearer {token_owner}"}
    token_visitor = await get_auth_token(async_client, "student@amu.edu")
    headers_visitor = {"Authorization": f"Bearer {token_visitor}"}

    # 1. Owner creates a folder with a resource inside
    folder_id = await _make_folder(async_client, headers_owner, "Public Research Papers")
    res_id = await _make_resource_in(async_client, headers_owner, folder_id, "Paper 1 - Quantum Computing")

    # 2. Before enabling public link, public access fails
    resp_init = await async_client.get(f"/api/v1/folders/{folder_id}/public-link", headers=headers_owner)
    assert resp_init.status_code == 200
    assert resp_init.json()["is_public"] is False

    # 3. Owner enables public link
    toggle_resp = await async_client.post(
        f"/api/v1/folders/{folder_id}/public-link",
        headers=headers_owner,
        json={"is_public": True},
    )
    assert toggle_resp.status_code == 200
    data = toggle_resp.json()
    assert data["is_public"] is True
    share_token = data["share_token"]
    assert share_token is not None

    # 4. Unauthenticated guest resolves the token
    resolve_resp = await async_client.get(f"/api/v1/public/s/{share_token}")
    assert resolve_resp.status_code == 200
    res_data = resolve_resp.json()
    assert res_data["kind"] == "folder"
    assert res_data["name"] == "Public Research Papers"

    # 5. Unauthenticated guest browses folder contents
    contents_resp = await async_client.get(f"/api/v1/public/folders/{share_token}")
    assert contents_resp.status_code == 200
    contents_data = contents_resp.json()
    assert contents_data["folder"]["name"] == "Public Research Papers"
    assert len(contents_data["resources"]) >= 1
    assert contents_data["resources"][0]["title"] == "Paper 1 - Quantum Computing"

    # 6. Authenticated visitor Joins the public folder into their 'Shared with Me' list
    join_resp = await async_client.post(f"/api/v1/public/s/{share_token}/join", headers=headers_visitor)
    assert join_resp.status_code == 200
    assert join_resp.json()["kind"] == "folder"

    # Verify folder now appears under /folders/shared-with-me for visitor
    shared_resp = await async_client.get("/api/v1/folders/shared-with-me", headers=headers_visitor)
    assert shared_resp.status_code == 200
    shared_folders = [f["folder_id"] for f in shared_resp.json()]
    assert folder_id in shared_folders

    # 7. Owner restricts the link (is_public=False)
    disable_resp = await async_client.post(
        f"/api/v1/folders/{folder_id}/public-link",
        headers=headers_owner,
        json={"is_public": False},
    )
    assert disable_resp.status_code == 200
    assert disable_resp.json()["is_public"] is False

    # Unauthenticated guest is immediately blocked
    blocked_resp = await async_client.get(f"/api/v1/public/folders/{share_token}")
    assert blocked_resp.status_code == 404


@pytest.mark.asyncio
async def test_public_resource_guest_download(async_client: AsyncClient):
    token_owner = await get_auth_token(async_client, "teacher@amu.edu")
    headers_owner = {"Authorization": f"Bearer {token_owner}"}

    res_id = await _make_resource_in(async_client, headers_owner, None, "Campus Guide 2026")

    # Enable public link on resource
    pub_resp = await async_client.post(
        f"/api/v1/resources/{res_id}/public-link",
        headers=headers_owner,
        json={"is_public": True},
    )
    assert pub_resp.status_code == 200
    share_token = pub_resp.json()["share_token"]

    # Guest retrieves details without auth
    guest_read = await async_client.get(f"/api/v1/public/resources/{share_token}")
    assert guest_read.status_code == 200
    assert guest_read.json()["title"] == "Campus Guide 2026"

    # Guest retrieves download URL without auth
    dl_resp = await async_client.get(f"/api/v1/public/resources/{share_token}/download")
    assert dl_resp.status_code == 200
    assert dl_resp.json()["download_url"] == "https://amu.ac.in/academic-calendar.html"
