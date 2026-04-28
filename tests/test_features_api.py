"""Tests for /api/me/features."""
import pytest
from httpx import ASGITransport, AsyncClient

from api.auth import hash_password
from api.main import app
from api.settings_store import set_setting
from config import settings as cfg

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client(db) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _login(client, username, password):
    r = await client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def _seed_staff(db, username: str, password: str, role: str = "staff"):
    await db.execute(
        "INSERT INTO staff_users (username, password_hash, role) "
        "VALUES (?, ?, ?)",
        (username, hash_password(password), role),
    )
    await db.commit()


async def test_features_requires_auth(client, db):
    r = await client.get("/api/me/features")
    assert r.status_code == 401


async def test_features_admin_sees_data_analyst_when_enabled(client, db):
    await set_setting("data_analyst_enabled", "true")
    h = await _login(client, cfg.staff_username, cfg.staff_password)
    body = (await client.get("/api/me/features", headers=h)).json()
    assert body["data_analyst_visible"] is True


async def test_features_admin_hidden_when_master_off(client, db):
    await set_setting("data_analyst_enabled", "false")
    h = await _login(client, cfg.staff_username, cfg.staff_password)
    body = (await client.get("/api/me/features", headers=h)).json()
    assert body["data_analyst_visible"] is False


async def test_features_staff_hidden_when_visibility_off(client, db):
    await set_setting("data_analyst_enabled", "true")
    await set_setting("data_analyst_visible_to_staff", "false")
    await _seed_staff(db, "reg", "r")
    h = await _login(client, "reg", "r")
    body = (await client.get("/api/me/features", headers=h)).json()
    assert body["data_analyst_visible"] is False


async def test_features_staff_sees_when_visibility_on(client, db):
    await set_setting("data_analyst_enabled", "true")
    await set_setting("data_analyst_visible_to_staff", "true")
    await _seed_staff(db, "reg2", "r")
    h = await _login(client, "reg2", "r")
    body = (await client.get("/api/me/features", headers=h)).json()
    assert body["data_analyst_visible"] is True
