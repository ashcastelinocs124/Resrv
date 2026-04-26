"""Analytics chatbot — multi-turn conversations grounded in analytics data."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.auth import require_staff
from api.routes.analytics import compute_analytics_response
from db import models

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/analytics/chat",
    tags=["analytics-chat"],
    dependencies=[Depends(require_staff)],
)

CHAT_MODEL = "gpt-5.4-mini"
HISTORY_LIMIT = 8

# Allowlist for the model dropdown. Add to this list as new OpenAI chat
# models are validated. Anything outside this set is rejected with 400 so
# users can't bill the org against arbitrary model names.
#
# Source: https://developers.openai.com/api/docs/models (GPT-5.x lineup) +
# the still-available legacy 4.x families. Order is "newest first" so the
# default GPT-5.4-mini sits near the top of the dropdown.
ALLOWED_MODELS: list[dict[str, str]] = [
    {"id": "gpt-5.5",       "label": "GPT-5.5 (flagship)"},
    {"id": "gpt-5.4",       "label": "GPT-5.4 (balanced)"},
    {"id": "gpt-5.4-mini",  "label": "GPT-5.4 mini (default — fast, cheap)"},
    {"id": "gpt-5.4-nano",  "label": "GPT-5.4 nano (cheapest)"},
    {"id": "gpt-4.1",       "label": "GPT-4.1 (legacy)"},
    {"id": "gpt-4.1-mini",  "label": "GPT-4.1 mini (legacy)"},
    {"id": "gpt-4o",        "label": "GPT-4o (legacy)"},
    {"id": "gpt-4o-mini",   "label": "GPT-4o mini (legacy)"},
    {"id": "o3-mini",       "label": "o3-mini (reasoning)"},
]
_ALLOWED_MODEL_IDS = {m["id"] for m in ALLOWED_MODELS}


def _resolve_model(requested: str | None) -> str:
    if requested is None or requested == "":
        return CHAT_MODEL
    if requested not in _ALLOWED_MODEL_IDS:
        raise HTTPException(
            status_code=400,
            detail=f"model must be one of: {sorted(_ALLOWED_MODEL_IDS)}",
        )
    return requested

SYSTEM_PROMPT_TEMPLATE = """\
You are an analytics assistant for the SCD makerspace queue system at the
University of Illinois. Staff use this dashboard to monitor queue health.

GROUND RULES
- Answer ONLY using the analytics data shown below.
- If the user asks about a metric or time window the data doesn't cover,
  say so plainly and suggest changing the period or date range.
- Never invent numbers. Round to 1 decimal where helpful.
- Be terse. 1-3 sentences for short questions, a short list for comparisons.
- Refer to machines by name (e.g. "Laser Cutter"), not by id.

CURRENT DASHBOARD CONTEXT
period: {period}
range:  {start_date} -> {end_date}
data:   {analytics_json}
"""


def _make_openai_client():
    """Lazy factory — returns None if the key is missing or the dep isn't installed."""
    try:
        from openai import AsyncOpenAI
        from config import settings
    except Exception:
        return None
    key = getattr(settings, "openai_api_key", None) or None
    if not key:
        return None
    return AsyncOpenAI(api_key=key)


# ── Schemas ──────────────────────────────────────────────────────────────


class ChatMessageOut(BaseModel):
    id: int
    conversation_id: int
    role: str
    content: str
    created_at: str


class ConversationSummaryOut(BaseModel):
    id: int
    title: str
    created_at: str
    updated_at: str


class ConversationDetailOut(BaseModel):
    id: int
    title: str
    messages: list[ChatMessageOut]


class ChatRequest(BaseModel):
    conversation_id: int | None = None
    message: str = Field(min_length=1, max_length=4000)
    period: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    model: str | None = None


class ModelOption(BaseModel):
    id: str
    label: str


class ModelsResponse(BaseModel):
    default: str
    models: list[ModelOption]


class ChatResponse(BaseModel):
    conversation_id: int
    message: ChatMessageOut


# ── Helpers ──────────────────────────────────────────────────────────────


def _trim_analytics_for_tokens(blob: dict[str, Any]) -> dict[str, Any]:
    """Best-effort drop of optional fields if the encoded blob is too large."""
    LIMIT = 12_000
    if len(json.dumps(blob)) <= LIMIT:
        return blob
    trimmed = {**blob, "daily_breakdown": []}
    if len(json.dumps(trimmed)) <= LIMIT:
        return trimmed
    trimmed["machines"] = [
        {**m, "ai_summary": None} for m in trimmed.get("machines", [])
    ]
    if len(json.dumps(trimmed)) <= LIMIT:
        return trimmed
    raise HTTPException(
        status_code=413,
        detail="This period is too large to chat about — narrow the range.",
    )


# ── Routes ───────────────────────────────────────────────────────────────


@router.post("", response_model=ChatResponse)
async def chat(
    body: ChatRequest, payload: dict[str, Any] = Depends(require_staff)
) -> dict:
    client = _make_openai_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Chat is not configured")

    model = _resolve_model(body.model)
    conversation_id, openai_messages = await _build_chat_request(
        body, payload["sub"]
    )

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=openai_messages,
            max_tokens=600,
            temperature=0.2,
        )
    except Exception as e:
        log.exception("OpenAI chat failure")
        raise HTTPException(status_code=502, detail=f"Upstream model error: {e}")

    content = (response.choices[0].message.content or "").strip() or "(no response)"
    saved = await models.append_message(
        conversation_id, role="assistant", content=content
    )
    return {
        "conversation_id": conversation_id,
        "message": {
            "id": saved["id"],
            "conversation_id": conversation_id,
            "role": saved["role"],
            "content": saved["content"],
            "created_at": saved["created_at"],
        },
    }


async def _build_chat_request(body: ChatRequest, staff_id: int) -> tuple[int, list[dict]]:
    """Resolve conversation, persist user message, build OpenAI messages list.

    Returns (conversation_id, openai_messages). Raises HTTPException on validation.
    """
    user_message = body.message.strip()
    if not user_message:
        raise HTTPException(status_code=400, detail="message must be non-empty")

    if body.conversation_id is not None:
        conv = await models.get_conversation(
            body.conversation_id, staff_user_id=staff_id
        )
        if conv is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        conversation_id = conv["id"]
    else:
        conv = await models.create_conversation(
            staff_user_id=staff_id, first_message=user_message
        )
        conversation_id = conv["id"]

    await models.append_message(
        conversation_id, role="user", content=user_message
    )

    analytics_blob = await compute_analytics_response(
        body.period, body.start_date, body.end_date
    )
    analytics_blob = _trim_analytics_for_tokens(analytics_blob)
    system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        period=analytics_blob["period"],
        start_date=analytics_blob["start_date"],
        end_date=analytics_blob["end_date"],
        analytics_json=json.dumps(analytics_blob),
    )

    history = await models.get_recent_messages(
        conversation_id, limit=HISTORY_LIMIT
    )
    openai_messages = [{"role": "system", "content": system_prompt}]
    for m in history:
        if m["role"] in {"user", "assistant"}:
            openai_messages.append({"role": m["role"], "content": m["content"]})

    return conversation_id, openai_messages


@router.post("/stream")
async def chat_stream(
    body: ChatRequest, payload: dict[str, Any] = Depends(require_staff)
):
    """Stream the assistant reply as Server-Sent Events.

    Event types (all JSON-encoded after `data: `):
      - {"type": "meta", "conversation_id": int}
      - {"type": "delta", "content": "..."}    (zero or more)
      - {"type": "done",  "message_id": int}
      - {"type": "error", "detail": "..."}
    """
    client = _make_openai_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Chat is not configured")

    model = _resolve_model(body.model)
    conversation_id, openai_messages = await _build_chat_request(
        body, payload["sub"]
    )

    async def _gen():
        def _evt(obj: dict[str, Any]) -> bytes:
            return f"data: {json.dumps(obj)}\n\n".encode("utf-8")

        yield _evt({"type": "meta", "conversation_id": conversation_id})

        full = []
        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=openai_messages,
                max_tokens=600,
                temperature=0.2,
                stream=True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta
                piece = getattr(delta, "content", None) or ""
                if piece:
                    full.append(piece)
                    yield _evt({"type": "delta", "content": piece})
        except Exception as e:
            log.exception("OpenAI streaming failure")
            yield _evt({"type": "error", "detail": f"Upstream model error: {e}"})
            return

        content = "".join(full).strip() or "(no response)"
        saved = await models.append_message(
            conversation_id, role="assistant", content=content
        )
        yield _evt({"type": "done", "message_id": saved["id"]})

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/models", response_model=ModelsResponse)
async def list_models() -> dict:
    return {"default": CHAT_MODEL, "models": ALLOWED_MODELS}


@router.get("/conversations", response_model=list[ConversationSummaryOut])
async def list_my_conversations(
    payload: dict[str, Any] = Depends(require_staff),
) -> list[dict]:
    return await models.list_conversations(payload["sub"])


@router.get(
    "/conversations/{conversation_id}", response_model=ConversationDetailOut
)
async def get_conversation_thread(
    conversation_id: int,
    payload: dict[str, Any] = Depends(require_staff),
) -> dict:
    conv = await models.get_conversation(
        conversation_id, staff_user_id=payload["sub"]
    )
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    msgs = await models.get_conversation_messages(
        conversation_id, staff_user_id=payload["sub"]
    )
    assert msgs is not None
    return {
        "id": conv["id"],
        "title": conv["title"],
        "messages": [
            {
                "id": m["id"],
                "conversation_id": m["conversation_id"],
                "role": m["role"],
                "content": m["content"],
                "created_at": m["created_at"],
            }
            for m in msgs
        ],
    }


@router.delete("/conversations/{conversation_id}")
async def delete_conversation_route(
    conversation_id: int,
    payload: dict[str, Any] = Depends(require_staff),
) -> dict:
    ok = await models.delete_conversation(
        conversation_id, staff_user_id=payload["sub"]
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"status": "deleted"}
