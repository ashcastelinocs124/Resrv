"""Staff authentication routes: /api/auth/login, /logout, /me."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from api.auth import (
    get_staff_by_username,
    issue_token,
    require_staff,
    verify_password,
)
from db import models

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    username: str
    role: str


class MeResponse(BaseModel):
    username: str
    staff_id: int
    role: str
    onboarded_at: str | None = None


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest) -> LoginResponse:
    staff = await get_staff_by_username(body.username)
    if staff is None or not verify_password(body.password, staff["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    role = staff.get("role") or "staff"
    token = issue_token(staff["id"], staff["username"], role)
    return LoginResponse(token=token, username=staff["username"], role=role)


@router.post("/logout")
async def logout(_: dict = Depends(require_staff)) -> dict[str, str]:
    # Stateless tokens — client just discards. Endpoint exists for symmetry
    # and to validate the token before the client clears it.
    return {"status": "ok"}


@router.get("/me", response_model=MeResponse)
async def me(payload: dict = Depends(require_staff)) -> MeResponse:
    onboarded_at = await models.get_staff_onboarded_at(payload["sub"])
    return MeResponse(
        username=payload["usr"],
        staff_id=payload["sub"],
        role=payload.get("rol", "staff"),
        onboarded_at=onboarded_at,
    )
