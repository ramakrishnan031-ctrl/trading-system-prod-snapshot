"""
tests/unit/test_sr_v2_retest_confirm.py — SNR-V2 Phase A retest state machine.

Every branch (spec): breakout, retest touch, reclaim+strong-close → CONFIRMED;
weak close → no confirm; timeout → REJECT; break-down → REJECT.
"""
from __future__ import annotations

from types import SimpleNamespace

from sr_detector.retest_confirm import (
    CONFIRMED, REJECT, WAIT_BREAKOUT, WAIT_CONFIRM, WAIT_RETEST,
    RetestParams, evaluate,
)

P = RetestParams(timeout_sec=1800.0, max_away_pct=1.0, reclaim_strong_close_frac=0.6)
LO, HI = 100.0, 101.0


def _c(high, low, close):
    return SimpleNamespace(open=close, high=high, low=low, close=close, volume=1000)


def _ev(candles, elapsed=10.0, params=P):
    return evaluate(band_low=LO, band_high=HI, candles=candles, elapsed_sec=elapsed, params=params)


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
                     reclaim_strong_close_frac=0.6, breakout_margin_pct=0.5)
    # close 101.2 does NOT exceed 101·1.005 = 101.505 → no breakout
    assert _ev([_c(101.3, 100.0, 101.2)], params=p).state == WAIT_BREAKOUT
    # close 101.8 DOES exceed → breakout
    assert _ev([_c(101.9, 100.0, 101.8)], params=p).state == WAIT_RETEST


def test_idempotent_replay_same_result():
    candles = [_c(102.2, 101.0, 102.0), _c(102.0, 100.5, 100.8), _c(103.2, 101.0, 103.0)]
    assert _ev(candles).state == _ev(candles).state == CONFIRMED
