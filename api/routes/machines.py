"""Machine management endpoints."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from api.auth import require_admin, require_staff
from api.deps import notify_embed_create, notify_embed_delete, notify_embed_update
from db import models

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/machines",
    tags=["machines"],
)


# ── Schemas ──────────────────────────────────────────────────────────────

class MachineOut(BaseModel):
    id: int
    name: str
    slug: str
    status: str
    archived_at: str | None = None
    created_at: str


class MachineCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    slug: str = Field(min_length=1, max_length=60)


class MachineUpdate(BaseModel):
    name: str | None = None
    slug: str | None = None
    status: Literal["active", "maintenance", "offline"] | None = None


class MachineStatusUpdate(BaseModel):
    status: Literal["active", "maintenance", "offline"]


class PurgeConfirm(BaseModel):
    confirm_slug: str


# ── Public / staff endpoints ─────────────────────────────────────────────

@router.get("/", response_model=list[MachineOut])
async def list_all(include_archived: bool = Query(False)) -> list[dict]:
    """List machines. Public; admin UI may request archived too."""
    return await models.list_machines(include_archived=include_archived)


@router.get("/{machine_id}", response_model=MachineOut)
async def get_single(machine_id: int) -> dict:
    machine = await models.get_machine(machine_id)
    if machine is None:
        raise HTTPException(status_code=404, detail="Machine not found")
    return machine


# ── Staff endpoints (write) ──────────────────────────────────────────────

@router.post(
    "/",
    response_model=MachineOut,
    status_code=201,
    dependencies=[Depends(require_staff)],
)
async def create(body: MachineCreate) -> dict:
    try:
        m = await models.create_machine(name=body.name, slug=body.slug)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    notify_embed_create(m["id"])
    return m


@router.patch(
    "/{machine_id}",
    response_model=MachineOut,
    dependencies=[Depends(require_staff)],
)
async def patch(machine_id: int, body: MachineUpdate) -> dict:
    if await models.get_machine(machine_id) is None:
        raise HTTPException(status_code=404, detail="Machine not found")
    try:
        await models.update_machine(
            machine_id,
            name=body.name,
            slug=body.slug,
            status=body.status,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    notify_embed_update(machine_id)
    updated = await models.get_machine(machine_id)
    assert updated is not None
    return updated


# ── Admin-only endpoints ─────────────────────────────────────────────────

@router.delete("/{machine_id}", dependencies=[Depends(require_admin)])
async def delete(
    machine_id: int,
    purge: bool = Query(False),
    body: PurgeConfirm | None = Body(default=None),
) -> dict:
    m = await models.get_machine(machine_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Machine not found")
    active = await models.count_active_queue_entries(machine_id)
    if active > 0:
        raise HTTPException(
            status_code=409,
            detail=f"Clear the queue first. {active} users still waiting.",
        )
    msg_id = m.get("embed_message_id")
    if purge:
        if body is None or body.confirm_slug != m["slug"]:
            raise HTTPException(
                status_code=400,
                detail="confirm_slug must equal the machine slug",
            )
        counts = await models.purge_machine(machine_id)
        notify_embed_delete(machine_id, msg_id)
        log.warning(
            "Purged machine slug=%s counts=%s", m["slug"], counts
        )
        return {"status": "purged", **counts}
    await models.archive_machine(machine_id)
    notify_embed_delete(machine_id, msg_id)
    return {"status": "archived"}


@router.post(
    "/{machine_id}/restore",
    response_model=MachineOut,
    dependencies=[Depends(require_admin)],
)
async def restore(machine_id: int) -> dict:
    try:
        await models.restore_machine(machine_id)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    notify_embed_create(machine_id)
    restored = await models.get_machine(machine_id)
    assert restored is not None
    return restored
