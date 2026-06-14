"""Tests for FIX-135 Item 41: shadow paper engine parallel to live."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from core.state_store import StateStore
from core.time_authority import today_ist
from capital.shadow_engine import ShadowEngine, ShadowTrade


@pytest.fixture()
def store(tmp_path: Path) -> StateStore:
    s = StateStore(db_path=tmp_path / "test.db")
    # O1 (v26): shadow_trades.signal_id and .live_trade_id are now FKs. These
    # tests use synthetic ids (sig-1..sig-16, trade-1); seed the parent rows so
    # record_shadow_trade persists (in production the signal/trade always exist).
    with s.transaction() as cur:
        for n in range(1, 17):
            cur.execute(
                "INSERT OR IGNORE INTO signals(signal_id,symbol,scanner,strategy,"
                "triggered_at,received_at,expires_at,status,fingerprint,fingerprint_date) "
                "VALUES(?,?,'sc','st','t','t','t','TRADED',?,?)",
                (f"sig-{n}", "X", f"fp-sig-{n}", "2026-04-16"),
            )
        cur.execute(
            "INSERT OR IGNORE INTO signals(signal_id,symbol,scanner,strategy,"
            "triggered_at,received_at,expires_at,status,fingerprint,fingerprint_date) "
            "VALUES('sig-for-trade-1','X','sc','st','t','t','t','TRADED','fp-t1','2026-04-16')"
        )
        cur.execute(
            "INSERT OR IGNORE INTO trades(trade_id,signal_id,symbol,direction,strategy,"
            "qty_planned,entry_target_price,sl_initial,tgt_initial,margin_reserved,"
            "risk_amount,created_at,updated_at,status,order_protocol) "
            "VALUES('trade-1','sig-for-trade-1','X','LONG','st',1,100,95,110,20,5,"
            "'t','t','OPEN','CO_PLUS_TGT')"
        )
    return s


def _make_engine(store, enabled=True):
    return ShadowEngine(
        state_store=store,
        logger=logging.getLogger("test_shadow"),
        enabled=enabled,
    )


# ── Recording ─────────────────────────────────────────────────────────────


class TestRecordShadowTrade:
    def test_records_and_returns_id(self, store):
        eng = _make_engine(store)
        sid = eng.record_shadow_trade(
            signal_id="sig-1", symbol="RELIANCE", strategy="gap_go_long",
            direction="LONG", entry_price=2500.0, qty=10,
            sys_sl=2450.0, sys_tgt=2600.0,
            live_trade_id="trade-1", live_status="TRADED",
        )
        assert sid is not None
        assert eng.active_count == 1

    def test_persists_to_db(self, store):
        eng = _make_engine(store)
        sid = eng.record_shadow_trade(
            signal_id="sig-2", symbol="INFY", strategy="first_pullback_long",
            direction="LONG", entry_price=1500.0, qty=5,
            sys_sl=1470.0, sys_tgt=1560.0,
        )
        row = store.fetch_one(
            "SELECT * FROM shadow_trades WHERE shadow_trade_id = ?", (sid,)
        )
        assert row is not None
        assert row["symbol"] == "INFY"
        assert row["entry_price"] == 1500.0
        assert row["sys_sl"] == 1470.0
        assert row["date"] == today_ist()

    def test_disabled_returns_none(self, store):
        eng = _make_engine(store, enabled=False)
        sid = eng.record_shadow_trade(
            signal_id="sig-3", symbol="TCS", strategy="test",
            direction="LONG", entry_price=100.0, qty=1,
            sys_sl=95.0, sys_tgt=110.0,
        )
        assert sid is None
        assert eng.active_count == 0

    def test_rejected_signal_recorded(self, store):
        eng = _make_engine(store)
        sid = eng.record_shadow_trade(
            signal_id="sig-4", symbol="HDFC", strategy="test",
            direction="SHORT", entry_price=2800.0, qty=3,
            sys_sl=2850.0, sys_tgt=2700.0,
            live_trade_id=None, live_status="REJECTED",
        )
        assert sid is not None
        row = store.fetch_one(
            "SELECT live_status FROM shadow_trades WHERE shadow_trade_id = ?", (sid,)
        )
        assert row["live_status"] == "REJECTED"


# ── Tick-based SL/TGT ────────────────────────────────────────────────────


class TestTickSimulation:
    def test_long_sl_hit(self, store):
        eng = _make_engine(store)
        eng.set_token_map({12345: "RELIANCE"})
        eng.record_shadow_trade(
            signal_id="sig-5", symbol="RELIANCE", strategy="test",
            direction="LONG", entry_price=2500.0, qty=10,
            sys_sl=2450.0, sys_tgt=2600.0,
        )
        eng.on_tick({"instrument_token": 12345, "last_price": 2440.0})
        assert eng.active_count == 1
        row = store.fetch_one(
            "SELECT simulated_exit_reason, simulated_pnl FROM shadow_trades WHERE signal_id = 'sig-5'"
        )
        assert row["simulated_exit_reason"] == "SL_HIT"
        assert row["simulated_pnl"] == (2440.0 - 2500.0) * 10

    def test_long_tgt_hit(self, store):
        eng = _make_engine(store)
        eng.set_token_map({12345: "RELIANCE"})
        eng.record_shadow_trade(
            signal_id="sig-6", symbol="RELIANCE", strategy="test",
            direction="LONG", entry_price=2500.0, qty=10,
            sys_sl=2450.0, sys_tgt=2600.0,
        )
        eng.on_tick({"instrument_token": 12345, "last_price": 2610.0})
        row = store.fetch_one(
            "SELECT simulated_exit_reason FROM shadow_trades WHERE signal_id = 'sig-6'"
        )
        assert row["simulated_exit_reason"] == "TGT_HIT"

    def test_short_sl_hit(self, store):
        eng = _make_engine(store)
        eng.set_token_map({67890: "INFY"})
        eng.record_shadow_trade(
            signal_id="sig-7", symbol="INFY", strategy="test",
            direction="SHORT", entry_price=1500.0, qty=5,
            sys_sl=1550.0, sys_tgt=1400.0,
        )
        eng.on_tick({"instrument_token": 67890, "last_price": 1560.0})
        row = store.fetch_one(
            "SELECT simulated_exit_reason, simulated_pnl FROM shadow_trades WHERE signal_id = 'sig-7'"
        )
        assert row["simulated_exit_reason"] == "SL_HIT"
        assert row["simulated_pnl"] == (1500.0 - 1560.0) * 5

    def test_short_tgt_hit(self, store):
        eng = _make_engine(store)
        eng.set_token_map({67890: "INFY"})
        eng.record_shadow_trade(
            signal_id="sig-8", symbol="INFY", strategy="test",
            direction="SHORT", entry_price=1500.0, qty=5,
            sys_sl=1550.0, sys_tgt=1400.0,
        )
        eng.on_tick({"instrument_token": 67890, "last_price": 1390.0})
        row = store.fetch_one(
            "SELECT simulated_exit_reason FROM shadow_trades WHERE signal_id = 'sig-8'"
        )
        assert row["simulated_exit_reason"] == "TGT_HIT"

    def test_no_hit_stays_open(self, store):
        eng = _make_engine(store)
        eng.set_token_map({12345: "RELIANCE"})
        eng.record_shadow_trade(
            signal_id="sig-9", symbol="RELIANCE", strategy="test",
            direction="LONG", entry_price=2500.0, qty=10,
            sys_sl=2450.0, sys_tgt=2600.0,
        )
        eng.on_tick({"instrument_token": 12345, "last_price": 2510.0})
        row = store.fetch_one(
            "SELECT simulated_exit_reason FROM shadow_trades WHERE signal_id = 'sig-9'"
        )
        assert row["simulated_exit_reason"] is None

    def test_unknown_token_ignored(self, store):
        eng = _make_engine(store)
        eng.set_token_map({99999: "OTHER"})
        eng.record_shadow_trade(
            signal_id="sig-10", symbol="RELIANCE", strategy="test",
            direction="LONG", entry_price=2500.0, qty=10,
            sys_sl=2450.0, sys_tgt=2600.0,
        )
        eng.on_tick({"instrument_token": 99999, "last_price": 100.0})
        row = store.fetch_one(
            "SELECT simulated_exit_reason FROM shadow_trades WHERE signal_id = 'sig-10'"
        )
        assert row["simulated_exit_reason"] is None

    def test_disabled_ignores_ticks(self, store):
        eng = _make_engine(store, enabled=False)
        eng.on_tick({"instrument_token": 12345, "last_price": 100.0})


# ── EOD close ─────────────────────────────────────────────────────────────


class TestEodClose:
    def test_close_all_eod(self, store):
        eng = _make_engine(store)
        eng.record_shadow_trade(
            signal_id="sig-11", symbol="RELIANCE", strategy="test",
            direction="LONG", entry_price=2500.0, qty=10,
            sys_sl=2450.0, sys_tgt=2600.0,
        )
        eng.record_shadow_trade(
            signal_id="sig-12", symbol="INFY", strategy="test",
            direction="SHORT", entry_price=1500.0, qty=5,
            sys_sl=1550.0, sys_tgt=1400.0,
        )
        closed = eng.close_all_eod(eod_price_map={"RELIANCE": 2520.0, "INFY": 1480.0})
        assert closed == 2
        rows = store.fetch_all(
            "SELECT simulated_exit_reason FROM shadow_trades WHERE date = ?",
            (today_ist(),),
        )
        assert all(r["simulated_exit_reason"] == "EOD" for r in rows)

    def test_eod_with_price_map(self, store):
        eng = _make_engine(store)
        eng.record_shadow_trade(
            signal_id="sig-13", symbol="RELIANCE", strategy="test",
            direction="LONG", entry_price=2500.0, qty=10,
            sys_sl=2450.0, sys_tgt=2600.0,
        )
        eng.close_all_eod(eod_price_map={"RELIANCE": 2520.0})
        row = store.fetch_one(
            "SELECT simulated_pnl FROM shadow_trades WHERE signal_id = 'sig-13'"
        )
        assert row["simulated_pnl"] == (2520.0 - 2500.0) * 10

    def test_already_closed_not_double_closed(self, store):
        eng = _make_engine(store)
        eng.set_token_map({12345: "RELIANCE"})
        eng.record_shadow_trade(
            signal_id="sig-14", symbol="RELIANCE", strategy="test",
            direction="LONG", entry_price=2500.0, qty=10,
            sys_sl=2450.0, sys_tgt=2600.0,
        )
        eng.on_tick({"instrument_token": 12345, "last_price": 2440.0})
        closed = eng.close_all_eod()
        assert closed == 0

    def test_disabled_returns_zero(self, store):
        eng = _make_engine(store, enabled=False)
        assert eng.close_all_eod() == 0


# ── Daily summary ─────────────────────────────────────────────────────────


class TestDailySummary:
    def test_returns_all_trades_for_date(self, store):
        eng = _make_engine(store)
        eng.record_shadow_trade(
            signal_id="sig-15", symbol="TCS", strategy="test",
            direction="LONG", entry_price=3500.0, qty=2,
            sys_sl=3450.0, sys_tgt=3600.0,
        )
        eng.record_shadow_trade(
            signal_id="sig-16", symbol="SBIN", strategy="test",
            direction="SHORT", entry_price=600.0, qty=20,
            sys_sl=620.0, sys_tgt=560.0,
        )
        summary = eng.get_daily_summary()
        assert len(summary) == 2

    def test_empty_date_returns_empty(self, store):
        eng = _make_engine(store)
        summary = eng.get_daily_summary("2020-01-01")
        assert summary == []
