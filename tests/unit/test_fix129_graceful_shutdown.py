"""
tests/unit/test_fix129_graceful_shutdown.py

Tests for FIX-129 Item 45: cancel_all_entry_orders() in graceful shutdown.
  - ENTRY orders cancelled on shutdown
  - SL/TGT/EOD orders preserved (not cancelled)
  - Empty watch list: no errors
  - Cancel failure is non-fatal (logs, continues)
  - Paper mode: same behavior (adapter.cancel_order simulated)
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from broker.order_monitor import OrderMonitor
from broker.order_state_machine import OrderStateMachine
from broker.zerodha_adapter import CancelResult
from core.events import EventBus
from core.time_authority import now_ist


def _log():
    return logging.getLogger("test_fix129_shutdown")


class _MockAdapter:
    def __init__(self, cancel_succeeds: bool = True):
        self._cancel_succeeds = cancel_succeeds
        self.cancelled: List[str] = []

    def get_order_history(self, broker_order_id):
        return []

    def cancel_order(self, broker_order_id, variety="regular"):
        self.cancelled.append(broker_order_id)
        return CancelResult(
            broker_order_id=broker_order_id,
            success=self._cancel_succeeds,
            reason="" if self._cancel_succeeds else "cancel failed",
        )

    def get_open_orders(self):
        return []


def _make_monitor(adapter=None) -> OrderMonitor:
    _adapter = adapter or _MockAdapter()
    return OrderMonitor(
        adapter=_adapter,
        state_machine=OrderStateMachine(),
        bus=EventBus(),
        logger=_log(),
        poll_interval_sec=60,
        fill_timeout_sec=300,
    )


def _track(monitor, broker_id, leg="ENTRY"):
    import uuid
    internal_id = f"ord_{uuid.uuid4().hex[:8]}"
    monitor.track(
        internal_order_id=internal_id,
        broker_order_id=broker_id,
        symbol="RELIANCE",
        side="BUY",
        qty=10,
        expected_price=1000.0,
        placed_at=now_ist(),
        leg=leg,
    )
    return internal_id


class TestCancelAllEntryOrders:

    def test_entry_orders_cancelled_on_shutdown(self) -> None:
        """cancel_all_entry_orders() cancels tracked ENTRY orders."""
        adapter = _MockAdapter()
        monitor = _make_monitor(adapter)
        _track(monitor, "ENTRY_001", leg="ENTRY")
        _track(monitor, "ENTRY_002", leg="ENTRY")

        n = monitor.cancel_all_entry_orders()

        assert n == 2, f"Expected 2 cancellations, got {n}"
        assert "ENTRY_001" in adapter.cancelled
        assert "ENTRY_002" in adapter.cancelled
        print("  OK: 2 ENTRY orders cancelled on shutdown")

    def test_exit_legs_preserved(self) -> None:
        """SL/TGT/EOD legs are NOT cancelled — exit protection stays at broker."""
        adapter = _MockAdapter()
        monitor = _make_monitor(adapter)
        _track(monitor, "ENTRY_001", leg="ENTRY")
        _track(monitor, "SL_001", leg="SL")
        _track(monitor, "TGT_001", leg="TGT")
        _track(monitor, "EOD_001", leg="EOD")

        n = monitor.cancel_all_entry_orders()

        assert n == 1, f"Expected 1 cancellation (ENTRY only), got {n}"
        assert "ENTRY_001" in adapter.cancelled
        assert "SL_001" not in adapter.cancelled
        assert "TGT_001" not in adapter.cancelled
        assert "EOD_001" not in adapter.cancelled
        print("  OK: only ENTRY cancelled; SL/TGT/EOD preserved")

    def test_empty_watch_list_no_error(self) -> None:
        """Empty watch list: cancel_all_entry_orders() returns 0 without error."""
        adapter = _MockAdapter()
        monitor = _make_monitor(adapter)

        n = monitor.cancel_all_entry_orders()

        assert n == 0
        assert adapter.cancelled == []
        print("  OK: empty watch list → returns 0, no error")

    def test_cancel_failure_non_fatal(self) -> None:
        """Cancel failure (success=False) is logged but does not raise."""
        adapter = _MockAdapter(cancel_succeeds=False)
        monitor = _make_monitor(adapter)
        _track(monitor, "ENTRY_001", leg="ENTRY")
        _track(monitor, "ENTRY_002", leg="ENTRY")

        # Should not raise even when cancel fails
        n = monitor.cancel_all_entry_orders()

        assert n == 0, "No successful cancellations expected when cancel fails"
        print("  OK: cancel failure is non-fatal, logged and continues")

    def test_unset_leg_treated_as_entry(self) -> None:
        """Leg='' (unset) is treated as ENTRY and cancelled."""
        adapter = _MockAdapter()
        monitor = _make_monitor(adapter)
        _track(monitor, "NO_LEG_001", leg="")

        n = monitor.cancel_all_entry_orders()

        assert n == 1
        assert "NO_LEG_001" in adapter.cancelled
        print("  OK: unset leg='' treated as ENTRY, cancelled on shutdown")

    def test_returns_correct_count(self) -> None:
        """Return value reflects number of SUCCESSFUL cancellations."""
        adapter = _MockAdapter(cancel_succeeds=True)
        monitor = _make_monitor(adapter)
        for i in range(5):
            _track(monitor, f"ENTRY_{i:03d}", leg="ENTRY")

        n = monitor.cancel_all_entry_orders()
        assert n == 5
        print("  OK: returns correct count of successful cancellations")


if __name__ == "__main__":
    tests = [
        TestCancelAllEntryOrders().test_entry_orders_cancelled_on_shutdown,
        TestCancelAllEntryOrders().test_exit_legs_preserved,
        TestCancelAllEntryOrders().test_empty_watch_list_no_error,
        TestCancelAllEntryOrders().test_cancel_failure_non_fatal,
        TestCancelAllEntryOrders().test_unset_leg_treated_as_entry,
        TestCancelAllEntryOrders().test_returns_correct_count,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
