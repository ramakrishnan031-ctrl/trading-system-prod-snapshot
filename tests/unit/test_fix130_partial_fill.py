"""
tests/unit/test_fix130_partial_fill.py

FIX-130 Item 16: Partial fill handling — immediate cancel on first PARTIAL for ENTRY legs.
  - ENTRY order with PARTIAL status: cancel immediately on first detection
  - SL/TGT/EOD legs: NOT immediately cancelled (keep timeout path)
  - OrderPartiallyTerminated event fires after cancel (triggering SL placement chain)
"""
from __future__ import annotations

import sys
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from broker.order_monitor import OrderMonitor
from broker.order_state_machine import OrderStateMachine
from broker.zerodha_adapter import OrderHistoryEntry
from core.events import EventBus, OrderPartiallyTerminated
from core.time_authority import now_ist


_IST = timezone(timedelta(hours=5, minutes=30))


def _log():
    return logging.getLogger("test_fix130_partial")


@dataclass
class _CancelResult:
    broker_order_id: str
    success: bool
    reason: str = ""


def _history_entry(status: str, filled_qty: int = 0, avg_price: float = 0.0):
    return OrderHistoryEntry(
        broker_order_id="KITE",
        status=status,
        filled_qty=filled_qty,
        avg_price=avg_price,
        rejection_reason="",
        ts=now_ist(),
    )


class _FakeAdapter:
    def __init__(self, status="PARTIAL", filled_qty=50, cancel_success=True):
        self._status = status
        self._filled_qty = filled_qty
        self._cancel_success = cancel_success
        self.cancel_calls: list[str] = []
        self.open_orders_response: list = []

    def get_order_history(self, broker_order_id):
        return [_history_entry(self._status, self._filled_qty, 2500.0)]

    def cancel_order(self, broker_order_id):
        self.cancel_calls.append(broker_order_id)
        return _CancelResult(
            broker_order_id=broker_order_id, success=self._cancel_success
        )

    def get_open_orders(self):
        return list(self.open_orders_response)


def _make_monitor(adapter, on_partial=None, on_cancelled=None):
    osm = OrderStateMachine()
    bus = EventBus()
    if on_partial:
        bus.subscribe(OrderPartiallyTerminated, on_partial)
    monitor = OrderMonitor(
        adapter=adapter,
        state_machine=osm,
        bus=bus,
        logger=_log(),
        poll_interval_sec=1,
        fill_timeout_sec=60,
        partial_fill_timeout_minutes=5,
    )
    return monitor, osm, bus


def _add_entry_order(monitor, osm, internal_id="ord_entry", broker_id="KITE_001",
                     symbol="RELIANCE", leg="ENTRY", qty=100):
    osm.register(internal_id)
    osm.transition(internal_id, "SUBMITTED")
    monitor.track(
        internal_order_id=internal_id,
        broker_order_id=broker_id,
        symbol=symbol,
        side="BUY",
        qty=qty,
        expected_price=2500.0,
        placed_at=now_ist(),
        leg=leg,
    )
    return monitor._internal_to_composite[internal_id]


class TestPartialFillImmediate:

    def test_entry_leg_cancelled_immediately_on_first_partial(self) -> None:
        """ENTRY leg: first PARTIAL detection triggers immediate cancel (no timeout wait)."""
        adapter = _FakeAdapter(status="PARTIAL", filled_qty=50)
        monitor, osm, _ = _make_monitor(adapter)
        composite = _add_entry_order(monitor, osm)

        monitor._process_order(monitor._watched[composite])

        assert len(adapter.cancel_calls) == 1, "ENTRY partial should trigger immediate cancel"
        assert adapter.cancel_calls[0] == "KITE_001"
        assert not monitor.is_watching("ord_entry"), "order should be untracked after cancel"
        print("  OK: ENTRY leg cancelled immediately on first PARTIAL")

    def test_sl_leg_not_immediately_cancelled(self) -> None:
        """SL leg with PARTIAL status does NOT cancel immediately."""
        adapter = _FakeAdapter(status="PARTIAL", filled_qty=50)
        monitor, osm, _ = _make_monitor(adapter)
        composite = _add_entry_order(monitor, osm, internal_id="ord_sl",
                                     broker_id="KITE_SL", leg="SL")

        monitor._process_order(monitor._watched[composite])

        assert len(adapter.cancel_calls) == 0, "SL leg must NOT be immediately cancelled"
        assert monitor.is_watching("ord_sl"), "SL leg should still be watched"
        print("  OK: SL leg NOT immediately cancelled on PARTIAL")

    def test_tgt_leg_not_immediately_cancelled(self) -> None:
        """TGT leg with PARTIAL status does NOT cancel immediately."""
        adapter = _FakeAdapter(status="PARTIAL", filled_qty=50)
        monitor, osm, _ = _make_monitor(adapter)
        composite = _add_entry_order(monitor, osm, internal_id="ord_tgt",
                                     broker_id="KITE_TGT", leg="TGT")

        monitor._process_order(monitor._watched[composite])

        assert len(adapter.cancel_calls) == 0, "TGT leg must NOT be immediately cancelled"
        print("  OK: TGT leg NOT immediately cancelled on PARTIAL")

    def test_partial_event_fires_with_correct_qty(self) -> None:
        """OrderPartiallyTerminated fires after immediate cancel with filled_qty."""
        events_received = []
        adapter = _FakeAdapter(status="PARTIAL", filled_qty=30, cancel_success=True)
        monitor, osm, _ = _make_monitor(
            adapter, on_partial=lambda e: events_received.append(e)
        )
        composite = _add_entry_order(monitor, osm, symbol="TCS", qty=100)

        monitor._process_order(monitor._watched[composite])

        assert len(events_received) == 1, f"Expected 1 OrderPartiallyTerminated, got {len(events_received)}"
        evt = events_received[0]
        assert evt.filled_qty == 30
        assert evt.symbol == "TCS"
        assert evt.reason == "CANCELLED"
        print(f"  OK: OrderPartiallyTerminated fired with filled_qty={evt.filled_qty}")

    def test_second_partial_poll_does_not_cancel_again(self) -> None:
        """After immediate cancel, order is untracked; no double-cancel on second poll."""
        adapter = _FakeAdapter(status="PARTIAL", filled_qty=25)
        monitor, osm, _ = _make_monitor(adapter)
        composite = _add_entry_order(monitor, osm)

        # First poll: immediate cancel → untracked
        monitor._process_order(monitor._watched[composite])
        assert len(adapter.cancel_calls) == 1
        assert not monitor.is_watching("ord_entry")

        # No second poll possible since order is untracked
        # (monitor._watched no longer has this composite key)
        assert composite not in monitor._watched, "order should be removed from _watched"
        print("  OK: no double-cancel after immediate cancel")


if __name__ == "__main__":
    tests = [
        TestPartialFillImmediate().test_entry_leg_cancelled_immediately_on_first_partial,
        TestPartialFillImmediate().test_sl_leg_not_immediately_cancelled,
        TestPartialFillImmediate().test_tgt_leg_not_immediately_cancelled,
        TestPartialFillImmediate().test_partial_event_fires_with_correct_qty,
        TestPartialFillImmediate().test_second_partial_poll_does_not_cancel_again,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
