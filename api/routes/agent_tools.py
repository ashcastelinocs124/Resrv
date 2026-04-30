"""Read-only tools exposed to the data-analyst agent.

Each tool returns a JSON-serializable dict and is safe to call repeatedly:
no writes, no destructive joins, hard row caps. Tools are pure on the call
boundary — date math is resolved here so the model can pass simple strings
(``"day" / "week" / "month"`` or named relative windows).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from db.database import get_db

ROW_CAP = 1000

# Group_by values supported by query_jobs / top_n.
_JOB_GROUP_BY = {"machine", "college", "status", "day", "hour", "user"}

# Metrics supported by query_jobs / top_n / compare_periods.
_JOB_METRICS = {
    "count",
    "completed_count",
    "no_show_count",
    "cancelled_count",
    "failure_count",
    "unique_users",
    "avg_wait_mins",
    "avg_serve_mins",
    "avg_rating",
}

# Group_by values for query_feedback.
_FEEDBACK_GROUP_BY = {"machine", "college", "rating"}

# Chart types accepted by make_chart.
_CHART_TYPES = {"bar", "line", "pie", "table"}


# ── Period resolution ────────────────────────────────────────────────────


def _today() -> datetime:
    return datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)


def _resolve_period(period: str | None) -> tuple[str, str]:
    """Resolve a period name to (start_iso, end_iso) inclusive.

    Defaults to ``"week"`` (trailing 7 days). Unknown values fall through to
    week so a misbehaving model can't crash a tool call.
    """
    today = _today().date()
    if period in (None, "", "week"):
        return ((today - timedelta(days=6)).isoformat(), today.isoformat())
    if period == "day" or period == "today":
        return (today.isoformat(), today.isoformat())
    if period == "yesterday":
        d = today - timedelta(days=1)
        return (d.isoformat(), d.isoformat())
    if period == "month":
        return ((today - timedelta(days=29)).isoformat(), today.isoformat())
    if period == "last_week":
        return (
            (today - timedelta(days=13)).isoformat(),
            (today - timedelta(days=7)).isoformat(),
        )
    if period == "this_week":
        return ((today - timedelta(days=6)).isoformat(), today.isoformat())
    if period == "last_month":
        return (
            (today - timedelta(days=59)).isoformat(),
            (today - timedelta(days=30)).isoformat(),
        )
    if period == "this_month":
        return ((today - timedelta(days=29)).isoformat(), today.isoformat())
    return ((today - timedelta(days=6)).isoformat(), today.isoformat())


# ── Filter / where helpers ───────────────────────────────────────────────


def _apply_filter(
    where: list[str], params: list[Any], filter: dict | None
) -> None:
    """Mutate ``where`` and ``params`` with optional filter clauses.

    Recognised keys: ``machine_id``, ``college_id``, ``status``,
    ``min_rating``, ``max_rating``. Unknown keys are silently ignored
    so the model can't blow up the SQL by passing garbage.
    """
    f = filter or {}
    if (mid := f.get("machine_id")) is not None:
        where.append("qe.machine_id = ?")
        params.append(mid)
    if (cid := f.get("college_id")) is not None:
        if cid == 0:
            where.append("u.college_id IS NULL")
        else:
            where.append("u.college_id = ?")
            params.append(cid)
    if (st := f.get("status")) is not None:
        where.append("qe.status = ?")
        params.append(st)


# ── Group-by SQL builders for query_jobs ─────────────────────────────────


def _job_group_sql(group_by: str) -> tuple[str, str, str]:
    """Return (group_value_expr, group_label_expr, group_by_clause).

    ``group_value_expr`` is the raw value used for filtering / charting.
    ``group_label_expr`` is the human-readable label.
    """
    if group_by == "machine":
        return ("m.id", "m.name", "m.id, m.name")
    if group_by == "college":
        return (
            "COALESCE(u.college_id, 0)",
            "COALESCE(c.name, 'Unspecified')",
            "COALESCE(u.college_id, 0), COALESCE(c.name, 'Unspecified')",
        )
    if group_by == "status":
        return ("qe.status", "qe.status", "qe.status")
    if group_by == "day":
        return (
            "date(qe.joined_at)",
            "date(qe.joined_at)",
            "date(qe.joined_at)",
        )
    if group_by == "hour":
        return (
            "CAST(strftime('%H', qe.joined_at) AS INTEGER)",
            "CAST(strftime('%H', qe.joined_at) AS INTEGER)",
            "CAST(strftime('%H', qe.joined_at) AS INTEGER)",
        )
    if group_by == "user":
        return (
            "u.id",
            "COALESCE(u.full_name, u.discord_name, 'unknown')",
            "u.id, u.full_name, u.discord_name",
        )
    raise ValueError(f"Unsupported group_by: {group_by}")


def _job_metric_sql(metric: str) -> str:
    """Return a SELECT expression that produces the value column."""
    if metric == "count":
        return "COUNT(qe.id)"
    if metric == "completed_count":
        return "SUM(CASE WHEN qe.status = 'completed' THEN 1 ELSE 0 END)"
    if metric == "no_show_count":
        return "SUM(CASE WHEN qe.status = 'no_show' THEN 1 ELSE 0 END)"
    if metric == "cancelled_count":
        return "SUM(CASE WHEN qe.status = 'cancelled' THEN 1 ELSE 0 END)"
    if metric == "failure_count":
        return "SUM(CASE WHEN qe.job_successful = 0 THEN 1 ELSE 0 END)"
    if metric == "unique_users":
        return "COUNT(DISTINCT qe.user_id)"
    if metric == "avg_wait_mins":
        return (
            "AVG(CASE WHEN qe.serving_at IS NOT NULL "
            "THEN (julianday(qe.serving_at) - julianday(qe.joined_at)) "
            "* 24 * 60 END)"
        )
    if metric == "avg_serve_mins":
        return (
            "AVG(CASE WHEN qe.completed_at IS NOT NULL "
            "AND qe.serving_at IS NOT NULL "
            "THEN (julianday(qe.completed_at) - julianday(qe.serving_at)) "
            "* 24 * 60 END)"
        )
    if metric == "avg_rating":
        return "AVG(f.rating)"
    raise ValueError(f"Unsupported metric: {metric}")


def _round(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, float):
        return round(v, 2)
    return v


# ── query_jobs ───────────────────────────────────────────────────────────


async def query_jobs(
    *,
    filter: dict | None = None,
    group_by: str = "machine",
    metric: str = "count",
    period: str | None = None,
) -> dict[str, Any]:
    """Group queue_entries by an attribute and compute a metric per group.

    Returns ``{rows: [{group_value, group_label, value}], truncated: bool}``.
    """
    if group_by not in _JOB_GROUP_BY:
        raise ValueError(
            f"group_by must be one of {sorted(_JOB_GROUP_BY)}; got {group_by!r}"
        )
    if metric not in _JOB_METRICS:
        raise ValueError(
            f"metric must be one of {sorted(_JOB_METRICS)}; got {metric!r}"
        )

    start, end = _resolve_period(period)
    value_expr = _job_group_sql(group_by)[0]
    label_expr = _job_group_sql(group_by)[1]
    group_clause = _job_group_sql(group_by)[2]
    metric_expr = _job_metric_sql(metric)
    needs_feedback = metric == "avg_rating"

    where = ["date(qe.joined_at) BETWEEN date(?) AND date(?)"]
    params: list[Any] = [start, end]
    _apply_filter(where, params, filter)

    feedback_join = (
        "LEFT JOIN feedback f ON f.queue_entry_id = qe.id"
        if needs_feedback else ""
    )

    sql = f"""
        SELECT {value_expr}              AS group_value,
               {label_expr}              AS group_label,
               {metric_expr}             AS value
        FROM queue_entries qe
        JOIN users u    ON u.id = qe.user_id
        JOIN machines m ON m.id = qe.machine_id
        LEFT JOIN colleges c ON c.id = u.college_id
        {feedback_join}
        WHERE {' AND '.join(where)}
        GROUP BY {group_clause}
        ORDER BY value DESC NULLS LAST
        LIMIT ?
    """
    db = await get_db()
    cursor = await db.execute(sql, params + [ROW_CAP + 1])
    raw = await cursor.fetchall()
    truncated = len(raw) > ROW_CAP
    rows = [
        {
            "group_value": r["group_value"],
            "group_label": str(r["group_label"]),
            "value": _round(r["value"]),
        }
        for r in raw[:ROW_CAP]
    ]
    return {
        "rows": rows,
        "truncated": truncated,
        "period": {"start": start, "end": end},
        "group_by": group_by,
        "metric": metric,
    }


# ── query_feedback ───────────────────────────────────────────────────────


async def query_feedback(
    *,
    filter: dict | None = None,
    group_by: str = "machine",
    period: str | None = None,
) -> dict[str, Any]:
    """Group feedback by machine / college / rating with avg + count."""
    if group_by not in _FEEDBACK_GROUP_BY:
        raise ValueError(
            f"group_by must be one of {sorted(_FEEDBACK_GROUP_BY)}; got {group_by!r}"
        )
    start, end = _resolve_period(period)
    where = ["date(f.created_at) BETWEEN date(?) AND date(?)"]
    params: list[Any] = [start, end]
    _apply_filter(where, params, filter)

    if (filter or {}).get("min_rating") is not None:
        where.append("f.rating >= ?")
        params.append(filter["min_rating"])
    if (filter or {}).get("max_rating") is not None:
        where.append("f.rating <= ?")
        params.append(filter["max_rating"])

    if group_by == "machine":
        value_expr = "m.id"
        label_expr = "m.name"
        group_clause = "m.id, m.name"
    elif group_by == "college":
        value_expr = "COALESCE(u.college_id, 0)"
        label_expr = "COALESCE(c.name, 'Unspecified')"
        group_clause = "COALESCE(u.college_id, 0), COALESCE(c.name, 'Unspecified')"
    else:  # rating
        value_expr = "f.rating"
        label_expr = "CAST(f.rating AS TEXT)"
        group_clause = "f.rating"

    sql = f"""
        SELECT {value_expr}        AS group_value,
               {label_expr}        AS group_label,
               AVG(f.rating)       AS avg_rating,
               COUNT(f.rating)     AS count
        FROM feedback f
        JOIN queue_entries qe ON qe.id = f.queue_entry_id
        JOIN users u          ON u.id  = qe.user_id
        JOIN machines m       ON m.id  = qe.machine_id
        LEFT JOIN colleges c  ON c.id  = u.college_id
        WHERE {' AND '.join(where)}
        GROUP BY {group_clause}
        ORDER BY count DESC, avg_rating DESC
        LIMIT ?
    """
    db = await get_db()
    cursor = await db.execute(sql, params + [ROW_CAP + 1])
    raw = await cursor.fetchall()
    truncated = len(raw) > ROW_CAP
    rows = [
        {
            "group_value": r["group_value"],
            "group_label": str(r["group_label"]),
            "avg_rating": _round(r["avg_rating"]),
            "count": r["count"],
        }
        for r in raw[:ROW_CAP]
    ]
    return {
        "rows": rows,
        "truncated": truncated,
        "period": {"start": start, "end": end},
        "group_by": group_by,
    }


# ── query_funnel ─────────────────────────────────────────────────────────


async def query_funnel(
    *,
    filter: dict | None = None,
    period: str | None = None,
) -> dict[str, Any]:
    """Funnel counts across queue statuses for the period."""
    start, end = _resolve_period(period)
    where = ["date(qe.joined_at) BETWEEN date(?) AND date(?)"]
    params: list[Any] = [start, end]
    _apply_filter(where, params, filter)

    sql = f"""
        SELECT
            COUNT(qe.id)
                AS joined,
            SUM(CASE WHEN qe.serving_at IS NOT NULL THEN 1 ELSE 0 END)
                AS served,
            SUM(CASE WHEN qe.status = 'completed' THEN 1 ELSE 0 END)
                AS completed,
            SUM(CASE WHEN qe.status = 'no_show' THEN 1 ELSE 0 END)
                AS no_show,
            SUM(CASE WHEN qe.status = 'cancelled' THEN 1 ELSE 0 END)
                AS cancelled,
            SUM(CASE WHEN qe.job_successful = 0 THEN 1 ELSE 0 END)
                AS failure
        FROM queue_entries qe
        JOIN users u    ON u.id = qe.user_id
        JOIN machines m ON m.id = qe.machine_id
        WHERE {' AND '.join(where)}
    """
    db = await get_db()
    cursor = await db.execute(sql, params)
    row = await cursor.fetchone()
    return {
        "joined":    row["joined"] or 0,
        "served":    row["served"] or 0,
        "completed": row["completed"] or 0,
        "no_show":   row["no_show"] or 0,
        "cancelled": row["cancelled"] or 0,
        "failure":   row["failure"] or 0,
        "period": {"start": start, "end": end},
    }


# ── top_n ────────────────────────────────────────────────────────────────


async def top_n(
    *,
    filter: dict | None = None,
    group_by: str = "machine",
    metric: str = "count",
    n: int = 5,
    period: str | None = None,
) -> dict[str, Any]:
    """Same as query_jobs but capped to ``n`` rows (max 100)."""
    n = max(1, min(int(n), 100))
    full = await query_jobs(
        filter=filter, group_by=group_by, metric=metric, period=period,
    )
    return {
        "rows": full["rows"][:n],
        "truncated": full["truncated"] or len(full["rows"]) > n,
        "period": full["period"],
        "group_by": group_by,
        "metric": metric,
    }


# ── compare_periods ──────────────────────────────────────────────────────


async def compare_periods(
    *,
    filter: dict | None = None,
    metric: str = "count",
    period_a: str = "last_week",
    period_b: str = "this_week",
) -> dict[str, Any]:
    """Compute a single aggregate metric over two windows and the delta."""
    if metric not in _JOB_METRICS:
        raise ValueError(
            f"metric must be one of {sorted(_JOB_METRICS)}; got {metric!r}"
        )

    async def _scalar(period: str) -> tuple[float | None, str, str]:
        start, end = _resolve_period(period)
        where = ["date(qe.joined_at) BETWEEN date(?) AND date(?)"]
        params: list[Any] = [start, end]
        _apply_filter(where, params, filter)
        metric_expr = _job_metric_sql(metric)
        feedback_join = (
            "LEFT JOIN feedback f ON f.queue_entry_id = qe.id"
            if metric == "avg_rating" else ""
        )
        sql = f"""
            SELECT {metric_expr} AS value
            FROM queue_entries qe
            JOIN users u    ON u.id = qe.user_id
            JOIN machines m ON m.id = qe.machine_id
            LEFT JOIN colleges c ON c.id = u.college_id
            {feedback_join}
            WHERE {' AND '.join(where)}
        """
        db = await get_db()
        cursor = await db.execute(sql, params)
        row = await cursor.fetchone()
        return (row["value"], start, end)

    a_val, a_start, a_end = await _scalar(period_a)
    b_val, b_start, b_end = await _scalar(period_b)

    delta_abs: float | None = None
    delta_pct: float | None = None
    if a_val is not None and b_val is not None:
        delta_abs = round(float(b_val) - float(a_val), 2)
        if a_val:
            delta_pct = round((float(b_val) - float(a_val)) / float(a_val) * 100, 1)
    return {
        "a": {
            "label": period_a, "value": _round(a_val),
            "start": a_start, "end": a_end,
        },
        "b": {
            "label": period_b, "value": _round(b_val),
            "start": b_start, "end": b_end,
        },
        "delta_abs": delta_abs,
        "delta_pct": delta_pct,
        "metric": metric,
    }


# ── make_chart ───────────────────────────────────────────────────────────


def make_chart(
    *,
    data: list[dict],
    type: str,
    x: dict,
    y: dict,
    title: str,
    context: dict | None = None,
) -> dict[str, Any]:
    """Format raw rows into a frontend-renderable ``chart_spec``.

    ``context`` (optional) preserves the originating query — staff can pin a
    chart and the API can re-run the query on refresh.
    """
    if type not in _CHART_TYPES:
        raise ValueError(f"type must be one of {sorted(_CHART_TYPES)}")
    if not isinstance(x, dict) or "field" not in x:
        raise ValueError("x must be {'field': str, 'label': str}")
    if not isinstance(y, dict) or "field" not in y:
        raise ValueError("y must be {'field': str, 'label': str}")
    spec: dict[str, Any] = {
        "type": type,
        "title": str(title)[:120],
        "x": {"field": str(x["field"]), "label": str(x.get("label", x["field"]))},
        "y": {"field": str(y["field"]), "label": str(y.get("label", y["field"]))},
        "data": list(data or [])[:ROW_CAP],
    }
    if context is not None:
        spec["context"] = context
    return spec
