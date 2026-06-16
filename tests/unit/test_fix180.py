"""
tests/unit/test_fix180.py -- FIX-180 AGY-audit bug fixes.

Covers the new units introduced by FIX-180:
  - Part 12: core.market_windows.is_market_day / is_broker_api_available
  - Bug 4:   StateStore.record_manual_close_financials
  - Part 11: KillSwitch._is_position_flat + _alert_exit_failed (dedup)

Bug 6 (order_placer LTP via adapter) and Bug 7 (sl_breach_monitor place_order
signature) are covered by the updated TestFix128EntrySlippageGuard tests in
test_order_placer.py and TestEmergencyExit in test_fix134_sl_breach.py.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from capital.kill_switch import KillSwitch
from core.events import EventBus
from core.market_windows import is_broker_api_available, is_market_day
from core.state_store import StateStore

from tests.unit.test_state_store import insert_test_trade
from tests.unit.test_order_reconciler import _make_reconciler

IST = timezone(timedelta(hours=5, minutes=30))


def _dt(y, m, d, hh=12, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=IST)


# ─────────────────────────────────────────────────────────────────────────────
# Part 12 — weekend / after-hours broker-API guard
# ─────────────────────────────────────────────────────────────────────────────

class TestBrokerApiAvailability:
    # 2026-06-15 Mon, 16 Tue, 17 Wed, 18 Thu, 19 Fri, 20 Sat, 21 Sun
    def test_weekdays_are_market_days(self):
        for day in (15, 16, 17, 18, 19):
            assert is_market_day(_dt(2026, 6, day)) is True

    def test_weekend_is_not_market_day(self):
        assert is_market_day(_dt(2026, 6, 20)) is False  # Saturday
        assert is_market_day(_dt(2026, 6, 21)) is False  # Sunday

    def test_api_available_weekday_daytime(self):
        assert is_broker_api_available(_dt(2026, 6, 16, 9, 0)) is True   # Tue 09:00
        assert is_broker_api_available(_dt(2026, 6, 17, 14, 0)) is True  # Wed 14:00

    def test_api_unavailable_weekend(self):
        assert is_broker_api_available(_dt(2026, 6, 20, 10, 0)) is False  # Sat
        assert is_broker_api_available(_dt(2026, 6, 21, 10, 0)) is False  # Sun

    def test_friday_before_cutoff_available(self):
        assert is_broker_api_available(_dt(2026, 6, 19, 17, 29)) is True  # Fri 17:29

    def test_friday_at_and_after_cutoff_unavailable(self):
        # Exactly 17:30 and later on Friday -> unavailable (boundary correctness;
        # the AGY-supplied `hour>=17 and minute>=30` form was wrong for 18:00-18:29).
        assert is_broker_api_available(_dt(2026, 6, 19, 17, 30)) is False
        assert is_broker_api_available(_dt(2026, 6, 19, 18, 0)) is False
        assert is_broker_api_available(_dt(2026, 6, 19, 20, 15)) is False

    def test_monday_morning_available(self):
        assert is_broker_api_available(_dt(2026, 6, 15, 9, 0)) is True


# ─────────────────────────────────────────────────────────────────────────────
# Bug 4 — StateStore.record_manual_close_financials
# ─────────────────────────────────────────────────────────────────────────────

class TestManualCloseFinancials:
    def test_records_exit_price_and_pnl_on_closed_manual(self, tmp_path: Path):
        store = StateStore(tmp_path / "t.db")
        insert_test_trade(store, "t1", status="OPEN")
        assert store.mark_trade_manually_closed("t1") is True  # -> CLOSED_MANUAL

        ok = store.record_manual_close_financials(
            "t1", exit_price=2480.0, net_pnl=-200.0,
        )
        assert ok is True

        row = store.fetch_one(
            "SELECT exit_price, net_pnl, gross_pnl, charges, exit_time, status "
            "FROM trades WHERE trade_id = 't1'"
        )
        assert row["status"] == "CLOSED_MANUAL"
        assert row["exit_price"] == 2480.0
        assert row["net_pnl"] == -200.0
        assert row["gross_pnl"] == -200.0      # costs=0.0 -> gross == net
        assert row["charges"] == 0.0
        assert row["exit_time"] is not None
        store.close()

    def test_does_not_touch_non_closed_manual_rows(self, tmp_path: Path):
        store = StateStore(tmp_path / "t.db")
        insert_test_trade(store, "t_open", status="OPEN")
        # Guard: an OPEN (not CLOSED_MANUAL) trade must not be financially mutated.
        assert store.record_manual_close_financials("t_open", 100.0, 5.0) is False
        row = store.fetch_one(
            "SELECT net_pnl, exit_price FROM trades WHERE trade_id = 't_open'"
        )
        assert row["net_pnl"] is None
        assert row["exit_price"] is None
        store.close()


# ─────────────────────────────────────────────────────────────────────────────
# Part 11 — KillSwitch broker-flat check + alert dedup
# ─────────────────────────────────────────────────────────────────────────────

def _make_ks_with_adapter(tmp_path: Path, adapter, notifier=None) -> KillSwitch:
    return KillSwitch(
        state_store=StateStore(tmp_path / "ks.db"),
        bus=EventBus(),
        logger=logging.getLogger("test_fix180_ks"),
        adapter=adapter,
        notifier=notifier,
    )


class TestKillSwitchFlatCheck:
    def test_flat_when_symbol_absent(self, tmp_path: Path):
        adapter = MagicMock()
        adapter.get_positions.return_value = [SimpleNamespace(symbol="INFY", qty=5)]
        ks = _make_ks_with_adapter(tmp_path, adapter)
        assert ks._is_position_flat("RELIANCE") is True

    def test_flat_when_qty_zero(self, tmp_path: Path):
        adapter = MagicMock()
        adapter.get_positions.return_value = [SimpleNamespace(symbol="RELIANCE", qty=0)]
        ks = _make_ks_with_adapter(tmp_path, adapter)
        assert ks._is_position_flat("RELIANCE") is True

    def test_not_flat_when_position_open(self, tmp_path: Path):
        adapter = MagicMock()
        adapter.get_positions.return_value = [SimpleNamespace(symbol="RELIANCE", qty=-3)]
        ks = _make_ks_with_adapter(tmp_path, adapter)
        assert ks._is_position_flat("RELIANCE") is False

    def test_broker_error_returns_not_flat(self, tmp_path: Path):
        # Cannot confirm flat -> err toward attempting the exit (return False).
        adapter = MagicMock()
        adapter.get_positions.side_effect = RuntimeError("broker down")
        ks = _make_ks_with_adapter(tmp_path, adapter)
        assert ks._is_position_flat("RELIANCE") is False


class TestKillSwitchExitAlertDedup:
    def test_duplicate_alert_suppressed_within_window(self, tmp_path: Path):
        notifier = MagicMock()
        ks = _make_ks_with_adapter(tmp_path, MagicMock(), notifier=notifier)
        failed = [("trd_1", "RELIANCE", "SELL", 10, "INTRADAY")]

        ks._alert_exit_failed(failed)
        ks._alert_exit_failed(failed)  # immediate repeat -> deduped

        assert notifier.send.call_count == 1
        kwargs = notifier.send.call_args.kwargs
        assert kwargs["severity"] == "CRITICAL"
        assert "RELIANCE" in kwargs["title"]

    def test_distinct_trades_each_alert(self, tmp_path: Path):
        notifier = MagicMock()
        ks = _make_ks_with_adapter(tmp_path, MagicMock(), notifier=notifier)
        ks._alert_exit_failed([
            ("trd_1", "RELIANCE", "SELL", 10, "INTRADAY"),
            ("trd_2", "INFY", "BUY", 5, "INTRADAY"),
        ])
        assert notifier.send.call_count == 2

    def test_no_notifier_is_noop(self, tmp_path: Path):
        ks = _make_ks_with_adapter(tmp_path, MagicMock(), notifier=None)
        # Must not raise when notifier is absent.
        ks._alert_exit_failed([("trd_1", "RELIANCE", "SELL", 10, "INTRADAY")])


# ─────────────────────────────────────────────────────────────────────────────
# Bug 3 — order_reconciler re-queries trades mid-cycle (no stale snapshot)
# ─────────────────────────────────────────────────────────────────────────────

class TestReconcilerFreshRequery:
    def test_reconcile_requeries_open_trades_before_sl_logic(self, tmp_path: Path):
        """
        Bug 3: checks 1-5 can close trades in-DB this cycle; the SL-placement
        logic (check9, G5b) must run on a freshly re-queried list, not the stale
        snapshot from the top of the cycle. So get_all_open_trades() is called
        at least twice per _reconcile() cycle.
        """
        store = StateStore(tmp_path / "r.db")
        real = store.get_all_open_trades
        store.get_all_open_trades = MagicMock(side_effect=real)

        rec = _make_reconciler(store)
        rec._reconcile()

        assert store.get_all_open_trades.call_count >= 2, (
            "reconciler must re-query trades after the mutating checks, not reuse "
            f"the stale snapshot (called {store.get_all_open_trades.call_count}x)"
        )
        store.close()
