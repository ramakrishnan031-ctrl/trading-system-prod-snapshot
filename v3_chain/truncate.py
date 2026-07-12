"""
v3_chain/truncate.py — Trading System v2 · V3 Step 10a · NO-LOOKAHEAD truncation.

THE anti-lookahead-bias guard (Step-10a addendum, MANDATORY correctness requirement).

The V3 enrichment worker runs ASYNCHRONOUSLY, AFTER the signal fired. If it computed
S&R / indicators from a candle series that includes bars which CLOSED DURING OR AFTER
the live entry, the verdict would carry hindsight — and the whole G-KALYAN measurement
("would the R:R gate have rejected our winners or our losers?") would be flattering and
WRONG. So EVERY series MUST be truncated to bars that had CLOSED STRICTLY BEFORE the
signal's decision instant (`as_of`). A bar still FORMING at `as_of` is EXCLUDED — we
never act on a forming candle (Constitution).

Pure: stdlib + the shared Candle type only (function-local import, no layer inversion).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional, Sequence

# Interval string → bucket size in MINUTES. "day" is handled separately (a daily bar for
# date D only completes after D's session close, so it is usable only on a LATER day).
_INTERVAL_MINUTES = {
    "1minute": 1,
    "3minute": 3,
    "5minute": 5,
    "10minute": 10,
    "15minute": 15,
    "30minute": 30,
    "60minute": 60,
    "hour": 60,
}

DAILY_INTERVALS = {"day", "daily"}


def interval_minutes(interval: str) -> Optional[int]:
    """Bucket size in minutes for an intraday interval, or None for a daily/unknown
    interval (daily is handled by date, not by a minute offset)."""
    return _INTERVAL_MINUTES.get(str(interval).strip().lower())


def _align_tz(ts: datetime, ref: datetime) -> datetime:
    """Make `ts` comparable to `ref`: match aware/naive-ness (assumes both are IST —
    the codebase's single convention). Never converts wall-clock, only attaches/strips
    tzinfo so a naive-IST and an aware-IST value compare correctly."""
    if ts.tzinfo is None and ref.tzinfo is not None:
        return ts.replace(tzinfo=ref.tzinfo)
    if ts.tzinfo is not None and ref.tzinfo is None:
        return ts.replace(tzinfo=None)
    return ts


def close_ts(candle, interval: str, as_of: Optional[datetime] = None) -> datetime:
    """The instant a candle is COMPLETE (its close becomes known).

    Intraday: bar START (candle.ts) + the interval length. Daily: the bar covers a whole
    session, so we treat it as completing at the END of its date — represented here as
    the next midnight (a daily bar for date D is usable only once D is fully in the past).
    `as_of` (when given) is used only to align tzinfo for a valid comparison.
    """
    ts = candle.ts if as_of is None else _align_tz(candle.ts, as_of)
    mins = interval_minutes(interval)
    if mins is not None:
        return ts + timedelta(minutes=mins)
    # daily: completes at the end of its date (start of the next day).
    day_start = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    return day_start + timedelta(days=1)


def truncate_to_asof(
    candles: Sequence, interval: str, as_of: datetime,
) -> List:
    """Return only the candles that had fully CLOSED at or before `as_of`.

    A candle qualifies iff its close instant <= as_of (i.e. it was NOT still forming at
    the decision time). For intraday intervals this compares bar-start + interval; for a
    daily interval it keeps only bars whose DATE is strictly before as_of's date (the
    current day's daily bar is still forming intraday). Input order is preserved.

    This is the single chokepoint the anti-lookahead test drives: feed a signal whose
    timestamp precedes a "future" bar and assert that bar is dropped here.
    """
    if not candles:
        return []
    mins = interval_minutes(interval)
    out: List = []
    if mins is None:
        # daily: keep strictly-prior dates only.
        aod = as_of.date()
        for c in candles:
            if c.ts.date() < aod:
                out.append(c)
        return out
    for c in candles:
        if close_ts(c, interval, as_of) <= as_of:
            out.append(c)
    return out


def last_close_ts(candles: Sequence, interval: str) -> Optional[str]:
    """ISO string of the latest bar-close among `candles` (for the would-be record's
    audit trail — a reviewer confirms every input pre-dates the signal). None if empty."""
    if not candles:
        return None
    latest = None
    for c in candles:
        ct = close_ts(c, interval)
        if latest is None or ct > latest:
            latest = ct
    return latest.isoformat() if latest is not None else None
