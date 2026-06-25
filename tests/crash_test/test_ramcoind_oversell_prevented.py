"""
tests/crash_test/test_ramcoind_oversell_prevented.py — ACCEPTANCE GATE for the
RAMCOIND duplicate-exit fix (25-Jun-2026).

Reproduces the RAMCOIND shape end-to-end against a broker simulator and asserts the
duplicate-SL over-sell can NEVER occur:

  * LAYER 1 — when the broker already shows the LIMIT_TRIPLE SL, G5b crash-recovery
    places NO duplicate (even with the local orders row not yet written — the race).
  * LAYER 2 — if a duplicate ever IS live, the reconciler's one-SL invariant cancels
    it (keeping the canonical) BEFORE the stop-hit, so on the SL fill the position
    ends FLAT (net 0), not -1.

Runs in PAPER and LIVE labels (the fix is shared code with no mode branch). A
controlled pytest scenario — NOT the destructive operator suite.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.unit.test_order_reconciler import _make_reconciler, _make_store, _cancel_res
from tests.unit.test_ramcoind_dup_exit_fix import _trade, _order


class BrokerSim:
    """A minimal broker: tracks orders + net positions; models cancel and an SL fill."""

    def __init__(self):
        self.orders: dict = {}
        self.positions: dict = {}
        self._seq = 0

    def place_order(self, *, symbol, side, qty, price=0.0, order_type="MARKET",
                    intent="INTRADAY", tag="", trigger_price=0.0):
        self._seq += 1
        oid = f"BSIM{self._seq}"
        self.orders[oid] = {
            "order_id": oid, "symbol": symbol, "transaction_type": side,
            "order_type": order_type, "quantity": qty, "price": price,
            "trigger_price": trigger_price,
            "status": "TRIGGER PENDING" if order_type == "SL" else "OPEN",
        }
        if order_type == "MARKET":
            self._fill(oid)
        return SimpleNamespace(broker_order_id=oid, product="MIS", variety="regular")

    def cancel_order(self, oid):
        o = self.orders.get(oid)
        if o and o["status"] not in ("COMPLETE", "CANCELLED"):
            o["status"] = "CANCELLED"
            return _cancel_res(True)
        return _cancel_res(False, "order not found")

    def get_open_orders(self):
        return [dict(o) for o in self.orders.values()
                if o["status"] in ("OPEN", "TRIGGER PENDING")]

    def get_positions(self):
        return [SimpleNamespace(symbol=s, qty=q, avg_price=334.0)
                for s, q in self.positions.items() if q != 0]

    def _fill(self, oid):
        o = self.orders[oid]
        o["status"] = "COMPLETE"
        delta = o["quantity"] if o["transaction_type"] == "BUY" else -o["quantity"]
        self.positions[o["symbol"]] = self.positions.get(o["symbol"], 0) + delta

    def trigger_sl(self, symbol):
        """The stop level is hit — every LIVE SL order for the symbol fills."""
        for oid, o in list(self.orders.items()):
            if (o["symbol"] == symbol and o["order_type"] == "SL"
                    and o["status"] == "TRIGGER PENDING"):
                self._fill(oid)


@pytest.mark.parametrize("mode", ["PAPER", "LIVE"])
def test_layer2_dedupe_prevents_oversell(tmp_path: Path, mode: str):
    """Two live SLs (the bug state) → reconciler cancels the duplicate → on the SL
    hit the position ends FLAT, not -1. This is the property that failed on 25-Jun."""
    store = _make_store(tmp_path)
    broker = BrokerSim()
    broker.place_order(symbol="RAMCOIND", side="BUY", qty=1, order_type="MARKET")  # entry → +1
    assert broker.positions["RAMCOIND"] == 1
    canon = broker.place_order(symbol="RAMCOIND", side="SELL", qty=1, order_type="SL",
                               trigger_price=334.38)                                # LIMIT_TRIPLE SL
    dup = broker.place_order(symbol="RAMCOIND", side="SELL", qty=1, order_type="SL",
                             trigger_price=334.38)                                  # G5b duplicate

    _trade(store, "t1", status="OPEN", direction="LONG", qty_filled=1, sl_initial=334.38)
    _order(store, canon.broker_order_id, "t1", leg="SL",
           placed_at="2026-06-25T10:00:24.976+05:30", trigger_price=334.38)
    _order(store, dup.broker_order_id, "t1", leg="SL",
           placed_at="2026-06-25T10:00:26.311+05:30", trigger_price=334.38)

    rec = _make_reconciler(store, adapter=broker, broker_orders_fn=broker.get_open_orders)
    rec._mode = mode

    # Layer 2: the invariant cancels the later duplicate, keeps the canonical.
    rec._check_duplicate_exits(store.get_all_open_trades())
    live_sl = [o for o in broker.get_open_orders() if o["order_type"] == "SL"]
    assert len(live_sl) == 1, "exactly one SL must remain after dedupe"
    assert live_sl[0]["order_id"] == canon.broker_order_id, "the canonical SL is kept"

    # The stop is hit: only the one remaining SL fills → position FLAT (no -1 over-sell).
    broker.trigger_sl("RAMCOIND")
    assert broker.positions["RAMCOIND"] == 0, "no over-sell — the position is flat"
    store.close()


@pytest.mark.parametrize("mode", ["PAPER", "LIVE"])
def test_layer1_g5b_places_no_duplicate_in_race(tmp_path: Path, mode: str):
    """The race: the broker already holds the LIMIT_TRIPLE SL but the local orders row
    is not yet written → G5b must place NO duplicate (authoritative broker check)."""
    from core.time_authority import now_ist
    from datetime import timedelta

    store = _make_store(tmp_path)
    broker = BrokerSim()
    broker.place_order(symbol="RAMCOIND", side="BUY", qty=1, order_type="MARKET")
    broker.place_order(symbol="RAMCOIND", side="SELL", qty=1, order_type="SL",
                       trigger_price=334.38)              # SL already at the broker

    old = (now_ist() - timedelta(minutes=30)).isoformat()  # past the settling window
    _trade(store, "t1", status="OPEN", direction="LONG", qty_filled=1,
           sl_initial=334.38, entry_time=old)
    # NB: NO local SL order row (the ~40ms race window) → the G5b loop gate would fire.

    rec = _make_reconciler(store, adapter=broker, broker_orders_fn=broker.get_open_orders)
    rec._mode = mode
    before = len([o for o in broker.get_open_orders() if o["order_type"] == "SL"])
    act = rec._g5b_crash_recovery_sl(store.get_all_open_trades()[0], broker_positions=None)
    after = len([o for o in broker.get_open_orders() if o["order_type"] == "SL"])

    assert act is None, "G5b must skip — a live SL already exists at the broker"
    assert after == before == 1, "no duplicate SL placed"
    # The stop hits: the single SL fills → flat, never -1.
    broker.trigger_sl("RAMCOIND")
    assert broker.positions["RAMCOIND"] == 0
    store.close()


@pytest.mark.parametrize("mode", ["PAPER", "LIVE"])
def test_layer2_dedupe_prevents_overbuy_on_short(tmp_path: Path, mode: str):
    """MIRROR of the RAMCOIND long over-sell: a SHORT LIMIT_TRIPLE trade with a
    duplicate SL (a BUY stop above entry) → dedupe cancels the duplicate → on the
    stop-hit the position ends FLAT, never +1 (an over-BUY). Proves the fix is
    symmetric across direction."""
    store = _make_store(tmp_path)
    broker = BrokerSim()
    broker.place_order(symbol="ABFRL", side="SELL", qty=1, order_type="MARKET")  # short entry → -1
    assert broker.positions["ABFRL"] == -1
    canon = broker.place_order(symbol="ABFRL", side="BUY", qty=1, order_type="SL",
                               trigger_price=102.0)                               # SHORT SL = BUY stop
    dup = broker.place_order(symbol="ABFRL", side="BUY", qty=1, order_type="SL",
                             trigger_price=102.0)                                 # duplicate

    _trade(store, "s1", symbol="ABFRL", status="OPEN", direction="SHORT", qty_filled=1,
           sl_initial=102.0, entry=100.0)
    _order(store, canon.broker_order_id, "s1", leg="SL", txn="BUY",
           placed_at="2026-06-25T11:00:24+05:30", trigger_price=102.0)
    _order(store, dup.broker_order_id, "s1", leg="SL", txn="BUY",
           placed_at="2026-06-25T11:00:26+05:30", trigger_price=102.0)

    rec = _make_reconciler(store, adapter=broker, broker_orders_fn=broker.get_open_orders)
    rec._mode = mode
    rec._check_duplicate_exits(store.get_all_open_trades())
    live_sl = [o for o in broker.get_open_orders() if o["order_type"] == "SL"]
    assert len(live_sl) == 1 and live_sl[0]["order_id"] == canon.broker_order_id

    broker.trigger_sl("ABFRL")
    assert broker.positions["ABFRL"] == 0, "no over-buy — the short is flat"
    store.close()
