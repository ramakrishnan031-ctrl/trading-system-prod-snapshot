"""pipeline_state — the pure color function (table-driven) + the 13-stage build."""
from __future__ import annotations

import pytest

from backend.services import pipeline_state as ps


# ── Pure colour function: table-driven ──
# ⭐ THE RULE CHANGED ON 18-Aug AND THIS TABLE CHANGED WITH IT, ⛔ not to make a
# red test green: the approved Screen-02 artwork does not tint a card by
# freshness (its Orders Filled at 7 s is GREEN while its SL Hit at 141 s is
# AMBER), so `derive_color` now takes the stage's FIXED semantic colour and gates
# it on count/failures alone. The clock left the signature entirely — the freshest
# stage is marked by `active`, a ring, not a colour.
@pytest.mark.parametrize("semantic,count,failures,colour", [
    # a zero stage is neutral WHATEVER its semantic colour
    ("ORANGE", 0, 0, "GRAY"),
    ("GREEN",  0, 0, "GRAY"),
    ("BLUE",   0, 0, "GRAY"),
    ("PURPLE", 0, 0, "GRAY"),
    ("RED",    0, 0, "GRAY"),
    # a counted stage wears its own semantic colour
    ("ORANGE", 5, 0, "ORANGE"),
    ("GREEN",  5, 0, "GREEN"),
    ("BLUE",   5, 0, "BLUE"),
    ("PURPLE", 3, 0, "PURPLE"),
    ("RED",    2, 0, "RED"),
    ("GRAY",   7, 0, "GRAY"),
    # failures override every semantic colour
    ("GREEN",  5, 2, "RED"),
    ("BLUE",   5, 1, "RED"),
    # ⛔ a zero stage with failures is still RED — failures outrank the zero rule
    ("GREEN",  0, 3, "RED"),
])
def test_derive_colour(semantic, count, failures, colour):
    assert ps.derive_color(semantic, count, failures) == colour


def test_colour_does_not_depend_on_any_clock():
    """⛔ THE SIGNATURE ITSELF must not accept a freshness reading again.

    A regression here would not show up as a wrong colour in any fixture — it
    would show up months later as a card that changes colour because a poll
    landed. So the guard is on the signature, not on one sampled output.
    """
    import inspect
    params = list(inspect.signature(ps.derive_color).parameters)
    assert params == ["semantic", "count", "failures"]
    for banned in ("age", "last_event_age_sec", "expected_activity", "now"):
        assert banned not in params


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
