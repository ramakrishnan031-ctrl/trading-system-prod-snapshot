"""
tests/unit/test_sr_v2_retest_confirm.py — SNR-V2 Phase A retest state machine.

SYMMETRIC by direction. Every LONG branch (unchanged): breakout, retest touch,
reclaim+strong-close → CONFIRMED; weak close → no confirm; timeout → REJECT;
break-down → REJECT. Every SHORT mirror: breakdown, retest touch,
rejection+strong-(lower)-close → CONFIRMED; weak close → no confirm; timeout →
REJECT; reclaim-back-up (rise > max_away above band_high) → REJECT.
"""
from __future__ import annotations

from types import SimpleNamespace

from sr_detector.retest_confirm import (
    CONFIRMED, REJECT, WAIT_BREAKOUT, WAIT_CONFIRM, WAIT_RETEST,
    RetestParams, evaluate,
)

P = RetestParams(timeout_sec=1800.0, max_away_pct=1.0, confirm_strong_close_frac=0.6)
LO, HI = 100.0, 101.0


def _c(high, low, close):
    return SimpleNamespace(open=close, high=high, low=low, close=close, volume=1000)


def _ev(candles, elapsed=10.0, params=P):
    return evaluate(band_low=LO, band_high=HI, candles=candles, elapsed_sec=elapsed, params=params)


def _evs(candles, elapsed=10.0, params=P):
    """SHORT-side evaluate (mirror of _ev)."""
    return evaluate(band_low=LO, band_high=HI, candles=candles, elapsed_sec=elapsed,
                    params=params, direction="SHORT")


# ── LONG (unchanged — proves the generalization didn't move LONG) ─────────────

def test_empty_candles_stays_wait_breakout():
    assert _ev([]).state == WAIT_BREAKOUT


def test_breakout_advances_to_wait_retest():
    assert _ev([_c(102.2, 101.0, 102.0)]).state == WAIT_RETEST


def test_breakout_then_touch_advances_to_wait_confirm():
    candles = [_c(102.2, 101.0, 102.0),      # breakout (close > 101)
               _c(102.0, 100.5, 100.8)]      # re-enters band [100,101]
    assert _ev(candles).state == WAIT_CONFIRM


def test_full_path_reclaim_strong_close_confirms():
    candles = [
        _c(102.2, 101.0, 102.0),   # breakout
        _c(102.0, 100.5, 100.8),   # retest touch
        _c(103.2, 101.0, 103.0),   # reclaim: close>101 AND strong (2.0/2.2 ≈ 0.91)
    ]
    r = _ev(candles)
    assert r.state == CONFIRMED and r.confirmed and r.terminal
    assert r.reason == "reclaim_strong_close"


def test_weak_reclaim_does_not_confirm():
    candles = [
        _c(102.2, 101.0, 102.0),   # breakout
        _c(102.0, 100.5, 100.8),   # retest touch
        _c(106.0, 101.0, 102.0),   # close>101 but weak (1.0/5.0 = 0.2 < 0.6)
    ]
    r = _ev(candles)
    assert r.state == WAIT_CONFIRM and not r.confirmed


def test_timeout_rejects_regardless_of_candles():
    r = _ev([_c(103.2, 101.0, 103.0)], elapsed=2000.0)   # > timeout 1800
    assert r.state == REJECT and r.reason == "timeout"


def test_break_down_rejects():
    # close < band_low·(1 − 1%) = 99.0
    r = _ev([_c(99.5, 97.0, 98.0)])
    assert r.state == REJECT and r.reason == "break_down"


def test_break_down_after_breakout_still_rejects():
    candles = [_c(102.2, 101.0, 102.0),   # breakout
               _c(101.0, 97.0, 98.0)]     # then collapses below band − 1%
    assert _ev(candles).state == REJECT


def test_breakout_margin_requires_clearing_above():
    p = RetestParams(timeout_sec=1800.0, max_away_pct=1.0,
                     confirm_strong_close_frac=0.6, breakout_margin_pct=0.5)
    # close 101.2 does NOT exceed 101·1.005 = 101.505 → no breakout
    assert _ev([_c(101.3, 100.0, 101.2)], params=p).state == WAIT_BREAKOUT
    # close 101.8 DOES exceed → breakout
    assert _ev([_c(101.9, 100.0, 101.8)], params=p).state == WAIT_RETEST


def test_idempotent_replay_same_result():
    candles = [_c(102.2, 101.0, 102.0), _c(102.0, 100.5, 100.8), _c(103.2, 101.0, 103.0)]
    assert _ev(candles).state == _ev(candles).state == CONFIRMED


# ── SHORT (mirror) ────────────────────────────────────────────────────────────

def test_short_empty_candles_stays_wait_breakout():
    assert _evs([]).state == WAIT_BREAKOUT


def test_short_breakdown_advances_to_wait_retest():
    # close 98 < band_low 100 → breakdown
    assert _evs([_c(99.5, 97.8, 98.0)]).state == WAIT_RETEST


def test_short_breakdown_then_touch_advances_to_wait_confirm():
    candles = [_c(99.5, 97.8, 98.0),        # breakdown (close < 100)
               _c(100.8, 99.5, 100.5)]      # re-enters band [100,101]
    assert _evs(candles).state == WAIT_CONFIRM


def test_short_full_path_rejection_strong_close_confirms():
    candles = [
        _c(99.5, 97.8, 98.0),       # breakdown
        _c(100.8, 99.5, 100.5),     # retest touch
        _c(100.0, 97.0, 97.3),      # rejection: close<100 AND strong LOWER (2.7/3.0 = 0.9)
    ]
    r = _evs(candles)
    assert r.state == CONFIRMED and r.confirmed and r.terminal
    assert r.reason == "rejection_strong_close"


def test_short_weak_rejection_does_not_confirm():
    candles = [
        _c(99.5, 97.8, 98.0),       # breakdown
        _c(100.8, 99.5, 100.5),     # retest touch
        _c(100.0, 94.0, 99.0),      # close<100 but weak LOWER (1.0/6.0 = 0.167 < 0.6)
    ]
    r = _evs(candles)
    assert r.state == WAIT_CONFIRM and not r.confirmed


def test_short_timeout_rejects_regardless_of_candles():
    r = _evs([_c(100.0, 97.0, 97.3)], elapsed=2000.0)    # > timeout 1800
    assert r.state == REJECT and r.reason == "timeout"


def test_short_reclaim_up_rejects():
    # close > band_high·(1 + 1%) = 102.01 → failed breakdown / reclaim back up
    r = _evs([_c(103.0, 101.5, 102.5)])
    assert r.state == REJECT and r.reason == "reclaim_up"


def test_short_reclaim_up_after_breakdown_still_rejects():
    candles = [_c(99.5, 97.8, 98.0),        # breakdown
               _c(103.0, 101.0, 102.5)]     # then rips back above band + 1%
    assert _evs(candles).state == REJECT


def test_short_breakdown_margin_requires_clearing_below():
    p = RetestParams(timeout_sec=1800.0, max_away_pct=1.0,
                     confirm_strong_close_frac=0.6, breakout_margin_pct=0.5)
    # close 99.8 does NOT clear below 100·0.995 = 99.5 → no breakdown
    assert _evs([_c(100.5, 99.0, 99.8)], params=p).state == WAIT_BREAKOUT
    # close 99.2 DOES clear below → breakdown
    assert _evs([_c(100.5, 99.0, 99.2)], params=p).state == WAIT_RETEST


def test_short_idempotent_replay_same_result():
    candles = [_c(99.5, 97.8, 98.0), _c(100.8, 99.5, 100.5), _c(100.0, 97.0, 97.3)]
    assert _evs(candles).state == _evs(candles).state == CONFIRMED
