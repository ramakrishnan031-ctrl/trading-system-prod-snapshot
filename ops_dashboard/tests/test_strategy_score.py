"""V3 — scorecard + silence truth tables (pure functions, table-driven).

Every RED / YELLOW / GREEN rule is hit at least once, plus disabled=GRAY and
the outside-window no-alarm case.
"""
from __future__ import annotations

import pytest

from backend.services import strategy_score as ss

_BASE = {
    "enabled": True, "expected_activity": True, "last_signal_age_min": 5.0,
    "failed_orders": 0, "consec_losses": 0, "net_pnl": 100.0,
    "win_rate": 60.0, "closed_decided": 5, "capacity_used_pct": 50.0,
    "in_entry_window": True,
}


def _case(**over):
    d = dict(_BASE)
    d.update(over)
    return d


# ── RED rules ──
@pytest.mark.parametrize("inputs,expect_reason", [
    (_case(last_signal_age_min=None), "silent"),                    # silent: none today
    (_case(last_signal_age_min=180.0), "silent"),                   # silent: >120m
    (_case(failed_orders=3), "failed orders"),                      # ≥3 FAILED
    (_case(consec_losses=3), "consecutive losses"),                 # ≥3 streak
    (_case(net_pnl=-500.0), "net P&L"),                             # ≤ floor
    (_case(net_pnl=-750.0), "net P&L"),
])
def test_red_rules(inputs, expect_reason):
    out = ss.score(inputs)
    assert out["badge"] == "RED"
    assert any(expect_reason in r for r in out["reasons"])


# ── YELLOW rules ──
@pytest.mark.parametrize("inputs,expect_reason", [
    (_case(last_signal_age_min=100.0), "no signal for"),            # >90m in window
    (_case(win_rate=30.0, closed_decided=3), "win rate"),           # <40% w/ ≥3
    (_case(failed_orders=1), "failed order"),                       # ≥1 FAILED
    (_case(capacity_used_pct=95.0), "position capacity"),           # ≥90%
])
def test_yellow_rules(inputs, expect_reason):
    out = ss.score(inputs)
    assert out["badge"] == "YELLOW"
    assert any(expect_reason in r for r in out["reasons"])


# ── GREEN + GRAY + precedence + gating ──
def test_green_when_all_clear():
    out = ss.score(_case())
    assert out == {"badge": "GREEN", "reasons": []}


def test_disabled_is_gray_never_red():
    # even with every RED condition present, disabled → neutral GRAY
    out = ss.score(_case(enabled=False, last_signal_age_min=None,
                         failed_orders=9, consec_losses=9, net_pnl=-9999.0))
    assert out["badge"] == "GRAY"


def test_red_wins_over_yellow():
    out = ss.score(_case(failed_orders=3, win_rate=10.0, closed_decided=5))
    assert out["badge"] == "RED"


def test_outside_window_no_silence_alarm():
    # activity NOT expected → silence never turns the badge RED...
    out = ss.score(_case(expected_activity=False, last_signal_age_min=None,
                         in_entry_window=False))
    assert out["badge"] == "GREEN"
    # ...and yellow-silence is entry-window-gated too
    out = ss.score(_case(expected_activity=False, in_entry_window=False,
                         last_signal_age_min=100.0))
    assert out["badge"] == "GREEN"


def test_win_rate_needs_min_closed():
    # 0% win rate with only 2 decided (< 3) → NOT yellow
    out = ss.score(_case(win_rate=0.0, closed_decided=2))
    assert out["badge"] == "GREEN"


def test_thresholds_configurable():
    out = ss.score(_case(net_pnl=-200.0), thresholds={"red_pnl_floor": -100.0})
    assert out["badge"] == "RED"


# ── silence tiers (§1.2) ──
@pytest.mark.parametrize("age,in_window,color,alarm", [
    (10.0, True, "GREEN", False),
    (30.0, True, "GREEN", False),      # boundary: ≤30 green
    (31.0, True, "YELLOW", True),
    (120.0, True, "YELLOW", True),     # boundary: ≤120 yellow
    (121.0, True, "RED", True),
    (None, True, "RED", True),         # none today
    (500.0, False, None, False),       # outside window → plain, no alarm
    (None, False, None, False),
])
def test_silence_tiers(age, in_window, color, alarm):
    out = ss.silence_tier(age, in_window)
    assert out["color"] == color and out["alarm"] == alarm
