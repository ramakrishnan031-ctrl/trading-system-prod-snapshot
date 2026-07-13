# tests/unit/test_daily_stats.py — M-S4 no-lookahead daily-stats core (core/daily_stats.py)
#
# The load-bearing property is NO-LOOKAHEAD: only candles that CLOSED BEFORE the target
# trading_date may influence the stats. A leak here would make the M-S4 threshold re-fit a
# flattering lie (a candle from the future informing "today's" score). Pure; paper==live.
from __future__ import annotations

from datetime import datetime, timedelta

from core.daily_stats import compute_daily_stats
from sr_detector.models import Candle


def _daily(n, start="2026-06-01", base=100.0):
    """n daily candles: close = base+i, high/low = close±1, volume = 1000+i."""
    d0 = datetime.strptime(start, "%Y-%m-%d")
    out = []
    for i in range(n):
        ts = (d0 + timedelta(days=i)).strftime("%Y-%m-%d") + " 00:00:00"
        c = base + i
        out.append(Candle(ts=ts, open=c, high=c + 1, low=c - 1, close=c, volume=1000 + i))
    return out


def test_no_lookahead_excludes_trading_date_and_after():
    candles = _daily(30, "2026-06-01")  # 06-01 .. 06-30
    stats = compute_daily_stats(candles, "2026-06-25")  # prior = 06-01..06-24
    assert stats["prev_close"] == 123.0  # 06-24 close = base+23
    # a WILD outlier stamped ON trading_date (or any later date) must NOT change anything
    outlier = Candle(ts="2026-06-25 00:00:00", open=9999, high=9999, low=9999, close=9999, volume=9_999_999)
    later = Candle(ts="2026-06-26 00:00:00", open=1, high=1, low=1, close=1, volume=1)
    assert compute_daily_stats(candles + [outlier, later], "2026-06-25") == stats


def test_avg_volume_20d_uses_last_20_prior():
    candles = _daily(30, "2026-06-01")
    stats = compute_daily_stats(candles, "2026-06-25")
    # last 20 prior (i=4..23) -> vols 1004..1023 -> mean 1013.5
    assert stats["avg_volume_20d"] == 1013.5


def test_atr_rsi_computed_when_enough_history():
    candles = _daily(30, "2026-06-01")
    stats = compute_daily_stats(candles, "2026-06-25")  # 24 prior >= period+1
    assert stats["atr14"] is not None and abs(stats["atr14"] - 2.0) < 1e-9  # TR is constant 2
    assert stats["rsi14"] == 100.0  # monotonically rising closes -> all gains -> RSI 100


def test_insufficient_history_returns_none_fields():
    candles = _daily(5, "2026-06-01")  # only 5 prior sessions
    stats = compute_daily_stats(candles, "2026-06-25")
    assert stats["prev_close"] == 104.0
    assert stats["avg_volume_20d"] is None  # < 20 sessions
    assert stats["atr14"] is None           # < period+1
    assert stats["rsi14"] is None


def test_no_prior_candle_all_none():
    candles = _daily(5, "2026-07-01")  # every candle is AFTER trading_date
    assert compute_daily_stats(candles, "2026-06-25") == {
        "prev_close": None, "avg_volume_20d": None, "atr14": None, "rsi14": None,
    }
