"""Pinned chart persistence — staff-readable, owner-deletable."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.auth import require_staff
from api.routes import agent_tools as T
from db import models

router = APIRouter(prefix="/api/pinned-charts", tags=["pinned-charts"])


class PinChartBody(BaseModel):
    chart_spec: dict
    title: str = Field(min_length=1, max_length=120)


class PinnedChartOut(BaseModel):
    id: int
    title: str
    chart_spec: dict
    pin_order: int
    created_at: str
    created_by_username: str | None


def _to_out(row: dict, username: str | None) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "chart_spec": json.loads(row["chart_spec_json"]),
        "pin_order": row["pin_order"],
        "created_at": row["created_at"],
        "created_by_username": username,
    }


async def _username_for(staff_id: int) -> str | None:
    rec = await models.get_staff(staff_id)
    return rec["username"] if rec else None


@router.get("", response_model=list[PinnedChartOut])
async def list_pinned(
    _: dict[str, Any] = Depends(require_staff),
) -> list[dict]:
    rows = await models.list_pinned_charts()
    out: list[dict] = []
    for r in rows:
        out.append(_to_out(r, await _username_for(r["created_by"])))
    return out


@router.post("", response_model=PinnedChartOut)
async def create_pinned(
    body: PinChartBody,
    payload: dict[str, Any] = Depends(require_staff),
) -> dict:
    row = await models.create_pinned_chart(
        chart_spec=body.chart_spec, title=body.title,
        created_by=payload["sub"],
    )
    return _to_out(row, payload.get("usr"))


@router.post("/{chart_id}/refresh", response_model=PinnedChartOut)
async def refresh_pinned(
    chart_id: int,
    _: dict[str, Any] = Depends(require_staff),
) -> dict:
    row = await models.get_pinned_chart(chart_id)
    if row is None:
        raise HTTPException(404, detail="Chart not found")
    spec = json.loads(row["chart_spec_json"])
    ctx = spec.get("context") or {}
    if ctx.get("group_by") and ctx.get("metric"):
        result = await T.query_jobs(
            filter=ctx.get("filter") or {},
            group_by=ctx["group_by"],
            metric=ctx["metric"],
            period=ctx.get("period"),
        )
        x_field = spec["x"]["field"]
        y_field = spec["y"]["field"]
        spec["data"] = [
            {x_field: r["group_label"], y_field: r["value"]}
            for r in result["rows"]
        ]
    fresh = {**row, "chart_spec_json": json.dumps(spec)}
    return _to_out(fresh, await _username_for(row["created_by"]))


@router.delete("/{chart_id}")
async def unpin(
    chart_id: int,
    _: dict[str, Any] = Depends(require_staff),
) -> dict:
    deleted = await models.delete_pinned_chart(chart_id)
    if not deleted:
        raise HTTPException(404, detail="Chart not found")
    return {"status": "ok"}
