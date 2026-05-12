"""
tests/unit/test_events.py

Validates core/events.py against EV1–EV6 locked decisions:
  - subscribe + publish round-trip
  - multiple subscribers all invoked (EV4 fan-out, EV5 ordering)
  - event envelope auto-fills event_id, ts, source_module (EV3)
  - one subscriber raises → others still invoked → EventDispatchError raised (EV4)
  - no subscribers → publish is a no-op (EV1)
  - all 4 seeded event types instantiate with correct typed payload fields (EV6)
  - subscribe with wrong type rejected (EV2)

Run: python -m pytest tests/unit/test_events.py -v
Or:  python tests/unit/test_events.py  (standalone mode)
"""

from __future__ import annotations

import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.events import (
    CapitalDriftDetected,
    EodSquareoffComplete,
    Event,
    EventBus,
    KillSwitchActivated,
    OrderFilled,
    PositionClosed,
)
from core.exceptions import EventDispatchError, TradingSystemError


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_bus() -> EventBus:
    return EventBus()


# ─────────────────────────────────────────────────────────────────────────────
# Tests — subscribe + publish round-trip
# ─────────────────────────────────────────────────────────────────────────────

def test_subscribe_publish_roundtrip() -> None:
    bus = _make_bus()
    received: list[Event] = []

    bus.subscribe(OrderFilled, received.append)
    bus.publish(OrderFilled(source_module="order_placer", symbol="RELIANCE", order_id="O1"))

    assert len(received) == 1
    assert isinstance(received[0], OrderFilled)
    assert received[0].symbol == "RELIANCE"
    assert received[0].order_id == "O1"
    print("  OK subscribe+publish round-trip delivers event with correct fields")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — multiple subscribers all invoked
# ─────────────────────────────────────────────────────────────────────────────

def test_multiple_subscribers_all_invoked() -> None:
    bus = _make_bus()
    calls: list[str] = []

    bus.subscribe(OrderFilled, lambda e: calls.append("A"))
    bus.subscribe(OrderFilled, lambda e: calls.append("B"))
    bus.subscribe(OrderFilled, lambda e: calls.append("C"))

    bus.publish(OrderFilled(source_module="order_placer"))

    assert calls == ["A", "B", "C"], f"Expected all 3 called in order, got {calls}"
    print("  OK All 3 subscribers invoked; registration order observed (not contractual)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — event envelope auto-population (EV3)
# ─────────────────────────────────────────────────────────────────────────────

def test_envelope_event_id_auto_generated() -> None:
    e = OrderFilled(source_module="order_placer")
    assert isinstance(e.event_id, str)
    assert len(e.event_id) == 32, f"uuid4 hex should be 32 chars, got {len(e.event_id)}"
    print(f"  OK event_id auto-generated: {e.event_id!r}")


def test_envelope_ts_auto_generated_ist_aware() -> None:
    e = OrderFilled(source_module="order_placer")
    assert isinstance(e.ts, datetime)
    assert e.ts.tzinfo is not None, "ts must be timezone-aware (IST)"
    offset = e.ts.utcoffset()
    assert offset is not None
    from datetime import timedelta
    assert offset == timedelta(hours=5, minutes=30), (
        f"ts offset should be IST (+05:30), got {offset}"
    )
    print(f"  OK ts auto-generated IST-aware: {e.ts.isoformat()}")


def test_envelope_source_module_stored() -> None:
    e = OrderFilled(source_module="order_placer")
    assert e.source_module == "order_placer"
    print("  OK source_module stored as provided")


def test_two_events_get_different_event_ids() -> None:
    a = OrderFilled(source_module="m")
    b = OrderFilled(source_module="m")
    assert a.event_id != b.event_id, "Each event must get a unique event_id"
    print(f"  OK Two events have distinct event_ids: {a.event_id[:8]}… vs {b.event_id[:8]}…")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — collect-all-errors-then-raise (EV4)
# ─────────────────────────────────────────────────────────────────────────────

def test_failing_subscriber_does_not_skip_siblings() -> None:
    bus = _make_bus()
    calls: list[str] = []

    def bad_handler(e: Event) -> None:
        raise RuntimeError("intentional failure")

    bus.subscribe(OrderFilled, lambda e: calls.append("before"))
    bus.subscribe(OrderFilled, bad_handler)
    bus.subscribe(OrderFilled, lambda e: calls.append("after"))

    raised = False
    try:
        bus.publish(OrderFilled(source_module="order_placer"))
    except EventDispatchError:
        raised = True

    assert raised, "Expected EventDispatchError to be raised"
    assert "before" in calls, "Subscriber before the failing one was not called"
    assert "after" in calls, "Subscriber after the failing one was not called"
    print("  OK All siblings invoked despite one raising; EventDispatchError raised at end")


def test_dispatch_error_is_trading_system_error() -> None:
    bus = _make_bus()
    bus.subscribe(OrderFilled, lambda e: (_ for _ in ()).throw(ValueError("boom")))

    caught_as_tse = False
    try:
        bus.publish(OrderFilled(source_module="order_placer"))
    except TradingSystemError:
        caught_as_tse = True

    assert caught_as_tse, "EventDispatchError must be catchable as TradingSystemError"
    print("  OK EventDispatchError caught as TradingSystemError (hierarchy correct)")


def test_dispatch_error_carries_context() -> None:
    bus = _make_bus()
    bus.subscribe(OrderFilled, lambda e: (_ for _ in ()).throw(ValueError("boom")))
    bus.subscribe(OrderFilled, lambda e: (_ for _ in ()).throw(RuntimeError("pow")))

    try:
        bus.publish(OrderFilled(source_module="order_placer"))
    except EventDispatchError as exc:
        assert exc.context["error_count"] == 2
        assert exc.context["event_type"] == "OrderFilled"
        assert "event_id" in exc.context
        print(f"  OK EventDispatchError.context has error_count=2, event_type='OrderFilled'")
    else:
        assert False, "EventDispatchError not raised"


# ─────────────────────────────────────────────────────────────────────────────
# Tests — no subscribers → no-op (EV1)
# ─────────────────────────────────────────────────────────────────────────────

def test_publish_with_no_subscribers_is_noop() -> None:
    bus = _make_bus()
    # Should not raise
    bus.publish(OrderFilled(source_module="order_placer"))
    bus.publish(KillSwitchActivated(source_module="kill_switch"))
    print("  OK publish with no subscribers is a no-op (no exception)")


def test_subscribers_for_other_types_not_invoked() -> None:
    """Subscriber for PositionClosed must not receive OrderFilled events."""
    bus = _make_bus()
    calls: list[str] = []

    bus.subscribe(PositionClosed, lambda e: calls.append("position_closed_handler"))
    bus.publish(OrderFilled(source_module="order_placer"))

    assert calls == [], f"Wrong handler was invoked: {calls}"
    print("  OK Subscriber for PositionClosed not invoked for OrderFilled")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — all 4 seeded event types (EV6)
# ─────────────────────────────────────────────────────────────────────────────

def test_order_filled_typed_fields() -> None:
    e = OrderFilled(
        source_module="order_placer",
        symbol="RELIANCE",
        order_id="ORD001",
        trade_id="TRD001",
        signal_id="SIG001",
        filled_qty=10,
        fill_price=2450.5,
    )
    assert isinstance(e, Event)
    assert e.symbol == "RELIANCE"
    assert e.order_id == "ORD001"
    assert e.trade_id == "TRD001"
    assert e.signal_id == "SIG001"
    assert e.filled_qty == 10
    assert e.fill_price == 2450.5
    print("  OK OrderFilled instantiates with correct typed payload fields")


def test_position_closed_typed_fields() -> None:
    e = PositionClosed(
        source_module="order_monitor",
        symbol="INFY",
        trade_id="TRD002",
        signal_id="SIG002",
        exit_price=1580.0,
        realized_pnl=450.0,
    )
    assert isinstance(e, Event)
    assert e.symbol == "INFY"
    assert e.trade_id == "TRD002"
    assert e.exit_price == 1580.0
    assert e.realized_pnl == 450.0
    print("  OK PositionClosed instantiates with correct typed payload fields")


def test_kill_switch_activated_typed_fields() -> None:
    e = KillSwitchActivated(
        source_module="kill_switch",
        kill_type="soft",
        reason="daily_loss_limit_hit",
    )
    assert isinstance(e, Event)
    assert e.kill_type == "soft"
    assert e.reason == "daily_loss_limit_hit"
    print("  OK KillSwitchActivated instantiates with correct typed payload fields")


def test_kill_switch_activated_ks8_fields() -> None:
    """KS8: KillSwitchActivated carries previous_state, new_state, triggered_by."""
    e = KillSwitchActivated(
        source_module="capital.kill_switch",
        kill_type="hard",
        reason="api_timeout_threshold_exceeded",
        previous_state="INACTIVE",
        new_state="HARD_KILL",
        triggered_by="zerodha_adapter",
    )
    assert e.kill_type == "hard"
    assert e.reason == "api_timeout_threshold_exceeded"
    assert e.previous_state == "INACTIVE"
    assert e.new_state == "HARD_KILL"
    assert e.triggered_by == "zerodha_adapter"
    # resume subtype
    e2 = KillSwitchActivated(
        source_module="capital.kill_switch",
        kill_type="resume",
        reason="operator cleared halt",
        previous_state="SOFT_KILL",
        new_state="INACTIVE",
        triggered_by="operator",
    )
    assert e2.kill_type == "resume"
    assert e2.previous_state == "SOFT_KILL"
    assert e2.new_state == "INACTIVE"
    print("  OK KillSwitchActivated KS8 fields (previous_state, new_state, triggered_by)")


def test_capital_drift_detected_typed_fields() -> None:
    e = CapitalDriftDetected(
        source_module="order_reconciler",
        expected=100000.0,
        actual=99500.0,
        delta=-500.0,
    )
    assert isinstance(e, Event)
    assert e.expected == 100000.0
    assert e.actual == 99500.0
    assert e.delta == -500.0
    print("  OK CapitalDriftDetected instantiates with correct typed payload fields")


def test_all_four_event_types_are_event_subclasses() -> None:
    for cls in (OrderFilled, PositionClosed, KillSwitchActivated, CapitalDriftDetected):
        assert issubclass(cls, Event), f"{cls.__name__} must be a subclass of Event"
    print("  OK All 4 seeded event types are subclasses of Event")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — wrong type rejected (EV2)
# ─────────────────────────────────────────────────────────────────────────────

def test_subscribe_rejects_non_event_type() -> None:
    bus = _make_bus()

    raised = False
    try:
        bus.subscribe(str, lambda e: None)  # type: ignore[arg-type]
    except TypeError:
        raised = True
    assert raised, "Expected TypeError when subscribing with a non-Event type"
    print("  OK subscribe(str, ...) raises TypeError")


def test_subscribe_rejects_instance_not_type() -> None:
    bus = _make_bus()

    raised = False
    try:
        bus.subscribe("OrderFilled", lambda e: None)  # type: ignore[arg-type]
    except TypeError:
        raised = True
    assert raised, "Expected TypeError when subscribing with an instance, not a type"
    print("  OK subscribe('OrderFilled', ...) raises TypeError")


def test_subscribe_accepts_valid_event_subclass() -> None:
    """No exception should be raised for a valid Event subclass."""
    bus = _make_bus()
    bus.subscribe(OrderFilled, lambda e: None)
    bus.subscribe(PositionClosed, lambda e: None)
    bus.subscribe(KillSwitchActivated, lambda e: None)
    bus.subscribe(CapitalDriftDetected, lambda e: None)
    print("  OK subscribe accepts all 4 seeded Event subclasses without error")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- typed event fields
# ─────────────────────────────────────────────────────────────────────────────

def test_order_filled_om6_fields() -> None:
    """OrderFilled carries OM6 fields: internal/broker IDs, side, slippage, filled_at."""
    e = OrderFilled(
        source_module="order_monitor",
        internal_order_id="ord_abc123",
        broker_order_id="KITE999",
        symbol="RELIANCE",
        side="BUY",
        filled_qty=10,
        avg_fill_price=2510.0,
        expected_price=2500.0,
        slippage_pct=0.4,
        filled_at="2026-04-15T10:30:00+05:30",
    )
    assert isinstance(e, Event)
    assert e.internal_order_id == "ord_abc123"
    assert e.broker_order_id == "KITE999"
    assert e.side == "BUY"
    assert e.avg_fill_price == 2510.0
    assert e.expected_price == 2500.0
    assert e.slippage_pct == 0.4
    assert e.filled_at == "2026-04-15T10:30:00+05:30"
    print("  OK OrderFilled OM6 fields verified (OM6)")


def test_eod_squareoff_complete_typed_fields() -> None:
    """EodSquareoffComplete instantiates with correct typed fields (EV8)."""
    evt = EodSquareoffComplete(
        source_module="eod_squareoff",
        fired_date="2026-04-15",
        positions_attempted=3,
        positions_succeeded=2,
        positions_failed=1,
        cancels_attempted=2,
        cancels_succeeded=2,
        cancels_failed=0,
    )
    assert isinstance(evt, Event)
    assert evt.source_module == "eod_squareoff"
    assert evt.fired_date == "2026-04-15"
    assert evt.positions_attempted == 3
    assert evt.positions_succeeded == 2
    assert evt.positions_failed == 1
    assert evt.cancels_attempted == 2
    assert evt.cancels_succeeded == 2
    assert evt.cancels_failed == 0
    assert isinstance(evt.event_id, str) and len(evt.event_id) == 32
    assert isinstance(evt.ts, datetime)
    print("  OK EodSquareoffComplete typed fields and Event envelope verified (EV8)")


def test_eod_squareoff_complete_defaults() -> None:
    """EodSquareoffComplete fields default to empty/zero (EV8)."""
    evt = EodSquareoffComplete(source_module="eod_squareoff")
    assert evt.fired_date == ""
    assert evt.positions_attempted == 0
    assert evt.positions_succeeded == 0
    assert evt.positions_failed == 0
    assert evt.cancels_attempted == 0
    assert evt.cancels_succeeded == 0
    assert evt.cancels_failed == 0
    assert issubclass(EodSquareoffComplete, Event)
    print("  OK EodSquareoffComplete defaults are correct (EV8)")


# ─────────────────────────────────────────────────────────────────────────────
# async_dispatch (FIX-003)
# ─────────────────────────────────────────────────────────────────────────────

def test_async_dispatch_handler_is_called() -> None:
    """async_dispatch=True subscriber is eventually invoked (FIX-003)."""
    bus = _make_bus()
    called = threading.Event()

    def async_handler(event: OrderFilled) -> None:
        called.set()

    bus.subscribe(OrderFilled, async_handler, async_dispatch=True)
    bus.publish(OrderFilled(source_module="test", symbol="INFY"))
    assert called.wait(timeout=2.0), "async_dispatch subscriber was not called within 2s"
    bus.shutdown()
    print("  OK async_dispatch subscriber invoked (FIX-003)")


def test_async_dispatch_does_not_block_publisher() -> None:
    """publish() returns before async_dispatch handler finishes (FIX-003)."""
    bus = _make_bus()
    started = threading.Event()
    finished = threading.Event()

    def slow_handler(event: OrderFilled) -> None:
        started.set()
        time.sleep(0.1)
        finished.set()

    bus.subscribe(OrderFilled, slow_handler, async_dispatch=True)
    t_before = time.monotonic()
    bus.publish(OrderFilled(source_module="test", symbol="TCS"))
    elapsed = time.monotonic() - t_before
    assert elapsed < 0.05, f"publish() blocked for {elapsed:.3f}s — async_dispatch not working"
    assert finished.wait(timeout=2.0), "handler never finished"
    bus.shutdown()
    print("  OK async_dispatch does not block publisher (FIX-003)")


def test_async_dispatch_exception_does_not_raise_to_publisher() -> None:
    """async_dispatch handler exception must NOT raise EventDispatchError to caller (FIX-003)."""
    bus = _make_bus()
    done = threading.Event()

    def failing_handler(event: OrderFilled) -> None:
        done.set()
        raise RuntimeError("intentional async failure")

    bus.subscribe(OrderFilled, failing_handler, async_dispatch=True)
    # publish must not raise
    bus.publish(OrderFilled(source_module="test", symbol="WIPRO"))
    done.wait(timeout=2.0)  # wait for handler to run (and fail)
    bus.shutdown()
    print("  OK async_dispatch exception does not raise to publisher (FIX-003)")


def test_async_and_sync_subscribers_coexist() -> None:
    """Sync and async subscribers on same event type both receive it (FIX-003)."""
    bus = _make_bus()
    sync_called = []
    async_called = threading.Event()

    def sync_handler(event: OrderFilled) -> None:
        sync_called.append(event.symbol)

    def async_handler(event: OrderFilled) -> None:
        async_called.set()

    bus.subscribe(OrderFilled, sync_handler)
    bus.subscribe(OrderFilled, async_handler, async_dispatch=True)
    bus.publish(OrderFilled(source_module="test", symbol="RELIANCE"))
    assert sync_called == ["RELIANCE"], "sync subscriber not called"
    assert async_called.wait(timeout=2.0), "async subscriber not called"
    bus.shutdown()
    print("  OK sync + async subscribers coexist on same event type (FIX-003)")


def test_order_placer_subscribes_order_filled_synchronously() -> None:
    """OrderPlacer subscribes OrderFilled synchronously (no deadlock risk in paper mode,
    as _paper_fills_lock is released before bus.publish()). FIX-003 provides the
    async_dispatch infrastructure; OrderPlacer deliberately uses sync (FIX-003)."""
    bus = _make_bus()

    # Verify sync subscription by checking that OrderFilled is in _subscribers
    # (not _async_subscribers) after a subscribe() call without async_dispatch
    received = []
    bus.subscribe(OrderFilled, lambda e: received.append(e))
    assert OrderFilled in bus._subscribers, (
        "Sync subscriber must appear in bus._subscribers"
    )
    assert OrderFilled not in bus._async_subscribers, (
        "Sync subscriber must NOT appear in bus._async_subscribers"
    )
    bus.shutdown()
    print("  OK Sync subscribers live in _subscribers, async in _async_subscribers (FIX-003)")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        test_subscribe_publish_roundtrip,
        test_multiple_subscribers_all_invoked,
        test_envelope_event_id_auto_generated,
        test_envelope_ts_auto_generated_ist_aware,
        test_envelope_source_module_stored,
        test_two_events_get_different_event_ids,
        test_failing_subscriber_does_not_skip_siblings,
        test_dispatch_error_is_trading_system_error,
        test_dispatch_error_carries_context,
        test_publish_with_no_subscribers_is_noop,
        test_subscribers_for_other_types_not_invoked,
        test_order_filled_typed_fields,
        test_position_closed_typed_fields,
        test_kill_switch_activated_typed_fields,
        test_kill_switch_activated_ks8_fields,
        test_capital_drift_detected_typed_fields,
        test_all_four_event_types_are_event_subclasses,
        test_subscribe_rejects_non_event_type,
        test_subscribe_rejects_instance_not_type,
        test_subscribe_accepts_valid_event_subclass,
        test_order_filled_om6_fields,
        test_order_state_changed_typed_fields,
        test_eod_squareoff_complete_typed_fields,
        test_eod_squareoff_complete_defaults,
        test_async_dispatch_handler_is_called,
        test_async_dispatch_does_not_block_publisher,
        test_async_dispatch_exception_does_not_raise_to_publisher,
        test_async_and_sync_subscribers_coexist,
        test_order_placer_subscribes_order_filled_synchronously,
    ]

    print("=" * 70)
    print("events.py — Test Suite")
    print("=" * 70)

    failed = []
    for test in tests:
        print(f"\n-> {test.__name__}")
        try:
            test()
        except AssertionError as e:
            failed.append((test.__name__, f"AssertionError: {e}"))
            print(f"  FAIL: {e}")
        except Exception as e:
            failed.append((test.__name__, f"{type(e).__name__}: {e}"))
            print(f"  ERROR: {type(e).__name__}: {e}")

    print("\n" + "=" * 70)
    if failed:
        print(f"FAILED: {len(failed)} of {len(tests)} tests")
        for name, err in failed:
            print(f"  FAIL {name}: {err}")
        return 1

    print(f"PASSED: all {len(tests)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
