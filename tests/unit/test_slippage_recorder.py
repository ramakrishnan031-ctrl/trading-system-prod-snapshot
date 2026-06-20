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
    def __init__(self):
        self.oel = []
        self.tsl = []
        self.mec = []
        self.trade = None

    def insert_order_execution_log(self, row):
        self.oel.append(row); return True

    def insert_trade_slippage_log(self, row):
        self.tsl.append(row); return True

    def insert_market_execution_context(self, row):
        self.mec.append(row); return True

    def fetch_one(self, sql, params=()):
        if "FROM orders" in sql:
            return {"leg": "ENTRY", "order_type": "LIMIT", "qty_requested": 1}
        if "entry_target_price" in sql:
            return self.trade
        if "FROM trades" in sql:
            return {"strategy": "gap_go_long"}
        return None


class _FakeBus:
    def __init__(self):
        self.subs = {}

    def subscribe(self, et, h, async_dispatch=False):
        self.subs[et.__name__] = h


def _log():
    return logging.getLogger("test_slip")


def test_recorder_subscribes_and_records_order_fill():
    store, bus = _FakeStore(), _FakeBus()
    SlippageRecorder(store, bus, _log(), price_bands=_BANDS)
    assert "OrderFilled" in bus.subs and "PositionClosed" in bus.subs
    bus.subs["OrderFilled"](OrderFilled(
        source_module="test", symbol="X", side="BUY", filled_qty=1,
        avg_fill_price=101.0, expected_price=100.0, slippage_pct=1.0,
        internal_order_id="o1", trade_id="t1", filled_at="2026-06-20T10:00:00"))
    assert len(store.oel) == 1 and store.oel[0]["leg"] == "ENTRY"
    assert round(store.oel[0]["slippage_rs"], 2) == 1.0
    assert len(store.mec) == 1   # context row (NULL bid/ask, no adapter)


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


# ── schema v31 ───────────────────────────────────────────────────────────────

def test_schema_v31_tables_and_version(tmp_path):
    from core.state_store import EXPECTED_SCHEMA_VERSION, StateStore
    assert EXPECTED_SCHEMA_VERSION == 31
    store = StateStore(tmp_path / "v31.db")
    try:
        for tbl in ("order_execution_log", "trade_slippage_log", "market_execution_context"):
            r = store.fetch_one(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,))
            assert r is not None, f"{tbl} not created"
        assert store.get_schema_version() == 31
        # best-effort insert round-trips
        assert store.insert_order_execution_log(
            {"symbol": "X", "leg": "ENTRY", "actual_price": 100.0}) is True
    finally:
        store.close()
