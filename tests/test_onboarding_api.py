"""Tests for /api/auth/me/onboarded + /api/auth/me onboarded_at exposure."""
import pytest
from httpx import ASGITransport, AsyncClient

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
        json={
            "username": cfg.staff_username,
            "password": cfg.staff_password,
        },
    )
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def test_onboarded_requires_auth(client, db):
    r = await client.post("/api/auth/me/onboarded")
    assert r.status_code == 401


async def test_onboarded_stamps_timestamp(client, db):
    conn = await models.get_db()
    await conn.execute("UPDATE staff_users SET onboarded_at = NULL")
    await conn.commit()
    h = await _admin_headers(client)
    res = await client.post("/api/auth/me/onboarded", headers=h)
    assert res.status_code == 200
    cursor = await conn.execute(
        "SELECT onboarded_at FROM staff_users WHERE username = ?",
        (cfg.staff_username,),
    )
    row = await cursor.fetchone()
    assert row["onboarded_at"] is not None


async def test_onboarded_idempotent(client, db):
    h = await _admin_headers(client)
    res1 = await client.post("/api/auth/me/onboarded", headers=h)
    res2 = await client.post("/api/auth/me/onboarded", headers=h)
    assert res1.status_code == 200
    assert res2.status_code == 200


async def test_me_returns_onboarded_at(client, db):
    h = await _admin_headers(client)
    res = await client.get("/api/auth/me", headers=h)
    body = res.json()
    assert "onboarded_at" in body
