# tests/unit/test_candle_math.py — V3 Common Utilities (Step 1)
#
# Unit tests for the two NEW pure helpers in core/candle_math.py:
#   (1) true_range_series / atr  (Wilder + SMA)
#   (2) resample  (timeframe aggregation)
#
# These helpers are PURE and mode-agnostic (no I/O, no clock, no broker, no
# mode branch) — the same tests hold for paper and live by construction. The
# tests REUSE the shared sr_detector.models.Candle type (no parallel type).

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from core.candle_math import (
    ATR_SMA,
    ATR_WILDER,
    ResampleResult,
    adx,
    atr,
    ema,
    ema_series,
    resample,
    rsi,
    session_vwap,
    true_range_series,
)
from core.market_windows import MarketWindows
from sr_detector.models import Candle


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

def _c(ts, o, h, l, c, v=10) -> Candle:
    return Candle(ts=ts, open=float(o), high=float(h), low=float(l), close=float(c), volume=int(v))


def _closes(seq) -> list:
    """Candles from a close series (o=h=l=c; RSI reads closes only)."""
    base = datetime(2026, 7, 6, 9, 15)
    return [_c(base + timedelta(minutes=i), v, v, v, v) for i, v in enumerate(seq)]


# ── (1d) Wilder's RSI ────────────────────────────────────────────────────────

def test_rsi_insufficient_data_returns_none():
    assert rsi(_closes([10, 11]), period=2) is None   # len 2 < period+1
    assert rsi([], period=14) is None


def test_rsi_invalid_period_raises():
    cs = _closes([1, 2, 3, 4])
    for bad in (0, -1, 2.0, True):
        with pytest.raises(ValueError):
            rsi(cs, period=bad)


def test_rsi_all_gains_is_100():
    assert rsi(_closes([1, 2, 3, 4, 5]), period=2) == 100.0


def test_rsi_all_losses_is_0():
    assert rsi(_closes([5, 4, 3, 2, 1]), period=2) == 0.0


def test_rsi_flat_series_is_neutral_50():
    assert rsi(_closes([5, 5, 5, 5]), period=2) == 50.0


def test_rsi_known_value_wilder():
    # closes [10,11,10,11,12], period=2 -> deltas +1,-1,+1,+1
    # seed avg_gain=0.5 avg_loss=0.5; Wilder smooth -> avg_gain=0.875 avg_loss=0.125
    # RS=7 -> RSI = 100 - 100/8 = 87.5
    assert rsi(_closes([10, 11, 10, 11, 12]), period=2) == pytest.approx(87.5)


# A small fixed OHLC series with hand-computed True Ranges.
# idx: (H, L, C)   prev_close   TR
#  0 : 10, 8,  9   —            2   (degenerate: high-low; EXCLUDED from atr)
#  1 : 11, 9, 10   9            max(2, |11-9|, |9-9|)  = 2
#  2 : 12,10, 11  10            max(2, |12-10|,|10-10|)= 2
#  3 : 13, 9, 12  11            max(4, |13-11|,|9-11|) = 4
#  4 : 12,10, 11  12            max(2, |12-12|,|10-12|)= 2
_ATR_SERIES_OHLC = [
    (10, 8, 9),
    (11, 9, 10),
    (12, 10, 11),
    (13, 9, 12),
    (12, 10, 11),
]


def _atr_series():
    base = datetime(2026, 7, 6, 9, 15)  # naive IST; ts value is irrelevant to atr
    out = []
    for i, (h, l, c) in enumerate(_ATR_SERIES_OHLC):
        out.append(_c(base + timedelta(minutes=i), o=l, h=h, l=l, c=c))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# true_range_series
# ─────────────────────────────────────────────────────────────────────────────

def test_true_range_series_exact():
    trs = true_range_series(_atr_series())
    assert trs == [2.0, 2.0, 2.0, 4.0, 2.0]


def test_true_range_first_candle_is_high_minus_low():
    c0 = _c(datetime(2026, 7, 6, 9, 15), o=8, h=10, l=8, c=9)
    assert true_range_series([c0]) == [2.0]


def test_true_range_series_empty():
    assert true_range_series([]) == []


# ─────────────────────────────────────────────────────────────────────────────
# atr — Wilder + SMA (hand-computed)
# ─────────────────────────────────────────────────────────────────────────────

def test_atr_wilder_exact():
    # real TRs (excl. degenerate first) = [2, 2, 4, 2], period=3
    # seed = mean(2,2,4) = 8/3 ; then smooth over [2]:
    # atr = ((8/3)*2 + 2)/3 = 22/9 ≈ 2.4444...
    val = atr(_atr_series(), period=3, method=ATR_WILDER)
    assert val == pytest.approx(22.0 / 9.0)


def test_atr_sma_exact():
    # real TRs = [2, 2, 4, 2]; sma over last 3 = mean(2,4,2) = 8/3
    val = atr(_atr_series(), period=3, method=ATR_SMA)
    assert val == pytest.approx(8.0 / 3.0)


def test_atr_default_method_is_wilder():
    assert atr(_atr_series(), period=3) == atr(_atr_series(), period=3, method=ATR_WILDER)


def test_atr_insufficient_data_returns_none():
    # 5 candles, period=5 -> need 6 -> None (never fabricate).
    assert atr(_atr_series(), period=5) is None
    # exactly at the boundary: 3 candles, period=3 -> need 4 -> None
    assert atr(_atr_series()[:3], period=3) is None
    # one candle -> None
    assert atr(_atr_series()[:1], period=1) is None


def test_atr_boundary_period_plus_one_ok():
    # 4 candles, period=3 -> exactly period+1 -> computable (not None)
    val = atr(_atr_series()[:4], period=3, method=ATR_SMA)
    # real TRs from first 4 candles = [2, 2, 4]; sma last 3 = 8/3
    assert val == pytest.approx(8.0 / 3.0)


def test_atr_determinism():
    s = _atr_series()
    assert atr(s, 3, ATR_WILDER) == atr(s, 3, ATR_WILDER)
    assert atr(s, 3, ATR_SMA) == atr(s, 3, ATR_SMA)


def test_atr_invalid_period_raises():
    for bad in (0, -1, 2.5, True):
        with pytest.raises(ValueError):
            atr(_atr_series(), period=bad)  # type: ignore[arg-type]


def test_atr_invalid_method_raises():
    with pytest.raises(ValueError):
        atr(_atr_series(), period=3, method="ema")


# ─────────────────────────────────────────────────────────────────────────────
# session_vwap
# ─────────────────────────────────────────────────────────────────────────────

def test_session_vwap_exact():
    # two candles: typical prices 10 and 20, volumes 100 and 300
    #   c1: H=11,L=9,C=10  -> typical 10, vol 100
    #   c2: H=22,L=18,C=20 -> typical 20, vol 300
    # vwap = (10*100 + 20*300) / 400 = 7000/400 = 17.5
    c1 = _c(datetime(2026, 7, 6, 9, 15), o=10, h=11, l=9, c=10, v=100)
    c2 = _c(datetime(2026, 7, 6, 9, 20), o=20, h=22, l=18, c=20, v=300)
    assert session_vwap([c1, c2]) == pytest.approx(17.5)


def test_session_vwap_single_candle_is_its_typical():
    c = _c(datetime(2026, 7, 6, 9, 15), o=100, h=102, l=98, c=101, v=50)
    assert session_vwap([c]) == pytest.approx((102 + 98 + 101) / 3.0)


def test_session_vwap_empty_or_zero_volume_returns_none():
    assert session_vwap([]) is None
    z = _c(datetime(2026, 7, 6, 9, 15), o=100, h=102, l=98, c=101, v=0)
    assert session_vwap([z]) is None


def test_session_vwap_skips_zero_volume_candles():
    real = _c(datetime(2026, 7, 6, 9, 15), o=10, h=11, l=9, c=10, v=100)   # typical 10
    ghost = _c(datetime(2026, 7, 6, 9, 16), o=99, h=99, l=99, c=99, v=0)   # ignored
    assert session_vwap([real, ghost]) == pytest.approx(10.0)


def test_session_vwap_determinism():
    cs = _one_min_candles(date(2026, 7, 6), 0, 10)
    assert session_vwap(cs) == session_vwap(cs)


# ─────────────────────────────────────────────────────────────────────────────
# ema / ema_series
# ─────────────────────────────────────────────────────────────────────────────

def test_ema_series_seed_is_sma_then_smooths():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    s = ema_series(vals, 3)
    # seed = mean(1,2,3) = 2.0 ; k = 2/4 = 0.5
    # next = 4*0.5 + 2.0*0.5 = 3.0 ; next = 5*0.5 + 3.0*0.5 = 4.0
    assert s == pytest.approx([2.0, 3.0, 4.0])
    assert ema(vals, 3) == pytest.approx(4.0)


def test_ema_insufficient_returns_empty_or_none():
    assert ema_series([1.0, 2.0], 3) == []
    assert ema([1.0, 2.0], 3) is None


def test_ema_constant_series_is_constant():
    assert ema([7.0] * 10, 4) == pytest.approx(7.0)


def test_ema_invalid_period_raises():
    for bad in (0, -1, 2.5, True):
        with pytest.raises(ValueError):
            ema_series([1.0, 2.0, 3.0], bad)  # type: ignore[arg-type]


def test_ema_determinism():
    vals = [float(i) for i in range(20)]
    assert ema(vals, 5) == ema(vals, 5)


# ─────────────────────────────────────────────────────────────────────────────
# adx
# ─────────────────────────────────────────────────────────────────────────────

def _ramp_candles(n, step=2.0):
    """Clean monotonic uptrend → +DM only → ADX ≈ 100."""
    base = datetime(2026, 7, 6, 9, 15)
    out = []
    for i in range(n):
        lo = 100.0 + step * i
        out.append(_c(base + timedelta(days=i), o=lo, h=lo + 1.0, l=lo, c=lo + 0.5))
    return out


def _choppy_candles(n):
    """Alternating up/down bars → +DM and -DM balance → low ADX."""
    base = datetime(2026, 7, 6, 9, 15)
    out = []
    for i in range(n):
        lo = 100.0 + (1.0 if i % 2 else 0.0)
        out.append(_c(base + timedelta(days=i), o=lo, h=lo + 1.0, l=lo - 1.0, c=lo))
    return out


def test_adx_strong_trend_is_high():
    val = adx(_ramp_candles(40), period=14)
    assert val is not None and val > 25.0    # a clean ramp trends hard


def test_adx_trend_exceeds_chop():
    trend = adx(_ramp_candles(40), period=14)
    chop = adx(_choppy_candles(40), period=14)
    assert trend > chop
    assert chop < 25.0                       # choppy = non-trending


def test_adx_insufficient_returns_none():
    assert adx(_ramp_candles(10), period=14) is None    # need >= 2*period+1


def test_adx_determinism_and_invalid_period():
    cs = _ramp_candles(40)
    assert adx(cs, 14) == adx(cs, 14)
    for bad in (0, -3, 1.5, True):
        with pytest.raises(ValueError):
            adx(cs, bad)  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# resample — intraday aggregation
# ─────────────────────────────────────────────────────────────────────────────

_SESSION_TINY = (time(9, 15), time(9, 30))   # 15-minute test session
_SESSION_NSE = (time(9, 15), time(15, 30))   # real NSE session (375 min)


def _one_min_candles(d: date, start_min: int, count: int, tz=None):
    """`count` sequential 1-minute candles from 09:15+start_min. Deterministic,
    distinctive OHLCV so aggregation is checkable."""
    out = []
    for i in range(count):
        idx = start_min + i
        ts = datetime.combine(d, time(9, 15 + idx), tzinfo=tz)
        # o=100+idx, distinctive high/low, close=100+idx+1, volume=10+idx
        out.append(_c(ts, o=100 + idx, h=100 + idx + 5, l=100 + idx - 3, c=100 + idx + 1, v=10 + idx))
    return out


def test_resample_1m_to_5m_ohlcv_and_alignment():
    d = date(2026, 7, 6)
    candles = _one_min_candles(d, 0, 15)  # 09:15..09:29
    res = resample(candles, 5, _SESSION_TINY)

    assert isinstance(res, ResampleResult)
    # 15 min / 5 = 3 full buckets, none partial, none empty
    assert len(res.bars) == 3
    assert res.partial_bucket_starts == ()
    assert res.empty_bucket_starts == ()
    assert res.partial_last is False

    b0 = res.bars[0]
    # session-start alignment: first bucket anchored to 09:15 (not 09:00/09:20)
    assert b0.ts == datetime.combine(d, time(9, 15))
    # bucket 0 aggregates idx 0..4
    assert b0.open == 100.0                      # first.open = 100 + 0
    assert b0.close == 105.0                     # last.close = 100 + 4 + 1
    assert b0.high == max(100 + i + 5 for i in range(5))   # 109
    assert b0.low == min(100 + i - 3 for i in range(5))    # 97
    assert b0.volume == sum(10 + i for i in range(5))      # 60

    # buckets are 09:15, 09:20, 09:25
    assert [b.ts.time() for b in res.bars] == [time(9, 15), time(9, 20), time(9, 25)]


def test_resample_5m_to_30m_single_bucket():
    d = date(2026, 7, 6)
    # six 5-minute candles spanning 09:15..09:45 collapse into one 30m bar
    base = []
    for i in range(6):
        ts = datetime.combine(d, time(9, 15)) + timedelta(minutes=5 * i)
        base.append(_c(ts, o=200 + i, h=210 + i, l=190 - i, c=205 + i, v=100))
    res = resample(base, 30, (time(9, 15), time(9, 45)))
    assert len(res.bars) == 1
    b = res.bars[0]
    assert b.ts == datetime.combine(d, time(9, 15))
    assert b.open == 200.0
    assert b.close == 210.0            # last close = 205 + 5
    assert b.high == 215.0             # max(210+i) = 210+5
    assert b.low == 185.0              # min(190-i) = 190-5
    assert b.volume == 600


def test_resample_partial_final_bucket_flagged():
    d = date(2026, 7, 6)
    # 15-min session on a 10-min grid -> slot 09:15 (full) + slot 09:25 (partial tail)
    candles = _one_min_candles(d, 0, 15)  # 09:15..09:29
    res = resample(candles, 10, _SESSION_TINY)

    assert len(res.bars) == 2
    starts = [b.ts.time() for b in res.bars]
    assert starts == [time(9, 15), time(9, 25)]
    # the 09:25 bucket window (09:25-09:35) overruns the 09:30 close -> partial
    assert datetime.combine(d, time(9, 25)) in res.partial_bucket_starts
    assert res.partial_last is True
    # partial bar is emitted from the data present (idx 10..14 = 5 candles), not padded
    partial_bar = res.bars[1]
    assert partial_bar.volume == sum(10 + i for i in range(10, 15))


def test_resample_nse_375min_30m_grid_tail():
    # Structural NSE case from the spec: 375-min day, 30-min grid -> the final
    # 15-min tail bucket (start 15:15) is partial. Verified via the slot flag
    # without needing 375 candles: one candle in the tail slot suffices.
    d = date(2026, 7, 6)
    tail_candle = _c(datetime.combine(d, time(15, 25)), o=500, h=501, l=499, c=500, v=5)
    res = resample([tail_candle], 30, _SESSION_NSE)
    assert len(res.bars) == 1
    assert res.bars[0].ts == datetime.combine(d, time(15, 15))
    assert datetime.combine(d, time(15, 15)) in res.partial_bucket_starts
    assert res.partial_last is True


def test_resample_empty_bucket_flagged_not_fabricated():
    d = date(2026, 7, 6)
    # 3 slots (09:15/09:20/09:25); provide data only for 09:15-19 and 09:25-29,
    # leaving the 09:20 slot empty.
    candles = _one_min_candles(d, 0, 5) + _one_min_candles(d, 10, 5)
    res = resample(candles, 5, _SESSION_TINY)
    assert [b.ts.time() for b in res.bars] == [time(9, 15), time(9, 25)]
    # the 09:20 slot had no data -> reported, NOT emitted as a bar
    assert datetime.combine(d, time(9, 20)) in res.empty_bucket_starts
    assert all(b.ts.time() != time(9, 20) for b in res.bars)


def test_resample_no_cross_day_merge():
    d1, d2 = date(2026, 7, 6), date(2026, 7, 7)
    base = _one_min_candles(d1, 0, 5) + _one_min_candles(d2, 0, 5)
    # tf=30 > 5-min of data each day -> one bucket per day, never merged
    res = resample(base, 30, _SESSION_NSE)
    assert len(res.bars) == 2
    assert res.bars[0].ts.date() == d1
    assert res.bars[1].ts.date() == d2
    assert res.bars[0].ts.date() != res.bars[1].ts.date()


def test_resample_excludes_preopen_and_afterhours():
    d = date(2026, 7, 6)
    pre = _c(datetime.combine(d, time(9, 0)), o=1, h=2, l=0.5, c=1.5, v=99)   # pre-open
    inn = _one_min_candles(d, 0, 5)                                            # 09:15..09:19
    post = _c(datetime.combine(d, time(15, 45)), o=9, h=9, l=9, c=9, v=99)     # after close
    res = resample([pre] + inn + [post], 5, _SESSION_NSE)
    assert len(res.bars) == 1
    assert res.bars[0].ts == datetime.combine(d, time(9, 15))
    assert res.bars[0].volume == sum(10 + i for i in range(5))  # only in-session data


# ─────────────────────────────────────────────────────────────────────────────
# resample — daily
# ─────────────────────────────────────────────────────────────────────────────

def test_resample_daily_one_bar_per_session():
    d = date(2026, 7, 6)
    inn = _one_min_candles(d, 0, 10)  # 09:15..09:24
    post = _c(datetime.combine(d, time(16, 0)), o=999, h=999, l=999, c=999, v=1)  # excluded
    res = resample(inn + [post], "day", _SESSION_NSE)
    assert len(res.bars) == 1
    b = res.bars[0]
    assert b.ts == datetime.combine(d, time(9, 15))          # ts = session open
    assert b.open == 100.0                                   # first in-session open
    assert b.close == 100 + 9 + 1                            # last in-session close
    assert b.high == max(100 + i + 5 for i in range(10))
    assert b.low == min(100 + i - 3 for i in range(10))
    assert b.volume == sum(10 + i for i in range(10))
    # daily resample flags nothing structural
    assert res.partial_bucket_starts == ()
    assert res.empty_bucket_starts == ()
    assert res.partial_last is False


def test_resample_daily_alias():
    d = date(2026, 7, 6)
    inn = _one_min_candles(d, 0, 5)
    assert resample(inn, "daily", _SESSION_NSE).bars[0].ts == datetime.combine(d, time(9, 15))


# ─────────────────────────────────────────────────────────────────────────────
# resample — session_bounds callable + MarketWindows integration + purity
# ─────────────────────────────────────────────────────────────────────────────

def test_resample_session_bounds_callable_from_market_windows():
    """The intended integration: derive session bounds from MarketWindows
    (REUSE, not duplicate). Uses only public MarketWindows attributes."""
    d = date(2026, 7, 6)
    mw = MarketWindows()  # defaults: open 09:15, close 15:30
    bounds_fn = lambda _d: (mw.market_open, mw.market_close)  # noqa: E731
    candles = _one_min_candles(d, 0, 10)
    res = resample(candles, 5, bounds_fn)
    assert res.bars[0].ts == datetime.combine(d, time(9, 15))
    assert len(res.bars) == 2  # 09:15 and 09:20


def test_resample_timezone_aware_preserved():
    from core.time_authority import ist_timezone
    tz = ist_timezone()
    d = date(2026, 7, 6)
    candles = _one_min_candles(d, 0, 5, tz=tz)
    res = resample(candles, 5, _SESSION_TINY)
    assert res.bars[0].ts.tzinfo == tz


def test_resample_empty_input():
    res = resample([], 5, _SESSION_TINY)
    assert res.bars == []
    assert res.partial_bucket_starts == ()
    assert res.empty_bucket_starts == ()
    assert res.partial_last is False


def test_resample_determinism():
    d = date(2026, 7, 6)
    candles = _one_min_candles(d, 0, 15)
    r1 = resample(candles, 5, _SESSION_TINY)
    r2 = resample(candles, 5, _SESSION_TINY)
    assert r1 == r2


def test_resample_invalid_target_tf_raises():
    d = date(2026, 7, 6)
    candles = _one_min_candles(d, 0, 5)
    for bad in (0, -5, 2.5, True, "week"):
        with pytest.raises(ValueError):
            resample(candles, bad, _SESSION_TINY)  # type: ignore[arg-type]


def test_resample_returns_shared_candle_type():
    d = date(2026, 7, 6)
    candles = _one_min_candles(d, 0, 5)
    res = resample(candles, 5, _SESSION_TINY)
    # bars are the SAME Candle type the rest of the system uses (no parallel type)
    assert all(isinstance(b, Candle) for b in res.bars)
