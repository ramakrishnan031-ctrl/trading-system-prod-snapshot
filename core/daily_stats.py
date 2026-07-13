# =============================================================================
# Script        : core/daily_stats.py
# Purpose       : M-S4 — compute the previously-DEAD scorer inputs (avg_volume_20d,
#                 atr14, rsi14, prev_close) from DAILY candles, with a hard
#                 NO-LOOKAHEAD guarantee: ONLY candles that CLOSED BEFORE the
#                 target trading_date are used. One pure implementation, reused by:
#                   - the pre-market cache job (scripts/build_daily_symbol_stats.py)
#                   - the historical backfill/recompute (the M-S4 impact artifact)
#                   - (via the daily_symbol_stats cache) the screener wiring.
# How it works  : Pure function over the SHARED sr_detector.models.Candle. Composes
#                 the existing core.candle_math.atr / rsi (no parallel indicators).
#                 No I/O, no clock, no broker, no config, no mode branch —
#                 deterministic, identical in paper and live by construction.
# Wiring        : NONE consumes this on the live path yet (M-S4 is staged behind a
#                 default-OFF flag until GATE 1). Substrate only.
# =============================================================================
from __future__ import annotations

from typing import Optional, Sequence, TYPE_CHECKING

from core.candle_math import atr, rsi

if TYPE_CHECKING:
    from sr_detector.models import Candle


def _candle_date(c) -> str:
    """YYYY-MM-DD of a candle's timestamp, whether `ts` is a datetime or a string.
    Empty string if absent (such a candle sorts before any real date → excluded)."""
    ts = getattr(c, "ts", None)
    return str(ts)[:10] if ts is not None else ""


def compute_daily_stats(
    candles: Sequence["Candle"],
    trading_date: str,
    *,
    atr_period: int = 14,
    rsi_period: int = 14,
    vol_window: int = 20,
) -> dict:
    """Return the four M-S4 scorer inputs for ``trading_date``:

        {"prev_close", "avg_volume_20d", "atr14", "rsi14"}

    computed ONLY from daily candles that CLOSED STRICTLY BEFORE ``trading_date``
    (NO-LOOKAHEAD — the same discipline as the V3 chain). Any stat is ``None`` when
    the available history is insufficient (never fabricated):
      - prev_close     : the most recent prior daily close (None if no prior candle).
      - avg_volume_20d : SMA of daily volume over the last ``vol_window`` prior
                         sessions (None if fewer than ``vol_window`` exist).
      - atr14 / rsi14  : Wilder ATR/RSI over the prior candles (None if fewer than
                         ``period + 1``).

    Pure and deterministic: same candles + trading_date -> same output. ``candles``
    are DAILY Candles in chronological order (any interleaved future/partial candle
    is filtered out by the date guard, so a stray same-day bar cannot leak in)."""
    prior = [c for c in candles if _candle_date(c) < trading_date]
    if not prior:
        return {"prev_close": None, "avg_volume_20d": None, "atr14": None, "rsi14": None}

    prev_close = float(prior[-1].close)

    avg_volume_20d: Optional[float] = None
    if len(prior) >= vol_window:
        vols = [float(c.volume) for c in prior[-vol_window:]]
        avg_volume_20d = sum(vols) / vol_window

    return {
        "prev_close": prev_close,
        "avg_volume_20d": avg_volume_20d,
        "atr14": atr(prior, atr_period),
        "rsi14": rsi(prior, rsi_period),
    }
