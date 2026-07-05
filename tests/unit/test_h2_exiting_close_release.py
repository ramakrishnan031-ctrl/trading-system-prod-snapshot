"""
tests/unit/test_h2_exiting_close_release.py — Wave 1, H-2 regression.

H-2 (docs/audit/full_system_audit_04july2026.md): an emergency-exit fill for a
trade in the transitional EXITING state could never cleanly close it.
`_emergency_market_exit` marks the trade EXITING, then the exit fill arrives at
`OrderPlacer._handle_exit_fill` → `OrderManager.close_trade`, whose guard accepted
only OPEN/PARTIAL and RAISED for EXITING. `_handle_exit_fill` caught that as
`exit_fill_already_closed` and early-returned — skipping `_cancel_oco_siblings`,
`release_used`, the `PositionClosed` publish, and cost/PnL recording. The trade was
finalized only ~30 min later by the reconciler's `_check_stuck_exiting` fallback
with costs=0.0.

Fix (orders/order_manager.py::close_trade): accept EXITING alongside OPEN/PARTIAL.
EXITING is a TRANSITIONAL state (core/schema.sql:155 — "before the fill handler /
reconciler closes the row"), so the exit fill is meant to close it. Truly-terminal
states (CLOSED/CLOSED_MANUAL/FAILED/CANCELLED/...) still raise → the double-close
guard is preserved. The reconciler's EXITING-exclusion (THELEELA double-sell guard)
is untouched.

These tests run the REAL `_handle_exit_fill` + REAL `OrderManager.close_trade`
against the REAL schema via the H-1 harness (tests/conftest.py `real_schema_store`
/ RealSchemaStore). Only the downstream deps (fund manager, OCO cancel, cost
calculator) are recorders — the store, OrderManager, and close path are real.
"""
from __future__ import annotations

import logging
import threading
from types import SimpleNamespace

import pytest

from broker.cost_calculator import CostBreakdown
from core.events import EventBus, OrderFilled, PositionClosed
from orders.order_manager import OrderManager
from orders.order_placer import OrderPlacer, _FillEntry, _LEG_SL

_NOW = "2026-07-05T10:00:00+05:30"
_ENTRY = 2500.0
_EXIT = 2450.0          # LONG stop → gross_pnl = (2450-2500)*10 = -500
_QTY = 10
_CHARGES = 25.0         # round-trip costs from the (stubbed) cost calculator
_NET = -500.0 - _CHARGES  # gross - charges = -525


def _insert_trade(conn, trade_id, status):
    conn.execute(
        """
        INSERT INTO trades (
            trade_id, signal_id, symbol, direction, strategy,
            qty_planned, qty_filled, entry_target_price, entry_actual_price,
            sl_initial, tgt_initial, margin_reserved, risk_amount,
            created_at, entry_time, order_protocol, status, updated_at
        ) VALUES (?, 'sig_h2', 'RELIANCE', 'LONG', 'gap_go_long',
                  ?, ?, ?, ?, 2450.0, 2600.0, 5000.0, 500.0,
                  ?, ?, 'LIMIT_TRIPLE', ?, ?)
        """,
        (trade_id, _QTY, _QTY, _ENTRY, _ENTRY, _NOW, _NOW, status, _NOW),
    )
    conn.commit()


def _build_exit_fill_stub(real_schema_store):
    """A duck-typed OrderPlacer ``self`` carrying exactly the collaborators
    `_handle_exit_fill` touches. The store + OrderManager + close_trade are REAL
    (they run against the real schema); only the downstream deps are recorders."""
    om = OrderManager(real_schema_store, logging.getLogger("test_h2_om"))
    bus = EventBus()
    pc_events: list = []
    bus.subscribe(PositionClosed, lambda e: pc_events.append(e))

    release_calls: list = []
    oco_calls: list = []
    _bd = CostBreakdown(
        brokerage=10.0, stt=5.0, exchange_txn=3.0,
        gst=2.0, sebi=0.5, stamp_duty=4.5, total=_CHARGES, turnover=50000.0,
    )

    stub = SimpleNamespace(
        _om=om,
        _bus=bus,
        _fm=SimpleNamespace(release_used=lambda **kw: release_calls.append(kw)),
        _cost_calculator=SimpleNamespace(round_trip_breakdown=lambda **kw: _bd),
        _cancel_oco_siblings=lambda **kw: oco_calls.append(kw),
        _fill_map={},
        _fill_map_lock=threading.Lock(),
        _log=logging.getLogger("test_h2"),
        _mode="PAPER",
        _notifier=None,
        _smart_tgt_manager=None,
        _breakeven_manager=None,
    )
    return stub, om, pc_events, release_calls, oco_calls


def _exit_fill_event(internal_id="sl_x"):
    return OrderFilled(
        source_module="test", payload={},
        internal_order_id=internal_id,
        broker_order_id=f"BRK_{internal_id}",
        symbol="RELIANCE", side="SELL",
        avg_fill_price=_EXIT, filled_qty=_QTY,
        filled_at=_NOW,
    )


def _exit_fill_entry(trade_id):
    return _FillEntry(
        trade_id=trade_id, reservation_id="res_exit",
        symbol="RELIANCE", qty=_QTY, leg=_LEG_SL,
        order_protocol="LIMIT_TRIPLE", direction="LONG",
    )


def test_exiting_fill_runs_full_close_and_release(real_schema_store):
    """H-2 fix: an emergency-exit fill for an EXITING trade closes it fully and
    immediately — CLOSED, capital released, OCO siblings cancelled, PositionClosed
    published, and REAL costs recorded (NOT the 0.0 stuck-exiting fallback).

    Pre-fix: close_trade rejects EXITING → _handle_exit_fill early-returns →
    the trade stays EXITING and release_used is never called → this test FAILS.
    """
    trade_id = "trd_h2_exiting"
    _insert_trade(real_schema_store.conn, trade_id, "EXITING")
    stub, om, pc_events, release_calls, oco_calls = _build_exit_fill_stub(real_schema_store)
    fe = _exit_fill_entry(trade_id)
    stub._fill_map["sl_x"] = fe

    OrderPlacer._handle_exit_fill(stub, _exit_fill_event("sl_x"), fe)

    row = om.get_trade(trade_id)
    assert row["status"] == "CLOSED", (
        "H-2: EXITING trade did not close on the exit fill — close_trade rejected "
        "EXITING and _handle_exit_fill early-returned (exit_fill_already_closed)."
    )
    assert row["exit_reason"] == "SL_HIT"
    # REAL costs via the normal path — the observable vs the costs=0.0 fallback.
    assert row["charges"] == _CHARGES
    assert row["charges"] != 0.0
    assert row["net_pnl"] == pytest.approx(_NET)
    # The full close+release ran:
    assert len(release_calls) == 1, "release_used must be called on the EXITING close"
    assert release_calls[0]["costs"] == _CHARGES
    assert len(oco_calls) == 1, "OCO siblings must be cancelled before release"
    assert oco_calls[0]["trade_id"] == trade_id
    assert len(pc_events) == 1
    assert pc_events[0].trade_id == trade_id
    assert pc_events[0].realized_pnl == pytest.approx(_NET)


def test_terminal_closed_fill_early_returns_no_double_release(real_schema_store):
    """Guard preserved: a genuinely CLOSED trade receiving a duplicate exit fill
    still early-returns — no double-close, no double-release, no PositionClosed.
    Passes both before and after the H-2 fix (proves double-close protection is
    intact)."""
    trade_id = "trd_h2_closed"
    _insert_trade(real_schema_store.conn, trade_id, "CLOSED")
    stub, om, pc_events, release_calls, oco_calls = _build_exit_fill_stub(real_schema_store)
    fe = _exit_fill_entry(trade_id)
    stub._fill_map["sl_x"] = fe

    OrderPlacer._handle_exit_fill(stub, _exit_fill_event("sl_x"), fe)

    assert om.get_trade(trade_id)["status"] == "CLOSED"
    assert release_calls == [], "double-close: release_used must NOT run again"
    assert oco_calls == [], "double-close: OCO cancel must NOT run"
    assert pc_events == [], "double-close: no PositionClosed on a terminal trade"
