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


# ── Machine endpoints ────────────────────────────────────────────────────


async def test_list_machines(client: AsyncClient):
    resp = await client.get("/api/machines/")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 4
    assert all("name" in m for m in data)


async def test_get_single_machine(client: AsyncClient):
    resp = await client.get("/api/machines/1")
    assert resp.status_code == 200
    assert resp.json()["id"] == 1


async def test_get_machine_not_found(client: AsyncClient):
    resp = await client.get("/api/machines/999")
    assert resp.status_code == 404


async def test_patch_machine_status(client: AsyncClient):
    resp = await client.patch("/api/machines/1", json={"status": "maintenance"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "maintenance"


async def test_patch_machine_invalid_status(client: AsyncClient):
    resp = await client.patch("/api/machines/1", json={"status": "broken"})
    assert resp.status_code == 422


# ── Queue endpoints ──────────────────────────────────────────────────────


async def test_list_all_queues(client: AsyncClient):
    resp = await client.get("/api/queue/")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 4
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


async def test_join_queue_machine_paused(client: AsyncClient):
    await client.patch("/api/machines/1", json={"status": "maintenance"})
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
