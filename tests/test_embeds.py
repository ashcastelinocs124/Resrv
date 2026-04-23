"""Tests for the multi-unit embed rendering."""

from __future__ import annotations

from bot.embeds import build_machine_embed


def _field_text(embed) -> str:
    return " | ".join(f"{f.name}::{f.value}" for f in embed.fields)


def test_embed_renders_units_block():
    machine = {"id": 1, "name": "3D Printer", "slug": "3d", "status": "active"}
    units = [
        {"id": 10, "label": "Prusa MK4", "status": "active", "serving_name": None},
        {"id": 11, "label": "Bambu X1", "status": "active", "serving_name": "alice"},
        {"id": 12, "label": "Ender 3", "status": "maintenance", "serving_name": None},
    ]
    embed = build_machine_embed(machine, queue_entries=[], units=units)
    text = _field_text(embed)
    assert "Prusa MK4" in text
    assert "Bambu X1" in text
    assert "alice" in text
    assert "Ender 3" in text


def test_embed_hides_units_block_when_single_main_unit():
    machine = {"id": 1, "name": "Laser", "slug": "laser", "status": "active"}
    units = [{"id": 10, "label": "Main", "status": "active", "serving_name": None}]
    embed = build_machine_embed(machine, queue_entries=[], units=units)
    names = [f.name for f in embed.fields]
    assert "Units" not in names


def test_embed_all_units_unavailable_when_all_maintenance():
    machine = {"id": 1, "name": "CNC", "slug": "cnc", "status": "active"}
    units = [
        {"id": 10, "label": "A", "status": "maintenance", "serving_name": None},
        {"id": 11, "label": "B", "status": "maintenance", "serving_name": None},
    ]
    embed = build_machine_embed(machine, queue_entries=[], units=units)
    text = _field_text(embed)
    assert "unavailable" in text.lower()
