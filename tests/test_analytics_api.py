"""Tests for the by-college dimension on /api/analytics/summary."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import app
from config import settings as cfg
from db import get_db
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


async def _make_completed_entry(
    *,
    discord_id: str,
    discord_name: str,
    college_id: int | None,
    machine_id: int = 1,
) -> int:
    """Register a user and stamp a completed queue entry on them.

    serving_at and completed_at are set so wait/serve calcs are non-null.
    Returns the user id.
    """
    user = await models.get_or_create_user(discord_id, discord_name)
    if college_id is not None:
        await models.register_user(
            user["id"],
            full_name=discord_name,
            email=f"{discord_id}@illinois.edu",
            major="X",
            college_id=college_id,
            graduation_year="2027",
        )
    else:
        # User has a queue entry but no college link.
        pass
    entry = await models.join_queue(user["id"], machine_id)
    db = await get_db()
    await db.execute(
        """
        UPDATE queue_entries
        SET status = 'completed',
            serving_at  = datetime('now', '-30 minutes'),
            completed_at = datetime('now', '-10 minutes')
        WHERE id = ?
        """,
        (entry["id"],),
    )
    await db.commit()
    return user["id"]


@pytest.fixture
async def seeded_completed_jobs(db):
    """Seed three completed queue entries split across two real colleges,
    plus matching analytics_snapshots rows so the unfiltered summary
    (which reads from snapshots) reflects them."""
    from datetime import datetime as _dt

    college_a = await models.create_college("Test College A")
    college_b = await models.create_college("Test College B")
    await _make_completed_entry(
        discord_id="user_a1",
        discord_name="A1",
        college_id=college_a["id"],
        machine_id=1,
    )
    await _make_completed_entry(
        discord_id="user_a2",
        discord_name="A2",
        college_id=college_a["id"],
        machine_id=1,
    )
    await _make_completed_entry(
        discord_id="user_b1",
        discord_name="B1",
        college_id=college_b["id"],
        machine_id=2,
    )
    today = _dt.utcnow().date().isoformat()
    await models.insert_analytics_snapshot(
        date=today, machine_id=1, total_jobs=2, completed_jobs=2,
        avg_wait_mins=20.0, avg_serve_mins=20.0, peak_hour=10,
        ai_summary=None, no_show_count=0, cancelled_count=0,
        unique_users=2, failure_count=0,
    )
    await models.insert_analytics_snapshot(
        date=today, machine_id=2, total_jobs=1, completed_jobs=1,
        avg_wait_mins=20.0, avg_serve_mins=20.0, peak_hour=10,
        ai_summary=None, no_show_count=0, cancelled_count=0,
        unique_users=1, failure_count=0,
    )
    return {"a_id": college_a["id"], "b_id": college_b["id"]}


@pytest.fixture
async def college_a_id(seeded_completed_jobs) -> int:
    return seeded_completed_jobs["a_id"]


@pytest.fixture
async def user_with_null_college(db):
    """A user with a completed queue entry and college_id IS NULL."""
    return await _make_completed_entry(
        discord_id="user_null",
        discord_name="UnsetCollege",
        college_id=None,
        machine_id=1,
    )


async def test_analytics_response_includes_colleges_block(
    client: AsyncClient, seeded_completed_jobs
):
    h = await _admin_headers(client)
    res = await client.get("/api/analytics/summary?period=week", headers=h)
    assert res.status_code == 200, res.text
    body = res.json()
    assert "colleges" in body
    assert isinstance(body["colleges"], list)
    # Both seeded colleges should appear.
    names = {c["college_name"] for c in body["colleges"]}
    assert "Test College A" in names
    assert "Test College B" in names


async def test_analytics_filter_by_college_id_narrows_results(
    client: AsyncClient, seeded_completed_jobs, college_a_id
):
    h = await _admin_headers(client)
    full = (
        await client.get("/api/analytics/summary?period=week", headers=h)
    ).json()
    filtered = (
        await client.get(
            f"/api/analytics/summary?period=week&college_id={college_a_id}",
            headers=h,
        )
    ).json()
    assert filtered["summary"]["total_jobs"] <= full["summary"]["total_jobs"]
    # The filtered colleges block should contain only college_a.
    filtered_names = {c["college_name"] for c in filtered["colleges"]}
    assert "Test College A" in filtered_names
    assert "Test College B" not in filtered_names


async def test_unspecified_bucket_aggregates_null_college_id(
    client: AsyncClient, seeded_completed_jobs, user_with_null_college
):
    h = await _admin_headers(client)
    body = (
        await client.get("/api/analytics/summary?period=week", headers=h)
    ).json()
    unspec = next(
        (c for c in body["colleges"] if c["college_name"] == "Unspecified"),
        None,
    )
    assert unspec is not None
    assert unspec["total_jobs"] >= 1
    assert unspec["college_id"] == 0


# ── Feedback rating accents on summary/machines/colleges ─────────────────


async def _seed_completed_entry(discord_id: str) -> dict:
    """Seed a user + completed queue entry; returns the entry dict."""
    user = await models.get_or_create_user(discord_id=discord_id, discord_name="u")
    machines = await models.get_machines()
    entry = await models.join_queue(user["id"], machines[0]["id"])
    await models.update_entry_status(entry["id"], "serving")
    await models.update_entry_status(entry["id"], "completed", job_successful=1)
    return entry


async def test_analytics_summary_avg_rating_none_when_empty(
    client: AsyncClient, db
):
    h = await _admin_headers(client)
    body = (
        await client.get("/api/analytics/summary?period=week", headers=h)
    ).json()
    assert body["summary"]["rating_count"] >= 0
    if body["summary"]["rating_count"] == 0:
        assert body["summary"]["avg_rating"] is None


async def test_analytics_summary_avg_rating_matches(client: AsyncClient, db):
    e1 = await _seed_completed_entry(discord_id="ar-1")
    e2 = await _seed_completed_entry(discord_id="ar-2")
    await models.create_feedback(queue_entry_id=e1["id"], rating=4, comment=None)
    await models.create_feedback(queue_entry_id=e2["id"], rating=5, comment=None)
    h = await _admin_headers(client)
    body = (
        await client.get("/api/analytics/summary?period=week", headers=h)
    ).json()
    assert body["summary"]["rating_count"] == 2
    assert body["summary"]["avg_rating"] == 4.5


async def test_analytics_machines_and_colleges_have_rating_fields(
    client: AsyncClient, db
):
    h = await _admin_headers(client)
    body = (
        await client.get("/api/analytics/summary?period=week", headers=h)
    ).json()
    if body["machines"]:
        assert "avg_rating" in body["machines"][0]
        assert "rating_count" in body["machines"][0]
    if body["colleges"]:
        assert "avg_rating" in body["colleges"][0]
        assert "rating_count" in body["colleges"][0]


# ---------------------------------------------------------------------------- #
# /api/analytics/export
# ---------------------------------------------------------------------------- #

async def test_export_requires_auth(client: AsyncClient, db):
    res = await client.get("/api/analytics/export?format=csv")
    assert res.status_code == 401


async def test_export_csv_returns_section_headers(
    client: AsyncClient, db
):
    h = await _admin_headers(client)
    res = await client.get(
        "/api/analytics/export?format=csv&period=week", headers=h,
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "attachment" in res.headers.get("content-disposition", "")
    body = res.text
    assert "## Summary" in body
    assert "## Machines" in body
    assert "## Colleges" in body
    assert "total_jobs" in body


async def test_export_pdf_returns_application_pdf(
    client: AsyncClient, db
):
    h = await _admin_headers(client)
    res = await client.get(
        "/api/analytics/export?format=pdf&period=week", headers=h,
    )
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert res.content.startswith(b"%PDF")
    assert b"%%EOF" in res.content


async def test_export_invalid_format_returns_400(client: AsyncClient, db):
    h = await _admin_headers(client)
    res = await client.get(
        "/api/analytics/export?format=xml&period=week", headers=h,
    )
    assert res.status_code == 400


async def test_export_csv_honors_college_filter(client: AsyncClient, db):
    college_a = await models.create_college("Export College A")
    e1 = await _seed_completed_entry(discord_id="exp-1")
    # tag user 1 with college A
    user_a = await models.get_user_by_discord_id("exp-1")
    db_conn = await models.get_db()
    await db_conn.execute(
        "UPDATE users SET college_id = ? WHERE id = ?",
        (college_a["id"], user_a["id"]),
    )
    await db_conn.commit()
    await models.create_feedback(
        queue_entry_id=e1["id"], rating=5, comment=None,
    )

    h = await _admin_headers(client)
    res = await client.get(
        f"/api/analytics/export?format=csv&period=week"
        f"&college_id={college_a['id']}",
        headers=h,
    )
    assert res.status_code == 200
    body = res.text
    assert f"filter_college_id,{college_a['id']}" in body
