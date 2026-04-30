"""DB-layer tests for pinned_charts."""
import json
import pytest
from db import models
from api.auth import hash_password

pytestmark = pytest.mark.asyncio


async def _seed_staff(username: str = "pin-user") -> int:
    return (await models.create_staff(
        username, hash_password("x"), "admin"
    ))["id"]


async def test_create_pinned_chart(db):
    sid = await _seed_staff("pin-create")
    spec = {"type": "bar", "title": "x", "x": {"field": "g"}, "y": {"field": "v"},
             "data": []}
    row = await models.create_pinned_chart(
        chart_spec=spec, title="My chart", created_by=sid,
    )
    assert row["id"] > 0
    assert row["title"] == "My chart"
    assert json.loads(row["chart_spec_json"])["type"] == "bar"


async def test_pin_order_auto_increments(db):
    sid = await _seed_staff("pin-order")
    a = await models.create_pinned_chart(
        chart_spec={"type": "bar"}, title="A", created_by=sid,
    )
    b = await models.create_pinned_chart(
        chart_spec={"type": "line"}, title="B", created_by=sid,
    )
    assert b["pin_order"] > a["pin_order"]


async def test_list_pinned_charts_ordered(db):
    sid = await _seed_staff("pin-list")
    await models.create_pinned_chart(chart_spec={"type": "bar"}, title="A",
                                       created_by=sid)
    await models.create_pinned_chart(chart_spec={"type": "bar"}, title="B",
                                       created_by=sid)
    rows = await models.list_pinned_charts()
    titles = [r["title"] for r in rows]
    assert titles.index("A") < titles.index("B")


async def test_delete_pinned_chart(db):
    sid = await _seed_staff("pin-del")
    row = await models.create_pinned_chart(
        chart_spec={"type": "bar"}, title="A", created_by=sid,
    )
    deleted = await models.delete_pinned_chart(row["id"])
    assert deleted is True


async def test_delete_pinned_chart_missing_returns_false(db):
    deleted = await models.delete_pinned_chart(99999)
    assert deleted is False
