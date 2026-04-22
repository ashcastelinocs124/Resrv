"""Tests for admin-gated machine CRUD."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import hash_password
from api.main import app
from config import settings as cfg
from db import models

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client(db) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _admin_headers(client):
    r = await client.post(
        "/api/auth/login",
        json={"username": cfg.staff_username, "password": cfg.staff_password},
    )
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def test_create_machine(client, db):
    h = await _admin_headers(client)
    r = await client.post(
        "/api/machines/", headers=h, json={"name": "New", "slug": "newest"}
    )
    assert r.status_code == 201
    assert r.json()["slug"] == "newest"


async def test_create_rejects_bad_slug(client, db):
    h = await _admin_headers(client)
    r = await client.post(
        "/api/machines/", headers=h, json={"name": "Bad", "slug": "Bad Slug"}
    )
    assert r.status_code == 400


async def test_archive_blocks_with_active_queue(client, db):
    h = await _admin_headers(client)
    machines = (await client.get("/api/machines/", headers=h)).json()
    mid = machines[0]["id"]
    user = await models.get_or_create_user("u1", "userA")
    await models.join_queue(user["id"], mid)
    r = await client.delete(f"/api/machines/{mid}", headers=h)
    assert r.status_code == 409


async def test_archive_then_include_archived(client, db):
    h = await _admin_headers(client)
    new = await client.post(
        "/api/machines/", headers=h, json={"name": "Z", "slug": "z-tool"}
    )
    mid = new.json()["id"]
    r = await client.delete(f"/api/machines/{mid}", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "archived"
    default = await client.get("/api/machines/", headers=h)
    assert all(m["id"] != mid for m in default.json())
    with_archived = await client.get(
        "/api/machines/?include_archived=true", headers=h
    )
    assert any(m["id"] == mid for m in with_archived.json())


async def test_purge_requires_confirm_slug(client, db):
    h = await _admin_headers(client)
    m = (await client.post(
        "/api/machines/", headers=h,
        json={"name": "Purge", "slug": "purge-me"},
    )).json()
    r = await client.request(
        "DELETE", f"/api/machines/{m['id']}?purge=true",
        headers=h, json={"confirm_slug": "wrong"},
    )
    assert r.status_code == 400
    r = await client.request(
        "DELETE", f"/api/machines/{m['id']}?purge=true",
        headers=h, json={"confirm_slug": "purge-me"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "purged"


async def test_restore(client, db):
    h = await _admin_headers(client)
    m = (await client.post(
        "/api/machines/", headers=h, json={"name": "R", "slug": "r-tool"}
    )).json()
    await client.delete(f"/api/machines/{m['id']}", headers=h)
    r = await client.post(f"/api/machines/{m['id']}/restore", headers=h)
    assert r.status_code == 200
    assert r.json()["archived_at"] is None


async def test_non_admin_cannot_archive(client, db):
    # Seed a staff-role user
    await db.execute(
        "INSERT INTO staff_users (username, password_hash, role) "
        "VALUES (?, ?, ?)",
        ("eve", hash_password("evepw"), "staff"),
    )
    await db.commit()
    login = await client.post(
        "/api/auth/login", json={"username": "eve", "password": "evepw"}
    )
    h = {"Authorization": f"Bearer {login.json()['token']}"}
    machines = (await client.get("/api/machines/", headers=h)).json()
    r = await client.delete(f"/api/machines/{machines[0]['id']}", headers=h)
    assert r.status_code == 403


async def test_public_can_read_machines(client, db):
    r = await client.get("/api/machines/")
    assert r.status_code == 200
    assert len(r.json()) > 0
