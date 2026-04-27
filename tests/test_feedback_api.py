"""HTTP-layer tests for /api/feedback."""

from __future__ import annotations

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


async def _admin_headers(client: AsyncClient) -> dict[str, str]:
    r = await client.post(
        "/api/auth/login",
        json={"username": cfg.staff_username, "password": cfg.staff_password},
    )
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def _seed_one_feedback(rating=5, college_name="Listed", discord_id="fbapi"):
    user = await models.get_or_create_user(discord_id=discord_id, discord_name="u")
    college = await models.create_college(college_name)
    await models.register_user(
        user_id=user["id"],
        full_name="API User",
        email=f"{discord_id}@illinois.edu",
        major="CS",
        college_id=college["id"],
        graduation_year="2027",
    )
    machines = await models.get_machines()
    entry = await models.join_queue(user["id"], machines[0]["id"])
    await models.update_entry_status(entry["id"], "serving")
    await models.update_entry_status(entry["id"], "completed", job_successful=1)
    await models.create_feedback(
        queue_entry_id=entry["id"], rating=rating, comment="hello",
    )
    return entry, user, machines[0], college


async def test_get_feedback_requires_staff(client):
    res = await client.get("/api/feedback/")
    assert res.status_code == 401


async def test_get_feedback_returns_joined_rows(client):
    h = await _admin_headers(client)
    await _seed_one_feedback(rating=4)
    res = await client.get("/api/feedback/", headers=h)
    assert res.status_code == 200
    body = res.json()
    assert len(body) >= 1
    row = body[0]
    assert row["rating"] == 4
    assert row["comment"] == "hello"
    assert row["full_name"] == "API User"
    assert row["machine_name"]
    assert row["college_name"] == "Listed"


async def test_get_feedback_filters_by_min_rating(client):
    h = await _admin_headers(client)
    await _seed_one_feedback(rating=2, college_name="A", discord_id="lo")
    await _seed_one_feedback(rating=5, college_name="B", discord_id="hi")
    res = await client.get("/api/feedback/?min_rating=4", headers=h)
    body = res.json()
    assert all(r["rating"] >= 4 for r in body)


async def test_get_feedback_filters_by_machine_id(client):
    h = await _admin_headers(client)
    _, _, machine, _ = await _seed_one_feedback(
        rating=5, college_name="M", discord_id="m1",
    )
    res = await client.get(
        f"/api/feedback/?machine_id={machine['id']}", headers=h,
    )
    body = res.json()
    assert all(r["machine_id"] == machine["id"] for r in body)


async def test_get_feedback_invalid_min_rating_returns_422(client):
    h = await _admin_headers(client)
    res = await client.get("/api/feedback/?min_rating=-1", headers=h)
    assert res.status_code == 422


async def test_get_feedback_limit_caps_results(client):
    h = await _admin_headers(client)
    for i in range(5):
        await _seed_one_feedback(
            rating=3, college_name=f"C{i}", discord_id=f"lim-{i}",
        )
    res = await client.get("/api/feedback/?limit=2", headers=h)
    body = res.json()
    assert len(body) == 2
