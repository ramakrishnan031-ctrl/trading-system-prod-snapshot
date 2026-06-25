"""
tests/unit/test_cnc_gtt_step4_wiring.py — SLICE2.5-P2 STEP 4 reconciler wiring.

(4a) startup runs the overnight-GTT reconcile; (4b) the poll loop fires it on the
15-min in-hours cadence + immediately on the first in-hours cycle (Y1 drain), and
never out-of-hours; and — critically — the reconciler EXCLUDES delivery trades (those
with an ACTIVE gtt_state row) from the position/SL/exit checks so a carried CNC
holding is never mis-closed as CLOSED_MANUAL.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from tests.unit.test_order_reconciler import _make_reconciler, _make_store, _insert_trade

_NOW = "2026-06-25T10:00:00+05:30"


def _gtt_row(store, trade_id, symbol, gtt_id=4242, qty=10):
    store.insert_gtt_state(gtt_id=gtt_id, trade_id=trade_id, symbol=symbol,
                           exit_side="SELL", qty=qty, sl_trigger=334.0, sl_limit=324.0,
                           tgt_trigger=343.0, tgt_limit=342.0, created_at=_NOW)


def test_reconciler_excludes_delivery_trade_from_manual_close(tmp_path: Path):
    store = _make_store(tmp_path)
    # Delivery trade: held in holdings(), so broker get_positions() does NOT list it.
    _insert_trade(store, "tdel", symbol="RAMCOIND", status="OPEN")
    _gtt_row(store, "tdel", "RAMCOIND")
    # Control intraday trade with no GTT — must still be closed (proves the loop runs).
    _insert_trade(store, "tint", symbol="RELIANCE", status="OPEN")

    adapter = MagicMock()
    adapter.get_positions.return_value = []          # broker shows no positions
    r = _make_reconciler(store, adapter=adapter)
    r.reconcile_once()

    # delivery trade preserved (CncGttMonitor owns it); control closed externally
    assert store.fetch_one("SELECT status FROM trades WHERE trade_id='tdel'")["status"] == "OPEN"
    assert store.fetch_one("SELECT status FROM trades WHERE trade_id='tint'")["status"] == "CLOSED_MANUAL"
    # the delivery GTT row is untouched (still ACTIVE)
    assert store.get_active_gtt_for_trade("tdel") is not None


def test_maybe_run_cnc_monitor_cadence(tmp_path: Path):
    store = _make_store(tmp_path)
    r = _make_reconciler(store)
    mon = MagicMock()
    hours = {"v": True}
    r._cnc_gtt_monitor = mon
    r._market_hours_fn = lambda: hours["v"]
    r._cnc_monitor_every = 60

    # out-of-hours: never runs
    hours["v"] = False
    for _ in range(5):
        r._maybe_run_cnc_monitor()
    assert mon.reconcile.call_count == 0

    # in-hours: FIRST cycle fires immediately (Y1 drain), not waiting for the 15-min mark
    hours["v"] = True
    r._maybe_run_cnc_monitor()
    assert mon.reconcile.call_count == 1
    mon.reconcile.assert_called_with(in_hours=True)

    # cycles 2..59: quiet
    for _ in range(58):
        r._maybe_run_cnc_monitor()
    assert mon.reconcile.call_count == 1

    # cycle 60: the 15-min cadence fires again
    r._maybe_run_cnc_monitor()
    assert mon.reconcile.call_count == 2


def test_sweep_stale_orders_never_touches_gtt_state(tmp_path: Path):
    # 8b: a gtt_state GTT is a LIVE protective leg — EXEMPT from order-cancel sweeps.
    store = _make_store(tmp_path)
    _insert_trade(store, "tg", symbol="RAMCOIND", status="OPEN")
    _gtt_row(store, "tg", "RAMCOIND")
    # a terminal trade with a stale non-terminal order — the legitimate sweep target
    _insert_trade(store, "tc", symbol="INFY", status="CLOSED")
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO orders (order_id,trade_id,leg,transaction_type,order_type,"
            "product,variety,qty_requested,status,placed_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("o1", "tc", "SL", "SELL", "SL", "MIS", "regular", 10, "OPEN", _NOW, _NOW),
        )
    r = _make_reconciler(store)
    swept = r.sweep_stale_orders()
    assert swept == 1
    assert store.fetch_one("SELECT status FROM orders WHERE order_id='o1'")["status"] == "CANCELLED"
    # the GTT row is UNTOUCHED — sweeps operate only on the orders table
    assert store.get_active_gtt_for_trade("tg") is not None
    assert len(store.get_active_gtt_states()) == 1


def test_start_runs_startup_gtt_reconcile(tmp_path: Path):
    store = _make_store(tmp_path)
    adapter = MagicMock()
    adapter.get_positions.return_value = []
    r = _make_reconciler(store, adapter=adapter)
    mon = MagicMock()
    r._cnc_gtt_monitor = mon
    r._market_hours_fn = lambda: False     # keep the poll-loop cadence quiet during the test
    r.start()
    try:
        assert mon.reconcile.called        # 4a startup re-verify
    finally:
        r.stop()
