"""Hidden black-box API acceptance suite for Dev Spec S1 (Bookmark Manager)."""

from __future__ import annotations

import importlib
import os
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest


@pytest.fixture
async def api_client() -> AsyncIterator[httpx.AsyncClient]:
    base_url = os.environ.get("FOUNDRY_BENCH_BASE_URL")
    if base_url:
        async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
            yield client
    else:
        app = importlib.import_module("app.main").app
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def _register_and_login(
    client: httpx.AsyncClient,
    *,
    email: str,
    password: str | None = None,
    name: str = "Test User",
) -> tuple[dict[str, str], dict[str, object]]:
    secret = password or f"pw_{uuid.uuid4().hex[:12]}"
    reg = await client.post(
        "/api/auth/register",
        json={"email": email, "password": secret, "name": name},
    )
    assert reg.status_code == 201, f"register failed: {reg.status_code} {reg.text}"
    user_data = reg.json()
    assert user_data["email"] == email
    assert "id" in user_data

    login = await client.post(
        "/api/auth/login",
        json={"email": email, "password": secret},
    )
    assert login.status_code == 200, f"login failed: {login.status_code} {login.text}"
    tok = login.json()
    assert tok["access_token"]
    assert tok.get("token_type", "").lower() == "bearer"
    return {"Authorization": f"Bearer {tok['access_token']}"}, user_data


async def test_health_endpoint(api_client: httpx.AsyncClient) -> None:
    resp = await api_client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json().get("status") == "ok"


async def test_auth_register_login_and_me_guard(api_client: httpx.AsyncClient) -> None:
    unauth = await api_client.get("/api/auth/me")
    assert unauth.status_code == 401

    email = f"alice_{uuid.uuid4().hex[:8]}@example.com"
    headers, created = await _register_and_login(api_client, email=email, name="Alice")

    me = await api_client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    me_body = me.json()
    assert me_body["id"] == created["id"]
    assert me_body["email"] == email
    assert me_body["name"] == "Alice"

    bad_login = await api_client.post(
        "/api/auth/login",
        json={"email": email, "password": "wrong-password"},
    )
    assert bad_login.status_code == 401


async def test_bookmark_crud_search_and_url_validation(api_client: httpx.AsyncClient) -> None:
    email = f"bob_{uuid.uuid4().hex[:8]}@example.com"
    headers, _ = await _register_and_login(api_client, email=email, name="Bob")

    invalid = await api_client.post(
        "/api/bookmarks",
        headers=headers,
        json={"url": "ftp://not-http.example", "title": "Invalid URL"},
    )
    assert invalid.status_code == 422

    created_1 = await api_client.post(
        "/api/bookmarks",
        headers=headers,
        json={
            "url": "https://fastapi.tiangolo.com",
            "title": "FastAPI Docs",
            "description": "Async Python web framework",
            "tags": ["python", "api"],
        },
    )
    assert created_1.status_code == 201
    bm1 = created_1.json()
    bm1_id = bm1["id"]
    assert bm1["title"] == "FastAPI Docs"
    assert set(bm1["tags"]) == {"python", "api"}

    created_2 = await api_client.post(
        "/api/bookmarks",
        headers=headers,
        json={
            "url": "https://react.dev",
            "title": "React Official",
            "description": "Frontend UI library",
            "tags": ["frontend"],
        },
    )
    assert created_2.status_code == 201

    search_resp = await api_client.get(
        "/api/bookmarks",
        headers=headers,
        params={"q": "fastapi", "page": 1, "size": 10},
    )
    assert search_resp.status_code == 200
    page = search_resp.json()
    assert page["total"] == 1
    assert page["page"] == 1
    assert page["size"] == 10
    assert [item["id"] for item in page["items"]] == [bm1_id]

    detail = await api_client.get(f"/api/bookmarks/{bm1_id}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["url"] == "https://fastapi.tiangolo.com"

    patched = await api_client.patch(
        f"/api/bookmarks/{bm1_id}",
        headers=headers,
        json={"title": "FastAPI Reference", "tags": ["python", "backend"]},
    )
    assert patched.status_code == 200
    assert patched.json()["title"] == "FastAPI Reference"
    assert set(patched.json()["tags"]) == {"python", "backend"}

    deleted = await api_client.delete(f"/api/bookmarks/{bm1_id}", headers=headers)
    assert deleted.status_code == 204

    after_del = await api_client.get(f"/api/bookmarks/{bm1_id}", headers=headers)
    assert after_del.status_code == 404


async def test_bookmark_user_isolation(api_client: httpx.AsyncClient) -> None:
    headers_a, _ = await _register_and_login(
        api_client, email=f"owner_{uuid.uuid4().hex[:8]}@example.com"
    )
    headers_b, _ = await _register_and_login(
        api_client, email=f"other_{uuid.uuid4().hex[:8]}@example.com"
    )

    resp = await api_client.post(
        "/api/bookmarks",
        headers=headers_a,
        json={"url": "https://secret.internal.example", "title": "Private Link", "tags": ["work"]},
    )
    assert resp.status_code == 201
    bm_id = resp.json()["id"]

    # User B cannot read, list, update, or delete User A's bookmark
    assert (await api_client.get(f"/api/bookmarks/{bm_id}", headers=headers_b)).status_code == 404
    assert (
        await api_client.patch(
            f"/api/bookmarks/{bm_id}", headers=headers_b, json={"title": "Hijack"}
        )
    ).status_code == 404
    assert (
        await api_client.delete(f"/api/bookmarks/{bm_id}", headers=headers_b)
    ).status_code == 404
    list_b = (await api_client.get("/api/bookmarks", headers=headers_b)).json()
    assert list_b["total"] == 0


async def test_tags_summary_counts(api_client: httpx.AsyncClient) -> None:
    headers_u1, _ = await _register_and_login(
        api_client, email=f"tagger1_{uuid.uuid4().hex[:8]}@example.com"
    )
    headers_u2, _ = await _register_and_login(
        api_client, email=f"tagger2_{uuid.uuid4().hex[:8]}@example.com"
    )

    await api_client.post(
        "/api/bookmarks",
        headers=headers_u1,
        json={"url": "https://a.example", "title": "A", "tags": ["zebra", "alpha"]},
    )
    await api_client.post(
        "/api/bookmarks",
        headers=headers_u1,
        json={"url": "https://b.example", "title": "B", "tags": ["alpha"]},
    )
    await api_client.post(
        "/api/bookmarks",
        headers=headers_u2,
        json={"url": "https://c.example", "title": "C", "tags": ["other_user_only"]},
    )

    tags_resp = await api_client.get("/api/tags", headers=headers_u1)
    assert tags_resp.status_code == 200
    items = tags_resp.json()["items"]
    assert items == [{"name": "alpha", "count": 2}, {"name": "zebra", "count": 1}]
