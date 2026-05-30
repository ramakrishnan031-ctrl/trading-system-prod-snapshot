"""
tests/unit/test_fix128_circuit_breaker.py

Tests for FIX-128 Fix C: position-level circuit breaker in OrderMonitor.
  - General API failure counter → on_critical_failure after max_api_failures
  - Partial fill stuck > partial_fill_timeout_minutes → cancel order
  - Force-close at force_close_time → on_force_close callback + ENTRY cancels
  - Paper mode behaves the same (paper adapter simulates cancel)
"""
from __future__ import annotations

import logging
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, time as _time, timedelta
from pathlib import Path
from typing import Callable, List, Optional
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from broker.order_monitor import OrderMonitor
from broker.order_state_machine import OrderStateMachine
from broker.zerodha_adapter import CancelResult
from core.events import EventBus
from core.exceptions import BrokerAuthError, BrokerTimeoutError
from core.time_authority import now_ist


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / mocks
# ─────────────────────────────────────────────────────────────────────────────

def _log() -> logging.Logger:
    return logging.getLogger("test_fix128_cb")


@dataclass
class _OrderEntry:
    """Minimal order info for adapter mock."""
    status: str = "OPEN"
    filled_qty: int = 0
    avg_price: float = 0.0


class _HistoryEntry:
    def __init__(self, status="OPEN", filled_qty=0, avg_price=0.0, status_message=""):
        self.status = status
        self.filled_qty = filled_qty
        self.avg_price = avg_price
        self.status_message = status_message


class _MockAdapter:
    """Mock ZerodhaAdapter for circuit breaker tests."""

    def __init__(
        self,
        history_side_effect=None,  # callable(broker_order_id) → list or raises
        cancel_succeeds: bool = True,
    ):
        self._history_fn = history_side_effect
        self._cancel_succeeds = cancel_succeeds
        self.cancelled: List[str] = []
        self.history_call_count = 0

    def get_order_history(self, broker_order_id: str):
        self.history_call_count += 1
        if self._history_fn:
            return self._history_fn(broker_order_id)
        return [_HistoryEntry(status="OPEN")]

    def cancel_order(self, broker_order_id: str):
        self.cancelled.append(broker_order_id)
        return CancelResult(
            broker_order_id=broker_order_id,
            success=self._cancel_succeeds,
            reason="" if self._cancel_succeeds else "cancel failed",
        )

    def get_open_orders(self):
        return []


def _make_monitor(
    adapter: _MockAdapter,
    on_critical_failure=None,
    partial_fill_timeout_minutes: int = 5,
    max_api_failures: int = 3,
    force_close_time: Optional[str] = None,
    on_force_close=None,
) -> OrderMonitor:
    osm = OrderStateMachine()
    bus = EventBus()
    return OrderMonitor(
        adapter=adapter,
        state_machine=osm,
        bus=bus,
        logger=_log(),
        poll_interval_sec=1,
        fill_timeout_sec=120,  # large so regular timeout doesn't interfere
        on_critical_failure=on_critical_failure,
        partial_fill_timeout_minutes=partial_fill_timeout_minutes,
        max_api_failures=max_api_failures,
        force_close_time=force_close_time,
        on_force_close=on_force_close,
    )


def _track_order(monitor: OrderMonitor, broker_order_id: str = "ORD001",
                 leg: str = "ENTRY") -> str:
    """Register a minimal order in the monitor and return internal_order_id."""
    import uuid
    internal_id = f"ord_{uuid.uuid4().hex[:8]}"
    monitor.track(
        internal_order_id=internal_id,
        broker_order_id=broker_order_id,
        symbol="RELIANCE",
        side="BUY",
        qty=10,
        expected_price=1000.0,
        placed_at=now_ist(),
        leg=leg,
    )
    return internal_id


# ─────────────────────────────────────────────────────────────────────────────
# General API failure → hard_kill
# ─────────────────────────────────────────────────────────────────────────────

class TestApiFailureCircuitBreaker:

    def test_general_error_counts_toward_limit(self) -> None:
        """General Exception in get_order_history increments _consecutive_api_fails."""
        calls = []
        adapter = _MockAdapter(history_side_effect=lambda bid: (_ for _ in ()).throw(
            RuntimeError("network error")
        ))
        critical_cb = lambda reason: calls.append(reason)
        monitor = _make_monitor(adapter, on_critical_failure=critical_cb, max_api_failures=3)
        internal_id = _track_order(monitor)

        # One failure: counter = 1, no callback yet
        monitor._process_order(monitor._watched[monitor._internal_to_composite[internal_id]])
        assert monitor._consecutive_api_fails == 1
        assert calls == []
        print("  OK: 1 failure → counter=1, no callback")

    def test_three_consecutive_errors_fire_critical(self) -> None:
        """3 consecutive API errors fire on_critical_failure and stop the monitor."""
        critical_reasons = []
        adapter = _MockAdapter(history_side_effect=lambda bid: (_ for _ in ()).throw(
            RuntimeError("broker down")
        ))
        monitor = _make_monitor(
            adapter,
            on_critical_failure=lambda r: critical_reasons.append(r),
            max_api_failures=3,
        )
        internal_id = _track_order(monitor)
        composite_key = monitor._internal_to_composite[internal_id]

        for _ in range(3):
            entry = monitor._watched.get(composite_key)
            if entry:
                monitor._process_order(entry)

        assert len(critical_reasons) == 1, "on_critical_failure should fire exactly once"
        assert "circuit_breaker" in critical_reasons[0].lower()
        assert monitor._stop_event.is_set(), "Monitor should have stopped"
        print("  OK: 3 API failures → critical callback + monitor stopped")

    def test_success_resets_api_fail_counter(self) -> None:
        """Successful poll resets _consecutive_api_fails to 0."""
        fail_count = [0]
        def _history(bid):
            fail_count[0] += 1
            if fail_count[0] <= 2:
                raise RuntimeError("intermittent error")
            return [_HistoryEntry(status="OPEN")]

        adapter = _MockAdapter(history_side_effect=_history)
        monitor = _make_monitor(adapter, max_api_failures=3)
        internal_id = _track_order(monitor)
        composite_key = monitor._internal_to_composite[internal_id]

        # Two failures
        for _ in range(2):
            monitor._process_order(monitor._watched[composite_key])
        assert monitor._consecutive_api_fails == 2

        # Success
        monitor._process_order(monitor._watched[composite_key])
        assert monitor._consecutive_api_fails == 0, "Success should reset counter"
        print("  OK: success resets API fail counter to 0")

    def test_timeout_error_does_not_count_as_api_failure(self) -> None:
        """BrokerTimeoutError is skipped, does not increment general API fail counter."""
        adapter = _MockAdapter(history_side_effect=lambda bid: (_ for _ in ()).throw(
            BrokerTimeoutError("timeout")
        ))
        monitor = _make_monitor(adapter, max_api_failures=3)
        internal_id = _track_order(monitor)
        composite_key = monitor._internal_to_composite[internal_id]

        monitor._process_order(monitor._watched[composite_key])
        assert monitor._consecutive_api_fails == 0, "Timeout should not count as API failure"
        print("  OK: BrokerTimeoutError does not count as API failure")


# ─────────────────────────────────────────────────────────────────────────────
# Partial fill stuck → cancel
# ─────────────────────────────────────────────────────────────────────────────

class TestPartialFillCircuitBreaker:

    def test_partial_fill_entry_cancelled_immediately(self) -> None:
        """FIX-130 Option A: ENTRY leg first PARTIAL -> cancel immediately (no timeout wait)."""
        adapter = _MockAdapter()
        monitor = _make_monitor(adapter, partial_fill_timeout_minutes=5)
        internal_id = _track_order(monitor)  # leg="ENTRY" by default
        composite_key = monitor._internal_to_composite[internal_id]
        entry = monitor._watched[composite_key]

        # First PARTIAL on ENTRY leg -> immediate cancel (FIX-130 Option A)
        monitor._handle_partial(entry, filled_qty=5, avg_price=1000.0)

        assert adapter.cancelled != [], "ENTRY first PARTIAL must cancel immediately"
        assert adapter.cancelled[0] == entry.broker_order_id
        print("  OK: ENTRY leg first PARTIAL -> immediate cancel (FIX-130 Option A)")

    def test_partial_fill_past_timeout_cancelled(self) -> None:
        """Partial fill stuck > timeout → order cancelled."""
        adapter = _MockAdapter()
        monitor = _make_monitor(adapter, partial_fill_timeout_minutes=5)
        internal_id = _track_order(monitor)
        composite_key = monitor._internal_to_composite[internal_id]
        entry = monitor._watched[composite_key]

        # Artificially set partial_since to 6 minutes ago
        entry.partial_since = now_ist() - timedelta(minutes=6)
        entry.filled_qty = 5

        monitor._handle_partial(entry, filled_qty=5, avg_price=1000.0)

        assert adapter.cancelled != [], "Should cancel stuck partial fill"
        assert adapter.cancelled[0] == entry.broker_order_id
        print("  OK: partial fill stuck > 5 min → order cancelled")

    def test_exit_leg_partial_not_cancelled(self) -> None:
        """SL/TGT legs with partial fills are exempt from partial fill timeout."""
        adapter = _MockAdapter()
        monitor = _make_monitor(adapter, partial_fill_timeout_minutes=1)
        internal_id = _track_order(monitor, leg="SL")
        composite_key = monitor._internal_to_composite[internal_id]
        entry = monitor._watched[composite_key]

        # Stuck for 10 minutes — but exit leg is exempt
        entry.partial_since = now_ist() - timedelta(minutes=10)
        entry.filled_qty = 5

        monitor._handle_partial(entry, filled_qty=5, avg_price=1000.0)
        assert adapter.cancelled == [], "Exit legs are exempt from partial fill timeout"
        print("  OK: exit leg partial fill → exempt from circuit breaker timeout")


# ─────────────────────────────────────────────────────────────────────────────
# Force-close at configured time
# ─────────────────────────────────────────────────────────────────────────────

class TestForceCloseCircuitBreaker:

    def test_force_close_not_triggered_before_time(self) -> None:
        """Force-close does NOT fire when current time < configured time."""
        adapter = _MockAdapter()
        force_close_calls = []
        monitor = _make_monitor(
            adapter,
            force_close_time="23:59",  # far in the future
            on_force_close=lambda: force_close_calls.append(1),
        )
        _track_order(monitor)

        monitor._check_force_close(dict(monitor._watched))

        assert force_close_calls == [], "Force-close should not fire before time"
        assert adapter.cancelled == []
        print("  OK: force-close does not fire before configured time")

    def test_force_close_fires_at_configured_time(self) -> None:
        """Force-close fires when now.time() >= force_close_time."""
        import datetime as _dt
        adapter = _MockAdapter()
        force_close_calls = []
        monitor = _make_monitor(
            adapter,
            force_close_time="00:01",  # always past this time today
            on_force_close=lambda: force_close_calls.append(1),
        )
        internal_id = _track_order(monitor)

        monitor._check_force_close(dict(monitor._watched))

        assert len(force_close_calls) == 1, "Force-close callback should fire"
        assert adapter.cancelled != [], "ENTRY order should have been cancelled"
        print("  OK: force-close fires at configured time, cancels ENTRY orders")

    def test_force_close_fires_only_once_per_day(self) -> None:
        """Force-close callback fires exactly once per calendar day."""
        adapter = _MockAdapter()
        force_close_calls = []
        monitor = _make_monitor(
            adapter,
            force_close_time="00:01",
            on_force_close=lambda: force_close_calls.append(1),
        )
        _track_order(monitor)

        monitor._check_force_close(dict(monitor._watched))
        monitor._check_force_close(dict(monitor._watched))
        monitor._check_force_close(dict(monitor._watched))

        assert len(force_close_calls) == 1, "Force-close should only fire once per day"
        print("  OK: force-close fires exactly once per day")

    def test_force_close_skips_exit_legs(self) -> None:
        """Force-close cancels ENTRY orders but leaves SL/TGT/EOD orders untouched."""
        adapter = _MockAdapter()
        monitor = _make_monitor(
            adapter,
            force_close_time="00:01",
            on_force_close=lambda: None,
        )
        entry_id = _track_order(monitor, broker_order_id="ORD_ENTRY", leg="ENTRY")
        sl_id = _track_order(monitor, broker_order_id="ORD_SL", leg="SL")
        tgt_id = _track_order(monitor, broker_order_id="ORD_TGT", leg="TGT")

        monitor._check_force_close(dict(monitor._watched))

        assert "ORD_ENTRY" in adapter.cancelled, "ENTRY order should be cancelled"
        assert "ORD_SL" not in adapter.cancelled, "SL order should not be cancelled"
        assert "ORD_TGT" not in adapter.cancelled, "TGT order should not be cancelled"
        print("  OK: force-close cancels ENTRY but leaves SL/TGT untouched")

    def test_force_close_no_callback_no_error(self) -> None:
        """Force-close with on_force_close=None does not raise."""
        adapter = _MockAdapter()
        monitor = _make_monitor(adapter, force_close_time="00:01", on_force_close=None)
        _track_order(monitor)
        # Should not raise
        monitor._check_force_close(dict(monitor._watched))
        print("  OK: force_close_time set, on_force_close=None → no error")


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        TestApiFailureCircuitBreaker().test_general_error_counts_toward_limit,
        TestApiFailureCircuitBreaker().test_three_consecutive_errors_fire_critical,
        TestApiFailureCircuitBreaker().test_success_resets_api_fail_counter,
        TestApiFailureCircuitBreaker().test_timeout_error_does_not_count_as_api_failure,
        TestPartialFillCircuitBreaker().test_partial_fill_within_timeout_not_cancelled,
        TestPartialFillCircuitBreaker().test_partial_fill_past_timeout_cancelled,
        TestPartialFillCircuitBreaker().test_exit_leg_partial_not_cancelled,
        TestForceCloseCircuitBreaker().test_force_close_not_triggered_before_time,
        TestForceCloseCircuitBreaker().test_force_close_fires_at_configured_time,
        TestForceCloseCircuitBreaker().test_force_close_fires_only_once_per_day,
        TestForceCloseCircuitBreaker().test_force_close_skips_exit_legs,
        TestForceCloseCircuitBreaker().test_force_close_no_callback_no_error,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
