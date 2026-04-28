"""Tests for the read-only data-analyst tools."""

from __future__ import annotations

import pytest

from api.routes import agent_tools as T
from db import models
from db.database import get_db

pytestmark = pytest.mark.asyncio


async def _seed_user(discord_id: str, college_id: int | None = None) -> int:
    user = await models.get_or_create_user(discord_id=discord_id, discord_name=discord_id)
    if college_id is not None:
        await models.register_user(
            user["id"],
            full_name=f"User {discord_id}",
            email=f"{discord_id}@illinois.edu",
            major="CS",
            college_id=college_id,
            graduation_year="2027",
        )
    return user["id"]


async def _seed_completed_entry(
    user_id: int,
    machine_id: int,
    *,
    joined_at: str = "datetime('now', '-1 hour')",
    serving_at: str = "datetime('now', '-30 minutes')",
    completed_at: str = "datetime('now')",
    job_successful: int | None = 1,
    rating: int | None = None,
) -> int:
    """Insert a completed queue entry with optional feedback."""
    db = await get_db()
    cur = await db.execute(
        f"""
        INSERT INTO queue_entries
            (user_id, machine_id, status, position,
             joined_at, serving_at, completed_at, job_successful)
        VALUES (?, ?, 'completed', 1,
                {joined_at}, {serving_at}, {completed_at}, ?)
        RETURNING id
        """,
        (user_id, machine_id, job_successful),
    )
    entry_id = (await cur.fetchone())[0]
    if rating is not None:
        await db.execute(
            "INSERT INTO feedback (queue_entry_id, rating) VALUES (?, ?)",
            (entry_id, rating),
        )
    await db.commit()
    return entry_id


# ── query_jobs ───────────────────────────────────────────────────────────


async def test_query_jobs_group_by_machine_returns_one_row_per_machine(db):
    machines = await models.list_machines()
    u = await _seed_user("u1")
    await _seed_completed_entry(u, machines[0]["id"])
    await _seed_completed_entry(u, machines[0]["id"])
    await _seed_completed_entry(u, machines[1]["id"])
    out = await T.query_jobs(group_by="machine", metric="count", period="day")
    rows = {r["group_label"]: r["value"] for r in out["rows"]}
    assert rows[machines[0]["name"]] == 2
    assert rows[machines[1]["name"]] == 1
    assert out["truncated"] is False


async def test_query_jobs_filter_by_college_id_narrows_results(db):
    machines = await models.list_machines()
    college = await models.create_college("Chess Academy")
    u_in_college = await _seed_user("u_in", college_id=college["id"])
    u_no_college = await _seed_user("u_out")
    await _seed_completed_entry(u_in_college, machines[0]["id"])
    await _seed_completed_entry(u_no_college, machines[0]["id"])

    out = await T.query_jobs(
        filter={"college_id": college["id"]},
        group_by="machine", metric="count", period="day",
    )
    total = sum(r["value"] for r in out["rows"])
    assert total == 1


async def test_query_jobs_avg_rating_metric_joins_feedback(db):
    machines = await models.list_machines()
    u = await _seed_user("u_rating")
    await _seed_completed_entry(u, machines[0]["id"], rating=5)
    await _seed_completed_entry(u, machines[0]["id"], rating=3)
    out = await T.query_jobs(
        group_by="machine", metric="avg_rating", period="day",
    )
    machine_row = next(
        r for r in out["rows"] if r["group_label"] == machines[0]["name"]
    )
    assert machine_row["value"] == 4.0


async def test_query_jobs_empty_data_returns_empty(db):
    out = await T.query_jobs(group_by="machine", metric="count", period="day")
    assert out["rows"] == []
    assert out["truncated"] is False


async def test_query_jobs_invalid_group_by_raises(db):
    with pytest.raises(ValueError):
        await T.query_jobs(group_by="zip_code", metric="count")


# ── query_feedback ───────────────────────────────────────────────────────


async def test_query_feedback_returns_avg_and_count_per_machine(db):
    machines = await models.list_machines()
    u = await _seed_user("u_fb")
    await _seed_completed_entry(u, machines[0]["id"], rating=4)
    await _seed_completed_entry(u, machines[0]["id"], rating=2)
    await _seed_completed_entry(u, machines[1]["id"], rating=5)
    out = await T.query_feedback(group_by="machine", period="day")
    by_label = {r["group_label"]: r for r in out["rows"]}
    assert by_label[machines[0]["name"]]["count"] == 2
    assert by_label[machines[0]["name"]]["avg_rating"] == 3.0
    assert by_label[machines[1]["name"]]["count"] == 1
    assert by_label[machines[1]["name"]]["avg_rating"] == 5.0


# ── query_funnel ─────────────────────────────────────────────────────────


async def test_query_funnel_sums_counts_across_statuses(db):
    machines = await models.list_machines()
    u = await _seed_user("u_funnel")
    await _seed_completed_entry(u, machines[0]["id"])
    await _seed_completed_entry(u, machines[0]["id"], job_successful=0)

    db_conn = await get_db()
    await db_conn.execute(
        """
        INSERT INTO queue_entries (user_id, machine_id, status, position, joined_at)
        VALUES (?, ?, 'cancelled', 1, datetime('now'))
        """,
        (u, machines[0]["id"]),
    )
    await db_conn.commit()

    out = await T.query_funnel(period="day")
    assert out["joined"] == 3
    assert out["served"] == 2
    assert out["completed"] == 2
    assert out["cancelled"] == 1
    assert out["failure"] == 1


# ── top_n ────────────────────────────────────────────────────────────────


async def test_top_n_returns_top_sorted_desc(db):
    machines = await models.list_machines()
    u = await _seed_user("u_top")
    for _ in range(3):
        await _seed_completed_entry(u, machines[0]["id"])
    await _seed_completed_entry(u, machines[1]["id"])

    out = await T.top_n(
        group_by="machine", metric="count", n=1, period="day",
    )
    assert len(out["rows"]) == 1
    assert out["rows"][0]["group_label"] == machines[0]["name"]
    assert out["rows"][0]["value"] == 3


# ── compare_periods ──────────────────────────────────────────────────────


async def test_compare_periods_returns_delta_numbers(db):
    """Place two events: one in last_week (~10 days ago), one in this_week (today)."""
    machines = await models.list_machines()
    u = await _seed_user("u_cmp")
    await _seed_completed_entry(
        u, machines[0]["id"],
        joined_at="datetime('now', '-10 days')",
        serving_at="datetime('now', '-10 days', '+30 minutes')",
        completed_at="datetime('now', '-10 days', '+1 hour')",
    )
    await _seed_completed_entry(u, machines[0]["id"])

    out = await T.compare_periods(
        metric="count", period_a="last_week", period_b="this_week",
    )
    assert out["a"]["value"] == 1
    assert out["b"]["value"] == 1
    assert out["delta_abs"] == 0
    assert out["delta_pct"] == 0


# ── make_chart ───────────────────────────────────────────────────────────


def test_make_chart_produces_well_formed_spec():
    spec = T.make_chart(
        data=[{"label": "A", "value": 1}, {"label": "B", "value": 2}],
        type="bar",
        x={"field": "label", "label": "Group"},
        y={"field": "value", "label": "Count"},
        title="Test chart",
    )
    assert spec["type"] == "bar"
    assert spec["title"] == "Test chart"
    assert spec["x"]["field"] == "label"
    assert spec["y"]["field"] == "value"
    assert len(spec["data"]) == 2


def test_make_chart_rejects_unknown_type():
    with pytest.raises(ValueError):
        T.make_chart(
            data=[], type="3d", x={"field": "g"}, y={"field": "v"}, title="x",
        )


# ── Row cap ──────────────────────────────────────────────────────────────


async def test_query_jobs_row_cap_truncates(db):
    """1100 distinct user-buckets group_by=user → 1000 rows + truncated=True."""
    machines = await models.list_machines()
    db_conn = await get_db()
    for i in range(1100):
        cur = await db_conn.execute(
            "INSERT INTO users (discord_id, discord_name) VALUES (?, ?) RETURNING id",
            (f"bulk-{i}", f"bulk-{i}"),
        )
        uid = (await cur.fetchone())[0]
        await db_conn.execute(
            """
            INSERT INTO queue_entries (user_id, machine_id, status, position, joined_at)
            VALUES (?, ?, 'completed', 1, datetime('now'))
            """,
            (uid, machines[0]["id"]),
        )
    await db_conn.commit()

    out = await T.query_jobs(group_by="user", metric="count", period="day")
    assert len(out["rows"]) == T.ROW_CAP
    assert out["truncated"] is True
