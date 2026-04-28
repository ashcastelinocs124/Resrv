"""Per-user feature flags + onboarding stamp."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import require_staff
from api.settings_store import get_setting_bool
from db import models

router = APIRouter(tags=["me"])


class FeatureFlags(BaseModel):
    data_analyst_visible: bool


@router.get("/api/me/features", response_model=FeatureFlags)
async def my_features(
    payload: dict[str, Any] = Depends(require_staff),
) -> FeatureFlags:
    enabled = await get_setting_bool("data_analyst_enabled", default=False)
    if not enabled:
        return FeatureFlags(data_analyst_visible=False)
    if payload.get("rol") == "admin":
        return FeatureFlags(data_analyst_visible=True)
    visible = await get_setting_bool(
        "data_analyst_visible_to_staff", default=False
    )
    return FeatureFlags(data_analyst_visible=visible)


@router.post("/api/auth/me/onboarded")
async def mark_onboarded(
    payload: dict[str, Any] = Depends(require_staff),
) -> dict[str, str]:
    await models.mark_staff_onboarded(payload["sub"])
    return {"status": "ok"}
