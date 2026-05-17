"""
tests/unit/test_phase18_batch2.py — Trading System v2

Tests for Phase 18 audit fixes: BATCH 2 (FIX-086, FIX-087, FIX-088)
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, patch

import pytest

from core.time_authority import now_ist, today_ist


# ─────────────────────────────────────────────────────────────────────────────
# FIX-086: OrderMonitor composite key (broker_order_id, symbol, date)
# ─────────────────────────────────────────────────────────────────────────────

def test_fix086_composite_key_same_broker_id_different_dates() -> None:
    """
    FIX-086: Same broker_order_id on different dates maps to different _watched entries.
    """
    from broker.order_monitor import OrderMonitor, _WatchEntry

    # Mock dependencies
    adapter = Mock()
    state_machine = Mock()
    bus = Mock()
    logger = Mock()

    monitor = OrderMonitor(adapter, state_machine, bus, logger)

    # Track same broker_order_id on day 1
    day1 = now_ist()
    day1_str = day1.strftime("%Y-%m-%d")
    # Patch at the import location (broker.order_monitor.today_ist)
    with patch('broker.order_monitor.today_ist', return_value=day1_str):
        monitor.track(
            internal_order_id="ord_001",
            broker_order_id="broker_123",
            symbol="RELIANCE",
            side="BUY",
            qty=10,
            expected_price=2500.0,
            placed_at=day1,
        )

    # Track same broker_order_id on day 2 (different date)
    day2 = day1 + timedelta(days=1)
    day2_str = day2.strftime("%Y-%m-%d")
    with patch('broker.order_monitor.today_ist', return_value=day2_str):
        monitor.track(
            internal_order_id="ord_002",
            broker_order_id="broker_123",  # Same broker_order_id!
            symbol="RELIANCE",
            side="BUY",
            qty=10,
            expected_price=2500.0,
            placed_at=day2,
        )

    # Both should be tracked (different composite keys)
    assert monitor.is_watching("ord_001"), "Day 1 order should be watched"
    assert monitor.is_watching("ord_002"), "Day 2 order should be watched"
    assert monitor.watched_count() == 2, f"Expected 2 entries, got {monitor.watched_count()}"
    print("  OK fix086_composite_key_same_broker_id_different_dates: 2 entries tracked")


def test_fix086_composite_key_same_broker_id_different_symbols() -> None:
    """
    FIX-086: Same broker_order_id, same date, different symbols → different entries.
    """
    from broker.order_monitor import OrderMonitor

    adapter = Mock()
    state_machine = Mock()
    bus = Mock()
    logger = Mock()

    monitor = OrderMonitor(adapter, state_machine, bus, logger)

    day = now_ist()
    day_str = day.strftime("%Y-%m-%d")
    with patch('broker.order_monitor.today_ist', return_value=day_str):
        monitor.track(
            internal_order_id="ord_001",
            broker_order_id="broker_123",
            symbol="RELIANCE",
            side="BUY",
            qty=10,
            expected_price=2500.0,
            placed_at=day,
        )
        monitor.track(
            internal_order_id="ord_002",
            broker_order_id="broker_123",  # Same broker_order_id!
            symbol="INFY",  # Different symbol
            side="BUY",
            qty=10,
            expected_price=1500.0,
            placed_at=day,
        )

    assert monitor.is_watching("ord_001"), "RELIANCE order should be watched"
    assert monitor.is_watching("ord_002"), "INFY order should be watched"
    assert monitor.watched_count() == 2, f"Expected 2 entries, got {monitor.watched_count()}"
    print("  OK fix086_composite_key_same_broker_id_different_symbols: 2 entries tracked")


def test_fix086_composite_key_normal_operation() -> None:
    """
    FIX-086: Normal operation (unique broker_order_id) unchanged.
    """
    from broker.order_monitor import OrderMonitor

    adapter = Mock()
    state_machine = Mock()
    bus = Mock()
    logger = Mock()

    monitor = OrderMonitor(adapter, state_machine, bus, logger)

    day = now_ist()
    day_str = day.strftime("%Y-%m-%d")
    with patch('broker.order_monitor.today_ist', return_value=day_str):
        monitor.track(
            internal_order_id="ord_001",
            broker_order_id="broker_123",
            symbol="RELIANCE",
            side="BUY",
            qty=10,
            expected_price=2500.0,
            placed_at=day,
        )

    assert monitor.is_watching("ord_001"), "Order should be watched"
    assert monitor.watched_count() == 1, f"Expected 1 entry, got {monitor.watched_count()}"

    monitor.untrack("ord_001")
    assert not monitor.is_watching("ord_001"), "Order should be unwatched after untrack"
    assert monitor.watched_count() == 0, f"Expected 0 entries after untrack, got {monitor.watched_count()}"
    print("  OK fix086_composite_key_normal_operation: track/untrack works")


# ─────────────────────────────────────────────────────────────────────────────
# FIX-087: Indestructible per-trade exit loop in kill_switch
# ─────────────────────────────────────────────────────────────────────────────

def test_fix087_per_trade_exception_isolation() -> None:
    """
    FIX-087: If one trade's exit fails, other trades still exit successfully.
    """
    from capital.kill_switch import KillSwitch

    # Mock state_store with 3 open trades
    store = Mock()
    store.fetch_one.return_value = None  # No persisted kill_switch state
    store.fetch_all.return_value = [
        {"trade_id": "t1", "symbol": "RELIANCE", "quantity": 10, "side": "BUY"},
        {"trade_id": "t2", "symbol": "INFY", "quantity": 5, "side": "BUY"},
        {"trade_id": "t3", "symbol": "TCS", "quantity": 8, "side": "BUY"},
    ]
    store.transaction = MagicMock()

    # Mock adapter: t2 fails, t1 and t3 succeed
    adapter = Mock()
    call_count = {"t1": 0, "t2": 0, "t3": 0}

    def place_order_side_effect(symbol, side, qty, order_type, price):
        if symbol == "INFY" and call_count["t2"] == 0:
            call_count["t2"] += 1
            raise RuntimeError("Broker 502 error")
        # t2 succeeds on retry
        result = Mock()
        result.success = True
        return result

    adapter.place_order.side_effect = place_order_side_effect

    bus = Mock()
    logger = Mock()

    ks = KillSwitch(store, bus, logger, adapter=adapter)

    # Trigger hard_kill - should exit all 3 trades (t2 retried)
    report = ks._exit_all_trades_indestructible()

    # All 3 trades attempted
    assert report.attempted == 3, f"Expected 3 attempted, got {report.attempted}"
    # All 3 eventually succeeded (t2 on retry)
    assert report.succeeded == 3, f"Expected 3 succeeded, got {report.succeeded}"
    assert len(report.failed) == 0, f"Expected 0 failed, got {report.failed}"
    print("  OK fix087_per_trade_exception_isolation: failed trade retried, others succeeded")


def test_fix087_infinite_retry_logic() -> None:
    """
    FIX-087: Retry loop runs indefinitely until all trades succeed.
    """
    from capital.kill_switch import KillSwitch

    store = Mock()
    store.fetch_one.return_value = None  # No persisted kill_switch state
    store.fetch_all.return_value = [
        {"trade_id": "t1", "symbol": "RELIANCE", "quantity": 10, "side": "BUY"},
    ]
    store.transaction = MagicMock()

    # Mock adapter: fails 3 times, then succeeds on 4th attempt
    adapter = Mock()
    call_count = [0]

    def place_order_side_effect(symbol, side, qty, order_type, price):
        call_count[0] += 1
        if call_count[0] <= 3:
            raise RuntimeError(f"Broker error (attempt {call_count[0]})")
        result = Mock()
        result.success = True
        return result

    adapter.place_order.side_effect = place_order_side_effect

    bus = Mock()
    logger = Mock()

    ks = KillSwitch(store, bus, logger, adapter=adapter)

    # Should retry until success (4th attempt)
    with patch('time.sleep'):  # Skip actual sleep delays in test
        report = ks._exit_all_trades_indestructible()

    assert report.succeeded == 1, f"Expected 1 succeeded, got {report.succeeded}"
    assert call_count[0] == 4, f"Expected 4 adapter calls (3 failures + 1 success), got {call_count[0]}"
    print("  OK fix087_infinite_retry_logic: retried until success")


def test_fix087_db_write_failure_non_fatal() -> None:
    """
    FIX-087: If DB write fails but broker exit succeeds, trade is marked as succeeded.
    """
    from capital.kill_switch import KillSwitch

    store = Mock()
    store.fetch_one.return_value = None  # No persisted kill_switch state
    store.fetch_all.return_value = [
        {"trade_id": "t1", "symbol": "RELIANCE", "quantity": 10, "side": "BUY"},
    ]
    # DB write raises exception (but fetch_one must succeed first)
    store.transaction.side_effect = RuntimeError("DB locked")

    adapter = Mock()
    result = Mock()
    result.success = True
    adapter.place_order.return_value = result

    bus = Mock()
    logger = Mock()

    ks = KillSwitch(store, bus, logger, adapter=adapter)

    # Should succeed despite DB write failure (broker truth > DB truth)
    with patch('time.sleep'):
        report = ks._exit_all_trades_indestructible()

    assert report.succeeded == 1, f"Expected 1 succeeded (broker exit worked), got {report.succeeded}"
    print("  OK fix087_db_write_failure_non_fatal: broker truth > DB truth")


# ─────────────────────────────────────────────────────────────────────────────
# FIX-088: Guard against DB checkpoint on ticker thread
# ─────────────────────────────────────────────────────────────────────────────

def test_fix088_checkpoint_skipped_on_ticker_thread() -> None:
    """
    FIX-088: checkpoint() called from ticker thread returns immediately with WARNING.
    """
    from core.state_store import StateStore
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = StateStore(db_path)

        # Mock live_feed with ticker_thread_id set to current thread
        live_feed = Mock()
        live_feed._ticker_thread_id = threading.get_ident()

        # Call checkpoint from ticker thread (simulated by matching thread ID)
        result = store.checkpoint(live_feed=live_feed)

        # Should skip checkpoint and return sentinel
        assert result.get("skipped") is True, "Expected checkpoint to be skipped"
        assert result["busy"] == 0 and result["log"] == 0, "Expected zero stats when skipped"

        # Close store to release file lock before temp dir cleanup
        store.close()
        print("  OK fix088_checkpoint_skipped_on_ticker_thread: skipped with WARNING")


def test_fix088_checkpoint_executes_on_main_thread() -> None:
    """
    FIX-088: checkpoint() called from main thread (not ticker) executes normally.
    """
    from core.state_store import StateStore
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = StateStore(db_path)

        # Mock live_feed with ticker_thread_id set to a different thread
        live_feed = Mock()
        live_feed._ticker_thread_id = 99999  # Different from current thread

        # Call checkpoint from main thread
        result = store.checkpoint(live_feed=live_feed)

        # Should execute normally (not skipped)
        assert result.get("skipped") is not True, "Expected checkpoint to execute"
        # Result will have WAL stats (busy, log, checkpointed)
        assert "busy" in result and "log" in result, "Expected WAL stats"

        # Close store to release file lock
        store.close()
        print("  OK fix088_checkpoint_executes_on_main_thread: executed normally")


def test_fix088_checkpoint_no_live_feed_executes() -> None:
    """
    FIX-088: checkpoint() called without live_feed parameter always executes.
    """
    from core.state_store import StateStore
    from pathlib import Path
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        store = StateStore(db_path)

        # Call checkpoint without live_feed (backward compat)
        result = store.checkpoint()

        # Should execute normally
        assert result.get("skipped") is not True, "Expected checkpoint to execute"
        assert "busy" in result and "log" in result, "Expected WAL stats"

        # Close store to release file lock
        store.close()
        print("  OK fix088_checkpoint_no_live_feed_executes: backward compat preserved")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n=== Phase 18 Batch 2 Tests ===\n")

    # FIX-086
    test_fix086_composite_key_same_broker_id_different_dates()
    test_fix086_composite_key_same_broker_id_different_symbols()
    test_fix086_composite_key_normal_operation()

    # FIX-087
    test_fix087_per_trade_exception_isolation()
    test_fix087_infinite_retry_logic()
    test_fix087_db_write_failure_non_fatal()

    # FIX-088
    test_fix088_checkpoint_skipped_on_ticker_thread()
    test_fix088_checkpoint_executes_on_main_thread()
    test_fix088_checkpoint_no_live_feed_executes()

    print("\n=== All Phase 18 Batch 2 tests passed ===\n")
