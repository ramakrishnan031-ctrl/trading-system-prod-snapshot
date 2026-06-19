"""
broker/position_helpers.py — FIX-190 (Bug A): reverse-aware close helpers.

Shared by the kill switch's HARD_KILL flatten and order_placer's emergency exit
so a *second* flatten of a position that a *first* actor already closed does NOT
fire another same-side order and open a NAKED OPPOSITE position (the 19-Jun
THELEELA oversell: BUY 1 -> SELL 1 (emergency) -> SELL 1 (HARD_KILL) -> short -1).

The close decision is driven by the ACTUAL broker position, not the local
intended direction:
  * net long  (qty > 0) -> SELL qty
  * net short (qty < 0) -> BUY  |qty|
  * flat      (qty == 0) -> do nothing (already closed)

On a broker error the qty cannot be determined; callers fall back to their
intended exit so the kill switch still errs toward flattening, never toward
leaving a position open.
"""
from __future__ import annotations

from typing import Optional, Tuple


def broker_net_qty(adapter, symbol: str) -> Optional[int]:
    """Signed net broker qty for `symbol` (long > 0, short < 0, flat == 0).

    Returns None if it cannot be determined (no adapter / broker error). A
    symbol absent from the positions list is treated as flat (0), matching
    KillSwitch._is_position_flat.
    """
    if adapter is None:
        return None
    try:
        positions = adapter.get_positions()
        net = 0
        for p in positions or []:
            if getattr(p, "symbol", None) == symbol:
                net += int(getattr(p, "qty", 0) or 0)
        return net
    except Exception:
        # Non-iterable / garbage payload / broker error -> cannot determine.
        return None


def determine_close_direction(
    adapter,
    symbol: str,
    fallback_side: str,
    fallback_qty: int,
) -> Tuple[Optional[str], int]:
    """Return (close_side, qty) to flatten the current broker position for
    `symbol`, or (None, 0) if the broker confirms it is already flat.

    Reverse-aware: a long closes with SELL, a short with BUY. On a broker error
    (qty unknown) falls back to (fallback_side, fallback_qty) so the caller still
    attempts the intended exit (the kill switch must err toward flattening).
    """
    net = broker_net_qty(adapter, symbol)
    if net is None:
        return (fallback_side, max(0, int(fallback_qty or 0)))
    if net > 0:
        return ("SELL", net)
    if net < 0:
        return ("BUY", -net)
    return (None, 0)  # broker confirms flat -> do NOT fire another exit
