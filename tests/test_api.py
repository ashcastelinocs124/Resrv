"""Tests for the FastAPI API endpoints."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import app
from db import models

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client(db) -> AsyncClient:
    """Async test client with an initialised in-memory database."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def admin_headers(db, client: AsyncClient) -> dict[str, str]:
    """Log in as the seeded default admin and return auth headers."""
    from config import settings
    resp = await client.post(
        "/api/auth/login",
        json={
            "username": settings.staff_username,
            "password": settings.staff_password,
        },
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


# ── Machine endpoints ────────────────────────────────────────────────────


async def test_list_machines(client: AsyncClient):
    resp = await client.get("/api/machines/")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 6
    assert all("name" in m for m in data)


async def test_get_single_machine(client: AsyncClient):
    resp = await client.get("/api/machines/1")
    assert resp.status_code == 200
    assert resp.json()["id"] == 1


async def test_get_machine_not_found(client: AsyncClient):
    resp = await client.get("/api/machines/999")
    assert resp.status_code == 404


async def test_patch_machine_status(client: AsyncClient, admin_headers):
    resp = await client.patch(
        "/api/machines/1",
        headers=admin_headers,
        json={"status": "maintenance"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "maintenance"


async def test_patch_machine_invalid_status(client: AsyncClient, admin_headers):
    resp = await client.patch(
        "/api/machines/1", headers=admin_headers, json={"status": "broken"}
    )
    assert resp.status_code == 422


# ── Queue endpoints ──────────────────────────────────────────────────────


async def test_list_all_queues(client: AsyncClient):
    resp = await client.get("/api/queue/")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 6
    assert all("entries" in q for q in data)


async def test_get_machine_queue_empty(client: AsyncClient):
    resp = await client.get("/api/queue/1")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_join_queue(client: AsyncClient):
    resp = await client.post(
        "/api/queue/1/join",
        json={"discord_id": "111", "discord_name": "Alice"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "waiting"
    assert data["position"] == 1
    assert data["discord_name"] == "Alice"


async def test_join_queue_duplicate(client: AsyncClient):
    await client.post(
        "/api/queue/1/join",
        json={"discord_id": "111", "discord_name": "Alice"},
    )
    resp = await client.post(
        "/api/queue/1/join",
        json={"discord_id": "111", "discord_name": "Alice"},
    )
    assert resp.status_code == 409


async def test_join_queue_machine_paused(client: AsyncClient, admin_headers):
    await client.patch(
        "/api/machines/1",
        headers=admin_headers,
        json={"status": "maintenance"},
    )
    resp = await client.post(
        "/api/queue/1/join",
        json={"discord_id": "111", "discord_name": "Alice"},
    )
    assert resp.status_code == 409


async def test_join_queue_machine_not_found(client: AsyncClient):
    resp = await client.post(
        "/api/queue/999/join",
        json={"discord_id": "111", "discord_name": "Alice"},
    )
    assert resp.status_code == 404


async def test_leave_queue(client: AsyncClient):
    resp = await client.post(
        "/api/queue/1/join",
        json={"discord_id": "111", "discord_name": "Alice"},
    )
    entry_id = resp.json()["id"]

    resp = await client.post(f"/api/queue/{entry_id}/leave")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


async def test_serve_entry(client: AsyncClient):
    resp = await client.post(
        "/api/queue/1/join",
        json={"discord_id": "111", "discord_name": "Alice"},
    )
    entry_id = resp.json()["id"]

    resp = await client.post(f"/api/queue/{entry_id}/serve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "serving"
    assert resp.json()["serving_at"] is not None


async def test_serve_entry_not_waiting(client: AsyncClient):
    resp = await client.post(
        "/api/queue/1/join",
        json={"discord_id": "111", "discord_name": "Alice"},
    )
    entry_id = resp.json()["id"]
    await client.post(f"/api/queue/{entry_id}/serve")

    # Try to serve again
    resp = await client.post(f"/api/queue/{entry_id}/serve")
    assert resp.status_code == 409


async def test_complete_entry(client: AsyncClient):
    resp = await client.post(
        "/api/queue/1/join",
        json={"discord_id": "111", "discord_name": "Alice"},
    )
    entry_id = resp.json()["id"]
    await client.post(f"/api/queue/{entry_id}/serve")

    resp = await client.post(
        f"/api/queue/{entry_id}/complete",
        json={"job_successful": True},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"
    assert resp.json()["job_successful"] == 1


async def test_complete_entry_with_failure(client: AsyncClient):
    resp = await client.post(
        "/api/queue/1/join",
        json={"discord_id": "111", "discord_name": "Alice"},
    )
    entry_id = resp.json()["id"]
    await client.post(f"/api/queue/{entry_id}/serve")

    resp = await client.post(
        f"/api/queue/{entry_id}/complete",
        json={"job_successful": False, "failure_notes": "Material jammed"},
    )
    assert resp.status_code == 200
    assert resp.json()["job_successful"] == 0
    assert resp.json()["failure_notes"] == "Material jammed"


async def test_complete_entry_not_serving(client: AsyncClient):
    resp = await client.post(
        "/api/queue/1/join",
        json={"discord_id": "111", "discord_name": "Alice"},
    )
    entry_id = resp.json()["id"]

    resp = await client.post(
        f"/api/queue/{entry_id}/complete",
        json={"job_successful": True},
    )
    assert resp.status_code == 409


async def test_bump_entry(client: AsyncClient):
    await client.post(
        "/api/queue/1/join",
        json={"discord_id": "1", "discord_name": "Alice"},
    )
    resp2 = await client.post(
        "/api/queue/1/join",
        json={"discord_id": "2", "discord_name": "Bob"},
    )
    bob_id = resp2.json()["id"]

    resp = await client.post(f"/api/queue/{bob_id}/bump")
    assert resp.status_code == 200
    assert resp.json()["position"] == 1


async def test_bump_entry_not_waiting(client: AsyncClient):
    resp = await client.post(
        "/api/queue/1/join",
        json={"discord_id": "111", "discord_name": "Alice"},
    )
    entry_id = resp.json()["id"]
    await client.post(f"/api/queue/{entry_id}/serve")

    resp = await client.post(f"/api/queue/{entry_id}/bump")
    assert resp.status_code == 409


# ── Health check ─────────────────────────────────────────────────────────


async def test_health_check(client: AsyncClient):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── Analytics endpoints ─────────────────────────────────────────────────


async def test_analytics_empty(client: AsyncClient, admin_headers):
    resp = await client.get("/api/analytics/?period=day", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["machines"] == []


async def test_analytics_with_snapshot(db, client: AsyncClient, admin_headers):
    await models.insert_analytics_snapshot(
        date="2026-04-08",
        machine_id=1,
        total_jobs=10,
        completed_jobs=8,
        avg_wait_mins=5.5,
        avg_serve_mins=20.0,
        peak_hour=14,
        ai_summary="Good day.",
        no_show_count=1,
        cancelled_count=1,
        unique_users=7,
        failure_count=0,
    )
    resp = await client.get(
        "/api/analytics/?start_date=2026-04-08&end_date=2026-04-08",
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["machines"]) == 1
    assert data["summary"]["total_jobs"] == 10


async def test_analytics_machine_filter(db, client: AsyncClient, admin_headers):
    await models.insert_analytics_snapshot(
        date="2026-04-08", machine_id=1, total_jobs=5, completed_jobs=4,
        avg_wait_mins=3.0, avg_serve_mins=15.0, peak_hour=10,
        ai_summary="", no_show_count=0, cancelled_count=0,
        unique_users=3, failure_count=0,
    )
    await models.insert_analytics_snapshot(
        date="2026-04-08", machine_id=2, total_jobs=8, completed_jobs=7,
        avg_wait_mins=4.0, avg_serve_mins=18.0, peak_hour=11,
        ai_summary="", no_show_count=0, cancelled_count=0,
        unique_users=5, failure_count=0,
    )
    resp = await client.get(
        "/api/analytics/2?start_date=2026-04-08&end_date=2026-04-08",
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["machines"]) == 1
    assert data["machines"][0]["machine_id"] == 2


async def test_analytics_today(db, client: AsyncClient, admin_headers):
    resp = await client.get("/api/analytics/today", headers=admin_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "machines" in data
