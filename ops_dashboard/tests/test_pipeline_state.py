"""pipeline_state — the pure color function (table-driven) + the 13-stage build."""
from __future__ import annotations

import pytest

from backend.services import pipeline_state as ps


# ── Pure color function: table-driven ──
@pytest.mark.parametrize("kind,count,failures,age,expected,color", [
    ("intake",           0, 0, None, False, "GRAY"),
    ("intake",           5, 0, None, False, "BLUE"),
    ("validated",        0, 0, None, False, "GRAY"),
    ("validated",        5, 0, 9999, True,  "GREEN"),
    ("duplicate",        0, 0, None, False, "GRAY"),
    ("duplicate",        3, 0, None, False, "PURPLE"),
    ("reject",           0, 0, None, False, "GRAY"),
    ("reject",           2, 0, None, False, "RED"),
    ("validation_issue", 0, 0, None, False, "GRAY"),
    ("validation_issue", 1, 0, None, False, "ORANGE"),
    ("success",          0, 0, None, False, "GRAY"),
    ("success",          5, 0, 9999, True,  "GREEN"),
    ("success",          5, 2, 10,   True,  "RED"),     # failures override
    ("success",          5, 0, 30,   True,  "YELLOW"),  # fresh + expected → processing
    ("success",          5, 0, 30,   False, "GREEN"),   # not expected → no yellow
])
def test_derive_color(kind, count, failures, age, expected, color):
    assert ps.derive_color(kind, count, failures, age, expected) == color


# ── Full pipeline build against the fixture ──
def _by_key(pipe):
    return {s["key"]: s for s in pipe["stages"]}


def test_pipeline_counts(gui_config, today):
    pipe = ps.build_pipeline(gui_config, today)
    s = _by_key(pipe)
    assert len(pipe["stages"]) == 13
    assert s["received"]["count"] == 100
    assert s["validated"]["count"] == 85
    assert s["duplicate"]["count"] == 10
    assert s["rejected"]["count"] == 5           # 15 webhook rejects − 10 dup slice
    assert s["risk_rejected"]["count"] == 3
    assert s["capital_rejected"]["count"] == 2
    assert s["orders_created"]["count"] == 70
    assert s["orders_placed"]["count"] == 70
    assert s["orders_filled"]["count"] == 60
    assert s["sl_hit"]["count"] == 8
    assert s["tgt_hit"]["count"] == 12
    assert s["manual_exit"]["count"] == 2
    assert s["trade_closed"]["count"] == 4


def test_pipeline_card_colors(gui_config, today):
    s = _by_key(ps.build_pipeline(gui_config, today))
    assert s["duplicate"]["color"] == "PURPLE"
    assert s["rejected"]["color"] == "RED"
    assert s["risk_rejected"]["color"] == "ORANGE"
    assert s["capital_rejected"]["color"] == "ORANGE"


def test_pipeline_halt_overlay(gui_config, today):
    pipe = ps.build_pipeline(gui_config, today)
    assert pipe["halt"]["state"] == "INACTIVE"
    assert pipe["halt"]["halted"] is False
