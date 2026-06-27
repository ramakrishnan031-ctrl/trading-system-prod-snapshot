"""
sr_detector/pivots.py — Trading System v2 · S&R Detector V1

Purpose:
    Swing-pivot detection on a single timeframe. A swing high/low is a bar whose
    high/low is the extreme over N bars on EACH side (N configurable per TF).
    Pure functions; stdlib only.

Locked Design Decisions:
    SR-P1 — A pivot HIGH at index i requires high[i] >= every high in the window
            [i-left, i+right] AND strictly greater than at least one neighbour in
            the window (so a perfectly flat plateau does not mark every bar).
            SUPPORT pivots are the low-side mirror.
    SR-P2 — The first `left` and last `right` bars can never be pivots (no full
            window) — they are skipped.
"""
from __future__ import annotations

from typing import List

from sr_detector.models import Candle, Pivot


def find_swing_pivots(candles: List[Candle], left: int, right: int) -> List[Pivot]:
    """
    Return swing HIGH and LOW pivots for the given candle series.

    Args:
        candles: chronological OHLCV bars.
        left:    bars required to the left of a pivot.
        right:   bars required to the right of a pivot.

    Returns:
        Pivots in bar order (a bar may yield both a HIGH and a LOW pivot only in
        degenerate single-bar windows; normally one or neither).
    """
    n = len(candles)
    pivots: List[Pivot] = []
    if left < 1 or right < 1 or n < (left + right + 1):
        return pivots

    for i in range(left, n - right):
        c = candles[i]
        window = candles[i - left : i + right + 1]
        others = [w for j, w in enumerate(window) if (i - left + j) != i]

        # Swing high (→ resistance): the extreme high over the window.
        if all(c.high >= w.high for w in window) and any(c.high > w.high for w in others):
            pivots.append(Pivot(price=c.high, ts=c.ts, kind="HIGH", bar_index=i))

        # Swing low (→ support): the extreme low over the window.
        if all(c.low <= w.low for w in window) and any(c.low < w.low for w in others):
            pivots.append(Pivot(price=c.low, ts=c.ts, kind="LOW", bar_index=i))

    return pivots
