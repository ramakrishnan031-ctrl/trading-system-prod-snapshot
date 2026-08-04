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

Also hosts `cancel_co_bracket` (ledger #2d): the one broker gesture that closes a
CO position, shared by `eod_squareoff` and the kill switch's HARD_KILL flatten.
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


def cancel_co_bracket(adapter, broker_order_id: str) -> Tuple[bool, str]:
    """Cancel a CO bracket at the broker; return (ok, reason). Ledger #2d.

    Audit 3.1: a CO position CANNOT be closed with a reverse MARKET -- Zerodha
    rejects it and auto-squares at 15:20 with a Rs50+GST penalty. The only
    correct gesture is `cancel_order(entry_broker_order_id, variety="co")`; the
    broker then collapses the bracket and closes the position at market. This
    function is that gesture and nothing else.

    ⛔ IT DELIBERATELY DOES NOT LOG. Both callers emit their own CRITICAL with
    their own grep sentinel (`CO_SQUAREOFF_CANCEL_REJECTED` for EOD -- asserted
    by tests/unit/test_eod_squareoff.py -- and `KS_CO_CANCEL_REJECTED` for the
    kill path). A log line here would duplicate them and change EOD's alert
    stream, which is the behaviour change ledger #2d's card forbids.

    ⛔⛔ IT ALSO DOES NOT CATCH. This looks like it contradicts the "neither logs
    nor raises" contract in the #2d design record (§A4), so read this before
    "fixing" it: today a raising `cancel_order` propagates out of EOD's CO branch
    to its generic handler, which logs `EOD exit unexpected error` and calls
    `_mark_exit_failed`. If this helper swallowed the exception and returned
    (False, reason) instead, EOD would take its `CO_SQUAREOFF_CANCEL_REJECTED`
    branch -- a DIFFERENT CRITICAL and a DIFFERENT sentinel. That is precisely
    the altered behaviour the card's STOP condition forbids, so the boundary rule
    wins over the literal wording: exceptions belong to the caller. The (ok,
    reason) pair covers only the non-raising rejection -- an adapter that returns
    a result object with success=False.
    """
    result = adapter.cancel_order(broker_order_id, variety="co")
    if not getattr(result, "success", False):
        return (False, getattr(result, "reason", "") or "rejected")
    return (True, "")
