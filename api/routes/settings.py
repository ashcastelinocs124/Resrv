"""Admin-only settings endpoints + tiny public endpoint for banner/public_mode."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.auth import require_admin
from api.settings_store import get_all_settings, get_setting, set_setting

router = APIRouter(prefix="/api/settings", tags=["settings"])
public_router = APIRouter(prefix="/api/public-settings", tags=["settings"])

ALLOWED_KEYS = {
    "reminder_minutes",
    "grace_minutes",
    "queue_reset_hour",
    "agent_tick_seconds",
    "public_mode",
    "maintenance_banner",
    "data_analyst_enabled",
    "data_analyst_visible_to_staff",
}


@router.get("/", dependencies=[Depends(require_admin)])
async def list_settings() -> dict[str, str]:
    return await get_all_settings()


@router.patch("/", dependencies=[Depends(require_admin)])
async def patch_settings(updates: dict[str, str]) -> dict[str, str]:
    bad = set(updates.keys()) - ALLOWED_KEYS
    if bad:
        raise HTTPException(
            status_code=400, detail=f"Unknown setting keys: {sorted(bad)}"
        )
    for key, value in updates.items():
        await set_setting(key, str(value))
    return await get_all_settings()


@public_router.get("/")
async def public_settings() -> dict[str, str]:
    """Non-sensitive settings visible to anonymous users."""
    return {
        "public_mode": (await get_setting("public_mode")) or "false",
        "maintenance_banner": (await get_setting("maintenance_banner")) or "",
    }
