"""freshness — the market-clock authority (pure, injected `now`)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.services import freshness

_IST = timezone(timedelta(hours=5, minutes=30))

# 2026-07-03 is a Friday; 2026-07-04 is a Saturday.
FRI_ENTRY = datetime(2026, 7, 3, 11, 0, tzinfo=_IST)     # entry window
FRI_PREOPEN = datetime(2026, 7, 3, 8, 0, tzinfo=_IST)    # before open
FRI_SQUAREOFF = datetime(2026, 7, 3, 15, 20, tzinfo=_IST)  # after entry, before close
SAT = datetime(2026, 7, 4, 11, 0, tzinfo=_IST)           # weekend


def test_phase(gui_config):
    assert freshness.phase(gui_config, FRI_ENTRY) == "ENTRY_WINDOW"
    assert freshness.phase(gui_config, FRI_PREOPEN) == "PRE_OPEN"
    assert freshness.phase(gui_config, FRI_SQUAREOFF) == "SQUAREOFF"
    assert freshness.phase(gui_config, SAT) == "WEEKEND"


def test_market_and_entry_windows(gui_config):
    assert freshness.market_is_open(gui_config, FRI_ENTRY) is True
    assert freshness.in_entry_window(gui_config, FRI_ENTRY) is True
    assert freshness.market_is_open(gui_config, FRI_PREOPEN) is False
    assert freshness.market_is_open(gui_config, SAT) is False
    # after entry window but before close: market open, entries no longer expected
    assert freshness.market_is_open(gui_config, FRI_SQUAREOFF) is True
    assert freshness.in_entry_window(gui_config, FRI_SQUAREOFF) is False


def test_expected_activity(gui_config):
    assert freshness.expected_activity(gui_config, "received", FRI_ENTRY) is True
    assert freshness.expected_activity(gui_config, "orders_created", FRI_ENTRY) is True
    # entry stage silent-normal after the window closes; exits still expected
    assert freshness.expected_activity(gui_config, "orders_created", FRI_SQUAREOFF) is False
    assert freshness.expected_activity(gui_config, "sl_hit", FRI_SQUAREOFF) is True
    # weekend: nothing expected
    assert freshness.expected_activity(gui_config, "received", SAT) is False


def test_poll_interval(gui_config):
    assert freshness.poll_interval_ms(gui_config, FRI_ENTRY) == 5000
    assert freshness.poll_interval_ms(gui_config, SAT) == 60000


def test_age_and_parse(gui_config):
    now = FRI_ENTRY
    ts = "2026-07-03T10:59:00+05:30"
    age = freshness.age_seconds(ts, now)
    assert age == 60.0
    assert freshness.age_seconds(None, now) is None
    assert freshness.parse_ist("not-a-date") is None
    # space-separated naive form is treated as IST
    assert freshness.parse_ist("2026-07-03 10:00:00") is not None
