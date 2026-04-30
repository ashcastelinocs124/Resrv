"""Tests for /api/pinned-charts."""

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


async def _admin_headers(client):
    r = await client.post(
        "/api/auth/login",
        json={
            "username": cfg.staff_username,
            "password": cfg.staff_password,
        },
    )
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _spec(title: str = "S", **extra) -> dict:
    return {
        "type": "bar",
        "title": title,
        "x": {"field": "label", "label": "Group"},
        "y": {"field": "value", "label": "Count"},
        "data": [{"label": "A", "value": 1}],
        **extra,
    }


async def test_get_requires_auth(client, db):
    r = await client.get("/api/pinned-charts")
    assert r.status_code == 401


async def test_post_creates_pinned_chart_with_creator_username(client, db):
    h = await _admin_headers(client)
    r = await client.post(
        "/api/pinned-charts",
        headers=h,
        json={"chart_spec": _spec(), "title": "First chart"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "First chart"
    assert body["chart_spec"]["type"] == "bar"
    assert body["created_by_username"] == cfg.staff_username


async def test_get_returns_rows_ordered_by_pin_order(client, db):
    h = await _admin_headers(client)
    await client.post(
        "/api/pinned-charts", headers=h,
        json={"chart_spec": _spec("A"), "title": "A"},
    )
    await client.post(
        "/api/pinned-charts", headers=h,
        json={"chart_spec": _spec("B"), "title": "B"},
    )
    r = await client.get("/api/pinned-charts", headers=h)
    titles = [c["title"] for c in r.json()]
    assert titles == ["A", "B"]


async def test_delete_then_404(client, db):
    h = await _admin_headers(client)
    r = await client.post(
        "/api/pinned-charts", headers=h,
        json={"chart_spec": _spec(), "title": "X"},
    )
    cid = r.json()["id"]
    r1 = await client.delete(f"/api/pinned-charts/{cid}", headers=h)
    assert r1.status_code == 200
    r2 = await client.delete(f"/api/pinned-charts/{cid}", headers=h)
    assert r2.status_code == 404


async def test_refresh_reruns_query(client, db):
    """Pin a chart with context, seed jobs, refresh — data updates."""
    machines = await models.list_machines()
    h = await _admin_headers(client)

    # Seed 2 jobs on machine 0.
    user = await models.get_or_create_user(discord_id="u1", discord_name="u1")
    conn = await models.get_db()
    for _ in range(2):
        await conn.execute(
            """
            INSERT INTO queue_entries (user_id, machine_id, status, position, joined_at)
            VALUES (?, ?, 'completed', 1, datetime('now'))
            """,
            (user["id"], machines[0]["id"]),
        )
    await conn.commit()

    spec = _spec(
        "Per-machine count",
        context={
            "filter": {},
            "group_by": "machine",
            "metric": "count",
            "period": "day",
        },
    )
    spec["data"] = []
    r = await client.post(
        "/api/pinned-charts", headers=h,
        json={"chart_spec": spec, "title": "Per-machine count"},
    )
    cid = r.json()["id"]

    refreshed = await client.post(
        f"/api/pinned-charts/{cid}/refresh", headers=h,
    )
    assert refreshed.status_code == 200
    fresh_spec = refreshed.json()["chart_spec"]
    assert fresh_spec["data"]
    machine_row = next(
        d for d in fresh_spec["data"] if d["label"] == machines[0]["name"]
    )
    assert machine_row["value"] == 2


async def test_post_validates_title_length(client, db):
    h = await _admin_headers(client)
    r = await client.post(
        "/api/pinned-charts", headers=h,
        json={"chart_spec": _spec(), "title": ""},
    )
    assert r.status_code == 422
