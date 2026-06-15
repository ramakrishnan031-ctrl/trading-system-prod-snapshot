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


# Default offset (fraction) past the trigger for a stop-limit (SL) order.
# Mirrors config capital.sl_limit_offset_pct; kept here so the pure helper has
# a sane fallback when a caller has no config wired (tests, recovery paths).
DEFAULT_SL_LIMIT_OFFSET_PCT = 0.005  # 0.5%


def calc_sl_limit_price(
    exit_side: str,
    trigger_price: float,
    offset_pct: float = DEFAULT_SL_LIMIT_OFFSET_PCT,
) -> float:
    """
    Compute the limit price for a stop-loss-LIMIT (order_type="SL") order.

    P0 (2026-06-15): Zerodha rejects SL-M orders via the API, so every SL leg
    is now placed as SL (stop-limit). A stop-limit needs BOTH a trigger and a
    limit price; the limit is offset *past* the trigger so a triggered stop
    fills like a market order instead of resting unfilled in a fast move:

        SELL stop (exits a LONG):  limit = trigger * (1 - offset_pct)
                                   -> willing to sell a little lower to get out
        BUY  stop (exits a SHORT): limit = trigger * (1 + offset_pct)
                                   -> willing to buy a little higher to get out

    Args:
        exit_side:     the side of the SL order itself ("SELL" for a long
                       position's stop, "BUY" for a short position's stop).
        trigger_price: the stop trigger (= the strategy SL level).
        offset_pct:    fraction past the trigger for the limit (default 0.5%).

    Returns:
        Positive limit price rounded to 2 decimals (paise). Always > 0 for a
        positive trigger, satisfying the adapter's "price > 0 for SL" check.

    Raises:
        ValueError: exit_side is not "BUY"/"SELL", or trigger_price <= 0.
    """
    side = (exit_side or "").upper()
    if trigger_price <= 0:
        raise ValueError(
            f"trigger_price must be > 0 for an SL order, got {trigger_price!r}"
        )
    if offset_pct < 0:
        raise ValueError(f"offset_pct must be >= 0, got {offset_pct!r}")

    if side == "SELL":
        limit = trigger_price * (1.0 - offset_pct)
    elif side == "BUY":
        limit = trigger_price * (1.0 + offset_pct)
    else:
        raise ValueError(
            f"exit_side must be 'BUY' or 'SELL', got {exit_side!r}"
        )

    return round(limit, 2)
