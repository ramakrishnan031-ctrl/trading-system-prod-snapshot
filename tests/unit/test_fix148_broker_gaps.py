"""
tests/unit/test_fix148_broker_gaps.py -- FIX-148 Broker Integration Gaps

Tests for all 6 priorities:
  P1 (GAP 5): RMS auto-squareoff — real exit price, orphan cancel, capital release
  P2 (GAP 4): SL cancelled by exchange — emergency market close
  P3 (GAP 1): SL placement failure — emergency market exit
  P4 (A2):    Network partition recovery — failure counter logging
  P5 (A3):    Partial RMS exit — qty update, stale SL cancel
  P6 (GAP 2): BreakevenManager retry + alert
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Optional
from unittest.mock import MagicMock, Mock, patch, call

import pytest

from core.state_store import StateStore
from core.events import EventBus


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_store(tmp: Path) -> StateStore:
    store = StateStore(tmp / "test.db")
    return store


_NOW = "2026-06-02T10:00:00+05:30"


def _seed_open_trade(store: StateStore, trade_id="T001", symbol="RELIANCE",
                     direction="LONG", qty=100, entry_price=2500.0,
                     signal_id="SIG001") -> None:
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO signals
              (signal_id, symbol, scanner, strategy, triggered_at, received_at,
               expires_at, status, fingerprint, fingerprint_date)
            VALUES (?, ?, 'SCANNER', 'strategy', ?, ?, ?, 'TRADED', ?, '2026-06-02')""",
            (signal_id, symbol, _NOW, _NOW, _NOW, f"fp_{trade_id}"),
        )
        cur.execute(
            """INSERT INTO trades
              (trade_id, signal_id, symbol, direction, strategy, sector,
               qty_planned, qty_filled, entry_target_price, sl_initial,
               tgt_initial, margin_reserved, risk_amount, created_at,
               status, order_protocol, updated_at, entry_actual_price)
            VALUES (?, ?, ?, ?, 'strategy', 'ENERGY',
                    ?, ?, ?, 2450.0, 2600.0, 10000.0, 500.0, ?,
                    'OPEN', 'LIMIT_TRIPLE', ?, ?)""",
            (trade_id, signal_id, symbol, direction,
             qty, qty, entry_price, _NOW, _NOW, entry_price),
        )
        # Insert an ENTRY order so get_all_open_trades JOIN returns product
        cur.execute(
            """INSERT INTO orders
              (order_id, trade_id, leg, transaction_type, order_type, product,
               variety, qty_requested, status, placed_at, updated_at)
            VALUES (?, ?, 'ENTRY', 'BUY', 'LIMIT', 'MIS', 'regular', ?, 'COMPLETE', ?, ?)""",
            (f"ENTRY_{trade_id}", trade_id, qty, _NOW, _NOW),
        )


def _seed_sl_order(store: StateStore, trade_id="T001", broker_order_id="SL001",
                   status="OPEN") -> None:
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO orders
              (order_id, trade_id, leg, transaction_type, order_type, product,
               variety, qty_requested, trigger_price, status, placed_at, updated_at)
            VALUES (?, ?, 'SL', 'SELL', 'SL-M', 'MIS', 'regular', 100, 2450.0, ?, ?, ?)""",
            (broker_order_id, trade_id, status, _NOW, _NOW),
        )


def _seed_tgt_order(store: StateStore, trade_id="T001", broker_order_id="TGT001",
                    status="OPEN") -> None:
    with store.transaction() as cur:
        cur.execute(
            """INSERT INTO orders
              (order_id, trade_id, leg, transaction_type, order_type, product,
               variety, qty_requested, price, status, placed_at, updated_at)
            VALUES (?, ?, 'TGT', 'SELL', 'LIMIT', 'MIS', 'regular', 100, 2600.0, ?, ?, ?)""",
            (broker_order_id, trade_id, status, _NOW, _NOW),
        )


class _FakeKillSwitch:
    def __init__(self):
        self.soft_kill_calls = []
        self.hard_kill_calls = []

    def soft_kill(self, reason="", triggered_by=""):
        self.soft_kill_calls.append({"reason": reason, "triggered_by": triggered_by})

    def hard_kill(self, reason="", triggered_by=""):
        self.hard_kill_calls.append({"reason": reason, "triggered_by": triggered_by})

    def is_active(self, scope=""):
        return False


class _FakeFundManager:
    def __init__(self):
        self.released = []
        self.release_result = MagicMock(pnl_delta=-250.0)

    def release_used(self, **kwargs):
        self.released.append(kwargs)
        return self.release_result

    def get_snapshot(self):
        return MagicMock(total=100000.0)


class _FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, severity="", title="", body="", source_module=""):
        self.sent.append({"severity": severity, "title": title, "body": body})


class _FakeAdapter:
    def __init__(self):
        self.cancelled_orders = []
        self.placed_orders = []
        self._trades = []
        self._positions = []
        self._quotes = {}

    def cancel_order(self, broker_order_id):
        self.cancelled_orders.append(broker_order_id)
        return MagicMock(success=True)

    def place_order(self, **kwargs):
        self.placed_orders.append(kwargs)
        return MagicMock(broker_order_id=f"PLACED_{len(self.placed_orders)}", internal_order_id="INT_001")

    def get_trades(self):
        return self._trades

    def get_positions(self):
        return self._positions

    def get_margins(self):
        return MagicMock(net=100000.0)

    def get_open_orders(self):
        return []

    def modify_order(self, broker_order_id, **kwargs):
        return MagicMock(success=True)


@dataclass
class _FakeQuote:
    symbol: str
    last_price: float


def _make_reconciler(store, adapter=None, fm=None, ks=None, notifier=None,
                     bus=None, quote_fn=None, broker_orders_fn=None):
    from core.config_loader import OrderReconcilerConfig
    from orders.order_reconciler import OrderReconciler

    adapter = adapter or _FakeAdapter()
    fm = fm or _FakeFundManager()
    ks = ks or _FakeKillSwitch()
    notifier = notifier or _FakeNotifier()
    bus = bus or EventBus()
    cfg = OrderReconcilerConfig(poll_interval_sec=15, capital_drift_tolerance=500.0)

    def default_quote(symbols):
        return {s: _FakeQuote(symbol=s, last_price=2400.0) for s in symbols}

    return OrderReconciler(
        state_store=store,
        adapter=adapter,
        fund_manager=fm,
        kill_switch=ks,
        notifier=notifier,
        bus=bus,
        logger=MagicMock(),
        cfg=cfg,
        quote_fn=quote_fn or default_quote,
        broker_orders_fn=broker_orders_fn or adapter.get_open_orders,
        mode="PAPER",
    )


# ═════════════════════════════════════════════════════════════════════════════
# P1: GAP 5 — RMS Auto-Squareoff
# ═════════════════════════════════════════════════════════════════════════════

class TestGap5RmsSquareoff:

    def _get_trade(self, store):
        """Helper: get trade dict via get_all_open_trades (includes JOIN columns)."""
        trades = store.get_all_open_trades()
        return dict(trades[0]) if trades else None

    def test_check1_fetches_real_exit_price_from_broker_trades(self):
        """RMS exit detected: exit_price from broker trades(), not breakeven."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            _seed_open_trade(store)
            _seed_sl_order(store)

            adapter = _FakeAdapter()
            adapter._trades = [
                {"tradingsymbol": "RELIANCE", "transaction_type": "SELL",
                 "quantity": 100, "average_price": 2350.0, "order_id": "EXT001",
                 "trade_id": "BT001", "fill_timestamp": ""},
            ]

            fm = _FakeFundManager()
            reconciler = _make_reconciler(store, adapter=adapter, fm=fm)

            trade = self._get_trade(store)
            action = reconciler._check1_manual_close(trade)

            assert action.check_name == "MANUAL_CLOSE"
            assert len(fm.released) == 1
            assert fm.released[0]["exit_price"] == 2350.0
            assert "2350.00" in action.description
            store.close()

    def test_check1_falls_back_to_ltp_when_no_broker_trades(self):
        """No broker trades -> uses LTP as exit price."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            _seed_open_trade(store)

            adapter = _FakeAdapter()
            adapter._trades = []

            def quote_fn(symbols):
                return {"RELIANCE": _FakeQuote("RELIANCE", 2380.0)}

            fm = _FakeFundManager()
            reconciler = _make_reconciler(store, adapter=adapter, fm=fm,
                                          quote_fn=quote_fn)

            trade = self._get_trade(store)
            action = reconciler._check1_manual_close(trade)

            assert len(fm.released) == 1
            assert fm.released[0]["exit_price"] == 2380.0
            store.close()

    def test_check1_cancels_orphaned_sl_tgt_orders(self):
        """RMS exit: orphaned SL/TGT orders are cancelled at broker."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            _seed_open_trade(store)
            _seed_sl_order(store, broker_order_id="SL001")
            _seed_tgt_order(store, broker_order_id="TGT001")

            adapter = _FakeAdapter()
            adapter._trades = [
                {"tradingsymbol": "RELIANCE", "transaction_type": "SELL",
                 "quantity": 100, "average_price": 2350.0, "order_id": "EXT001",
                 "trade_id": "BT001", "fill_timestamp": ""},
            ]

            reconciler = _make_reconciler(store, adapter=adapter)

            trade = self._get_trade(store)
            reconciler._check1_manual_close(trade)

            assert "SL001" in adapter.cancelled_orders
            assert "TGT001" in adapter.cancelled_orders
            store.close()

    def test_check1_sends_critical_telegram(self):
        """RMS exit sends CRITICAL Telegram alert with PnL."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            _seed_open_trade(store)

            adapter = _FakeAdapter()
            adapter._trades = [
                {"tradingsymbol": "RELIANCE", "transaction_type": "SELL",
                 "quantity": 100, "average_price": 2350.0, "order_id": "EXT001",
                 "trade_id": "BT001", "fill_timestamp": ""},
            ]

            notifier = _FakeNotifier()
            reconciler = _make_reconciler(store, adapter=adapter, notifier=notifier)

            trade = self._get_trade(store)
            reconciler._check1_manual_close(trade)

            assert len(notifier.sent) >= 1
            alert = notifier.sent[0]
            assert alert["severity"] == "CRITICAL"
            assert "RELIANCE" in alert["title"]
            assert "2350.00" in alert["body"]
            store.close()

    def test_check1_short_trade_resolves_buy_exit(self):
        """SHORT trade: exit resolved from BUY trades in broker history."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            _seed_open_trade(store, direction="SHORT")

            adapter = _FakeAdapter()
            adapter._trades = [
                {"tradingsymbol": "RELIANCE", "transaction_type": "BUY",
                 "quantity": 100, "average_price": 2600.0, "order_id": "EXT002",
                 "trade_id": "BT002", "fill_timestamp": ""},
            ]

            fm = _FakeFundManager()
            reconciler = _make_reconciler(store, adapter=adapter, fm=fm)

            trade = self._get_trade(store)
            reconciler._check1_manual_close(trade)

            assert fm.released[0]["exit_price"] == 2600.0
            store.close()


# ═════════════════════════════════════════════════════════════════════════════
# P2: GAP 4 — Broker-Side SL Cancellation (Emergency Exit)
# ═════════════════════════════════════════════════════════════════════════════

class TestGap4NakedPosition:

    def test_check9_emergency_market_close_on_missing_sl(self):
        """Missing SL detected: emergency MARKET exit placed."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            _seed_open_trade(store)
            _seed_sl_order(store, broker_order_id="SL001")

            adapter = _FakeAdapter()

            def broker_orders_fn():
                return []

            ks = _FakeKillSwitch()
            notifier = _FakeNotifier()
            reconciler = _make_reconciler(
                store, adapter=adapter, ks=ks, notifier=notifier,
                broker_orders_fn=broker_orders_fn,
            )

            trades = store.get_all_open_trades()
            actions = reconciler._check9_missing_exits(trades)

            assert len(actions) == 1
            assert actions[0].check_name == "MISSING_EXITS"
            assert len(adapter.placed_orders) == 1
            placed = adapter.placed_orders[0]
            assert placed["order_type"] == "MARKET"
            assert placed["side"] == "SELL"
            assert placed["qty"] == 100
            assert len(ks.soft_kill_calls) == 1
            assert len(notifier.sent) >= 1
            store.close()

    def test_check9_short_trade_buys_on_emergency(self):
        """SHORT trade: emergency exit places BUY MARKET order."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            _seed_open_trade(store, direction="SHORT")
            _seed_sl_order(store, broker_order_id="SL001")

            adapter = _FakeAdapter()

            def broker_orders_fn():
                return []

            reconciler = _make_reconciler(
                store, adapter=adapter,
                broker_orders_fn=broker_orders_fn,
            )

            trades = store.get_all_open_trades()
            actions = reconciler._check9_missing_exits(trades)

            assert len(adapter.placed_orders) == 1
            assert adapter.placed_orders[0]["side"] == "BUY"
            store.close()

    def test_check9_sends_critical_telegram(self):
        """Missing SL sends CRITICAL Telegram alert."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            _seed_open_trade(store)
            _seed_sl_order(store, broker_order_id="SL001")

            adapter = _FakeAdapter()
            notifier = _FakeNotifier()

            def broker_orders_fn():
                return []

            reconciler = _make_reconciler(
                store, adapter=adapter, notifier=notifier,
                broker_orders_fn=broker_orders_fn,
            )

            trades = store.get_all_open_trades()
            reconciler._check9_missing_exits(trades)

            assert any(a["severity"] == "CRITICAL" for a in notifier.sent)
            assert any("NAKED" in a["title"] for a in notifier.sent)
            store.close()


# ═════════════════════════════════════════════════════════════════════════════
# P3: GAP 1 — SL Placement Failure Emergency Market Exit
# ═════════════════════════════════════════════════════════════════════════════

class TestGap1EmergencyExit:

    def test_emergency_market_exit_places_order(self):
        """Emergency exit places MARKET order via adapter."""
        from orders.order_placer import OrderPlacer, _FillEntry

        placer = OrderPlacer(
            entry_engine=Mock(),
            order_manager=Mock(),
            fund_manager=Mock(),
            bus=Mock(),
            logger=MagicMock(),
            order_monitor=Mock(),
            cost_calculator=Mock(),
            kill_switch=Mock(),
            product_resolver=Mock(resolve=Mock(return_value="MIS")),
            mode="PAPER",
        )

        fill_entry = _FillEntry(
            trade_id="T001", reservation_id="R001", symbol="TESTSTOCK",
            qty=100, leg="ENTRY", order_protocol="LIMIT_TRIPLE",
            direction="LONG", side="BUY", sl_price=95.0,
            tgt_price=105.0, intent="INTRADAY",
        )

        mock_placed = Mock(broker_order_id="EMG_001", internal_order_id="INT_EMG_001")
        # Bug B (P0 2026-06-15): emergency exit goes through self._adapter, NOT
        # self._engine.adapter (FullEntryEngine has no `adapter` attribute).
        placer._adapter = Mock()
        placer._adapter.place_order = Mock(return_value=mock_placed)

        result = placer._emergency_market_exit("T001", fill_entry, 100, "test_reason")

        assert result is True
        call_args = placer._adapter.place_order.call_args
        assert call_args.kwargs["order_type"] == "MARKET"
        assert call_args.kwargs["side"] == "SELL"
        assert call_args.kwargs["qty"] == 100

    def test_emergency_market_exit_returns_false_on_failure(self):
        """Emergency exit returns False if placement raises."""
        from orders.order_placer import OrderPlacer, _FillEntry

        placer = OrderPlacer(
            entry_engine=Mock(),
            order_manager=Mock(),
            fund_manager=Mock(),
            bus=Mock(),
            logger=MagicMock(),
            order_monitor=Mock(),
            cost_calculator=Mock(),
            kill_switch=Mock(),
            mode="PAPER",
        )

        fill_entry = _FillEntry(
            trade_id="T001", reservation_id="R001", symbol="TESTSTOCK",
            qty=100, leg="ENTRY", order_protocol="LIMIT_TRIPLE",
            direction="LONG", side="BUY", sl_price=95.0,
            tgt_price=105.0, intent="INTRADAY",
        )

        # Bug B (P0 2026-06-15): emergency exit uses self._adapter.
        placer._adapter = Mock()
        placer._adapter.place_order = Mock(side_effect=Exception("broker down"))

        result = placer._emergency_market_exit("T001", fill_entry, 100, "test_reason")
        assert result is False


# ═════════════════════════════════════════════════════════════════════════════
# P4: A2 — Network Partition Recovery Logging
# ═════════════════════════════════════════════════════════════════════════════

class TestA2NetworkRecovery:

    def test_monitor_logs_recovery_from_auth_failures(self):
        """OrderMonitor logs when recovering from consecutive auth failures."""
        from broker.order_monitor import OrderMonitor
        from broker.order_state_machine import OrderStateMachine

        adapter = Mock()
        osm = OrderStateMachine()
        bus = EventBus()
        log = MagicMock()

        monitor = OrderMonitor(
            adapter=adapter,
            state_machine=osm,
            bus=bus,
            logger=log,
        )

        # Simulate prior auth failures
        monitor._consecutive_auth_fails = 2
        monitor._consecutive_api_fails = 0

        # Simulate successful poll for an order
        entry = Mock()
        entry.broker_order_id = "ORD001"
        entry.internal_order_id = "INT001"
        entry.symbol = "RELIANCE"
        entry.side = "BUY"
        entry.qty = 100
        entry.expected_price = 2500.0
        entry.leg = "ENTRY"
        entry.filled_qty = 0
        entry.avg_fill_price = 0.0
        from core.time_authority import now_ist
        entry.placed_at = now_ist()
        entry.empty_history_count = 0

        adapter.get_order_history = Mock(return_value=[
            Mock(status="OPEN", filled_quantity=0, average_price=0.0),
        ])

        monitor._process_order(entry)

        # Verify recovery was logged
        assert monitor._consecutive_auth_fails == 0
        log.warning.assert_any_call(
            "order_monitor.recovered_from_failures",
            extra={
                "broker_order_id": "ORD001",
                "prior_auth_fails": 2,
                "prior_api_fails": 0,
            },
        )


# ═════════════════════════════════════════════════════════════════════════════
# P5: A3 — Partial RMS Exit
# ═════════════════════════════════════════════════════════════════════════════

class TestA3PartialRms:

    def _get_trade(self, store):
        trades = store.get_all_open_trades()
        return dict(trades[0]) if trades else None

    def test_check4_updates_qty_and_cancels_stale_orders(self):
        """Partial close: qty updated + stale SL/TGT cancelled."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            _seed_open_trade(store)
            _seed_sl_order(store, broker_order_id="SL001")
            _seed_tgt_order(store, broker_order_id="TGT001")

            adapter = _FakeAdapter()
            notifier = _FakeNotifier()
            reconciler = _make_reconciler(store, adapter=adapter, notifier=notifier)

            trade = self._get_trade(store)
            action = reconciler._check4_partial_close(trade, broker_qty=40)

            assert action.check_name == "PARTIAL_CLOSE"
            updated = store.fetch_one("SELECT qty_filled FROM trades WHERE trade_id='T001'")
            assert updated["qty_filled"] == 40

            assert "SL001" in adapter.cancelled_orders
            assert "TGT001" in adapter.cancelled_orders

            assert len(notifier.sent) >= 1
            assert notifier.sent[0]["severity"] == "WARNING"
            assert "40" in notifier.sent[0]["body"]
            store.close()

    def test_check4_sends_telegram_alert(self):
        """Partial close sends WARNING Telegram."""
        with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            store = _make_store(Path(tmp))
            _seed_open_trade(store)

            notifier = _FakeNotifier()
            reconciler = _make_reconciler(store, notifier=notifier)

            trade = self._get_trade(store)
            reconciler._check4_partial_close(trade, broker_qty=60)

            assert any("PARTIAL CLOSE" in a["title"] for a in notifier.sent)
            store.close()


# ═════════════════════════════════════════════════════════════════════════════
# P6: GAP 2 — BreakevenManager Retry + Alert
# ═════════════════════════════════════════════════════════════════════════════

class TestGap2BreakevenRetry:

    def test_modify_retries_on_exception(self):
        """BreakevenManager retries modify_order on exception."""
        from orders.breakeven_manager import BreakevenManager, _TradeInfo

        adapter = Mock()
        adapter.modify_order = Mock(side_effect=[
            Exception("timeout"),
            Mock(success=True),
        ])
        store = Mock()
        store.fetch_one = Mock(return_value={"broker_order_id": "SL001"})

        mgr = BreakevenManager(
            adapter=adapter, state_store=store, logger=MagicMock(),
            modify_max_retries=3, modify_retry_backoff_sec=0.0,
        )

        info = _TradeInfo(
            trade_id="T001", symbol="RELIANCE", direction="LONG",
            entry_price=2500.0, target_price=2600.0,
            breakeven_trigger_pct=60.0, partial_lock_trigger_pct=80.0,
            partial_lock_sl_pct=40.0,
        )

        mgr._advance_sl("T001", info, 2500.0, "breakeven")

        assert adapter.modify_order.call_count == 2

    def test_modify_alerts_after_exhausted_retries(self):
        """BreakevenManager sends WARNING Telegram after max retries."""
        from orders.breakeven_manager import BreakevenManager, _TradeInfo

        adapter = Mock()
        adapter.modify_order = Mock(return_value=Mock(success=False, reason="broker error"))
        store = Mock()
        store.fetch_one = Mock(return_value={"broker_order_id": "SL001"})
        notifier = _FakeNotifier()

        mgr = BreakevenManager(
            adapter=adapter, state_store=store, logger=MagicMock(),
            notifier=notifier,
            modify_max_retries=2, modify_retry_backoff_sec=0.0,
        )

        info = _TradeInfo(
            trade_id="T001", symbol="RELIANCE", direction="LONG",
            entry_price=2500.0, target_price=2600.0,
            breakeven_trigger_pct=60.0, partial_lock_trigger_pct=80.0,
            partial_lock_sl_pct=40.0,
        )

        mgr._advance_sl("T001", info, 2500.0, "breakeven")

        assert adapter.modify_order.call_count == 2
        assert len(notifier.sent) == 1
        assert notifier.sent[0]["severity"] == "WARNING"
        assert "RELIANCE" in notifier.sent[0]["title"]
        assert "2 exhausted" in notifier.sent[0]["body"]

    def test_modify_success_resets_failure_counter(self):
        """Successful modify resets consecutive failure counter."""
        from orders.breakeven_manager import BreakevenManager, _TradeInfo

        adapter = Mock()
        adapter.modify_order = Mock(return_value=Mock(success=True))
        store = Mock()
        store.fetch_one = Mock(return_value={"broker_order_id": "SL001"})

        mgr = BreakevenManager(
            adapter=adapter, state_store=store, logger=MagicMock(),
            modify_max_retries=3, modify_retry_backoff_sec=0.0,
        )
        mgr._consecutive_failures["T001"] = 5

        info = _TradeInfo(
            trade_id="T001", symbol="RELIANCE", direction="LONG",
            entry_price=2500.0, target_price=2600.0,
            breakeven_trigger_pct=60.0, partial_lock_trigger_pct=80.0,
            partial_lock_sl_pct=40.0,
        )
        mgr._tracked["T001"] = info

        mgr._advance_sl("T001", info, 2500.0, "breakeven")

        assert "T001" not in mgr._consecutive_failures

    def test_modify_no_sl_order_returns_silently(self):
        """No SL order in DB: _advance_sl returns without error."""
        from orders.breakeven_manager import BreakevenManager, _TradeInfo

        adapter = Mock()
        store = Mock()
        store.fetch_one = Mock(return_value=None)

        mgr = BreakevenManager(
            adapter=adapter, state_store=store, logger=MagicMock(),
        )

        info = _TradeInfo(
            trade_id="T001", symbol="RELIANCE", direction="LONG",
            entry_price=2500.0, target_price=2600.0,
            breakeven_trigger_pct=60.0, partial_lock_trigger_pct=80.0,
            partial_lock_sl_pct=40.0,
        )

        mgr._advance_sl("T001", info, 2500.0, "breakeven")
        adapter.modify_order.assert_not_called()


# ═════════════════════════════════════════════════════════════════════════════
# Adapter: get_trades
# ═════════════════════════════════════════════════════════════════════════════

class TestAdapterGetTrades:

    def test_get_trades_paper_mode_returns_empty(self):
        """Paper adapter returns empty list for get_trades."""
        from broker.zerodha_adapter import ZerodhaAdapter
        from broker.order_state_machine import OrderStateMachine
        from broker.cost_calculator import CostCalculator
        from broker.product_resolver import ProductResolver

        adapter = ZerodhaAdapter(
            kite_client=Mock(),
            rate_limiter=Mock(),
            product_resolver=ProductResolver({"zerodha": {"INTRADAY": "MIS"}}),
            cost_calculator=CostCalculator(MagicMock()),
            state_machine=OrderStateMachine(),
            logger=MagicMock(),
            paper_mode=True,
        )
        assert adapter.get_trades() == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
