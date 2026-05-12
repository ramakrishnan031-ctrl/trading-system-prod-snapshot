"""
orders/price_math.py — Trading System v2

Single source of truth for SL and target price calculations.

Centralises the FIXED_PCT and RISK_REWARD formulas used by both
order_placer._compute_tgt() and shadow_tracker._derive_sl_tgt_from_strategy()
so future tweaks update exactly one place (FIX-004).

Convention:
    direction — "LONG" | "SHORT"   (DB/strategy terminology)
    side      — "BUY"  | "SELL"    (broker order terminology)

Both are accepted; BUY is mapped to LONG, SELL to SHORT.
"""

from __future__ import annotations


def _is_long(direction_or_side: str) -> bool:
    v = direction_or_side.upper()
    return v in ("LONG", "BUY")


def calc_sl_price(
    direction: str,
    entry_price: float,
    sl_pct: float,
) -> float:
    """
    Compute stop-loss price using FIXED_PCT method.

    LONG:  entry * (1 - sl_pct)
    SHORT: entry * (1 + sl_pct)
    """
    if _is_long(direction):
        return entry_price * (1.0 - sl_pct)
    return entry_price * (1.0 + sl_pct)


def calc_tgt_price(
    direction: str,
    entry_price: float,
    sl_price: float,
    rr_ratio: float,
) -> float:
    """
    Compute target price using RISK_REWARD method.

    risk = abs(entry - sl)
    LONG:  entry + risk * rr_ratio
    SHORT: entry - risk * rr_ratio
    """
    risk = abs(entry_price - sl_price)
    if _is_long(direction):
        return entry_price + risk * rr_ratio
    return entry_price - risk * rr_ratio
