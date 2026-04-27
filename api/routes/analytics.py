"""Analytics endpoints — pre-computed snapshots + live today stats."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from api.auth import require_staff
from db import get_db, models

router = APIRouter(
    prefix="/api/analytics",
    tags=["analytics"],
    dependencies=[Depends(require_staff)],
)


class MachineStat(BaseModel):
    machine_id: int
    machine_name: str
    total_jobs: int
    completed_jobs: int
    unique_users: int
    avg_wait_mins: float | None
    avg_serve_mins: float | None
    no_show_count: int
    cancelled_count: int
    failure_count: int
    peak_hour: int | None
    ai_summary: str | None
    avg_rating: float | None = None
    rating_count: int = 0


class DailyBreakdown(BaseModel):
    date: str
    total_jobs: int
    completed_jobs: int


class AnalyticsSummary(BaseModel):
    total_jobs: int
    completed_jobs: int
    unique_users: int
    avg_wait_mins: float | None
    avg_serve_mins: float | None
    no_show_count: int
    cancelled_count: int
    failure_count: int
    avg_rating: float | None = None
    rating_count: int = 0


class CollegeStat(BaseModel):
    college_id: int
    college_name: str
    total_jobs: int
    completed_jobs: int
    unique_users: int
    avg_wait_mins: float | None
    avg_serve_mins: float | None
    avg_rating: float | None = None
    rating_count: int = 0


class AnalyticsResponse(BaseModel):
    period: str
    start_date: str
    end_date: str
    summary: AnalyticsSummary
    machines: list[MachineStat]
    daily_breakdown: list[DailyBreakdown]
    colleges: list[CollegeStat]


class TodayResponse(BaseModel):
    date: str
    machines: list[MachineStat]


def _date_range(
    period: str | None,
    start_date: str | None,
    end_date: str | None,
) -> tuple[str, str, str]:
    today = datetime.utcnow().date()
    if start_date and end_date:
        return (period or "custom", start_date, end_date)
    if period == "week":
        start = today - timedelta(days=7)
    elif period == "month":
        start = today - timedelta(days=30)
    else:
        start = today - timedelta(days=1)
        period = "day"
    return (period, start.isoformat(), today.isoformat())


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "summary": {
                "total_jobs": 0, "completed_jobs": 0, "unique_users": 0,
                "avg_wait_mins": None, "avg_serve_mins": None,
                "no_show_count": 0, "cancelled_count": 0, "failure_count": 0,
            },
            "machines": [],
            "daily_breakdown": [],
            "colleges": [],
        }

    machine_map: dict[int, dict[str, Any]] = {}
    for r in rows:
        mid = r["machine_id"]
        if mid not in machine_map:
            machine_map[mid] = {
                "machine_id": mid,
                "machine_name": r.get("machine_name", ""),
                "total_jobs": 0, "completed_jobs": 0, "unique_users": 0,
                "no_show_count": 0, "cancelled_count": 0, "failure_count": 0,
                "peak_hour": r.get("peak_hour"),
                "ai_summary": r.get("ai_summary"),
                "_wait_sum": 0.0, "_wait_count": 0,
                "_serve_sum": 0.0, "_serve_count": 0,
            }
        m = machine_map[mid]
        m["total_jobs"] += r["total_jobs"]
        m["completed_jobs"] += r["completed_jobs"]
        m["unique_users"] += r.get("unique_users", 0)
        m["no_show_count"] += r.get("no_show_count", 0)
        m["cancelled_count"] += r.get("cancelled_count", 0)
        m["failure_count"] += r.get("failure_count", 0)
        if r.get("avg_wait_mins") is not None:
            m["_wait_sum"] += r["avg_wait_mins"] * r["total_jobs"]
            m["_wait_count"] += r["total_jobs"]
        if r.get("avg_serve_mins") is not None:
            m["_serve_sum"] += r["avg_serve_mins"] * r["completed_jobs"]
            m["_serve_count"] += r["completed_jobs"]
        m["ai_summary"] = r.get("ai_summary")
        m["peak_hour"] = r.get("peak_hour")

    machines = []
    for m in machine_map.values():
        machines.append({
            "machine_id": m["machine_id"],
            "machine_name": m["machine_name"],
            "total_jobs": m["total_jobs"],
            "completed_jobs": m["completed_jobs"],
            "unique_users": m["unique_users"],
            "avg_wait_mins": round(m["_wait_sum"] / m["_wait_count"], 1) if m["_wait_count"] else None,
            "avg_serve_mins": round(m["_serve_sum"] / m["_serve_count"], 1) if m["_serve_count"] else None,
            "no_show_count": m["no_show_count"],
            "cancelled_count": m["cancelled_count"],
            "failure_count": m["failure_count"],
            "peak_hour": m["peak_hour"],
            "ai_summary": m["ai_summary"],
        })

    day_map: dict[str, dict[str, int]] = {}
    for r in rows:
        d = r["date"]
        if d not in day_map:
            day_map[d] = {"date": d, "total_jobs": 0, "completed_jobs": 0}
        day_map[d]["total_jobs"] += r["total_jobs"]
        day_map[d]["completed_jobs"] += r["completed_jobs"]

    summary = {
        "total_jobs": sum(m["total_jobs"] for m in machines),
        "completed_jobs": sum(m["completed_jobs"] for m in machines),
        "unique_users": sum(m["unique_users"] for m in machines),
        "avg_wait_mins": None,
        "avg_serve_mins": None,
        "no_show_count": sum(m["no_show_count"] for m in machines),
        "cancelled_count": sum(m["cancelled_count"] for m in machines),
        "failure_count": sum(m["failure_count"] for m in machines),
    }
    wait_vals = [m["avg_wait_mins"] for m in machines if m["avg_wait_mins"] is not None]
    serve_vals = [m["avg_serve_mins"] for m in machines if m["avg_serve_mins"] is not None]
    if wait_vals:
        summary["avg_wait_mins"] = round(sum(wait_vals) / len(wait_vals), 1)
    if serve_vals:
        summary["avg_serve_mins"] = round(sum(serve_vals) / len(serve_vals), 1)

    return {
        "summary": summary,
        "machines": machines,
        "daily_breakdown": sorted(day_map.values(), key=lambda d: d["date"]),
    }


async def _compute_colleges_block(
    *,
    start_date: str,
    end_date: str,
    college_id: int | None,
    machine_id: int | None,
) -> list[dict[str, Any]]:
    """Live aggregation of queue entries grouped by user.college_id.

    Always returned alongside the snapshot-based summary so the dashboard /
    chat can answer college-grouped questions. ``NULL`` college_ids bucket
    under a synthetic id=0 / name='Unspecified' row.
    """
    db = await get_db()
    sql = """
        SELECT
            COALESCE(u.college_id, 0)               AS college_id,
            COALESCE(c.name, 'Unspecified')         AS college_name,
            COUNT(qe.id)                            AS total_jobs,
            SUM(CASE WHEN qe.status='completed' THEN 1 ELSE 0 END) AS completed_jobs,
            COUNT(DISTINCT u.id)                    AS unique_users,
            AVG(CASE
                WHEN qe.serving_at IS NOT NULL
                THEN (julianday(qe.serving_at) - julianday(qe.joined_at)) * 24 * 60
            END)                                    AS avg_wait_mins,
            AVG(CASE
                WHEN qe.completed_at IS NOT NULL AND qe.serving_at IS NOT NULL
                THEN (julianday(qe.completed_at) - julianday(qe.serving_at)) * 24 * 60
            END)                                    AS avg_serve_mins
        FROM queue_entries qe
        JOIN users u ON u.id = qe.user_id
        LEFT JOIN colleges c ON c.id = u.college_id
        WHERE date(qe.joined_at) BETWEEN ? AND ?
    """
    params: list[Any] = [start_date, end_date]
    if machine_id is not None:
        sql += " AND qe.machine_id = ?"
        params.append(machine_id)
    if college_id is not None:
        sql += " AND COALESCE(u.college_id, 0) = ?"
        params.append(college_id)
    sql += """
        GROUP BY COALESCE(u.college_id, 0)
        ORDER BY total_jobs DESC
    """
    cursor = await db.execute(sql, params)
    rows = await cursor.fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        if d.get("avg_wait_mins") is not None:
            d["avg_wait_mins"] = round(d["avg_wait_mins"], 1)
        if d.get("avg_serve_mins") is not None:
            d["avg_serve_mins"] = round(d["avg_serve_mins"], 1)
        out.append(d)
    return out


async def _compute_live_for_college(
    *,
    start_date: str,
    end_date: str,
    college_id: int,
    machine_id: int | None,
) -> dict[str, Any]:
    """Live aggregation of queue entries scoped to one college.

    Used when ``college_id`` is set, since pre-computed analytics_snapshots
    have no per-user dimension. Mirrors the shape produced by ``_aggregate``.
    """
    db = await get_db()
    user_filter = (
        " AND qe.user_id IN (SELECT id FROM users WHERE college_id = ?)"
    )
    base_params: list[Any] = [start_date, end_date, college_id]
    machine_clause = ""
    if machine_id is not None:
        machine_clause = " AND qe.machine_id = ?"
        base_params.append(machine_id)

    # Per-machine block.
    sql_machines = f"""
        SELECT
            qe.machine_id,
            m.name AS machine_name,
            COUNT(*) AS total_jobs,
            SUM(CASE WHEN qe.status = 'completed' THEN 1 ELSE 0 END) AS completed_jobs,
            SUM(CASE WHEN qe.status = 'no_show' THEN 1 ELSE 0 END) AS no_show_count,
            SUM(CASE WHEN qe.status = 'cancelled' THEN 1 ELSE 0 END) AS cancelled_count,
            SUM(CASE WHEN qe.job_successful = 0 THEN 1 ELSE 0 END) AS failure_count,
            COUNT(DISTINCT qe.user_id) AS unique_users,
            AVG(CASE
                WHEN qe.serving_at IS NOT NULL
                THEN (julianday(qe.serving_at) - julianday(qe.joined_at)) * 24 * 60
            END) AS avg_wait_mins,
            AVG(CASE
                WHEN qe.completed_at IS NOT NULL AND qe.serving_at IS NOT NULL
                THEN (julianday(qe.completed_at) - julianday(qe.serving_at)) * 24 * 60
            END) AS avg_serve_mins
        FROM queue_entries qe
        JOIN machines m ON m.id = qe.machine_id
        WHERE date(qe.joined_at) BETWEEN ? AND ?
        {user_filter}
        {machine_clause}
        GROUP BY qe.machine_id
        ORDER BY qe.machine_id
    """
    cursor = await db.execute(sql_machines, base_params)
    raw_machines = [dict(r) for r in await cursor.fetchall()]
    machines: list[dict[str, Any]] = []
    for r in raw_machines:
        machines.append({
            "machine_id": r["machine_id"],
            "machine_name": r["machine_name"],
            "total_jobs": r["total_jobs"],
            "completed_jobs": r["completed_jobs"],
            "unique_users": r["unique_users"],
            "avg_wait_mins": (
                round(r["avg_wait_mins"], 1) if r["avg_wait_mins"] is not None else None
            ),
            "avg_serve_mins": (
                round(r["avg_serve_mins"], 1) if r["avg_serve_mins"] is not None else None
            ),
            "no_show_count": r["no_show_count"] or 0,
            "cancelled_count": r["cancelled_count"] or 0,
            "failure_count": r["failure_count"] or 0,
            "peak_hour": None,
            "ai_summary": None,
        })

    # Daily breakdown.
    sql_daily = f"""
        SELECT
            date(qe.joined_at) AS date,
            COUNT(*) AS total_jobs,
            SUM(CASE WHEN qe.status = 'completed' THEN 1 ELSE 0 END) AS completed_jobs
        FROM queue_entries qe
        WHERE date(qe.joined_at) BETWEEN ? AND ?
        {user_filter}
        {machine_clause}
        GROUP BY date(qe.joined_at)
        ORDER BY date(qe.joined_at) ASC
    """
    cursor = await db.execute(sql_daily, base_params)
    daily = [
        {
            "date": r["date"],
            "total_jobs": r["total_jobs"],
            "completed_jobs": r["completed_jobs"] or 0,
        }
        for r in await cursor.fetchall()
    ]

    # Top-level summary.
    summary = {
        "total_jobs": sum(m["total_jobs"] for m in machines),
        "completed_jobs": sum(m["completed_jobs"] for m in machines),
        "unique_users": sum(m["unique_users"] for m in machines),
        "avg_wait_mins": None,
        "avg_serve_mins": None,
        "no_show_count": sum(m["no_show_count"] for m in machines),
        "cancelled_count": sum(m["cancelled_count"] for m in machines),
        "failure_count": sum(m["failure_count"] for m in machines),
    }
    wait_vals = [m["avg_wait_mins"] for m in machines if m["avg_wait_mins"] is not None]
    serve_vals = [m["avg_serve_mins"] for m in machines if m["avg_serve_mins"] is not None]
    if wait_vals:
        summary["avg_wait_mins"] = round(sum(wait_vals) / len(wait_vals), 1)
    if serve_vals:
        summary["avg_serve_mins"] = round(sum(serve_vals) / len(serve_vals), 1)

    return {
        "summary": summary,
        "machines": machines,
        "daily_breakdown": daily,
    }


async def compute_analytics_response(
    period: str | None,
    start_date: str | None,
    end_date: str | None,
    machine_id: int | None = None,
    college_id: int | None = None,
) -> dict[str, Any]:
    """Shared aggregation used by both the analytics routes and the chat router.

    When ``college_id`` is set, the summary / machines / daily_breakdown blocks
    are computed live from ``queue_entries`` joined to ``users`` (snapshots
    have no per-user dimension). The ``colleges`` block is always live.
    """
    p, sd, ed = _date_range(period, start_date, end_date)
    if college_id is not None:
        agg = await _compute_live_for_college(
            start_date=sd,
            end_date=ed,
            college_id=college_id,
            machine_id=machine_id,
        )
    else:
        rows = await models.get_analytics_snapshots(
            start_date=sd, end_date=ed, machine_id=machine_id
        )
        agg = _aggregate(rows)
    colleges = await _compute_colleges_block(
        start_date=sd,
        end_date=ed,
        college_id=college_id,
        machine_id=machine_id,
    )

    # Merge feedback aggregates into the summary / machines / colleges blocks.
    overall = await models.feedback_aggregates_overall(
        sd, ed, college_id=college_id, machine_id=machine_id
    )
    summary = agg.get("summary", {})
    summary["avg_rating"] = (
        round(overall["avg_rating"], 2) if overall["avg_rating"] is not None else None
    )
    summary["rating_count"] = overall["rating_count"] or 0

    by_machine = await models.feedback_aggregates_by_machine(
        sd, ed, college_id=college_id
    )
    for m in agg.get("machines", []):
        ratings = by_machine.get(m["machine_id"])
        if ratings is not None:
            m["avg_rating"] = (
                round(ratings["avg_rating"], 2)
                if ratings["avg_rating"] is not None
                else None
            )
            m["rating_count"] = ratings["rating_count"] or 0
        else:
            m["avg_rating"] = None
            m["rating_count"] = 0

    by_college = await models.feedback_aggregates_by_college(
        sd, ed, machine_id=machine_id
    )
    for c in colleges:
        ratings = by_college.get(c["college_id"])
        if ratings is not None:
            c["avg_rating"] = (
                round(ratings["avg_rating"], 2)
                if ratings["avg_rating"] is not None
                else None
            )
            c["rating_count"] = ratings["rating_count"] or 0
        else:
            c["avg_rating"] = None
            c["rating_count"] = 0

    return {
        "period": p,
        "start_date": sd,
        "end_date": ed,
        **agg,
        "colleges": colleges,
    }


@router.get("/today", response_model=TodayResponse)
async def get_today_stats() -> dict:
    today = datetime.utcnow().date().isoformat()
    stats = await models.compute_live_today_stats()
    machines = [
        {
            "machine_id": s["machine_id"],
            "machine_name": s["machine_name"],
            "total_jobs": s["total_jobs"],
            "completed_jobs": s["completed_jobs"],
            "unique_users": s["unique_users"],
            "avg_wait_mins": round(s["avg_wait_mins"], 1) if s["avg_wait_mins"] else None,
            "avg_serve_mins": round(s["avg_serve_mins"], 1) if s["avg_serve_mins"] else None,
            "no_show_count": s["no_show_count"],
            "cancelled_count": s["cancelled_count"],
            "failure_count": s["failure_count"],
            "peak_hour": s.get("peak_hour"),
            "ai_summary": None,
        }
        for s in stats
    ]
    return {"date": today, "machines": machines}


@router.get("/summary", response_model=AnalyticsResponse)
async def get_analytics_summary(
    period: str | None = "day",
    start_date: str | None = None,
    end_date: str | None = None,
    machine_id: int | None = Query(None),
    college_id: int | None = Query(None),
) -> dict:
    return await compute_analytics_response(
        period, start_date, end_date, machine_id, college_id
    )


@router.get("/{machine_id}", response_model=AnalyticsResponse)
async def get_machine_analytics(
    machine_id: int,
    period: str | None = "day",
    start_date: str | None = None,
    end_date: str | None = None,
    college_id: int | None = Query(None),
) -> dict:
    return await compute_analytics_response(
        period, start_date, end_date, machine_id, college_id
    )


@router.get("/", response_model=AnalyticsResponse)
async def get_analytics(
    period: str | None = "day",
    start_date: str | None = None,
    end_date: str | None = None,
    college_id: int | None = Query(None),
) -> dict:
    return await compute_analytics_response(
        period, start_date, end_date, None, college_id
    )
