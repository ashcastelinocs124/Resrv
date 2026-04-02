"""Queue CRUD endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.deps import notify_embed_update
from db.models import (
    get_machine,
    get_machines,
    get_or_create_user,
    get_queue_for_machine,
    get_user_active_entry,
    join_queue as db_join_queue,
    leave_queue as db_leave_queue,
    update_entry_status,
    bump_entry_to_top,
)

router = APIRouter(prefix="/api/queue", tags=["queue"])


# ── Schemas ──────────────────────────────────────────────────────────────

class QueueEntryOut(BaseModel):
    id: int
    user_id: int
    machine_id: int
    status: str
    position: int
    joined_at: str
    serving_at: str | None = None
    completed_at: str | None = None
    reminded: int
    job_successful: int | None = None
    failure_notes: str | None = None
    discord_id: str | None = None
    discord_name: str | None = None


class MachineQueueOut(BaseModel):
    machine_id: int
    machine_name: str
    machine_slug: str
    machine_status: str
    entries: list[QueueEntryOut]


class JoinRequest(BaseModel):
    discord_id: str
    discord_name: str


class CompleteRequest(BaseModel):
    job_successful: bool
    failure_notes: str | None = None


# ── Helpers ──────────────────────────────────────────────────────────────

async def _get_entry_or_404(entry_id: int) -> dict[str, Any]:
    """Fetch a single queue entry by ID or raise 404."""
    from db.database import get_db

    db = await get_db()
    cursor = await db.execute(
        """
        SELECT qe.*, u.discord_id, u.discord_name
        FROM queue_entries qe
        JOIN users u ON u.id = qe.user_id
        WHERE qe.id = ?
        """,
        (entry_id,),
    )
    result = await cursor.fetchone()
    if result is None:
        raise HTTPException(status_code=404, detail="Queue entry not found")
    return dict(result)


# ── Endpoints ────────────────────────────────────────────────────────────

@router.get("/", response_model=list[MachineQueueOut])
async def list_all_queues() -> list[dict]:
    """Get all machines with their current queue entries."""
    machines = await get_machines()
    result: list[dict] = []
    for m in machines:
        entries = await get_queue_for_machine(m["id"])
        result.append(
            {
                "machine_id": m["id"],
                "machine_name": m["name"],
                "machine_slug": m["slug"],
                "machine_status": m["status"],
                "entries": entries,
            }
        )
    return result


@router.get("/{machine_id}", response_model=list[QueueEntryOut])
async def get_machine_queue(machine_id: int) -> list[dict]:
    """Get the current queue (waiting + serving) for a specific machine."""
    machine = await get_machine(machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found")
    return await get_queue_for_machine(machine_id)


@router.post("/{machine_id}/join", response_model=QueueEntryOut, status_code=201)
async def join_machine_queue(machine_id: int, body: JoinRequest) -> dict:
    """Join the queue for a machine."""
    machine = await get_machine(machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found")

    if machine["status"] != "active":
        raise HTTPException(
            status_code=409,
            detail=f"Machine is currently {machine['status']} and not accepting queue entries",
        )

    # Resolve discord user to internal user
    user = await get_or_create_user(body.discord_id, body.discord_name)

    # Check for duplicate active entry
    existing = await get_user_active_entry(user["id"], machine_id)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="User already has an active entry in this queue",
        )

    entry = await db_join_queue(user["id"], machine_id)

    # The join_queue RETURNING * doesn't include user fields, so enrich
    entry["discord_id"] = body.discord_id
    entry["discord_name"] = body.discord_name
    notify_embed_update(machine_id)
    return entry


@router.post("/{entry_id}/leave", response_model=QueueEntryOut)
async def leave_queue_entry(entry_id: int) -> dict:
    """Leave / cancel a queue entry."""
    entry = await _get_entry_or_404(entry_id)

    if entry["status"] not in ("waiting", "serving"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot leave — entry status is '{entry['status']}'",
        )

    await db_leave_queue(entry_id)
    notify_embed_update(entry["machine_id"])

    # Re-fetch to return updated state
    return await _get_entry_or_404(entry_id)


@router.post("/{entry_id}/serve", response_model=QueueEntryOut)
async def serve_entry(entry_id: int) -> dict:
    """Move a queue entry to 'serving' status."""
    entry = await _get_entry_or_404(entry_id)

    if entry["status"] != "waiting":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot serve — entry status is '{entry['status']}', expected 'waiting'",
        )

    await update_entry_status(entry_id, "serving")
    notify_embed_update(entry["machine_id"])
    return await _get_entry_or_404(entry_id)


@router.post("/{entry_id}/complete", response_model=QueueEntryOut)
async def complete_entry(entry_id: int, body: CompleteRequest) -> dict:
    """Mark a serving entry as completed."""
    entry = await _get_entry_or_404(entry_id)

    if entry["status"] != "serving":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot complete — entry status is '{entry['status']}', expected 'serving'",
        )

    extra: dict[str, Any] = {"job_successful": int(body.job_successful)}
    if body.failure_notes is not None:
        extra["failure_notes"] = body.failure_notes

    await update_entry_status(entry_id, "completed", **extra)
    notify_embed_update(entry["machine_id"])
    return await _get_entry_or_404(entry_id)


@router.post("/{entry_id}/bump", response_model=QueueEntryOut)
async def bump_entry(entry_id: int) -> dict:
    """Bump a waiting entry to the top of the queue."""
    entry = await _get_entry_or_404(entry_id)

    if entry["status"] != "waiting":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot bump — entry status is '{entry['status']}', expected 'waiting'",
        )

    await bump_entry_to_top(entry_id, entry["machine_id"])
    notify_embed_update(entry["machine_id"])
    return await _get_entry_or_404(entry_id)
