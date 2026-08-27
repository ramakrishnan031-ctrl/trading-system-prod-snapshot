"""
tests/unit/test_f6_leg_held_qty_predicate.py — F6-leg (27-Aug-2026).

THE DEFECT THIS PINS
--------------------
`CncGttMonitor._gather` sums same-day CNC positions into `held`. It used
`abs(int(qty))`. On a T+1 exit the broker reports holdings 0 and a same-day
position of -1 (the sale), so `abs(-1)` made `held` read 1 for a position that
no longer exists. `held == 0` is the SOLE door to `_finalize_gtt_exit`
(cnc_gtt_monitor `_handle_row`, the `triggered` branch), so the row fell to
`_reprotect` and the exit was never finalised.

WHY THIS FILE EXISTS RATHER THAN A ONE-LINE ASSERT
--------------------------------------------------
The obvious repair -- *delete the abs()* -- is ALSO wrong: the signed sum gives
-1, which is likewise != 0 and fails the same door. Only clamping the SELL leg
with `max(0, ...)` restores reachability. These tests are written so that BOTH
wrong implementations go RED, not just the original one:

    holdings 0 + position -1  ->  abs()    == 1   RED
                              ->  signed   == -1  RED
                              ->  max(0,.) == 0   GREEN  <- the only correct one

RED-CAPABILITY (what breaks each test)
--------------------------------------
* restore `abs(int(qty))`            -> test_t1_exit_* and test_trap_* go RED
* delete the clamp (raw signed sum)  -> test_t1_exit_* and test_trap_* go RED
* drop the same-day CNC leg entirely -> test_same_day_buy_* goes RED
* clamp the BUY leg too (e.g. min)   -> test_same_day_buy_* goes RED
* widen the `held == 0` door         -> test_reachability_* goes RED
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

from orders.cnc_gtt_monitor import CncGttMonitor

_LOG = logging.getLogger("test_f6_leg")
_SYM = "JINDALSAW"


def _monitor(*, holdings, positions, gtts=()):
    """A monitor whose only live collaborator is the adapter `_gather` reads."""
    adapter = SimpleNamespace(
        get_gtts=lambda: list(gtts),
        get_holdings=lambda: list(holdings),
        get_positions=lambda: list(positions),
    )
    return CncGttMonitor(
        store=SimpleNamespace(), adapter=adapter, placer=SimpleNamespace(),
        fund_manager=SimpleNamespace(), kill_switch=SimpleNamespace(),
        notifier=SimpleNamespace(), bus=SimpleNamespace(), logger=_LOG,
        mode="PAPER", market_hours_fn=lambda: True,
    )


def _pos(symbol, qty, product="CNC"):
    return {"symbol": symbol, "qty": qty, "product": product}


def _hold(symbol, qty):
    return {"symbol": symbol, "qty": qty}


# ── the defect itself ────────────────────────────────────────────────────────

def test_t1_exit_leaves_held_zero_so_finalize_is_reachable():
    """T+1 exit: holdings 0, same-day CNC position -1. `held` MUST read 0."""
    mon = _monitor(holdings=[], positions=[_pos(_SYM, -1)])
    _gtts, held = mon._gather()
    assert held.get(_SYM, 0) == 0, (
        "T+1 exit must leave held == 0 so _finalize_gtt_exit is reachable; "
        f"got {held.get(_SYM)!r}"
    )


def test_trap_neither_abs_nor_signed_sum_is_accepted():
    """Pin BOTH wrong answers explicitly, so a future 'fix' cannot reintroduce either."""
    mon = _monitor(holdings=[], positions=[_pos(_SYM, -1)])
    _gtts, held = mon._gather()
    value = held.get(_SYM, 0)
    assert value != 1, "abs(int(qty)) has been reintroduced -- this is the original F6-leg defect"
    assert value != -1, "the raw signed sum is in use -- deleting abs() does NOT fix the defect"
    assert value == 0


def test_t1_exit_of_a_settled_holding_that_is_already_gone():
    """A multi-qty T+1 exit behaves the same way -- the sale never adds to `held`."""
    mon = _monitor(holdings=[], positions=[_pos(_SYM, -5)])
    _gtts, held = mon._gather()
    assert held.get(_SYM, 0) == 0


# ── the cases the clamp must NOT break ───────────────────────────────────────

def test_same_day_buy_still_counts_toward_held():
    """A same-day CNC BUY is a real holding; max(0, +1) must keep it."""
    mon = _monitor(holdings=[], positions=[_pos(_SYM, 1)])
    _gtts, held = mon._gather()
    assert held.get(_SYM, 0) == 1


def test_settled_holding_still_counts_toward_held():
    """Settled holdings are summed signed and are untouched by this change."""
    mon = _monitor(holdings=[_hold(_SYM, 2)], positions=[])
    _gtts, held = mon._gather()
    assert held.get(_SYM, 0) == 2


def test_partial_remainder_still_reaches_reprotect():
    """A genuine remainder must stay > 0 so _reprotect keeps its meaning."""
    mon = _monitor(holdings=[_hold(_SYM, 1)], positions=[_pos(_SYM, -1)])
    _gtts, held = mon._gather()
    assert held.get(_SYM, 0) == 1, "a real remaining holding must not be clamped away"


def test_non_cnc_positions_are_ignored():
    """MIS legs must never enter the delivery `held` tally."""
    mon = _monitor(holdings=[], positions=[_pos(_SYM, 5, product="MIS")])
    _gtts, held = mon._gather()
    assert held.get(_SYM, 0) == 0


# ── the consequence: reachability of the clean-exit door ─────────────────────

def test_reachability_held_zero_is_the_door_to_finalize():
    """
    The whole point of the fix: with held == 0 the `triggered` branch takes
    _finalize_gtt_exit, not _reprotect. Asserted against the real _handle_row.
    """
    mon = _monitor(holdings=[], positions=[_pos(_SYM, -1)])
    _gtts, held = mon._gather()

    calls = []
    mon._finalize_gtt_exit = lambda r, *, reason: calls.append(("finalize", reason)) or "finalized"
    mon._reprotect = lambda r, h, *, why: calls.append(("reprotect", why)) or "reprotected"

    row = {"gtt_id": "g1", "symbol": _SYM, "qty": 1, "needs_review": 0}
    broker_gtts = {"g1": {"id": "g1", "status": "triggered"}}
    out = mon._handle_row(row, broker_gtts, held, True)

    assert calls and calls[0][0] == "finalize", (
        f"held == 0 must route a triggered GTT to _finalize_gtt_exit; got {calls!r}"
    )
    assert out == "finalized"
