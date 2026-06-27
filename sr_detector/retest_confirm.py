"""
sr_detector/retest_confirm.py — Trading System v2 · S&R V2 Phase A (SNR-V2)

Purpose:
    The WAIT_FOR_RETEST confirmation state machine — PURE and unit-testable. No
    candle patterns / indicator names; only "broke + touched + strong close".
    Given the matched zone band + the 1m candles since divert + elapsed time, it
    returns the resulting state. SYMMETRIC: the same fold drives both sides; only
    the directional predicates flip (LONG = above resistance, SHORT = below
    support).

Design:
    Deterministic FOLD from WAIT_BREAKOUT over the candles (chronological). This
    is idempotent and restart-safe: the state is a pure function of
    (zone, direction, candles-since-divert, elapsed). The monitor persists the
    result as a snapshot but recomputes it every poll, so re-seeing candles never
    mis-advances. The persisted state names are shared by both sides (WAIT_BREAKOUT
    means "waiting for the directional break") → no schema/glob change.

State machine (the break is direction-relative):
    LONG  : break = 1m CLOSE above band_high ; confirm close strong in UPPER frac.
    SHORT : break = 1m CLOSE below band_low  ; confirm close strong in LOWER frac.

    WAIT_BREAKOUT → WAIT_RETEST  : a 1m candle BREAKS in the trade direction.
    WAIT_RETEST   → WAIT_CONFIRM : a 1m candle RE-ENTERS the band (the retest touch).
    WAIT_CONFIRM  → CONFIRMED    : a 1m candle BREAKS again AND closes in the
                                   strong `confirm_strong_close_frac` of its range.
    any state     → REJECT       : elapsed > timeout_sec, OR the candle CLOSES more
                                   than `max_away_pct` the WRONG way past the band
                                   (LONG: below band_low → "break_down";
                                    SHORT: above band_high → "reclaim_up").
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

WAIT_BREAKOUT = "WAIT_BREAKOUT"
WAIT_RETEST = "WAIT_RETEST"
WAIT_CONFIRM = "WAIT_CONFIRM"
CONFIRMED = "CONFIRMED"
REJECT = "REJECT"

# States persisted in retest_state.state (the in-progress ones only).
IN_PROGRESS_STATES = (WAIT_BREAKOUT, WAIT_RETEST, WAIT_CONFIRM)

_LONG = ("LONG", "BUY")


def _is_long(direction: str) -> bool:
    return str(direction).strip().upper() in _LONG


@dataclass(frozen=True)
class RetestParams:
    timeout_sec: float
    max_away_pct: float                 # wrong-way reject: close past the band by this %
    confirm_strong_close_frac: float    # strong close: directional close position ≥ this
    breakout_margin_pct: float = 0.0    # close must clear the band by this % (small)


@dataclass(frozen=True)
class RetestResult:
    state: str
    reason: str = ""

    @property
    def confirmed(self) -> bool:
        return self.state == CONFIRMED

    @property
    def rejected(self) -> bool:
        return self.state == REJECT

    @property
    def terminal(self) -> bool:
        return self.state in (CONFIRMED, REJECT)


def evaluate(
    *,
    band_low: float,
    band_high: float,
    candles: List,            # 1m Candle-likes (.open/.high/.low/.close), chronological
    elapsed_sec: float,
    params: RetestParams,
    direction: str = "LONG",  # "LONG"/"BUY" or "SHORT"/"SELL"
) -> RetestResult:
    if elapsed_sec > params.timeout_sec:
        return RetestResult(REJECT, "timeout")

    is_long = _is_long(direction)
    margin = params.breakout_margin_pct / 100.0
    away = params.max_away_pct / 100.0

    if is_long:
        break_level = band_high * (1.0 + margin)     # close must clear ABOVE band_high
        reject_level = band_low * (1.0 - away)       # close below → failed (break_down)
        broke = lambda c: c.close > break_level      # noqa: E731
        failed = lambda c: c.close < reject_level    # noqa: E731
        fail_reason = "break_down"
        confirm_reason = "reclaim_strong_close"
    else:
        break_level = band_low * (1.0 - margin)      # close must clear BELOW band_low
        reject_level = band_high * (1.0 + away)      # close above → failed (reclaim_up)
        broke = lambda c: c.close < break_level      # noqa: E731
        failed = lambda c: c.close > reject_level    # noqa: E731
        fail_reason = "reclaim_up"
        confirm_reason = "rejection_strong_close"

    state = WAIT_BREAKOUT
    for c in candles:
        if failed(c):
            return RetestResult(REJECT, fail_reason)

        if state == WAIT_BREAKOUT:
            if broke(c):
                state = WAIT_RETEST
        elif state == WAIT_RETEST:
            # retest touch: the candle's range re-enters the band (side-agnostic).
            if c.low <= band_high and c.high >= band_low:
                state = WAIT_CONFIRM
        elif state == WAIT_CONFIRM:
            if broke(c) and _strong_close(c, params.confirm_strong_close_frac, is_long):
                return RetestResult(CONFIRMED, confirm_reason)

    return RetestResult(state, "in_progress")


def _strong_close(c, frac: float, is_long: bool = True) -> bool:
    """Directional strong close: LONG wants the close in the UPPER frac of the 1m
    range, SHORT in the LOWER frac."""
    rng = c.high - c.low
    if rng <= 0:                                       # degenerate (flat) bar
        return c.close >= c.high if is_long else c.close <= c.low
    pos = (c.close - c.low) / rng if is_long else (c.high - c.close) / rng
    return pos >= frac
