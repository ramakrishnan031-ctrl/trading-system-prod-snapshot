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

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal


# Default NSE equity tick. Most equities trade in 0.05 increments; a handful
# (and FNO/index instruments) use 0.01/0.10/0.50/1.0/5.0. Always prefer the
# instrument's real tick from InstrumentCache; this is only the fallback when
# a caller has no cache wired (recovery paths, tests).
DEFAULT_TICK = 0.05


def round_to_tick(price: float, tick: float = DEFAULT_TICK, mode: str = "nearest") -> float:
    """
    Round `price` to a valid exchange tick multiple.

    Uses decimal.Decimal (not float arithmetic) to avoid precision drift such
    as 580.6500000001 / 563.3999999998 — mirrors slippage_engine's FIX-014
    approach so SL/TGT prices and slippage rounding stay byte-identical.

    mode:
        "up"      -> ROUND_CEILING (BUY stop-limit: limit must stay >= trigger)
        "down"    -> ROUND_FLOOR   (SELL stop-limit: limit must stay <= trigger)
        "nearest" -> ROUND_HALF_UP (entry/TGT LIMIT — closest valid tick)

    tick <= 0 falls back to plain 2-decimal rounding (defensive; a 0/neg tick
    is a config error caught at InstrumentCache load).
    """
    if tick <= 0:
        return round(price, 2)
    d_price = Decimal(str(price))
    d_tick = Decimal(str(tick))
    if mode == "up":
        rounding = ROUND_CEILING
    elif mode == "down":
        rounding = ROUND_FLOOR
    else:
        rounding = ROUND_HALF_UP
    d_result = (d_price / d_tick).quantize(Decimal("1"), rounding=rounding) * d_tick
    return float(d_result)


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


# FIX-181: default buffer past LTP for a marketable-LIMIT emergency/kill exit.
# Emergency exits (SL-placement failure, HARD_KILL, SL-breach with no broker SL)
# must FILL. A MARKET order fills but can slip badly in a fast move; a LIMIT
# priced 1% through the touch crosses the spread and fills like a market while
# capping the worst-case price. Overridable via capital.emergency_exit_buffer_pct.
EMERGENCY_EXIT_BUFFER_PCT = 0.01  # 1%


def marketable_limit_price(
    exit_side: str,
    ltp: float,
    buffer_pct: float = EMERGENCY_EXIT_BUFFER_PCT,
    tick_size: float = DEFAULT_TICK,
) -> float:
    """
    Compute a marketable LIMIT price that crosses the spread to force a fill.

        SELL exit -> price BELOW ltp (willing to sell lower) -> round DOWN
        BUY  exit -> price ABOVE ltp (willing to buy higher) -> round UP

    Result is snapped to a valid tick (Zerodha rejects off-tick prices).

    Raises:
        ValueError: exit_side not "BUY"/"SELL", or ltp <= 0.
    """
    side = (exit_side or "").upper()
    if ltp <= 0:
        raise ValueError(f"ltp must be > 0 for a marketable limit, got {ltp!r}")
    if buffer_pct < 0:
        raise ValueError(f"buffer_pct must be >= 0, got {buffer_pct!r}")
    if side == "SELL":
        return round_to_tick(ltp * (1.0 - buffer_pct), tick_size, mode="down")
    elif side == "BUY":
        return round_to_tick(ltp * (1.0 + buffer_pct), tick_size, mode="up")
    raise ValueError(f"exit_side must be 'BUY' or 'SELL', got {exit_side!r}")


# Default offset (fraction) past the trigger for a stop-limit (SL) order.
# Mirrors config capital.sl_limit_offset_pct; kept here so the pure helper has
# a sane fallback when a caller has no config wired (tests, recovery paths).
DEFAULT_SL_LIMIT_OFFSET_PCT = 0.005  # 0.5%


def calc_sl_limit_price(
    exit_side: str,
    trigger_price: float,
    offset_pct: float = DEFAULT_SL_LIMIT_OFFSET_PCT,
    tick_size: float = DEFAULT_TICK,
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
        Positive limit price snapped to a valid `tick_size` multiple. Always
        > 0 for a positive trigger, satisfying the adapter's "price > 0 for
        SL" check. P0 (2026-06-16, GICRE incident): the limit is now snapped
        to tick — Zerodha rejects any price that is not a tick multiple, and
        trigger * (1 ± offset_pct) almost never lands on one. Rounding is
        directional so the limit stays past the trigger (fills like a market):
          SELL stop -> round DOWN  (willing to sell a little lower)
          BUY  stop -> round UP    (willing to buy a little higher)

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
        return round_to_tick(limit, tick_size, mode="down")
    elif side == "BUY":
        limit = trigger_price * (1.0 + offset_pct)
        return round_to_tick(limit, tick_size, mode="up")
    else:
        raise ValueError(
            f"exit_side must be 'BUY' or 'SELL', got {exit_side!r}"
        )
