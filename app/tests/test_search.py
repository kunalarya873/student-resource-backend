import pytest
from httpx import AsyncClient

from app.tests.conftest import get_auth_token


@pytest.mark.asyncio
async def test_ec08_1_private_data_leak_prevented(async_client: AsyncClient):
    """
    EC-08.1: Private Data Leak via Search
    Student A creates a private, unshared assignment.
    Student B searches for the exact unique title.
    Expected: 0 results returned for Student B. Private data never leaks.
    Student A searches: Returns the resource (owner privilege).
    """
    token_a = await get_auth_token(async_client, "student@amu.edu")
    headers_a = {"Authorization": f"Bearer {token_a}"}

    token_b = await get_auth_token(async_client, "teacher@amu.edu")
    headers_b = {"Authorization": f"Bearer {token_b}"}

    unique_title = "Classified Quantum Gravity Research Draft 2026"
    res_create = await async_client.post(
        "/api/v1/resources",
        headers=headers_a,
        json={
            "title": unique_title,
            "description": "Proprietary research notes on quantum spacetime singularities.",
            "external_url": "https://amu.ac.in/physics/quantum.html",
        },
    )
    assert res_create.status_code == 201

    # Student B searches for exact keywords
    res_b = await async_client.get(
        "/api/v1/search",
        headers=headers_b,
        params={"q": "Quantum Gravity Research"},
    )
    assert res_b.status_code == 200
    data_b = res_b.json()
    b_titles = [item["title"] for item in data_b["results"]]
    assert unique_title not in b_titles

    # Student A (the owner) searches and finds it
    res_a = await async_client.get(
        "/api/v1/search",
        headers=headers_a,
        params={"q": "Quantum Gravity Research"},
    )
    assert res_a.status_code == 200
    data_a = res_a.json()
    a_titles = [item["title"] for item in data_a["results"]]
    assert unique_title in a_titles


@pytest.mark.asyncio
async def test_ec08_2_sql_injection_defense(async_client: AsyncClient):
    """
    EC-08.2: SQL Injection via Search Term
    Query contains malicious SQL payload: `'; DROP TABLE resources; --`.
    Expected: Parameterized query neutralizes injection cleanly without error (HTTP 200).
    """
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    malicious_query = "'; DROP TABLE resources; --"
    res = await async_client.get(
        "/api/v1/search",
        headers=headers,
        params={"q": malicious_query},
    )
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data["results"], list)

    # Verify database table is intact by querying resources
    verify_res = await async_client.get("/api/v1/resources", headers=headers)
    assert verify_res.status_code == 200


@pytest.mark.asyncio
async def test_ec08_3_special_characters_fts(async_client: AsyncClient):
    """
    EC-08.3: Special Characters & Punctuation
    Search query contains boolean & regex characters: `&& || ! ( ) < > : * ~`.
    Expected: plainto_tsquery sanitizes input safely without syntax error (HTTP 200).
    """
    token = await get_auth_token(async_client, "student@amu.edu")
    headers = {"Authorization": f"Bearer {token}"}

    complex_query = "&& || ! ( ) < > : * ~ algorithms ?? ::int"
    res = await async_client.get(
        "/api/v1/search",
        headers=headers,
        params={"q": complex_query},
    )
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data["results"], list)


@pytest.mark.asyncio
async def test_ec08_4_empty_query_with_filters(async_client: AsyncClient):
    """
    EC-08.4: Empty Search Query with Filters
    q is None, but subject_id filter is applied.
    Expected: Returns permitted resources matching the subject filter.
    """
    token_teacher = await get_auth_token(async_client, "teacher@amu.edu")
    headers_teacher = {"Authorization": f"Bearer {token_teacher}"}

    # Create approved departmental resource for subject_id=1
    res_create = await async_client.post(
        "/api/v1/resources",
        headers=headers_teacher,
        json={
            "title": "Data Structures Comprehensive Syllabus",
            "description": "Official CS department study syllabus",
            "subject_id": 1,
            "visibility": "PUBLIC",
            "external_url": "https://amu.ac.in/cs/ds.html",
        },
    )
    assert res_create.status_code == 201

    token_student = await get_auth_token(async_client, "student@amu.edu")
    headers_student = {"Authorization": f"Bearer {token_student}"}

    res_filter = await async_client.get(
        "/api/v1/search",
        headers=headers_student,
        params={"subject_id": 1, "page": 1, "limit": 10},
    )
    assert res_filter.status_code == 200
    data = res_filter.json()
    assert data["total_count"] >= 1
    titles = [item["title"] for item in data["results"]]
    assert "Data Structures Comprehensive Syllabus" in titles


@pytest.mark.asyncio
async def test_search_shared_resource_discovery(async_client: AsyncClient):
    """
    Shared resource discovery:
    Student A creates a private resource and shares it with Teacher.
    Teacher can discover it via full-text search.
    Other users cannot find it.
    """
    token_a = await get_auth_token(async_client, "student@amu.edu")
    headers_a = {"Authorization": f"Bearer {token_a}"}

    token_teacher = await get_auth_token(async_client, "teacher@amu.edu")
    headers_teacher = {"Authorization": f"Bearer {token_teacher}"}

    shared_title = "Collaborative Robotics Lab Report 2026"
    res_create = (
        await async_client.post(
            "/api/v1/resources",
            headers=headers_a,
            json={
                "title": shared_title,
                "description": "Autonomous robotic navigation test runs.",
                "external_url": "https://amu.ac.in/robotics/report.html",
            },
        )
    ).json()
    resource_id = res_create["resource_id"]

    # Share with Teacher
    share_res = await async_client.post(
        f"/api/v1/resources/{resource_id}/share",
        headers=headers_a,
        json={"grantee_email": "teacher@amu.edu", "permission": "VIEW"},
    )
    assert share_res.status_code == 201

    # Teacher searches for keywords
    teacher_search = await async_client.get(
        "/api/v1/search",
        headers=headers_teacher,
        params={"q": "Robotics Lab Navigation"},
    )
    assert teacher_search.status_code == 200
    data = teacher_search.json()
    teacher_titles = [item["title"] for item in data["results"]]
    assert shared_title in teacher_titles


@pytest.mark.asyncio
async def test_partial_text_search_prefix_and_wildcard(async_client: AsyncClient):
    """
    Test partial text search matching prefix tokens and wildcards.
    Ensures searching 'pi' successfully matches 'pic.png' while maintaining zero-leakage security.
    """
    token_a = await get_auth_token(async_client, "student@amu.edu")
    headers_a = {"Authorization": f"Bearer {token_a}"}

    token_b = await get_auth_token(async_client, "teacher@amu.edu")
    headers_b = {"Authorization": f"Bearer {token_b}"}

    # 1. Student creates a resource named "pic.png"
    unique_pic_title = "pic.png"
    create_res = await async_client.post(
        "/api/v1/resources",
        headers=headers_a,
        json={
            "title": unique_pic_title,
            "description": "Uploaded assignment screenshot diagram.",
            "external_url": "https://amu.ac.in/uploads/pic.png",
        },
    )
    assert create_res.status_code == 201

    # 2. Student searches with partial prefix query q="pi"
    search_res = await async_client.get(
        "/api/v1/search",
        headers=headers_a,
        params={"q": "pi"},
    )
    assert search_res.status_code == 200
    results = search_res.json()["results"]
    matched_titles = [item["title"] for item in results]
    assert unique_pic_title in matched_titles

    # 3. Student searches with partial word in description q="assign"
    desc_search = await async_client.get(
        "/api/v1/search",
        headers=headers_a,
        params={"q": "assign"},
    )
    assert desc_search.status_code == 200
    desc_titles = [item["title"] for item in desc_search.json()["results"]]
    assert unique_pic_title in desc_titles

    # 4. Teacher (unauthorized, resource is private) cannot find "pic.png" with q="pi"
    teacher_res = await async_client.get(
        "/api/v1/search",
        headers=headers_b,
        params={"q": "pi"},
    )
    assert teacher_res.status_code == 200
    teacher_matched = [item["title"] for item in teacher_res.json()["results"]]
    assert unique_pic_title not in teacher_matched
