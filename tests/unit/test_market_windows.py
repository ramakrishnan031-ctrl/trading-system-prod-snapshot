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


def test_seconds_to_market_open_after_open_rolls_to_next_day():
    mw = MarketWindows()
    # Wed 2026-04-15 10:00 -> next open is Thu 2026-04-16 09:15.
    # delta = 23h15m = 83700s
    assert mw.seconds_to_market_open(TRADING_DAY(10, 0)) == 83700


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
    test_is_eod_squareoff_due_before_and_after,
    test_is_eod_squareoff_due_holiday_or_weekend,
    test_eod_squareoff_time_preserves_tz_and_date,
    test_seconds_to_eod_squareoff_positive_and_negative,
    test_seconds_to_market_open_same_day_before_open,
    test_seconds_to_market_open_after_open_rolls_to_next_day,
    test_seconds_to_market_open_from_weekend,
    test_is_trading_holiday_weekend_and_configured,
    test_next_trading_day_simple_weekday,
    test_next_trading_day_skips_weekend,
    test_next_trading_day_skips_holiday,
    test_next_trading_day_lookahead_cap,
    test_next_trading_day_11_consecutive_holidays_succeeds,
    test_custom_window_overrides,
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
