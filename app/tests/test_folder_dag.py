import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.resource import Resource
from app.models.user import User
from app.tests.conftest import get_auth_token


@pytest.mark.asyncio
async def test_create_root_and_nested_subfolders(async_client: AsyncClient):
    """Test creating root and nested subfolders."""
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create root folder
    res1 = await async_client.post("/api/v1/folders", headers=headers, json={"name": "Root Folder"})
    assert res1.status_code == 201
    root = res1.json()
    assert root["parent_folder_id"] is None

    # 2. Create subfolder
    res2 = await async_client.post(
        "/api/v1/folders",
        headers=headers,
        json={"name": "Subfolder 1", "parent_folder_id": root["folder_id"]},
    )
    assert res2.status_code == 201
    sub = res2.json()
    assert sub["parent_folder_id"] == root["folder_id"]


@pytest.mark.asyncio
async def test_breadcrumbs_recursive_cte(async_client: AsyncClient):
    """Test recursive CTE constructing breadcrumb hierarchy."""
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    # Hierarchy: A -> B -> C
    f_a = (
        await async_client.post("/api/v1/folders", headers=headers, json={"name": "Dept CS"})
    ).json()
    f_b = (
        await async_client.post(
            "/api/v1/folders",
            headers=headers,
            json={"name": "Semester 1", "parent_folder_id": f_a["folder_id"]},
        )
    ).json()
    f_c = (
        await async_client.post(
            "/api/v1/folders",
            headers=headers,
            json={"name": "Algorithms", "parent_folder_id": f_b["folder_id"]},
        )
    ).json()

    contents_res = await async_client.get(
        f"/api/v1/folders/{f_c['folder_id']}/contents", headers=headers
    )
    assert contents_res.status_code == 200
    data = contents_res.json()
    breadcrumbs = data["breadcrumbs"]
    assert len(breadcrumbs) == 3
    assert breadcrumbs[0]["name"] == "Dept CS"
    assert breadcrumbs[1]["name"] == "Semester 1"
    assert breadcrumbs[2]["name"] == "Algorithms"


@pytest.mark.asyncio
async def test_ec04_1_self_parenting_rejected(async_client: AsyncClient):
    """EC-04.1: Moving folder into itself must be rejected with HTTP 400."""
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    folder = (
        await async_client.post("/api/v1/folders", headers=headers, json={"name": "Self Loop Test"})
    ).json()

    res = await async_client.patch(
        f"/api/v1/folders/{folder['folder_id']}",
        headers=headers,
        json={"parent_folder_id": folder["folder_id"]},
    )
    assert res.status_code == 400
    assert "circular hierarchy" in res.json()["detail"]


@pytest.mark.asyncio
async def test_ec04_2_deep_tree_cycle_rejected(async_client: AsyncClient):
    """EC-04.2: Moving ancestor folder into its descendant must be rejected with HTTP 400."""
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    # A -> B -> C
    f_a = (
        await async_client.post("/api/v1/folders", headers=headers, json={"name": "Grandparent A"})
    ).json()
    f_b = (
        await async_client.post(
            "/api/v1/folders",
            headers=headers,
            json={"name": "Parent B", "parent_folder_id": f_a["folder_id"]},
        )
    ).json()
    f_c = (
        await async_client.post(
            "/api/v1/folders",
            headers=headers,
            json={"name": "Child C", "parent_folder_id": f_b["folder_id"]},
        )
    ).json()

    # Attempt to move A under C (making A a child of C -> circular loop)
    res = await async_client.patch(
        f"/api/v1/folders/{f_a['folder_id']}",
        headers=headers,
        json={"parent_folder_id": f_c["folder_id"]},
    )
    assert res.status_code == 400
    assert "circular hierarchy" in res.json()["detail"]


@pytest.mark.asyncio
async def test_ec04_3_non_empty_deletion_rejected(
    async_client: AsyncClient, db_session: AsyncSession
):
    """EC-04.3: Deleting a non-empty folder must be rejected with HTTP 409 Conflict (SD-09)."""
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    # Case 1: Folder containing subfolder
    parent = (
        await async_client.post("/api/v1/folders", headers=headers, json={"name": "Parent Folder"})
    ).json()
    await async_client.post(
        "/api/v1/folders",
        headers=headers,
        json={"name": "Sub Child", "parent_folder_id": parent["folder_id"]},
    )

    del_res = await async_client.delete(f"/api/v1/folders/{parent['folder_id']}", headers=headers)
    assert del_res.status_code == 409
    assert "not empty" in del_res.json()["detail"]

    # Case 2: Folder containing a resource
    single_folder = (
        await async_client.post(
            "/api/v1/folders", headers=headers, json={"name": "Resource Holding Folder"}
        )
    ).json()

    # Query student user
    student = await db_session.scalar(select(User).where(User.email == "student@amu.edu"))
    assert student is not None
    res_obj = Resource(
        owner_id=student.user_id,
        folder_id=single_folder["folder_id"],
        title="Inside Folder Resource",
        status="PRIVATE",
        visibility="PRIVATE",
    )
    db_session.add(res_obj)
    await db_session.commit()

    del_res2 = await async_client.delete(
        f"/api/v1/folders/{single_folder['folder_id']}", headers=headers
    )
    assert del_res2.status_code == 409
    assert "not empty" in del_res2.json()["detail"]


@pytest.mark.asyncio
async def test_ec04_4_cross_tenant_folder_access_blocked(async_client: AsyncClient):
    """EC-04.4: Student cannot access, modify, or delete another student's private folder."""
    # Student A creates folder
    token_a = await get_auth_token(async_client, "student@amu.edu")
    folder_a = (
        await async_client.post(
            "/api/v1/folders",
            headers={"Authorization": f"Bearer {token_a}"},
            json={"name": "Student A Private Notes"},
        )
    ).json()

    # Teacher or another user attempts to inspect or delete Student A's private folder
    token_teacher = await get_auth_token(async_client, "teacher@amu.edu")
    headers_teacher = {"Authorization": f"Bearer {token_teacher}"}

    # 1. Content access blocked
    res1 = await async_client.get(
        f"/api/v1/folders/{folder_a['folder_id']}/contents", headers=headers_teacher
    )
    assert res1.status_code == 403

    # 2. Rename/move blocked
    res2 = await async_client.patch(
        f"/api/v1/folders/{folder_a['folder_id']}",
        headers=headers_teacher,
        json={"name": "Tampered Name"},
    )
    assert res2.status_code == 403

    # 3. Deletion blocked
    res3 = await async_client.delete(
        f"/api/v1/folders/{folder_a['folder_id']}", headers=headers_teacher
    )
    assert res3.status_code == 403


@pytest.mark.asyncio
async def test_delete_empty_folder_success(async_client: AsyncClient):
    """Test successful deletion of an empty folder."""
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    folder = (
        await async_client.post(
            "/api/v1/folders", headers=headers, json={"name": "Temporary Folder"}
        )
    ).json()

    del_res = await async_client.delete(f"/api/v1/folders/{folder['folder_id']}", headers=headers)
    assert del_res.status_code == 200
    assert "deleted successfully" in del_res.json()["message"]


@pytest.mark.asyncio
async def test_get_folder_by_id_metadata(async_client: AsyncClient):
    """Test retrieving single folder metadata by ID."""
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    create_res = await async_client.post(
        "/api/v1/folders", headers=headers, json={"name": "Metadata Test Folder"}
    )
    folder_id = create_res.json()["folder_id"]

    get_res = await async_client.get(f"/api/v1/folders/{folder_id}", headers=headers)
    assert get_res.status_code == 200
    data = get_res.json()
    assert data["folder_id"] == folder_id
    assert data["name"] == "Metadata Test Folder"


@pytest.mark.asyncio
async def test_folder_contents_pagination_filtering_and_sorting(
    async_client: AsyncClient,
):
    """Test folder contents pagination, query filter, and sorting."""
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create main folder
    root_res = await async_client.post(
        "/api/v1/folders", headers=headers, json={"name": "Semester Archive"}
    )
    root_id = root_res.json()["folder_id"]

    # 2. Create subfolders
    for sub_name in ["Alpha Course", "Beta Course", "Gamma Course"]:
        await async_client.post(
            "/api/v1/folders",
            headers=headers,
            json={"name": sub_name, "parent_folder_id": root_id},
        )

    # 3. Create resources in folder
    for title in ["Math Homework 1", "Math Homework 2", "Physics Notes"]:
        await async_client.post(
            "/api/v1/resources",
            headers=headers,
            json={
                "title": title,
                "description": f"Study notes for {title}",
                "folder_id": root_id,
                "external_url": "https://amu.ac.in/notes.html",
            },
        )

    # 4. Test query filter q="Math"
    q_res = await async_client.get(
        f"/api/v1/folders/{root_id}/contents",
        headers=headers,
        params={"q": "Math"},
    )
    assert q_res.status_code == 200
    q_data = q_res.json()
    assert len(q_data["subfolders"]) == 0
    assert len(q_data["resources"]) == 2
    assert all("Math" in r["title"] for r in q_data["resources"])

    # 5. Test subfolder search q="Alpha"
    alpha_res = await async_client.get(
        f"/api/v1/folders/{root_id}/contents",
        headers=headers,
        params={"q": "Alpha"},
    )
    assert alpha_res.status_code == 200
    alpha_data = alpha_res.json()
    assert len(alpha_data["subfolders"]) == 1
    assert alpha_data["subfolders"][0]["name"] == "Alpha Course"

    # 6. Test pagination metadata
    page_res = await async_client.get(
        f"/api/v1/folders/{root_id}/contents",
        headers=headers,
        params={"page": 1, "limit": 2},
    )
    assert page_res.status_code == 200
    p_data = page_res.json()
    assert p_data["page"] == 1
    assert p_data["limit"] == 2
    assert p_data["total_subfolders"] == 3
    assert p_data["total_resources"] == 3
    assert len(p_data["subfolders"]) == 2
    assert len(p_data["resources"]) == 2

    # 7. Test sorting descending
    sort_res = await async_client.get(
        f"/api/v1/folders/{root_id}/contents",
        headers=headers,
        params={"sort_by": "name", "order": "desc"},
    )
    assert sort_res.status_code == 200
    s_data = sort_res.json()
    sub_names = [f["name"] for f in s_data["subfolders"]]
    assert sub_names == ["Gamma Course", "Beta Course", "Alpha Course"]


@pytest.mark.asyncio
async def test_folder_recursive_cascade_deletion(async_client: AsyncClient):
    """Test recursive=true cascade deletion of folder tree, child folders, and resources."""
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create top folder
    top_res = await async_client.post(
        "/api/v1/folders", headers=headers, json={"name": "Tree Root To Delete"}
    )
    top_id = top_res.json()["folder_id"]

    # 2. Create nested subfolder
    sub_res = await async_client.post(
        "/api/v1/folders",
        headers=headers,
        json={"name": "Child Level 1", "parent_folder_id": top_id},
    )
    sub_id = sub_res.json()["folder_id"]

    # 3. Create resource in subfolder
    res_obj = await async_client.post(
        "/api/v1/resources",
        headers=headers,
        json={
            "title": "Nested Leaf Resource",
            "folder_id": sub_id,
            "external_url": "https://amu.ac.in/leaf.html",
        },
    )
    resource_id = res_obj.json()["resource_id"]

    # 4. Standard delete without recursive flag must fail (409 Conflict)
    fail_del = await async_client.delete(
        f"/api/v1/folders/{top_id}",
        headers=headers,
    )
    assert fail_del.status_code == 409

    # 5. Recursive delete must succeed (200 OK)
    success_del = await async_client.delete(
        f"/api/v1/folders/{top_id}",
        headers=headers,
        params={"recursive": True},
    )
    assert success_del.status_code == 200

    # 6. Verify top folder, subfolder, and resource are all gone (404)
    check_top = await async_client.get(f"/api/v1/folders/{top_id}", headers=headers)
    assert check_top.status_code == 404

    check_sub = await async_client.get(f"/api/v1/folders/{sub_id}", headers=headers)
    assert check_sub.status_code == 404

    check_res = await async_client.get(f"/api/v1/resources/{resource_id}", headers=headers)
    assert check_res.status_code == 404
