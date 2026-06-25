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

from dataclasses import dataclass
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


def tier_slippage_tolerance_rs(
    price: float,
    tiers: list[tuple[float, float]],
    default_rs: float,
) -> float:
    """Max acceptable entry slippage (in Rs) for a trigger `price`, from price bands.

    `tiers` is a list of (max_price, max_slippage_rs). The first band with
    ``price < max_price`` wins — the lower bound is EXCLUSIVE, so e.g. exactly
    100.00 falls into the 100-200 band, not the <100 band. Falls back to
    `default_rs` when no band matches (price at/above the top band's max_price).
    Empty `tiers` -> `default_rs`.
    """
    for max_price, max_slip in sorted(tiers, key=lambda t: t[0]):
        if price < max_price:
            return max_slip
    return default_rs


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


def calc_gtt_limit_price(
    exit_side: str,
    trigger_price: float,
    offset_pct: float,
    tick_size: float = DEFAULT_TICK,
) -> float:
    """SLICE2.5-P1: limit price for ONE leg of a CNC OCO-GTT (Good-Till-Triggered).

    A dedicated helper (NOT calc_sl_limit_price) so the GTT path never inherits the
    intraday 0.5% SL offset: a GTT SL leg is a DEEP protective limit (e.g. 3%) so an
    overnight gap-down still fills within the floor, while the GTT TGT leg uses a
    SMALL offset so it fills at/just-below the target. Same directional, tick-snapped
    math as the intraday SL limit, but the offset is supplied explicitly by the
    caller (gtt_sl_limit_offset_pct for SL, sl_limit_offset_pct for TGT):

        SELL leg (exits a LONG):  limit = trigger * (1 - offset_pct), round DOWN
        BUY  leg (exits a SHORT): limit = trigger * (1 + offset_pct), round UP

    Both OCO legs of a LONG delivery position are SELL (SL sells low, TGT sells high).

    Raises:
        ValueError: exit_side not BUY/SELL, trigger_price <= 0, or offset_pct < 0.
    """
    side = (exit_side or "").upper()
    if trigger_price <= 0:
        raise ValueError(f"trigger_price must be > 0 for a GTT leg, got {trigger_price!r}")
    if offset_pct < 0:
        raise ValueError(f"offset_pct must be >= 0, got {offset_pct!r}")
    if side == "SELL":
        return round_to_tick(trigger_price * (1.0 - offset_pct), tick_size, mode="down")
    if side == "BUY":
        return round_to_tick(trigger_price * (1.0 + offset_pct), tick_size, mode="up")
    raise ValueError(f"exit_side must be 'BUY' or 'SELL', got {exit_side!r}")


# FIX-190 (Bug D): default safety margin inside the circuit band (2%).
DEFAULT_CIRCUIT_MARGIN_PCT = 0.02


@dataclass(frozen=True)
class ClampResult:
    """Outcome of :func:`clamp_exit_into_band` — an exit price clamped into the
    circuit band AND validated against the entry fill for its (leg, direction).

    Frozen on purpose: ``placeable`` cannot be silently ignored — every caller
    must branch on it (a wrong-side exit is never sent to the broker).

    Attributes:
        price:       the clamped, tick-snapped price (meaningful when placeable).
        was_clamped: True if the band actually moved the input price.
        placeable:   True iff ``price`` is inside the band AND on the correct side
                     of the entry fill for this leg+direction. False = UNPLACEABLE:
                     no in-band price exists on the profit (TGT) / protective (SL)
                     side of the fill. A wrong-side TGT would be instantly
                     marketable (the 22-Jun NOCIL scratch); a wrong-side SL would
                     instant-stop-out. The caller MUST NOT place such an order.
        reason:      short human-readable explanation (logs / verdicts).
    """
    price: float
    was_clamped: bool
    placeable: bool
    reason: str = "ok"


def clamp_exit_into_band(
    price: float,
    *,
    leg: str,
    direction: str,
    entry_fill: float,
    upper_circuit: float | None,
    lower_circuit: float | None,
    tick: float = DEFAULT_TICK,
    margin_pct: float = DEFAULT_CIRCUIT_MARGIN_PCT,
) -> ClampResult:
    """Clamp an exit price into the circuit band, THEN verify the result is on
    the correct side of the entry fill for ``(leg, direction)``.

    This is the SINGLE chokepoint that stops a clamped exit from becoming a
    wrong-side, self-defeating order. The 22-Jun NOCIL defect: a LONG TGT
    recalc'd to 197.12 was clamped DOWN to ``upper*(1-margin)=187.00``, which
    sat BELOW the 189.78 fill — an instantly-marketable SELL that scratched the
    trade 2.35s after entry. The band-clamp alone (FIX-190 Bug D) is
    side-agnostic and cannot see that; this function adds the leg/direction
    polarity check against the fill.

    Band: clamp into ``[lower*(1+margin), upper*(1-margin)]`` (tick-snapped —
    upper rounds DOWN to stay inside, lower rounds UP).

    Polarity (the crux — must NOT over-fire on benign SL clamps)::

        TGT (profit side):    LONG wrong-side = price <= fill ; SHORT = price >= fill
        SL  (protective side):LONG wrong-side = price >= fill ; SHORT = price <= fill

    A LONG's SL clamped UP off the lower circuit but still BELOW the fill is a
    valid, tighter stop -> ``placeable=True`` (NOT rejected).

    Fail-open: if band data is missing/non-positive there is no band to violate
    -> price passes through unchanged, ``placeable=True``. Likewise if
    ``entry_fill`` is non-positive (no reference to judge the side against).
    A non-positive ``price`` is returned ``placeable=False``.

    PURE: no quote I/O — the caller fetches the band (e.g. ``_circuit_limits``).

    Args:
        leg: "TGT" or "SL".
        direction: "LONG"/"SHORT" (or "BUY"/"SELL").
        entry_fill: the actual entry fill price the exit must sit on the right
            side of (avg_fill_price at placement / entry_actual_price on retry).
    """
    leg_u = (leg or "").upper()
    if leg_u not in ("TGT", "SL"):
        raise ValueError(f"leg must be 'TGT' or 'SL', got {leg!r}")
    is_long = _is_long(direction)

    if price <= 0:
        return ClampResult(
            price=price, was_clamped=False, placeable=False,
            reason="non-positive price",
        )

    clamped = price
    if upper_circuit and upper_circuit > 0:
        upper_safe = round_to_tick(
            upper_circuit * (1.0 - margin_pct), tick, mode="down"
        )
        if clamped > upper_safe:
            clamped = upper_safe
    if lower_circuit and lower_circuit > 0:
        lower_safe = round_to_tick(
            lower_circuit * (1.0 + margin_pct), tick, mode="up"
        )
        if clamped < lower_safe:
            clamped = lower_safe
    was_clamped = abs(clamped - price) > (tick / 2.0)

    # Leg/direction polarity vs the entry fill. entry_fill<=0 -> no reference to
    # violate -> fail open (placeable).
    if entry_fill and entry_fill > 0:
        if leg_u == "TGT":
            wrong = (clamped <= entry_fill) if is_long else (clamped >= entry_fill)
            side = "profit"
        else:  # SL
            wrong = (clamped >= entry_fill) if is_long else (clamped <= entry_fill)
            side = "protective"
        if wrong:
            return ClampResult(
                price=clamped, was_clamped=was_clamped, placeable=False,
                reason=(
                    f"clamped {leg_u} {clamped} on wrong side of fill "
                    f"{entry_fill} ({'LONG' if is_long else 'SHORT'}; no in-band "
                    f"price on the {side} side)"
                ),
            )

    return ClampResult(
        price=clamped, was_clamped=was_clamped, placeable=True, reason="ok",
    )
