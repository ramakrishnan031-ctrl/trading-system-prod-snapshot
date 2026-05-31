"""Tests for FIX-134 Item 39: SL breach detection via tick feed."""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.state_store import StateStore
from core.time_authority import today_ist
from orders.sl_breach_monitor import SlBreachMonitor


@pytest.fixture()
def store(tmp_path: Path) -> StateStore:
    return StateStore(db_path=tmp_path / "test.db")


def _insert_open_trade(store, symbol, direction, sl_initial, qty_filled=10):
    ts = f"{today_ist()}T10:00:00+05:30"
    trade_id = str(uuid.uuid4())
    signal_id = str(uuid.uuid4())
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals
               (signal_id, symbol, scanner, strategy, triggered_at, received_at,
                expires_at, status, fingerprint, fingerprint_date, trigger_price)
               VALUES (?, ?, 'test', 'test', ?, ?, ?, 'TRADED', ?, ?, 100.0)""",
            (signal_id, symbol, ts, ts, ts, f"fp-{signal_id}", today_ist()),
        )
        cur.execute(
            """INSERT INTO trades
               (trade_id, signal_id, symbol, direction, strategy, qty_planned,
                qty_filled, entry_target_price, sl_initial, tgt_initial,
                margin_reserved, risk_amount, created_at, status,
                order_protocol, updated_at)
               VALUES (?, ?, ?, ?, 'test', ?, ?, 100.0, ?, 110.0,
                       2000.0, 500.0, ?, 'OPEN', 'LIMIT_TRIPLE', ?)""",
            (trade_id, signal_id, symbol, direction, qty_filled, qty_filled,
             sl_initial, ts, ts),
        )
    return trade_id


def _insert_sl_order(store, trade_id, status="TRIGGER PENDING"):
    ts = f"{today_ist()}T10:01:00+05:30"
    order_id = f"SL-{uuid.uuid4()}"
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO orders
               (order_id, trade_id, leg, transaction_type, order_type,
                product, variety, qty_requested, status, placed_at, updated_at)
               VALUES (?, ?, 'SL', 'SELL', 'SL-M', 'MIS', 'regular', 10, ?, ?, ?)""",
            (order_id, trade_id, status, ts, ts),
        )
    return order_id


def _make_monitor(store, mode="PAPER", adapter=None, notifier=None):
    return SlBreachMonitor(
        state_store=store,
        adapter=adapter,
        logger=logging.getLogger("test_sl_breach"),
        notifier=notifier,
        mode=mode,
        check_interval_sec=0,
    )


# ── Exposed trade detection ──────────────────────────────────────────────


class TestExposedTradeDetection:
    def test_no_trades_returns_empty(self, store):
        mon = _make_monitor(store)
        exposed = mon._get_exposed_trades()
        assert exposed == []

    def test_trade_with_active_sl_not_exposed(self, store):
        tid = _insert_open_trade(store, "RELIANCE", "LONG", 95.0)
        _insert_sl_order(store, tid, "TRIGGER PENDING")
        mon = _make_monitor(store)
        exposed = mon._get_exposed_trades()
        assert len(exposed) == 0

    def test_trade_without_sl_is_exposed(self, store):
        tid = _insert_open_trade(store, "INFY", "LONG", 95.0)
        mon = _make_monitor(store)
        exposed = mon._get_exposed_trades()
        assert len(exposed) == 1
        assert exposed[0]["trade_id"] == tid

    def test_trade_with_cancelled_sl_is_exposed(self, store):
        tid = _insert_open_trade(store, "TCS", "SHORT", 105.0)
        _insert_sl_order(store, tid, "CANCELLED")
        mon = _make_monitor(store)
        exposed = mon._get_exposed_trades()
        assert len(exposed) == 1

    def test_closed_trade_not_exposed(self, store):
        tid = _insert_open_trade(store, "HDFC", "LONG", 95.0)
        with store.transaction() as cur:
            cur.execute("UPDATE trades SET status = 'CLOSED' WHERE trade_id = ?", (tid,))
        mon = _make_monitor(store)
        exposed = mon._get_exposed_trades()
        assert len(exposed) == 0

    def test_zero_qty_not_exposed(self, store):
        _insert_open_trade(store, "SBIN", "LONG", 95.0, qty_filled=0)
        mon = _make_monitor(store)
        exposed = mon._get_exposed_trades()
        assert len(exposed) == 0


# ── Breach detection ─────────────────────────────────────────────────────


class TestBreachDetection:
    def test_long_ltp_below_sl_triggers(self, store):
        tid = _insert_open_trade(store, "RELIANCE", "LONG", 95.0)
        mon = _make_monitor(store)
        mon.set_token_map({12345: "RELIANCE"})
        mon.on_tick({"instrument_token": 12345, "last_price": 94.0})
        assert mon.fired_count == 1

    def test_long_ltp_above_sl_no_trigger(self, store):
        _insert_open_trade(store, "RELIANCE", "LONG", 95.0)
        mon = _make_monitor(store)
        mon.set_token_map({12345: "RELIANCE"})
        mon.on_tick({"instrument_token": 12345, "last_price": 100.0})
        assert mon.fired_count == 0

    def test_short_ltp_above_sl_triggers(self, store):
        tid = _insert_open_trade(store, "INFY", "SHORT", 105.0)
        mon = _make_monitor(store)
        mon.set_token_map({67890: "INFY"})
        mon.on_tick({"instrument_token": 67890, "last_price": 106.0})
        assert mon.fired_count == 1

    def test_short_ltp_below_sl_no_trigger(self, store):
        _insert_open_trade(store, "INFY", "SHORT", 105.0)
        mon = _make_monitor(store)
        mon.set_token_map({67890: "INFY"})
        mon.on_tick({"instrument_token": 67890, "last_price": 100.0})
        assert mon.fired_count == 0

    def test_breach_with_active_sl_no_trigger(self, store):
        tid = _insert_open_trade(store, "RELIANCE", "LONG", 95.0)
        _insert_sl_order(store, tid, "TRIGGER PENDING")
        mon = _make_monitor(store)
        mon.set_token_map({12345: "RELIANCE"})
        mon.on_tick({"instrument_token": 12345, "last_price": 94.0})
        assert mon.fired_count == 0

    def test_no_duplicate_fire(self, store):
        _insert_open_trade(store, "TCS", "LONG", 95.0)
        mon = _make_monitor(store)
        mon.set_token_map({11111: "TCS"})
        mon.on_tick({"instrument_token": 11111, "last_price": 94.0})
        mon.on_tick({"instrument_token": 11111, "last_price": 93.0})
        assert mon.fired_count == 1


# ── Emergency exit behavior ──────────────────────────────────────────────


class TestEmergencyExit:
    def test_paper_mode_no_order_placed(self, store):
        adapter = MagicMock()
        _insert_open_trade(store, "RELIANCE", "LONG", 95.0)
        mon = _make_monitor(store, mode="PAPER", adapter=adapter)
        mon.set_token_map({12345: "RELIANCE"})
        mon.on_tick({"instrument_token": 12345, "last_price": 94.0})
        adapter.place_order.assert_not_called()

    def test_live_mode_places_market_order(self, store):
        adapter = MagicMock()
        adapter.place_order.return_value = "BROKER-123"
        _insert_open_trade(store, "RELIANCE", "LONG", 95.0)
        mon = _make_monitor(store, mode="LIVE", adapter=adapter)
        mon.set_token_map({12345: "RELIANCE"})
        mon.on_tick({"instrument_token": 12345, "last_price": 94.0})
        adapter.place_order.assert_called_once()
        call_kwargs = adapter.place_order.call_args.kwargs
        assert call_kwargs["order_type"] == "MARKET"
        assert call_kwargs["transaction_type"] == "SELL"
        assert call_kwargs["symbol"] == "RELIANCE"

    def test_live_short_exit_side_is_buy(self, store):
        adapter = MagicMock()
        adapter.place_order.return_value = "BROKER-456"
        _insert_open_trade(store, "INFY", "SHORT", 105.0)
        mon = _make_monitor(store, mode="LIVE", adapter=adapter)
        mon.set_token_map({67890: "INFY"})
        mon.on_tick({"instrument_token": 67890, "last_price": 106.0})
        call_kwargs = adapter.place_order.call_args.kwargs
        assert call_kwargs["transaction_type"] == "BUY"

    def test_telegram_alert_sent(self, store):
        notifier = MagicMock()
        _insert_open_trade(store, "TCS", "LONG", 95.0)
        mon = _make_monitor(store, mode="PAPER", notifier=notifier)
        mon.set_token_map({11111: "TCS"})
        mon.on_tick({"instrument_token": 11111, "last_price": 94.0})
        assert notifier.send.called
        call_kwargs = notifier.send.call_args.kwargs
        assert "EMERGENCY" in call_kwargs["title"]
        assert call_kwargs["severity"] == "ERROR"

    def test_notifier_failure_silenced(self, store):
        notifier = MagicMock()
        notifier.send.side_effect = RuntimeError("Telegram down")
        _insert_open_trade(store, "SBIN", "LONG", 95.0)
        mon = _make_monitor(store, mode="PAPER", notifier=notifier)
        mon.set_token_map({22222: "SBIN"})
        mon.on_tick({"instrument_token": 22222, "last_price": 94.0})
        assert mon.fired_count == 1

    def test_db_updated_with_emergency_reason(self, store):
        _insert_open_trade(store, "RELIANCE", "LONG", 95.0)
        mon = _make_monitor(store, mode="PAPER")
        mon.set_token_map({12345: "RELIANCE"})
        mon.on_tick({"instrument_token": 12345, "last_price": 94.0})
        row = store.fetch_one(
            "SELECT exit_reason, exit_price FROM trades WHERE symbol = 'RELIANCE'"
        )
        assert row["exit_reason"] == "EMERGENCY_SL_TICK"
        assert row["exit_price"] == 94.0

    def test_adapter_failure_logged_not_raised(self, store):
        adapter = MagicMock()
        adapter.place_order.side_effect = RuntimeError("Broker down")
        _insert_open_trade(store, "RELIANCE", "LONG", 95.0)
        mon = _make_monitor(store, mode="LIVE", adapter=adapter)
        mon.set_token_map({12345: "RELIANCE"})
        mon.on_tick({"instrument_token": 12345, "last_price": 94.0})
        assert mon.fired_count == 1

    def test_reset_fired_allows_refire(self, store):
        _insert_open_trade(store, "TCS", "LONG", 95.0)
        mon = _make_monitor(store, mode="PAPER")
        mon.set_token_map({11111: "TCS"})
        mon.on_tick({"instrument_token": 11111, "last_price": 94.0})
        assert mon.fired_count == 1
        mon.reset_fired()
        assert mon.fired_count == 0


# ── Edge cases ────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_unknown_token_ignored(self, store):
        _insert_open_trade(store, "RELIANCE", "LONG", 95.0)
        mon = _make_monitor(store)
        mon.set_token_map({99999: "OTHER"})
        mon.on_tick({"instrument_token": 99999, "last_price": 94.0})
        assert mon.fired_count == 0

    def test_zero_ltp_ignored(self, store):
        _insert_open_trade(store, "RELIANCE", "LONG", 95.0)
        mon = _make_monitor(store)
        mon.set_token_map({12345: "RELIANCE"})
        mon.on_tick({"instrument_token": 12345, "last_price": 0.0})
        assert mon.fired_count == 0

    def test_missing_token_field_ignored(self, store):
        mon = _make_monitor(store)
        mon.on_tick({"last_price": 100.0})
        assert mon.fired_count == 0

    def test_ltp_equals_sl_triggers_long(self, store):
        _insert_open_trade(store, "RELIANCE", "LONG", 95.0)
        mon = _make_monitor(store)
        mon.set_token_map({12345: "RELIANCE"})
        mon.on_tick({"instrument_token": 12345, "last_price": 95.0})
        assert mon.fired_count == 1

    def test_ltp_equals_sl_triggers_short(self, store):
        _insert_open_trade(store, "INFY", "SHORT", 105.0)
        mon = _make_monitor(store)
        mon.set_token_map({67890: "INFY"})
        mon.on_tick({"instrument_token": 67890, "last_price": 105.0})
        assert mon.fired_count == 1
