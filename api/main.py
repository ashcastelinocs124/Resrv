"""FastAPI application setup.

Import this module's ``app`` object from the main entrypoint and run it
with uvicorn.  Do NOT start the server here — the main entrypoint
handles that so it can co-ordinate with the Discord bot and queue agent.

    from api.main import app
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes.analytics import router as analytics_router
from api.routes.auth import router as auth_router
from api.routes.colleges import router as colleges_router
from api.routes.feedback import router as feedback_router
from api.routes.machines import router as machines_router
from api.routes.queue import router as queue_router
from api.routes.settings import public_router as public_settings_router, router as settings_router
from api.routes.staff import router as staff_router
from api.routes.units import router as units_router
from api.routes.chat import router as chat_router
from api.routes.me import router as me_router

app = FastAPI(title="Reserv API")

# ── CORS (allow all origins for MVP) ─────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ──────────────────────────────────────────────────────────────

app.include_router(analytics_router)
app.include_router(auth_router)
app.include_router(colleges_router)
app.include_router(feedback_router)
app.include_router(machines_router)
app.include_router(queue_router)
app.include_router(settings_router)
app.include_router(public_settings_router)
app.include_router(staff_router)
app.include_router(units_router)
app.include_router(chat_router)
app.include_router(me_router)


# ── Health check ─────────────────────────────────────────────────────────

@app.get("/api/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
