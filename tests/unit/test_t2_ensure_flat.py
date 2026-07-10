# tests/unit/test_t2_ensure_flat.py — T2 proof-script ensure-flat safety (30-Jun)
"""
The T2 proof script must be self-safe on EVERY exit path: no failure path may leave a
naked/unprotected CNC position, and the GTT (overnight protection) must never be
deleted while a position is still held. These tests exercise the refactored,
broker-injected flow functions with fakes (no real broker, no real money).
"""
import logging
from types import SimpleNamespace

import pytest

from scripts.t2_cnc_gtt_realtest import (
    ensure_flat, run_single_session, run_close_overnight, run_dry_run, main, _net_qty,
)

LOG = logging.getLogger("t2_test")

GTT_OK = {
    "type": "two-leg",
    "condition": {"trigger_values": [97.0, 105.0]},
    "orders": [
        {"transaction_type": "SELL", "product": "CNC", "quantity": 1},
        {"transaction_type": "SELL", "product": "CNC", "quantity": 1},
    ],
}


class FakeKite:
    def __init__(self, net_qty=0, gtt=None, ltp=100.0):
        self.statuses = {}          # order_id -> status string (set by FakeAdapter)
        self._net_qty = net_qty
        self._gtt = gtt or GTT_OK
        self._ltp = ltp
        self.deleted = []           # gtt ids passed to delete_gtt

    def order_history(self, oid):
        return [{"status": self.statuses.get(oid, "COMPLETE")}]

    def positions(self):
        q = self._net_qty
        return {"day": ([{"tradingsymbol": "IDEA", "quantity": q}] if q else [])}

    def ltp(self, syms):
        return {syms[0]: {"last_price": self._ltp}}

    def get_gtt(self, gid):
        return self._gtt

    def delete_gtt(self, gid):
        self.deleted.append(int(gid))


class FakeAdapter:
    def __init__(self, kite, reject_tags=(), raise_tags=(), paper=False, tick=0.05):
        self.kite = kite
        self.reject_tags = set(reject_tags)
        self.raise_tags = set(raise_tags)
        self.orders = []            # (side, qty, tag, order_id)
        self._n = 0
        self._paper = paper         # run_dry_run guards on this
        self._tick = tick

    # 10-Jul MARKET->marketable-LIMIT: _marketable_limit resolves the tick via the adapter.
    def _resolve_tick(self, symbol):
        return self._tick

    def get_quote(self, symbols):   # CncGttPlacer wiring uses this in the dry-run path
        return {}

    def get_gtt(self, gid):         # paper get_gtt in run_dry_run
        return self.kite.get_gtt(gid)

    def delete_gtt(self, gid):      # paper delete_gtt in run_dry_run
        return self.kite.delete_gtt(gid)

    def place_order(self, *, symbol, side, qty, price, order_type, intent, tag):
        if tag in self.raise_tags:
            raise RuntimeError(f"broker reject for {tag}")
        self._n += 1
        oid = f"ord{self._n}"
        self.orders.append((side, qty, tag, oid))
        self.kite.statuses[oid] = "REJECTED" if tag in self.reject_tags else "COMPLETE"
        return SimpleNamespace(broker_order_id=oid)

    def tags(self):
        return [o[2] for o in self.orders]


class FakeGttPlacer:
    def __init__(self, raise_=False, gtt_id=1001):
        self.raise_ = raise_
        self.gtt_id = gtt_id

    def place_for_fill(self, **kw):
        if self.raise_:
            raise RuntimeError("GTT placement failed (C8 / no LTP)")
        return SimpleNamespace(gtt_id=self.gtt_id, sl_trigger=97.0, tgt_trigger=105.0,
                               sl_limit=96.0, tgt_limit=104.5)

    def hydrate_from_store(self):   # no-op mirror of the real placer (main() calls it)
        return 0


class FakeStore:
    """Throwaway-store stand-in for the T2 flow (SLICE2.5-P2 durable gtt_state).
    ef442ab added the store param to run_single_session/run_dry_run/main."""
    def __init__(self, gtt_id=1001, qty=1):
        self._gtt_id, self._qty, self._active = gtt_id, qty, True

    def get_active_gtt_for_trade(self, trade_id):
        return ({"gtt_id": self._gtt_id, "status": "ACTIVE", "qty": self._qty,
                 "trade_id": trade_id}                       # run_dry_run asserts this
                if self._active else None)

    def get_active_gtt_states(self):
        return [{"gtt_id": self._gtt_id}] if self._active else []

    def set_gtt_state_status(self, gid, status, ts):
        if str(gid) == str(self._gtt_id) and status == "CANCELLED":
            self._active = False

    def close(self):
        pass


# ───────────────────────── ensure_flat ─────────────────────────
def test_ensure_flat_squares_held():
    kite = FakeKite(net_qty=1)
    adapter = FakeAdapter(kite)
    flat, detail = ensure_flat(adapter, kite, "IDEA", LOG)
    assert flat is True
    assert "t2_ensure_flat" in adapter.tags()

def test_ensure_flat_already_flat_no_sell():
    kite = FakeKite(net_qty=0)
    adapter = FakeAdapter(kite)
    flat, detail = ensure_flat(adapter, kite, "IDEA", LOG)
    assert flat is True and "already flat" in detail
    assert adapter.orders == []          # never sells when nothing is held (no short)

def test_ensure_flat_sell_reject_flags_manual():
    kite = FakeKite(net_qty=1)
    adapter = FakeAdapter(kite, reject_tags={"t2_ensure_flat"})
    flat, detail = ensure_flat(adapter, kite, "IDEA", LOG)
    assert flat is False and "MANUAL" in detail


# ───────────────────────── single-session flow ─────────────────────────
def test_buy_then_gtt_fail_ensures_flat():
    """BUY ok -> GTT placement fails -> finally squares the held position (no naked hold)."""
    kite = FakeKite(net_qty=1)            # the bought share is held
    adapter = FakeAdapter(kite)
    placer = FakeGttPlacer(raise_=True)   # GTT placement raises
    with pytest.raises(RuntimeError):     # the GTT exception propagates (loud)...
        run_single_session(adapter, kite, placer, FakeStore(), "IDEA", 1, LOG)
    # ...but the finally squared the position first:
    assert "t2_ensure_flat" in adapter.tags()
    assert kite.deleted == []             # no GTT was placed -> none to delete

def test_square_reject_keeps_gtt():
    """BUY+GTT ok -> square SELL rejects (DDPI) -> GTT KEPT (protection), flagged manual."""
    kite = FakeKite(net_qty=1)
    adapter = FakeAdapter(kite, reject_tags={"t2_square", "t2_ensure_flat"})  # DDPI blocks both
    placer = FakeGttPlacer(gtt_id=1001)
    rc = run_single_session(adapter, kite, placer, FakeStore(), "IDEA", 1, LOG)
    assert rc == 1
    assert kite.deleted == []             # GTT g1 NOT deleted (position still held)
    assert "t2_square" in adapter.tags()  # the square was attempted

def test_success_confirms_then_deletes_gtt():
    """Happy path: square confirmed COMPLETE, THEN the GTT is deleted (never naked)."""
    kite = FakeKite(net_qty=1)
    adapter = FakeAdapter(kite)           # all orders COMPLETE
    placer = FakeGttPlacer(gtt_id=1001)
    rc = run_single_session(adapter, kite, placer, FakeStore(), "IDEA", 1, LOG)
    assert rc == 0
    assert "t2_square" in adapter.tags()
    assert "t2_ensure_flat" not in adapter.tags()   # square succeeded -> no ensure-flat needed
    assert kite.deleted == [1001]                    # GTT deleted only after the confirmed square

def test_arm_overnight_holds_and_keeps_gtt():
    kite = FakeKite(net_qty=1)
    adapter = FakeAdapter(kite)
    placer = FakeGttPlacer(gtt_id=1001)
    rc = run_single_session(adapter, kite, placer, FakeStore(), "IDEA", 1, LOG, arm_overnight=True)
    assert rc == 0
    assert "t2_square" not in adapter.tags()   # intentionally not squared
    assert kite.deleted == []                  # GTT intentionally kept for the overnight test


# ───────────────────────── close-overnight ─────────────────────────
def test_close_overnight_complete_deletes_gtt():
    kite = FakeKite(net_qty=1)
    adapter = FakeAdapter(kite)
    rc = run_close_overnight(adapter, kite, "IDEA", 1, "55", LOG)
    assert rc == 0 and kite.deleted == [55]

def test_close_overnight_reject_keeps_gtt():
    kite = FakeKite(net_qty=1)
    adapter = FakeAdapter(kite, reject_tags={"t2_close"})
    rc = run_close_overnight(adapter, kite, "IDEA", 1, "55", LOG)
    assert rc == 1 and kite.deleted == []      # failed sell -> GTT KEPT


# ───────────────────────── dry-run + guards ─────────────────────────
def test_dry_run_places_nothing(tmp_path):
    # ef442ab: run_dry_run now takes (adapter, kite, gtt_placer, store, store_path, ...)
    # and exercises the store-wired paper GTT path; it still places NO real order.
    kite = FakeKite(ltp=12.3)
    adapter = FakeAdapter(kite, paper=True)
    rc = run_dry_run(adapter, kite, FakeGttPlacer(), FakeStore(),
                     tmp_path / "t2_proof.db", "IDEA", 1, LOG)
    assert rc == 0
    assert adapter.orders == []                # rehearsal places no buy/sell

def test_main_refuses_without_confirm():
    assert main(["--symbol", "IDEA"]) == 2      # no --confirm, no --dry-run

def test_main_refuses_outside_market_hours(monkeypatch):
    monkeypatch.setattr("scripts.t2_cnc_gtt_realtest._is_market_hours", lambda: False)
    assert main(["--symbol", "IDEA", "--i-understand-this-places-a-real-cnc-order"]) == 2

def test_main_dry_run_bypasses_guards_and_places_nothing(monkeypatch, tmp_path):
    kite = FakeKite(ltp=12.3)
    adapter = FakeAdapter(kite, paper=True)
    cfg = SimpleNamespace(system=SimpleNamespace(capital=SimpleNamespace(
        gtt_sl_limit_offset_pct=0.03, sl_limit_offset_pct=0.005)))
    monkeypatch.setattr("scripts.t2_cnc_gtt_realtest._build_live_adapter",
                        lambda account, paper=False: (adapter, kite, cfg))
    # ef442ab: main() now builds a THROWAWAY store + a store-wired CncGttPlacer; mock
    # both so the rehearsal stays hermetic (no real DB, no real placer).
    monkeypatch.setattr("scripts.t2_cnc_gtt_realtest._open_throwaway_store",
                        lambda log: (FakeStore(), tmp_path / "t2_proof.db"))
    monkeypatch.setattr("orders.cnc_gtt.CncGttPlacer", lambda *a, **k: FakeGttPlacer())
    rc = main(["--symbol", "IDEA", "--dry-run"])   # no confirm, no market-hours
    assert rc == 0
    assert adapter.orders == []                    # rehearsal places nothing real
