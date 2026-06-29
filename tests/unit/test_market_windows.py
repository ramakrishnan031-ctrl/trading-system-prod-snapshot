# =============================================================================
# Script        : tests/unit/test_market_windows.py
# Purpose       : Standalone test runner for core/market_windows.py.
# Manual Input  : No.
# How it works  : Defines a tiny assert-based runner (no pytest). Each test
#                 is a function returning None on success, raising AssertionError
#                 on failure. Runs them all, prints PASS/FAIL summary, exits
#                 with code 0 on full pass else 1.
# Inputs        : None.
# Outputs       : stdout summary; process exit code.
# Entry point   : run from CLI: `python tests/unit/test_market_windows.py`
# =============================================================================

from __future__ import annotations

import sys
import traceback
from datetime import date, datetime, time, timedelta, timezone

# Allow running from repo root without installing.
sys.path.insert(0, "/home/claude/v2")

from core.market_windows import MarketWindows  # noqa: E402


IST = timezone(timedelta(hours=5, minutes=30))


def _dt(y, m, d, hh=0, mm=0, ss=0):
    return datetime(y, m, d, hh, mm, ss, tzinfo=IST)


# A known weekday (Wed 2026-04-15) and a known weekend (Sat 2026-04-18).
TRADING_DAY = lambda hh, mm=0, ss=0: _dt(2026, 4, 15, hh, mm, ss)
SATURDAY = lambda hh=10, mm=0: _dt(2026, 4, 18, hh, mm)
SUNDAY = lambda hh=10, mm=0: _dt(2026, 4, 19, hh, mm)


# ---------- tests ----------

def test_defaults_match_spec():
    mw = MarketWindows()
    assert mw.entry_start == time(9, 30)
    assert mw.entry_end == time(13, 30)
    assert mw.market_open == time(9, 15)
    assert mw.market_close == time(15, 30)
    assert mw.eod_squareoff_t == time(15, 17)
    assert mw.holidays == set()


def test_is_market_open_within_hours():
    mw = MarketWindows()
    assert mw.is_market_open(TRADING_DAY(9, 15)) is True
    assert mw.is_market_open(TRADING_DAY(12, 0)) is True
    assert mw.is_market_open(TRADING_DAY(15, 29, 59)) is True


def test_is_market_open_boundary_close_excluded():
    mw = MarketWindows()
    # 15:30 exact = closed (half-open interval).
    assert mw.is_market_open(TRADING_DAY(15, 30)) is False


def test_is_market_open_before_open():
    mw = MarketWindows()
    assert mw.is_market_open(TRADING_DAY(9, 14, 59)) is False
    assert mw.is_market_open(TRADING_DAY(0, 0)) is False


def test_is_market_open_weekend():
    mw = MarketWindows()
    assert mw.is_market_open(SATURDAY(10)) is False
    assert mw.is_market_open(SUNDAY(10)) is False


def test_is_market_open_holiday():
    mw = MarketWindows(holidays={date(2026, 4, 15)})
    assert mw.is_market_open(TRADING_DAY(10, 0)) is False


def test_is_entry_allowed_window():
    mw = MarketWindows()
    assert mw.is_entry_allowed(TRADING_DAY(9, 30)) is True
    assert mw.is_entry_allowed(TRADING_DAY(11, 0)) is True
    assert mw.is_entry_allowed(TRADING_DAY(13, 29, 59)) is True


def test_is_entry_allowed_boundaries():
    mw = MarketWindows()
    # Before window.
    assert mw.is_entry_allowed(TRADING_DAY(9, 29, 59)) is False
    # End is exclusive.
    assert mw.is_entry_allowed(TRADING_DAY(13, 30)) is False
    # After window but market still open -> still no entry.
    assert mw.is_entry_allowed(TRADING_DAY(14, 0)) is False


def test_is_entry_allowed_weekend_and_holiday():
    mw = MarketWindows(holidays={date(2026, 4, 15)})
    assert mw.is_entry_allowed(SATURDAY(11)) is False
    assert mw.is_entry_allowed(TRADING_DAY(11, 0)) is False  # holiday


def test_is_eod_squareoff_due_before_and_after():
    mw = MarketWindows()
    assert mw.is_eod_squareoff_due(TRADING_DAY(15, 16, 59)) is False
    assert mw.is_eod_squareoff_due(TRADING_DAY(15, 17, 0)) is True
    assert mw.is_eod_squareoff_due(TRADING_DAY(15, 25)) is True


def test_is_entry_allowed_for_strategy_narrower_window():
    """
    CFG-5 (2026-04-26 audit): per-strategy entry window must compose with
    the global window. A strategy declaring 09:30-11:30 must reject 12:00
    even though the global window allows it.
    """
    class _Strat:
        entry_start_time = "09:30"
        entry_end_time = "11:30"
    mw = MarketWindows()
    s = _Strat()
    assert mw.is_entry_allowed_for_strategy(TRADING_DAY(10, 0), s) is True
    assert mw.is_entry_allowed_for_strategy(TRADING_DAY(11, 30), s) is False
    assert mw.is_entry_allowed_for_strategy(TRADING_DAY(12, 0), s) is False
    # Outside global window -> always False even if inside per-strategy.
    assert mw.is_entry_allowed_for_strategy(TRADING_DAY(9, 0), s) is False


def test_is_entry_allowed_for_strategy_default_matches_global():
    """If a strategy uses the default 09:30-13:30, behavior matches global."""
    class _Strat:
        entry_start_time = "09:30"
        entry_end_time = "13:30"
    mw = MarketWindows()
    s = _Strat()
    assert mw.is_entry_allowed_for_strategy(TRADING_DAY(10, 0), s) is True
    assert mw.is_entry_allowed_for_strategy(TRADING_DAY(13, 29, 59), s) is True
    assert mw.is_entry_allowed_for_strategy(TRADING_DAY(13, 30), s) is False


def test_t5_global_entry_end_1500_caps_entries():
    """T5 (29-Jun): global entry_end aligned 15:15->15:00. An entry after 15:00 is
    rejected (end is exclusive)."""
    mw = MarketWindows(entry_start=time(10, 0), entry_end=time(15, 0))
    assert mw.is_entry_allowed(TRADING_DAY(14, 59, 59)) is True
    assert mw.is_entry_allowed(TRADING_DAY(15, 0)) is False
    assert mw.is_entry_allowed(TRADING_DAY(15, 5)) is False


def test_t5_strategy_1515_capped_by_global_1500():
    """T5 hardening: a strategy that sets a later entry_end_time=15:15 (e.g. a future
    override, or the pre-T5 schema default) can no longer enter 15:00-15:15 -- the
    global 15:00 binds. (Schema default is itself 15:00 since the T5 followup.)"""
    class _Strat:
        entry_start_time = "09:25"
        entry_end_time = "15:15"
    mw = MarketWindows(entry_start=time(10, 0), entry_end=time(15, 0))
    s = _Strat()
    assert mw.is_entry_allowed_for_strategy(TRADING_DAY(14, 59), s) is True
    assert mw.is_entry_allowed_for_strategy(TRADING_DAY(15, 5), s) is False  # global 15:00 binds


def test_t5_strategy_missing_entry_end_inherits_global_1500():
    """A strategy that omits its own entry_end_time falls back to the global window
    -> bounded by 15:00 (was 15:15)."""
    class _Strat:
        pass  # no per-strategy times -> AttributeError -> global window binds
    mw = MarketWindows(entry_start=time(10, 0), entry_end=time(15, 0))
    s = _Strat()
    assert mw.is_entry_allowed_for_strategy(TRADING_DAY(14, 59), s) is True
    assert mw.is_entry_allowed_for_strategy(TRADING_DAY(15, 5), s) is False


def test_t5_existing_1500_strategy_unchanged():
    """Regression: a strategy with entry_end_time=15:00 (every current strategy)
    behaves identically before/after T5."""
    class _Strat:
        entry_start_time = "09:25"
        entry_end_time = "15:00"
    mw = MarketWindows(entry_start=time(10, 0), entry_end=time(15, 0))
    s = _Strat()
    assert mw.is_entry_allowed_for_strategy(TRADING_DAY(14, 59, 59), s) is True
    assert mw.is_entry_allowed_for_strategy(TRADING_DAY(15, 0), s) is False
    assert mw.is_entry_allowed_for_strategy(TRADING_DAY(15, 1), s) is False


def test_is_eod_squareoff_due_holiday_or_weekend():
    mw = MarketWindows(holidays={date(2026, 4, 15)})
    # Holiday -> never due.
    assert mw.is_eod_squareoff_due(TRADING_DAY(15, 17)) is False
    # Weekend -> never due.
    mw2 = MarketWindows()
    assert mw2.is_eod_squareoff_due(SATURDAY(15, 30)) is False


def test_eod_squareoff_time_preserves_tz_and_date():
    mw = MarketWindows()
    now = TRADING_DAY(10, 0)
    eod = mw.eod_squareoff_time(now)
    assert eod.year == 2026 and eod.month == 4 and eod.day == 15
    assert eod.hour == 15 and eod.minute == 17 and eod.second == 0
    assert eod.tzinfo == IST


def test_seconds_to_eod_squareoff_positive_and_negative():
    mw = MarketWindows()
    # 10:00 -> 15:17 = 5h17m = 19020s
    assert mw.seconds_to_eod_squareoff(TRADING_DAY(10, 0)) == 19020
    # 15:18 -> -60s
    assert mw.seconds_to_eod_squareoff(TRADING_DAY(15, 18)) == -60
    # Exactly at EOD -> 0.
    assert mw.seconds_to_eod_squareoff(TRADING_DAY(15, 17)) == 0


def test_seconds_to_market_open_same_day_before_open():
    mw = MarketWindows()
    # 08:00 -> 09:15 = 1h15m = 4500s on a trading day.
    assert mw.seconds_to_market_open(TRADING_DAY(8, 0)) == 4500


def test_fix053_seconds_to_market_open_during_market_hours_returns_zero():
    """FIX-053: Boot at 09:15:01 or any time during market hours -> return 0."""
    mw = MarketWindows()
    # Boot at 09:15:01 (just after open) -> market is open, return 0
    assert mw.seconds_to_market_open(TRADING_DAY(9, 15, 1)) == 0
    # Boot at 12:00:00 (mid-session) -> market is open, return 0
    assert mw.seconds_to_market_open(TRADING_DAY(12, 0)) == 0
    # Boot at 15:29:59 (1 second before close) -> market is open, return 0
    assert mw.seconds_to_market_open(TRADING_DAY(15, 29, 59)) == 0
    # Exactly at open -> market is open, return 0
    assert mw.seconds_to_market_open(TRADING_DAY(9, 15, 0)) == 0


def test_fix053_seconds_to_market_open_after_close_rolls_to_next_day():
    """FIX-053: Boot at 16:00 (after close) -> roll to next trading day."""
    mw = MarketWindows()
    # Wed 2026-04-15 16:00 -> next open is Thu 2026-04-16 09:15.
    # delta = 17h15m = 62100s
    assert mw.seconds_to_market_open(TRADING_DAY(16, 0)) == 62100
    # At exact close (15:30) -> also rolls to next day
    # delta = 17h45m = 63900s
    assert mw.seconds_to_market_open(TRADING_DAY(15, 30)) == 63900


def test_seconds_to_market_open_from_weekend():
    mw = MarketWindows()
    # Sat 2026-04-18 10:00 -> next open Mon 2026-04-20 09:15.
    # delta = 2 days - 45 min = 47h15m = 170100s
    assert mw.seconds_to_market_open(SATURDAY(10, 0)) == 170100


def test_is_trading_holiday_weekend_and_configured():
    mw = MarketWindows(holidays={date(2026, 4, 15)})
    assert mw.is_trading_holiday(SATURDAY(10)) is True
    assert mw.is_trading_holiday(SUNDAY(10)) is True
    assert mw.is_trading_holiday(TRADING_DAY(10)) is True
    # A clean trading weekday with no holiday set.
    mw2 = MarketWindows()
    assert mw2.is_trading_holiday(TRADING_DAY(10)) is False


def test_next_trading_day_simple_weekday():
    mw = MarketWindows()
    # Wed -> Thu
    assert mw.next_trading_day(TRADING_DAY(10)) == date(2026, 4, 16)


def test_next_trading_day_skips_weekend():
    mw = MarketWindows()
    # Fri 2026-04-17 -> Mon 2026-04-20
    assert mw.next_trading_day(_dt(2026, 4, 17, 16)) == date(2026, 4, 20)


def test_next_trading_day_skips_holiday():
    mw = MarketWindows(holidays={date(2026, 4, 16), date(2026, 4, 17)})
    # Wed -> skip Thu (holiday) + Fri (holiday) + Sat/Sun -> Mon
    assert mw.next_trading_day(TRADING_DAY(10)) == date(2026, 4, 20)


def test_next_trading_day_lookahead_cap():
    # Block 31 consecutive days -> should raise (exceeds cap of 30).
    blocked = {date(2026, 4, 15) + timedelta(days=i) for i in range(1, 38)}
    mw = MarketWindows(holidays=blocked)
    raised = False
    try:
        mw.next_trading_day(TRADING_DAY(10))
    except ValueError:
        raised = True
    assert raised, "Expected ValueError when lookahead cap exceeded"


def test_next_trading_day_11_consecutive_holidays_succeeds():
    # MED #2: 11 consecutive configured holidays should no longer raise with cap=30.
    # Block Mon-Fri for 2 full weeks + 1 extra day (11 weekday holidays).
    # We start from a Friday; the next 11 weekdays blocked by holidays.
    base = date(2026, 4, 17)  # Friday
    blocked = set()
    d = base + timedelta(days=1)
    added = 0
    while added < 11:
        if d.weekday() < 5:  # weekday
            blocked.add(d)
            added += 1
        d += timedelta(days=1)
    mw = MarketWindows(holidays=blocked)
    result = mw.next_trading_day(_dt(2026, 4, 17, 10))
    assert result > base, f"Expected a date after {base}, got {result}"


def test_custom_window_overrides():
    mw = MarketWindows(
        entry_start=time(10, 0),
        entry_end=time(12, 0),
        eod_squareoff=time(15, 0),
    )
    assert mw.is_entry_allowed(TRADING_DAY(9, 45)) is False
    assert mw.is_entry_allowed(TRADING_DAY(10, 0)) is True
    assert mw.is_entry_allowed(TRADING_DAY(12, 0)) is False
    assert mw.is_eod_squareoff_due(TRADING_DAY(15, 0)) is True
    assert mw.is_eod_squareoff_due(TRADING_DAY(14, 59)) is False


# ---------- FIX-094: Special sessions ----------

def test_fix094_no_special_sessions_uses_defaults():
    """When no special sessions configured, defaults are used."""
    mw = MarketWindows()
    # Wednesday, normal market hours
    assert mw.is_market_open(TRADING_DAY(9, 15)) is True
    assert mw.is_market_open(TRADING_DAY(15, 30)) is False
    assert mw.is_eod_squareoff_due(TRADING_DAY(15, 17)) is True


def test_fix094_special_session_overrides_market_hours():
    """Muhurat trading: evening session 18:15-19:15."""
    muhurat_date = date(2026, 11, 1)
    special_sessions = {
        muhurat_date: (time(18, 15), time(19, 15), time(19, 12)),
    }
    mw = MarketWindows(special_sessions=special_sessions)

    # On Muhurat date, market NOT open at normal hours
    muhurat_dt = lambda hh, mm=0: _dt(2026, 11, 1, hh, mm)
    assert mw.is_market_open(muhurat_dt(9, 15)) is False
    assert mw.is_market_open(muhurat_dt(15, 0)) is False

    # Market IS open during special session
    assert mw.is_market_open(muhurat_dt(18, 15)) is True
    assert mw.is_market_open(muhurat_dt(19, 0)) is True
    assert mw.is_market_open(muhurat_dt(19, 15)) is False  # close time excluded


def test_fix094_special_session_eod_squareoff_override():
    """EOD square-off uses special session time."""
    muhurat_date = date(2026, 11, 1)
    special_sessions = {
        muhurat_date: (time(18, 15), time(19, 15), time(19, 12)),
    }
    mw = MarketWindows(special_sessions=special_sessions)

    muhurat_dt = lambda hh, mm=0: _dt(2026, 11, 1, hh, mm)
    # Normal EOD time 15:17 should NOT trigger
    assert mw.is_eod_squareoff_due(muhurat_dt(15, 17)) is False

    # Special session EOD time 19:12 triggers
    assert mw.is_eod_squareoff_due(muhurat_dt(19, 11)) is False
    assert mw.is_eod_squareoff_due(muhurat_dt(19, 12)) is True


def test_fix094_special_session_eod_squareoff_time_returns_correct_datetime():
    """eod_squareoff_time() returns special session time."""
    muhurat_date = date(2026, 11, 1)
    special_sessions = {
        muhurat_date: (time(18, 15), time(19, 15), time(19, 12)),
    }
    mw = MarketWindows(special_sessions=special_sessions)

    muhurat_dt = _dt(2026, 11, 1, 18, 30)
    eod_dt = mw.eod_squareoff_time(muhurat_dt)
    assert eod_dt == _dt(2026, 11, 1, 19, 12)
    assert eod_dt.tzinfo == IST


def test_fix094_special_session_not_carried_forward_to_next_day():
    """Special session on 2026-11-01 does NOT affect 2026-11-02."""
    muhurat_date = date(2026, 11, 1)
    special_sessions = {
        muhurat_date: (time(18, 15), time(19, 15), time(19, 12)),
    }
    mw = MarketWindows(special_sessions=special_sessions)

    # Next day (Sunday) should use defaults (weekend -> not open)
    next_day_dt = _dt(2026, 11, 2, 18, 30)  # Sunday
    assert mw.is_market_open(next_day_dt) is False  # weekend

    # Monday after Muhurat: normal hours
    monday_dt = _dt(2026, 11, 3, 9, 30)  # Monday
    assert mw.is_market_open(monday_dt) is True


def test_fix094_seconds_to_market_open_uses_special_session():
    """seconds_to_market_open uses special session time for next day."""
    muhurat_date = date(2026, 11, 1)  # Saturday
    special_sessions = {
        muhurat_date: (time(18, 15), time(19, 15), time(19, 12)),
    }
    # Do NOT add muhurat_date to holidays - special sessions override weekend check
    mw = MarketWindows(special_sessions=special_sessions)

    # From Friday afternoon, next open is Saturday Muhurat at 18:15
    friday_dt = _dt(2026, 10, 31, 16, 0)  # Friday 4pm
    seconds = mw.seconds_to_market_open(friday_dt)

    # Calculate expected: Friday 16:00 to Saturday 18:15
    # = 26 hours 15 min = 94500 seconds
    expected = ((24 + 2) * 3600) + (15 * 60)
    assert seconds == expected, f"Expected {expected}, got {seconds}"


# ---------- runner ----------

TESTS = [
    test_defaults_match_spec,
    test_is_market_open_within_hours,
    test_is_market_open_boundary_close_excluded,
    test_is_market_open_before_open,
    test_is_market_open_weekend,
    test_is_market_open_holiday,
    test_is_entry_allowed_window,
    test_is_entry_allowed_boundaries,
    test_is_entry_allowed_weekend_and_holiday,
    test_is_entry_allowed_for_strategy_narrower_window,
    test_is_entry_allowed_for_strategy_default_matches_global,
    test_is_eod_squareoff_due_before_and_after,
    test_is_eod_squareoff_due_holiday_or_weekend,
    test_eod_squareoff_time_preserves_tz_and_date,
    test_seconds_to_eod_squareoff_positive_and_negative,
    test_seconds_to_market_open_same_day_before_open,
    test_fix053_seconds_to_market_open_during_market_hours_returns_zero,
    test_fix053_seconds_to_market_open_after_close_rolls_to_next_day,
    test_seconds_to_market_open_from_weekend,
    test_is_trading_holiday_weekend_and_configured,
    test_next_trading_day_simple_weekday,
    test_next_trading_day_skips_weekend,
    test_next_trading_day_skips_holiday,
    test_next_trading_day_lookahead_cap,
    test_next_trading_day_11_consecutive_holidays_succeeds,
    test_custom_window_overrides,
    test_fix094_no_special_sessions_uses_defaults,
    test_fix094_special_session_overrides_market_hours,
    test_fix094_special_session_eod_squareoff_override,
    test_fix094_special_session_eod_squareoff_time_returns_correct_datetime,
    test_fix094_special_session_not_carried_forward_to_next_day,
    test_fix094_seconds_to_market_open_uses_special_session,
]


def main() -> int:
    passed = 0
    failed = 0
    for t in TESTS:
        try:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
        except Exception:
            print(f"FAIL  {t.__name__}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{len(TESTS)} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
