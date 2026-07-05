"""
tests/unit/test_h4_killswitch_retry_rederive.py — Wave 2, H-4.

The HARD_KILL flatten's retry loop (KillSwitch._exit_all_trades_indestructible)
re-fired the STALE full qty captured at first-pass time, gated only by a binary
`_is_position_flat` check. If the first exit raised AFTER transmission
(BrokerTimeoutError — the A-2 ambiguity class) and PARTIALLY filled, the position
is non-flat, so the retry re-fired the full qty → oversell → new naked reverse.
The first pass is broker-net-aware (`determine_close_direction`); the retry was not.

Fix: re-derive `(close_side, close_qty)` via `determine_close_direction` from a
FRESH signed broker-position read on EVERY retry (mirror the first pass); the flat
case `(None, 0)` subsumes the binary `_is_position_flat` gate.

These tests drive the REAL `_exit_all_trades_indestructible` over the REAL schema
with a recording adapter that injects a correctly-SIGNED residual position (so the
test is NOT gated by H-12). `time.sleep` is patched to a no-op.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from broker.zerodha_adapter import CancelResult, PlacedOrder
from capital.kill_switch import KillSwitch
from core.events import EventBus
from core.exceptions import BrokerTimeoutError
from core.state_store import StateStore

_NOW = "2026-07-05T10:00:00+05:30"


class _FlattenAdapter:
    """Broker-boundary simulator: the FIRST exit is an A-2 ambiguity (records the
    order, mutates the position to `residual`, then raises BrokerTimeoutError);
    later exits record + succeed. `get_positions` returns a correctly-SIGNED
    residual (H-12-independent). `get_quote_raw` returns a live LTP."""

    def __init__(self, symbol, start_qty, residual):
        self.symbol = symbol
        self.net_qty = start_qty       # signed: long > 0, short < 0
        self.residual = residual       # signed residual after the ambiguous 1st exit
        self.placed = []
        self.cancelled = []
        self._n = 0

    def get_positions(self):
        return [SimpleNamespace(symbol=self.symbol, qty=self.net_qty)]

    def get_quote_raw(self, instruments):
        return {k: {"last_price": 2450.0} for k in instruments}

    def cancel_order(self, oid):
        self.cancelled.append(oid)
        return CancelResult(broker_order_id=oid, success=True, reason="")

    def place_order(self, symbol, side, qty, order_type, price, intent, tag=None, **kw):
        self._n += 1
        self.placed.append({"side": side, "qty": qty})
        if self._n == 1:
            # A-2: transmitted, partially filled, then the ack timed out.
            self.net_qty = self.residual
            raise BrokerTimeoutError("exit ack timed out after transmission")
        return PlacedOrder(
            internal_order_id=f"int_{self._n}", broker_order_id=f"brk_{self._n}",
            symbol=symbol, side=side, qty=qty, price=price, order_type=order_type,
            product="MIS", status="SUBMITTED", ts=datetime.now(),
        )


def _seed_open_trade(store, symbol, direction, qty_filled):
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id, symbol, scanner, strategy, triggered_at, "
            "received_at, expires_at, status, fingerprint, fingerprint_date) VALUES "
            "('sig_h4', ?, 'gap_go_long', 'gap_go_long', ?, ?, ?, 'PROCESSING', 'fp_h4', ?)",
            (symbol, _NOW, _NOW, _NOW, _NOW[:10]),
        )
        cur.execute(
            "INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy, "
            "qty_planned, qty_filled, entry_target_price, entry_actual_price, sl_initial, "
            "tgt_initial, margin_reserved, risk_amount, created_at, entry_time, "
            "order_protocol, status, updated_at) VALUES "
            "('T_H4', 'sig_h4', ?, ?, 'gap_go_long', ?, ?, 2500.0, 2500.0, 2450.0, 2600.0, "
            "5000.0, 500.0, ?, ?, 'LIMIT_TRIPLE', 'OPEN', ?)",
            (symbol, direction, qty_filled, qty_filled, _NOW, _NOW, _NOW),
        )
        cur.execute(
            "INSERT INTO orders (order_id, trade_id, leg, transaction_type, order_type, "
            "product, variety, qty_requested, status, placed_at, updated_at) VALUES "
            "('ENTRY_H4', 'T_H4', 'ENTRY', ?, 'LIMIT', 'MIS', 'regular', ?, 'COMPLETE', ?, ?)",
            ("BUY" if direction == "LONG" else "SELL", qty_filled, _NOW, _NOW),
        )


def _run_hard_kill(tmp_path, monkeypatch, *, direction, qty, start_qty, residual,
                   symbol="RELIANCE"):
    monkeypatch.setattr(time, "sleep", lambda s: None)   # no real retry backoff
    store = StateStore(str(tmp_path / "h4.db"))
    _seed_open_trade(store, symbol, direction, qty)
    adapter = _FlattenAdapter(symbol, start_qty=start_qty, residual=residual)
    ks = KillSwitch(
        state_store=store, bus=EventBus(), logger=logging.getLogger("test_h4"),
        adapter=adapter, enable_auto_trip=False,
    )
    report = ks._exit_all_trades_indestructible()
    store.close()
    return adapter, report


def test_retry_refires_residual_qty_long(tmp_path, monkeypatch):
    """Test 1 (core bug): 100 long; first exit partially fills 60 → residual 40 long;
    the retry must fire SELL 40 (the residual), NOT SELL 100 (the stale full qty)."""
    adapter, _ = _run_hard_kill(tmp_path, monkeypatch, direction="LONG",
                                qty=100, start_qty=100, residual=40)
    assert adapter.placed[0] == {"side": "SELL", "qty": 100}   # first pass (raised)
    assert adapter.placed[-1] == {"side": "SELL", "qty": 40}, (
        "H-4: the retry re-fired the STALE full qty instead of the residual — oversell."
    )
    assert len(adapter.placed) == 2   # first pass + one retry


def test_retry_rederives_direction_short(tmp_path, monkeypatch):
    """Test 2 (direction re-derivation): 100 short; residual 40 short → the retry
    must fire BUY 40 (residual side+qty from the signed net)."""
    adapter, _ = _run_hard_kill(tmp_path, monkeypatch, direction="SHORT",
                                qty=100, start_qty=-100, residual=-40)
    assert adapter.placed[0] == {"side": "BUY", "qty": 100}
    assert adapter.placed[-1] == {"side": "BUY", "qty": 40}
    assert len(adapter.placed) == 2


def test_retry_no_fire_when_flat(tmp_path, monkeypatch):
    """Test 3 (already flat): first exit fully closed the position (residual 0) →
    determine_close_direction reports flat → the retry fires NOTHING."""
    adapter, _ = _run_hard_kill(tmp_path, monkeypatch, direction="LONG",
                                qty=100, start_qty=100, residual=0)
    assert adapter.placed == [{"side": "SELL", "qty": 100}]   # only first pass; no retry fire
