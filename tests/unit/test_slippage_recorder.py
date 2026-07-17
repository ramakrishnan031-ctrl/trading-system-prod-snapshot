"""Tests for slippage-intelligence Phase 1: recorder, rr_damage, schema v31."""
from __future__ import annotations

import logging

import pytest

from core.events import OrderFilled, PositionClosed
from orders.slippage_recorder import (
    SlippageRecorder,
    adverse_entry_slip,
    adverse_sl_slip,
    calc_rr_damage_pct,
    favorable_tgt_slip,
    get_price_band,
)

_BANDS = ["0-100", "100-200", "200-300", "300-500", "500-1000", "1000+"]


# ── sign convention (adverse = positive) ─────────────────────────────────────

def test_adverse_entry_slip():
    assert adverse_entry_slip("LONG", 100, 101) == 1.0     # filled higher = worse
    assert adverse_entry_slip("LONG", 100, 99) == -1.0     # filled lower = better
    assert adverse_entry_slip("SHORT", 100, 99) == 1.0     # filled lower = worse (short)
    assert adverse_entry_slip("SHORT", 100, 101) == -1.0


def test_adverse_sl_and_favorable_tgt():
    assert adverse_sl_slip("LONG", 95, 94) == 1.0          # sold below stop = worse
    assert adverse_sl_slip("SHORT", 105, 106) == 1.0       # covered above stop = worse
    assert favorable_tgt_slip("LONG", 110, 111) == 1.0     # sold above target = better
    assert favorable_tgt_slip("SHORT", 90, 89) == 1.0      # covered below target = better


# ── rr_damage (THE key metric) ───────────────────────────────────────────────

def test_rr_damage_ramas_example():
    assert calc_rr_damage_pct(1.0, 1.0, 0.0, 10.0) == 20.0     # 20% of risk budget


def test_rr_damage_tgt_reduces():
    assert calc_rr_damage_pct(1.0, 1.0, 0.5, 10.0) == 15.0     # favourable TGT reduces


def test_rr_damage_none_or_missing_legs():
    assert calc_rr_damage_pct(1.0, 1.0, 0.0, 0) is None        # no distance
    assert calc_rr_damage_pct(1.0, None, None, 10.0) == 10.0   # missing legs -> 0


# ── price band ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("price,band", [
    (50, "0-100"), (99.99, "0-100"), (100, "100-200"), (250, "200-300"),
    (300, "300-500"), (999, "500-1000"), (1000, "1000+"), (5000, "1000+"),
])
def test_price_band(price, band):
    assert get_price_band(price, _BANDS) == band


def test_price_band_empty_or_unmatched():
    assert get_price_band(250, []) is None


# ── build_trade_slippage_row roll-up ─────────────────────────────────────────

def test_build_row_theleela_like():
    t = {
        "trade_id": "tr1", "symbol": "THELEELA", "strategy": "positional_sector_rotation",
        "direction": "LONG", "qty_filled": 1, "created_at": "2026-06-19T10:00:00",
        "entry_target_price": 481.50, "entry_actual_price": 484.60,
        "sl_initial": 471.87, "tgt_initial": 500.97,
        "exit_price": 471.50, "exit_reason": "SL_HIT", "net_pnl": -13.1,
    }
    row = SlippageRecorder.build_trade_slippage_row(t, _BANDS)
    assert row["price_band"] == "300-500"
    assert round(row["entry_slippage_rs"], 2) == 3.10
    assert round(row["planned_sl_distance"], 2) == 9.63
    # damage = (entry 3.10 + sl 0.37 - tgt 0) / 9.63 * 100 ≈ 36%
    assert row["rr_damage_pct"] == pytest.approx(36.0, abs=1.0)
    assert row["trade_result"] == "LOSS" and row["exit_reason"] == "SL_HIT"


# ── recorder integration (fake store/bus; best-effort) ───────────────────────

class _FakeStore:
    """Key-aware fake. `orders` is keyed by the BROKER order id, because that is
    the real PK (order_manager.insert_order: "broker_order_id is the PK ...
    internal_order_id is NOT stored"). A lookup keyed on the internal ord_<hex>
    id therefore misses here exactly as it missed in production — without this,
    a fake that answers any "FROM orders" query cannot go red."""

    _BROKER_ENTRY = "260716170346053"
    _BROKER_SL = "260716170346115"

    def __init__(self):
        self.oel = []
        self.tsl = []
        self.mec = []
        self.trade = None
        self.tol_frac = None     # Phase 3a: trades.tolerance_fraction_used
        self.tol_source = None   # Phase 3a: trades.tolerance_source
        self.orders = {
            self._BROKER_ENTRY: {"leg": "ENTRY", "order_type": "LIMIT",
                                 "qty_requested": 1, "trade_id": "t1",
                                 "placed_at": "2026-06-20T09:59:58"},
            self._BROKER_SL: {"leg": "SL", "order_type": "SL",
                              "qty_requested": 1, "trade_id": "t1",
                              "placed_at": "2026-06-20T09:59:59"},
        }
        self.trades = {"t1": {"signal_id": "sig1", "strategy": "gap_go_long"}}

    def insert_order_execution_log(self, row):
        self.oel.append(row); return True

    def insert_trade_slippage_log(self, row):
        self.tsl.append(row); return True

    def insert_market_execution_context(self, row):
        self.mec.append(row); return True

    def fetch_one(self, sql, params=()):
        key = params[0] if params else None
        if "FROM orders" in sql:
            return self.orders.get(key)
        if "entry_target_price" in sql:
            return self.trade
        if "FROM trades" in sql:
            row = self.trades.get(key)
            if row is None:
                return None
            return {**row, "tolerance_fraction_used": self.tol_frac,
                    "tolerance_source": self.tol_source}
        return None


class _FakeBus:
    def __init__(self):
        self.subs = {}

    def subscribe(self, et, h, async_dispatch=False):
        self.subs[et.__name__] = h


def _log():
    return logging.getLogger("test_slip")


def _production_fill(store, **over):
    """An OrderFilled shaped exactly as the two real publishers emit it
    (order_monitor._handle_complete / zerodha_adapter paper-synth): internal +
    broker ids, and NO trade_id/signal_id — neither publisher sets them."""
    kw = dict(source_module="order_monitor", symbol="HUHTAMAKI", side="BUY",
              filled_qty=1, avg_fill_price=101.0, expected_price=100.0,
              slippage_pct=1.0, filled_at="2026-06-20T10:00:00",
              internal_order_id="ord_b2205e6196c74c04b66dcde6c5a1f783",
              broker_order_id=store._BROKER_ENTRY)
    kw.update(over)
    return OrderFilled(**kw)


def test_recorder_subscribes_and_records_order_fill():
    store, bus = _FakeStore(), _FakeBus()
    SlippageRecorder(store, bus, _log(), price_bands=_BANDS)
    assert "OrderFilled" in bus.subs and "PositionClosed" in bus.subs
    bus.subs["OrderFilled"](_production_fill(store))
    assert len(store.oel) == 1 and store.oel[0]["leg"] == "ENTRY"
    assert round(store.oel[0]["slippage_rs"], 2) == 1.0
    assert len(store.mec) == 1   # context row (NULL bid/ask, no adapter)


def test_oel_parent_trade_id_resolves_so_exec_log_joins_to_trades():
    # X7: neither publisher sets ev.trade_id, and the orders PK is the broker id
    # -- so keying the lookup on the internal id left parent_trade_id NULL on
    # 262/262 live rows and the GUI drill-down (db_reader: "WHERE
    # parent_trade_id = ?") returned nothing for every trade.
    store, bus = _FakeStore(), _FakeBus()
    SlippageRecorder(store, bus, _log(), price_bands=_BANDS)
    bus.subs["OrderFilled"](_production_fill(store))
    row = store.oel[0]
    assert row["parent_trade_id"] == "t1"      # the join key -- was None
    assert row["signal_id"] == "sig1"
    assert row["strategy_name"] == "gap_go_long"
    assert row["order_type"] == "LIMIT"
    assert row["qty"] == 1
    assert row["order_timestamp"] == "2026-06-20T09:59:58"
    assert store.mec[0]["trade_id"] == "t1"    # context row joins too


def test_oel_exit_leg_is_not_relabelled_entry():
    # The `leg or "ENTRY"` default turned every unresolved fill into an ENTRY:
    # live had 155 ENTRY + 79 SL + 50 TGT + 21 EOD completed orders but 262/262
    # exec rows said ENTRY. A wrong leg is worse than a missing one.
    store, bus = _FakeStore(), _FakeBus()
    SlippageRecorder(store, bus, _log(), price_bands=_BANDS)
    bus.subs["OrderFilled"](_production_fill(
        store, broker_order_id=store._BROKER_SL, side="SELL"))
    assert store.oel[0]["leg"] == "SL"
    assert store.oel[0]["parent_trade_id"] == "t1"


def test_oel_unresolvable_order_is_unknown_not_entry():
    # Unknown broker id (e.g. the orders row lost a race): record the fill, but
    # do not assert a leg we never resolved.
    store, bus = _FakeStore(), _FakeBus()
    SlippageRecorder(store, bus, _log(), price_bands=_BANDS)
    bus.subs["OrderFilled"](_production_fill(store, broker_order_id="999999"))
    assert len(store.oel) == 1                 # still recorded
    assert store.oel[0]["leg"] == "UNKNOWN"    # not a fabricated "ENTRY"
    assert store.oel[0]["parent_trade_id"] is None


def test_oel_event_trade_id_wins_over_orders_row():
    # Legacy callers may set ev.trade_id directly; it must beat the fallback.
    store, bus = _FakeStore(), _FakeBus()
    store.trades["t_explicit"] = {"signal_id": "sig9", "strategy": "manual"}
    SlippageRecorder(store, bus, _log(), price_bands=_BANDS)
    bus.subs["OrderFilled"](_production_fill(store, trade_id="t_explicit"))
    assert store.oel[0]["parent_trade_id"] == "t_explicit"
    assert store.oel[0]["strategy_name"] == "manual"


def test_oel_is_partial_reflects_real_requested_qty():
    # is_partial was computed from a qty_requested the broken lookup never
    # returned, so it was hardcoded-0 in practice ("no partial ever").
    store, bus = _FakeStore(), _FakeBus()
    store.orders[store._BROKER_ENTRY]["qty_requested"] = 10
    SlippageRecorder(store, bus, _log(), price_bands=_BANDS)
    bus.subs["OrderFilled"](_production_fill(store, filled_qty=4))
    assert store.oel[0]["qty"] == 10
    assert store.oel[0]["is_partial"] == 1


def test_recorder_copies_tolerance_source_to_oel():
    # Phase 3a: the entry-slippage override rule recorded on the trade is copied
    # onto the order_execution_log row (read from trades in _enrich_order).
    store, bus = _FakeStore(), _FakeBus()
    store.tol_frac, store.tol_source = 0.15, "symbol:IDEA"
    SlippageRecorder(store, bus, _log(), price_bands=_BANDS)
    bus.subs["OrderFilled"](OrderFilled(
        source_module="test", symbol="IDEA", side="BUY", filled_qty=1,
        avg_fill_price=83.4, expected_price=83.0,
        internal_order_id="o1", trade_id="t1", filled_at="2026-06-20T10:00:00"))
    assert len(store.oel) == 1
    assert store.oel[0]["tolerance_fraction_used"] == 0.15
    assert store.oel[0]["tolerance_source"] == "symbol:IDEA"


def test_recorder_position_closed_rollup():
    store, bus = _FakeStore(), _FakeBus()
    store.trade = {
        "trade_id": "t1", "symbol": "X", "strategy": "gap_go_long", "direction": "LONG",
        "qty_filled": 1, "created_at": "2026-06-20T10:00:00",
        "entry_target_price": 100.0, "entry_actual_price": 100.5,
        "sl_initial": 99.0, "tgt_initial": 102.0,
        "exit_price": 102.5, "exit_reason": "TGT_HIT", "net_pnl": 2.0,
    }
    SlippageRecorder(store, bus, _log(), price_bands=_BANDS)
    bus.subs["PositionClosed"](PositionClosed(source_module="test", trade_id="t1", symbol="X"))
    assert len(store.tsl) == 1
    assert store.tsl[0]["trade_result"] == "WIN" and store.tsl[0]["price_band"] == "100-200"


def test_recorder_never_raises_on_store_failure():
    class _BoomStore(_FakeStore):
        def insert_order_execution_log(self, row):
            raise RuntimeError("db down")

        def insert_market_execution_context(self, row):
            raise RuntimeError("db down")
    bus = _FakeBus()
    SlippageRecorder(_BoomStore(), bus, _log(), price_bands=_BANDS)
    # must swallow the store error (best-effort) — this call must NOT raise
    bus.subs["OrderFilled"](OrderFilled(source_module="test", symbol="X", side="BUY",
                                        avg_fill_price=1.0, expected_price=1.0))


# ── schema v31+ (slippage tables present; version tracks EXPECTED) ────────────

def test_schema_v31_tables_and_version(tmp_path):
    from core.state_store import EXPECTED_SCHEMA_VERSION, StateStore
    assert EXPECTED_SCHEMA_VERSION >= 31
    store = StateStore(tmp_path / "v31.db")
    try:
        for tbl in ("order_execution_log", "trade_slippage_log", "market_execution_context"):
            r = store.fetch_one(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,))
            assert r is not None, f"{tbl} not created"
        assert store.get_schema_version() == EXPECTED_SCHEMA_VERSION
        # best-effort insert round-trips
        assert store.insert_order_execution_log(
            {"symbol": "X", "leg": "ENTRY", "actual_price": 100.0}) is True
    finally:
        store.close()
