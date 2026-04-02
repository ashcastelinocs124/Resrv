"""Machine management endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.deps import notify_embed_update
from db.models import get_machines, get_machine, update_machine_status

router = APIRouter(prefix="/api/machines", tags=["machines"])


# ── Schemas ──────────────────────────────────────────────────────────────

class MachineOut(BaseModel):
    id: int
    name: str
    slug: str
    status: str
    created_at: str


class MachineStatusUpdate(BaseModel):
    status: Literal["active", "maintenance", "offline"]


# ── Endpoints ────────────────────────────────────────────────────────────

@router.get("/", response_model=list[MachineOut])
async def list_machines() -> list[dict]:
    """List all machines."""
    return await get_machines()


@router.get("/{machine_id}", response_model=MachineOut)
async def get_single_machine(machine_id: int) -> dict:
    """Get a single machine by ID."""
    machine = await get_machine(machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found")
    return machine


@router.patch("/{machine_id}", response_model=MachineOut)
async def patch_machine_status(
    machine_id: int,
    body: MachineStatusUpdate,
) -> dict:
    """Update a machine's status (active / maintenance / offline)."""
    machine = await get_machine(machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found")

    await update_machine_status(machine_id, body.status)
    notify_embed_update(machine_id)

    # Re-fetch to return the updated record
    updated = await get_machine(machine_id)
    assert updated is not None  # should never fail since we just verified existence
    return updated
