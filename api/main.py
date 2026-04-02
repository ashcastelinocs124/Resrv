"""FastAPI application setup.

Import this module's ``app`` object from the main entrypoint and run it
with uvicorn.  Do NOT start the server here — the main entrypoint
handles that so it can co-ordinate with the Discord bot and queue agent.

    from api.main import app
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes.machines import router as machines_router
from api.routes.queue import router as queue_router

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

app.include_router(machines_router)
app.include_router(queue_router)


# ── Health check ─────────────────────────────────────────────────────────

@app.get("/api/health")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
