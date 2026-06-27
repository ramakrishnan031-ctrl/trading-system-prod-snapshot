"""
sr_detector/retest_confirm.py — Trading System v2 · S&R V2 Phase A (SNR-V2)

Purpose:
    The WAIT_FOR_RETEST confirmation state machine — PURE and unit-testable. No
    candle patterns / indicator names; only "closed above + touched + strong
    close". Given the matched resistance band + the 1m candles since divert +
    elapsed time, it returns the resulting state.

Design:
    Deterministic FOLD from WAIT_BREAKOUT over the candles (chronological). This
    is idempotent and restart-safe: the state is a pure function of
    (zone, candles-since-divert, elapsed). The monitor persists the result as a
    snapshot but recomputes it every poll, so re-seeing candles never mis-advances.

State machine (spec STEP 4):
    WAIT_BREAKOUT → WAIT_RETEST  : a 1m candle CLOSES above band_high (breakout).
    WAIT_RETEST   → WAIT_CONFIRM : a 1m candle RE-ENTERS the band (the retest touch).
    WAIT_CONFIRM  → CONFIRMED    : a 1m candle CLOSES above band_high AND closes in
                                   the upper `reclaim_strong_close_frac` of its range.
    any state     → REJECT       : elapsed > timeout_sec, OR a candle CLOSES more
                                   than `max_away_pct` below band_low (break-down).
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


@dataclass(frozen=True)
class RetestParams:
    timeout_sec: float
    max_away_pct: float                 # break-down: close < band_low·(1 − this%)
    reclaim_strong_close_frac: float    # strong close: (close−low)/(high−low) ≥ this
    breakout_margin_pct: float = 0.0    # close must exceed band_high by this % (small)


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
) -> RetestResult:
    if elapsed_sec > params.timeout_sec:
        return RetestResult(REJECT, "timeout")

    breakout_level = band_high * (1.0 + params.breakout_margin_pct / 100.0)
    breakdown_level = band_low * (1.0 - params.max_away_pct / 100.0)

    state = WAIT_BREAKOUT
    for c in candles:
        if c.close < breakdown_level:
            return RetestResult(REJECT, "break_down")

        if state == WAIT_BREAKOUT:
            if c.close > breakout_level:
                state = WAIT_RETEST
        elif state == WAIT_RETEST:
            # retest touch: the candle's range re-enters the band.
            if c.low <= band_high and c.high >= band_low:
                state = WAIT_CONFIRM
        elif state == WAIT_CONFIRM:
            if c.close > breakout_level and _strong_close(c, params.reclaim_strong_close_frac):
                return RetestResult(CONFIRMED, "reclaim_strong_close")

    return RetestResult(state, "in_progress")


def _strong_close(c, frac: float) -> bool:
    rng = c.high - c.low
    if rng <= 0:
        return c.close >= c.high          # degenerate (flat) bar
    return (c.close - c.low) / rng >= frac
