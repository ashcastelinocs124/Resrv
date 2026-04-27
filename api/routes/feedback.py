"""Feedback browse routes — staff-only read."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api.auth import require_staff
from db import models

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class FeedbackRow(BaseModel):
    id: int
    queue_entry_id: int
    rating: int
    comment: str | None
    created_at: str
    user_id: int
    full_name: str | None
    discord_name: str | None
    machine_id: int
    machine_name: str
    college_id: int | None
    college_name: str


@router.get(
    "/",
    response_model=list[FeedbackRow],
    dependencies=[Depends(require_staff)],
)
async def list_feedback_endpoint(
    limit: int = Query(50, ge=1, le=500),
    machine_id: int | None = Query(None, ge=1),
    college_id: int | None = Query(None, ge=1),
    min_rating: int | None = Query(None, ge=1, le=5),
    max_rating: int | None = Query(None, ge=1, le=5),
):
    rows = await models.list_feedback(
        limit=limit,
        machine_id=machine_id,
        college_id=college_id,
        min_rating=min_rating,
        max_rating=max_rating,
    )
    return [FeedbackRow(**r) for r in rows]
