"""Data-analyst agent — OpenAI tool-calling chart builder.

Mirrors the analytics chatbot but adds a multi-round tool-call loop.
The loop is hard-capped at ``MAX_TOOL_ROUND_TRIPS`` so a misbehaving
model can't run away with API calls. Inner OpenAI calls are non-
streaming; the SSE endpoint frames *loop progress* as events
(meta → tool_call* → chart? → delta → done).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.auth import require_data_analyst
from api.routes import agent_tools as T
from db import models

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics/agent", tags=["analytics-agent"])


# ── Configuration ────────────────────────────────────────────────────────


DEFAULT_MODEL = "gpt-5.4-mini"
MAX_TOOL_ROUND_TRIPS = 4
HISTORY_LIMIT = 16

ALLOWED_MODELS: list[dict[str, str]] = [
    {"id": "gpt-5.4",      "label": "GPT-5.4 (balanced)"},
    {"id": "gpt-5.4-mini", "label": "GPT-5.4 mini (default — fast, cheap)"},
    {"id": "gpt-4o",       "label": "GPT-4o (legacy)"},
]
_ALLOWED_MODEL_IDS = {m["id"] for m in ALLOWED_MODELS}


def _resolve_model(requested: str | None) -> str:
    if requested in (None, ""):
        return DEFAULT_MODEL
    if requested not in _ALLOWED_MODEL_IDS:
        raise HTTPException(
            status_code=400,
            detail=f"model must be one of: {sorted(_ALLOWED_MODEL_IDS)}",
        )
    return requested


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


# ── System prompt + tool registry ────────────────────────────────────────


SYSTEM_PROMPT = """\
You are a data-analyst agent for the Reserv queue management system at the
University of Illinois SCD makerspace. Staff use you to build charts and
investigate trends from the queue, feedback, and funnel data.

GROUND RULES
- Use the tools to fetch real numbers — never invent data.
- For chart-building requests: call query_jobs / query_feedback / query_funnel
  / top_n / compare_periods first, then call make_chart with the rows.
- When you call make_chart, set ``context`` to ``{"filter": ..., "group_by":
  ..., "metric": ..., "period": ...}`` so the chart can be refreshed later.
- After tool calls, write a short (1-3 sentence) summary that references
  the chart. Don't restate every number.
- If a tool errors or returns empty rows, say so plainly.
- Round to 1 decimal where helpful.
"""


def _tool_schemas() -> list[dict[str, Any]]:
    """OpenAI tool definitions matching agent_tools.py."""
    filter_schema = {
        "type": "object",
        "properties": {
            "machine_id":  {"type": "integer"},
            "college_id":  {"type": "integer"},
            "status":      {"type": "string"},
            "min_rating":  {"type": "integer"},
            "max_rating":  {"type": "integer"},
        },
        "additionalProperties": False,
    }
    period_enum = [
        "day", "week", "month",
        "today", "yesterday",
        "this_week", "last_week",
        "this_month", "last_month",
    ]
    return [
        {
            "type": "function",
            "function": {
                "name": "query_jobs",
                "description": (
                    "Group queue_entries by an attribute and compute a metric "
                    "per group over a date window."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filter":   filter_schema,
                        "group_by": {
                            "type": "string",
                            "enum": [
                                "machine", "college", "status",
                                "day", "hour", "user",
                            ],
                        },
                        "metric": {
                            "type": "string",
                            "enum": [
                                "count", "completed_count", "no_show_count",
                                "cancelled_count", "failure_count",
                                "unique_users",
                                "avg_wait_mins", "avg_serve_mins",
                                "avg_rating",
                            ],
                        },
                        "period": {"type": "string", "enum": period_enum},
                    },
                    "required": ["group_by", "metric"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_feedback",
                "description": (
                    "Group post-visit feedback (rating + count) by machine, "
                    "college, or rating value."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filter": filter_schema,
                        "group_by": {
                            "type": "string",
                            "enum": ["machine", "college", "rating"],
                        },
                        "period": {"type": "string", "enum": period_enum},
                    },
                    "required": ["group_by"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "query_funnel",
                "description": (
                    "Funnel counts (joined / served / completed / no_show / "
                    "cancelled / failure) for the period."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filter": filter_schema,
                        "period": {"type": "string", "enum": period_enum},
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "top_n",
                "description": "Top N groups by a metric (descending).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filter":   filter_schema,
                        "group_by": {
                            "type": "string",
                            "enum": [
                                "machine", "college", "status",
                                "day", "hour", "user",
                            ],
                        },
                        "metric": {
                            "type": "string",
                            "enum": [
                                "count", "completed_count", "no_show_count",
                                "cancelled_count", "failure_count",
                                "unique_users",
                                "avg_wait_mins", "avg_serve_mins",
                                "avg_rating",
                            ],
                        },
                        "n":      {"type": "integer", "minimum": 1, "maximum": 100},
                        "period": {"type": "string", "enum": period_enum},
                    },
                    "required": ["group_by", "metric", "n"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "compare_periods",
                "description": (
                    "Compute a single aggregate metric over two named "
                    "windows and return values + delta_abs + delta_pct."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "filter":   filter_schema,
                        "metric": {
                            "type": "string",
                            "enum": [
                                "count", "completed_count", "no_show_count",
                                "cancelled_count", "failure_count",
                                "unique_users",
                                "avg_wait_mins", "avg_serve_mins",
                                "avg_rating",
                            ],
                        },
                        "period_a": {"type": "string", "enum": period_enum},
                        "period_b": {"type": "string", "enum": period_enum},
                    },
                    "required": ["metric", "period_a", "period_b"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "make_chart",
                "description": (
                    "Format rows into a frontend-renderable chart spec. "
                    "Call this AFTER one of the query_* tools."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "data":  {"type": "array", "items": {"type": "object"}},
                        "type":  {"type": "string", "enum": ["bar", "line", "pie", "table"]},
                        "x":     {
                            "type": "object",
                            "properties": {
                                "field": {"type": "string"},
                                "label": {"type": "string"},
                            },
                            "required": ["field"],
                        },
                        "y":     {
                            "type": "object",
                            "properties": {
                                "field": {"type": "string"},
                                "label": {"type": "string"},
                            },
                            "required": ["field"],
                        },
                        "title": {"type": "string"},
                        "context": {"type": "object"},
                    },
                    "required": ["data", "type", "x", "y", "title"],
                },
            },
        },
    ]


_TOOL_REGISTRY = {
    "query_jobs":      T.query_jobs,
    "query_feedback":  T.query_feedback,
    "query_funnel":    T.query_funnel,
    "top_n":           T.top_n,
    "compare_periods": T.compare_periods,
    "make_chart":      T.make_chart,
}


async def _execute_tool(name: str, args: dict) -> dict:
    fn = _TOOL_REGISTRY.get(name)
    if fn is None:
        raise ValueError(f"Unknown tool: {name}")
    if name == "make_chart":
        return fn(**args)
    return await fn(**args)


# ── Schemas ──────────────────────────────────────────────────────────────


class AgentRequest(BaseModel):
    conversation_id: int | None = None
    message: str = Field(min_length=1, max_length=4000)
    model: str | None = None


class AgentMessageOut(BaseModel):
    id: int
    role: str
    content: str
    chart_spec: dict | None = None
    created_at: str


class AgentResponse(BaseModel):
    conversation_id: int
    message_id: int
    content: str
    chart_spec: dict | None = None


class AgentConversationSummary(BaseModel):
    id: int
    title: str
    created_at: str
    updated_at: str


class AgentConversationDetail(BaseModel):
    id: int
    title: str
    messages: list[AgentMessageOut]


class ModelOption(BaseModel):
    id: str
    label: str


class ModelsResponse(BaseModel):
    default: str
    models: list[ModelOption]


# ── Conversation prep ────────────────────────────────────────────────────


def _title_from_message(text: str) -> str:
    return (text or "New analysis").strip()[:60] or "New analysis"


async def _resolve_conversation(
    body: AgentRequest, staff_id: int
) -> tuple[int, str]:
    user_message = body.message.strip()
    if not user_message:
        raise HTTPException(400, detail="message must be non-empty")

    if body.conversation_id is not None:
        conv = await models.get_agent_conversation(body.conversation_id)
        if conv is None or conv["staff_user_id"] != staff_id:
            # 404 cross-user per CLAUDE.md convention.
            raise HTTPException(404, detail="Conversation not found")
        return (conv["id"], user_message)

    conv = await models.create_agent_conversation(
        staff_user_id=staff_id, title=_title_from_message(user_message),
    )
    return (conv["id"], user_message)


def _history_to_openai(rows: list[dict]) -> list[dict]:
    """Convert persisted agent_messages into OpenAI message dicts.

    Tool-call assistant rows carry ``tool_calls_json``; tool result rows
    carry ``tool_call_id``. Order matters — keep the original sequence.
    """
    out: list[dict] = []
    for r in rows[-HISTORY_LIMIT:]:
        if r["role"] == "assistant":
            msg: dict[str, Any] = {"role": "assistant", "content": r["content"] or ""}
            if r["tool_calls_json"]:
                try:
                    msg["tool_calls"] = json.loads(r["tool_calls_json"])
                except Exception:
                    pass
            out.append(msg)
        elif r["role"] == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": r["tool_call_id"] or "",
                "content": r["content"] or "",
            })
        elif r["role"] in ("user", "system"):
            out.append({"role": r["role"], "content": r["content"] or ""})
    return out


# ── Tool-call loop ───────────────────────────────────────────────────────


async def _run_tool_loop(
    *,
    client,
    model: str,
    conversation_id: int,
    user_message: str,
    on_event=None,
):
    """Run the multi-round tool-call loop. Returns (content, chart_spec).

    ``on_event`` is an optional async callable used by the SSE endpoint to
    progressively stream events. It receives ``dict`` events and may return
    an awaitable; if it doesn't, we treat the call as fire-and-forget.
    """
    await models.append_agent_message(
        conversation_id=conversation_id, role="user", content=user_message,
    )
    persisted = await models.get_agent_messages(conversation_id)
    openai_messages = (
        [{"role": "system", "content": SYSTEM_PROMPT}]
        + _history_to_openai(persisted)
    )

    final_content = ""
    final_chart_spec: dict | None = None

    for _ in range(MAX_TOOL_ROUND_TRIPS):
        response = await client.chat.completions.create(
            model=model,
            messages=openai_messages,
            tools=_tool_schemas(),
            tool_choice="auto",
            max_tokens=800,
            temperature=0.2,
        )
        msg = response.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None) or []

        if not tool_calls:
            final_content = (msg.content or "").strip() or "(no response)"
            break

        # Persist the assistant's tool-call request.
        tool_calls_serialised = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "{}",
                },
            }
            for tc in tool_calls
        ]
        await models.append_agent_message(
            conversation_id=conversation_id,
            role="assistant",
            content=(msg.content or ""),
            tool_calls_json=json.dumps(tool_calls_serialised),
        )
        openai_messages.append(
            {
                "role": "assistant",
                "content": (msg.content or ""),
                "tool_calls": tool_calls_serialised,
            }
        )

        # Execute each tool call.
        for tc, serialised in zip(tool_calls, tool_calls_serialised):
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if on_event:
                await on_event({"type": "tool_call", "name": name, "args": args})

            try:
                result = await _execute_tool(name, args)
            except Exception as e:
                log.exception("tool %s failed", name)
                result = {"error": str(e)}

            if name == "make_chart" and isinstance(result, dict) and "type" in result:
                final_chart_spec = result
                if on_event:
                    await on_event({"type": "chart", "spec": result})

            result_str = json.dumps(result, default=str)
            await models.append_agent_message(
                conversation_id=conversation_id,
                role="tool",
                content=result_str,
                tool_call_id=tc.id,
            )
            openai_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_str,
                }
            )
    else:
        # Cap reached; force a final answer with tools disabled.
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=openai_messages
                + [{
                    "role": "system",
                    "content": (
                        "You've reached the tool-call limit. "
                        "Summarise the findings now using the data already gathered."
                    ),
                }],
                tools=_tool_schemas(),
                tool_choice="none",
                max_tokens=400,
                temperature=0.2,
            )
            final_content = (
                (response.choices[0].message.content or "").strip()
                or "(no response — tool-call limit reached)"
            )
        except Exception as e:
            log.exception("agent fallback completion failed")
            final_content = (
                "(tool-call limit reached and fallback completion failed: "
                f"{e})"
            )

    saved = await models.append_agent_message(
        conversation_id=conversation_id,
        role="assistant",
        content=final_content,
        chart_spec_json=(
            json.dumps(final_chart_spec) if final_chart_spec else None
        ),
    )
    return saved, final_content, final_chart_spec


# ── Routes ───────────────────────────────────────────────────────────────


@router.get("/models", response_model=ModelsResponse)
async def list_models(
    _: dict[str, Any] = Depends(require_data_analyst),
) -> dict:
    return {"default": DEFAULT_MODEL, "models": ALLOWED_MODELS}


@router.get("/conversations", response_model=list[AgentConversationSummary])
async def list_my_conversations(
    payload: dict[str, Any] = Depends(require_data_analyst),
) -> list[dict]:
    rows = await models.list_agent_conversations(payload["sub"])
    return [
        {
            "id": r["id"],
            "title": r["title"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


@router.get(
    "/conversations/{conversation_id}", response_model=AgentConversationDetail,
)
async def get_my_conversation(
    conversation_id: int,
    payload: dict[str, Any] = Depends(require_data_analyst),
) -> dict:
    conv = await models.get_agent_conversation(conversation_id)
    if conv is None or conv["staff_user_id"] != payload["sub"]:
        raise HTTPException(404, detail="Conversation not found")
    msgs = await models.get_agent_messages(conversation_id)
    return {
        "id": conv["id"],
        "title": conv["title"],
        "messages": [
            {
                "id": m["id"],
                "role": m["role"],
                "content": m["content"],
                "chart_spec": (
                    json.loads(m["chart_spec_json"])
                    if m["chart_spec_json"] else None
                ),
                "created_at": m["created_at"],
            }
            for m in msgs
        ],
    }


@router.delete("/conversations/{conversation_id}")
async def delete_my_conversation(
    conversation_id: int,
    payload: dict[str, Any] = Depends(require_data_analyst),
) -> dict:
    conv = await models.get_agent_conversation(conversation_id)
    if conv is None or conv["staff_user_id"] != payload["sub"]:
        raise HTTPException(404, detail="Conversation not found")
    await models.delete_agent_conversation(conversation_id)
    return {"status": "deleted"}


@router.post("", response_model=AgentResponse)
async def post_agent(
    body: AgentRequest,
    payload: dict[str, Any] = Depends(require_data_analyst),
) -> dict:
    client = _make_openai_client()
    if client is None:
        raise HTTPException(503, detail="Agent is not configured")
    model = _resolve_model(body.model)
    conversation_id, user_message = await _resolve_conversation(
        body, payload["sub"]
    )
    saved, content, chart_spec = await _run_tool_loop(
        client=client, model=model, conversation_id=conversation_id,
        user_message=user_message,
    )
    return {
        "conversation_id": conversation_id,
        "message_id": saved["id"],
        "content": content,
        "chart_spec": chart_spec,
    }


@router.post("/stream")
async def post_agent_stream(
    body: AgentRequest,
    payload: dict[str, Any] = Depends(require_data_analyst),
):
    """Stream tool-loop progress + final reply as SSE.

    Event types:
      - meta       {"conversation_id": int}
      - tool_call  {"name": str, "args": object}
      - chart      {"spec": object}
      - delta      {"content": str}      (single delta — final text)
      - done       {"message_id": int}
      - error      {"detail": str}
    """
    client = _make_openai_client()
    if client is None:
        raise HTTPException(503, detail="Agent is not configured")
    model = _resolve_model(body.model)
    conversation_id, user_message = await _resolve_conversation(
        body, payload["sub"]
    )

    async def _gen():
        def _evt(obj: dict[str, Any]) -> bytes:
            return f"data: {json.dumps(obj)}\n\n".encode("utf-8")

        events_buffer: list[dict] = []

        async def _on_event(ev: dict) -> None:
            events_buffer.append(ev)

        yield _evt({"type": "meta", "conversation_id": conversation_id})

        try:
            saved, content, chart_spec = await _run_tool_loop(
                client=client, model=model,
                conversation_id=conversation_id,
                user_message=user_message,
                on_event=_on_event,
            )
        except Exception as e:
            log.exception("agent loop failure")
            yield _evt({"type": "error", "detail": f"Agent error: {e}"})
            return

        for ev in events_buffer:
            yield _evt({"type": ev["type"], **{k: v for k, v in ev.items() if k != "type"}})

        if content:
            yield _evt({"type": "delta", "content": content})
        if chart_spec is not None:
            yield _evt({"type": "chart", "spec": chart_spec})
        yield _evt({"type": "done", "message_id": saved["id"]})

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
