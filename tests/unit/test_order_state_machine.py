"""
tests/unit/test_order_state_machine.py

Validates broker/order_state_machine.py against OSM1-OSM14:
  - register() puts order in PENDING (OSM9)
  - register() twice -> ValueError (OSM9)
  - current_state(unknown) -> ValueError (OSM8)
  - PENDING -> SUBMITTED OK (OSM2)
  - PENDING -> OPEN -> InvalidTransitionError (OSM2, OSM3)
  - SUBMITTED -> OPEN OK (OSM2)
  - OPEN -> PARTIAL OK (OSM2)
  - PARTIAL -> PARTIAL OK - self-loop (OSM2)
  - PARTIAL -> COMPLETE OK (OSM2)
  - COMPLETE -> anything -> InvalidTransitionError (OSM4)
  - CANCELLED -> anything -> InvalidTransitionError (OSM4)
  - FAILED -> anything -> InvalidTransitionError (OSM4)
  - EXPIRED -> anything -> InvalidTransitionError (OSM4)
  - All 4 terminal states reject same-state self-loop (OSM4)
  - Successful transition publishes OrderStateChanged event (OSM7)
  - Failed transition does NOT publish event (OSM7)
  - bus=None -> no event, no error (OSM7)
  - is_terminal() correct for all 8 states (OSM13)
  - allowed_transitions() returns correct list per state (OSM13)
  - Thread safety: 50 registrations + transitions from 5 threads (OSM5)
  - InvalidTransitionError.context has correct fields (OSM6, OSM12)

Run: python -m pytest tests/unit/test_order_state_machine.py -v
Or:  python tests/unit/test_order_state_machine.py  (standalone mode)
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from broker.order_state_machine import (
    STATES,
    TERMINAL_STATES,
    OrderStateMachine,
    all_states,
    allowed_transitions,
    is_terminal,
)
from core.events import EventBus, OrderStateChanged
from core.exceptions import InvalidTransitionError


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_osm(with_bus: bool = False):
    """Return (osm, bus) or (osm, None)."""
    if with_bus:
        bus = EventBus()
        return OrderStateMachine(bus=bus), bus
    return OrderStateMachine(bus=None), None


def _reg(osm: OrderStateMachine, order_id: str = "ord_test") -> str:
    osm.register(order_id)
    return order_id


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- register() (OSM9)
# ─────────────────────────────────────────────────────────────────────────────

def test_register_puts_order_in_pending() -> None:
    osm, _ = _make_osm()
    osm.register("ord_001")
    assert osm.current_state("ord_001") == "PENDING"
    print("  OK register() puts order in PENDING (OSM9)")


def test_register_twice_raises_value_error() -> None:
    osm, _ = _make_osm()
    osm.register("ord_001")
    raised = False
    try:
        osm.register("ord_001")
    except ValueError:
        raised = True
    assert raised, "Expected ValueError on duplicate register"
    print("  OK register() twice raises ValueError (OSM9)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- current_state() (OSM8)
# ─────────────────────────────────────────────────────────────────────────────

def test_current_state_unknown_raises_value_error() -> None:
    osm, _ = _make_osm()
    raised = False
    try:
        osm.current_state("ord_unknown")
    except ValueError:
        raised = True
    assert raised, "Expected ValueError for unknown order_id"
    print("  OK current_state(unknown) raises ValueError (OSM8)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- valid transitions (OSM2, OSM3)
# ─────────────────────────────────────────────────────────────────────────────

def test_pending_to_submitted_ok() -> None:
    osm, _ = _make_osm()
    _reg(osm, "ord_001")
    osm.transition("ord_001", "SUBMITTED")
    assert osm.current_state("ord_001") == "SUBMITTED"
    print("  OK PENDING -> SUBMITTED (OSM2)")


def test_submitted_to_open_ok() -> None:
    osm, _ = _make_osm()
    _reg(osm, "ord_001")
    osm.transition("ord_001", "SUBMITTED")
    osm.transition("ord_001", "OPEN")
    assert osm.current_state("ord_001") == "OPEN"
    print("  OK SUBMITTED -> OPEN (OSM2)")


def test_open_to_partial_ok() -> None:
    osm, _ = _make_osm()
    _reg(osm, "ord_001")
    osm.transition("ord_001", "SUBMITTED")
    osm.transition("ord_001", "OPEN")
    osm.transition("ord_001", "PARTIAL")
    assert osm.current_state("ord_001") == "PARTIAL"
    print("  OK OPEN -> PARTIAL (OSM2)")


def test_partial_self_loop_ok() -> None:
    """Multiple partial fills: PARTIAL -> PARTIAL is allowed (OSM2)."""
    osm, _ = _make_osm()
    _reg(osm, "ord_001")
    osm.transition("ord_001", "SUBMITTED")
    osm.transition("ord_001", "PARTIAL")
    osm.transition("ord_001", "PARTIAL")   # second partial fill
    osm.transition("ord_001", "PARTIAL")   # third partial fill
    assert osm.current_state("ord_001") == "PARTIAL"
    print("  OK PARTIAL -> PARTIAL self-loop (OSM2)")


def test_partial_to_complete_ok() -> None:
    osm, _ = _make_osm()
    _reg(osm, "ord_001")
    osm.transition("ord_001", "SUBMITTED")
    osm.transition("ord_001", "PARTIAL")
    osm.transition("ord_001", "COMPLETE")
    assert osm.current_state("ord_001") == "COMPLETE"
    print("  OK PARTIAL -> COMPLETE (OSM2)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- invalid transitions (OSM2, OSM3)
# ─────────────────────────────────────────────────────────────────────────────

def test_pending_to_open_raises_invalid_transition() -> None:
    """PENDING -> OPEN is not in the transition table (OSM2)."""
    osm, _ = _make_osm()
    _reg(osm, "ord_001")
    raised = False
    try:
        osm.transition("ord_001", "OPEN")
    except InvalidTransitionError as exc:
        raised = True
        assert exc.context["from_state"] == "PENDING"
        assert exc.context["to_state"] == "OPEN"
    assert raised, "Expected InvalidTransitionError for PENDING -> OPEN"
    print("  OK PENDING -> OPEN raises InvalidTransitionError (OSM2, OSM3)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- terminal states reject all transitions (OSM4)
# ─────────────────────────────────────────────────────────────────────────────

def _assert_terminal_rejects(terminal_state: str, attempt: str) -> None:
    """Helper: register order, drive it to terminal_state, then try attempt."""
    osm, _ = _make_osm()
    osm.register("ord_t")

    # Drive to terminal state via valid path
    if terminal_state == "COMPLETE":
        osm.transition("ord_t", "SUBMITTED")
        osm.transition("ord_t", "COMPLETE")
    elif terminal_state == "CANCELLED":
        osm.transition("ord_t", "SUBMITTED")
        osm.transition("ord_t", "CANCELLED")
    elif terminal_state == "FAILED":
        osm.transition("ord_t", "FAILED")
    elif terminal_state == "EXPIRED":
        osm.transition("ord_t", "SUBMITTED")
        osm.transition("ord_t", "EXPIRED")

    raised = False
    try:
        osm.transition("ord_t", attempt)
    except InvalidTransitionError as exc:
        raised = True
        assert exc.context["from_state"] == terminal_state
        assert exc.context["allowed_next_states"] == []
    assert raised, (
        f"Expected InvalidTransitionError from terminal {terminal_state} -> {attempt}"
    )


def test_complete_rejects_all_transitions() -> None:
    _assert_terminal_rejects("COMPLETE", "OPEN")
    _assert_terminal_rejects("COMPLETE", "PENDING")
    _assert_terminal_rejects("COMPLETE", "COMPLETE")  # same-state also rejected
    print("  OK COMPLETE -> anything raises InvalidTransitionError (OSM4)")


def test_cancelled_rejects_all_transitions() -> None:
    _assert_terminal_rejects("CANCELLED", "OPEN")
    _assert_terminal_rejects("CANCELLED", "CANCELLED")  # same-state also rejected
    print("  OK CANCELLED -> anything raises InvalidTransitionError (OSM4)")


def test_failed_rejects_all_transitions() -> None:
    _assert_terminal_rejects("FAILED", "SUBMITTED")
    _assert_terminal_rejects("FAILED", "FAILED")  # same-state also rejected
    print("  OK FAILED -> anything raises InvalidTransitionError (OSM4)")


def test_expired_rejects_all_transitions() -> None:
    _assert_terminal_rejects("EXPIRED", "OPEN")
    _assert_terminal_rejects("EXPIRED", "EXPIRED")  # same-state also rejected
    print("  OK EXPIRED -> anything raises InvalidTransitionError (OSM4)")


def test_all_four_terminal_states_reject_same_state() -> None:
    """Explicit same-state self-loop rejection for every terminal state (OSM4)."""
    for terminal in TERMINAL_STATES:
        _assert_terminal_rejects(terminal, terminal)
    print(f"  OK All {len(TERMINAL_STATES)} terminal states reject same-state self-loop (OSM4)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- event publishing (OSM7)
# ─────────────────────────────────────────────────────────────────────────────

def test_successful_transition_publishes_event() -> None:
    osm, bus = _make_osm(with_bus=True)
    received: list[OrderStateChanged] = []
    bus.subscribe(OrderStateChanged, received.append)  # type: ignore[arg-type]

    osm.register("ord_001")
    osm.transition("ord_001", "SUBMITTED")

    assert len(received) == 1
    evt = received[0]
    assert isinstance(evt, OrderStateChanged)
    assert evt.order_id == "ord_001"
    assert evt.from_state == "PENDING"
    assert evt.to_state == "SUBMITTED"
    assert evt.source_module == "order_state_machine"
    print("  OK Successful transition publishes OrderStateChanged event (OSM7)")


def test_failed_transition_does_not_publish_event() -> None:
    osm, bus = _make_osm(with_bus=True)
    received: list[OrderStateChanged] = []
    bus.subscribe(OrderStateChanged, received.append)  # type: ignore[arg-type]

    osm.register("ord_001")
    try:
        osm.transition("ord_001", "OPEN")   # PENDING -> OPEN is illegal
    except InvalidTransitionError:
        pass

    assert len(received) == 0, "No event should be published on failed transition"
    print("  OK Failed transition does NOT publish event (OSM7)")


def test_bus_none_no_event_no_error() -> None:
    """bus=None: transitions work normally, no event publishing, no AttributeError."""
    osm, _ = _make_osm(with_bus=False)
    osm.register("ord_001")
    osm.transition("ord_001", "SUBMITTED")   # must not raise
    assert osm.current_state("ord_001") == "SUBMITTED"
    print("  OK bus=None: transition succeeds silently (OSM7)")


def test_multiple_transitions_publish_multiple_events() -> None:
    osm, bus = _make_osm(with_bus=True)
    received: list[OrderStateChanged] = []
    bus.subscribe(OrderStateChanged, received.append)  # type: ignore[arg-type]

    osm.register("ord_001")
    osm.transition("ord_001", "SUBMITTED")
    osm.transition("ord_001", "OPEN")
    osm.transition("ord_001", "PARTIAL")
    osm.transition("ord_001", "COMPLETE")

    assert len(received) == 4
    states = [(e.from_state, e.to_state) for e in received]
    assert states == [
        ("PENDING", "SUBMITTED"),
        ("SUBMITTED", "OPEN"),
        ("OPEN", "PARTIAL"),
        ("PARTIAL", "COMPLETE"),
    ]
    print("  OK 4 transitions publish 4 events in order (OSM7)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- module-level helpers (OSM13)
# ─────────────────────────────────────────────────────────────────────────────

def test_is_terminal_correct_for_all_states() -> None:
    terminal_set = set(TERMINAL_STATES)
    for state in STATES:
        expected = state in terminal_set
        assert is_terminal(state) == expected, (
            f"is_terminal({state!r}) returned {is_terminal(state)}, expected {expected}"
        )
    print(f"  OK is_terminal() correct for all {len(STATES)} states (OSM13)")


def test_allowed_transitions_returns_correct_lists() -> None:
    assert set(allowed_transitions("PENDING")) == {"SUBMITTED", "FAILED"}
    assert set(allowed_transitions("SUBMITTED")) == {
        "OPEN", "PARTIAL", "COMPLETE", "CANCELLED", "FAILED", "EXPIRED"
    }
    assert set(allowed_transitions("OPEN")) == {"PARTIAL", "COMPLETE", "CANCELLED", "EXPIRED", "FAILED"}
    assert set(allowed_transitions("PARTIAL")) == {"PARTIAL", "COMPLETE", "CANCELLED", "FAILED"}
    for terminal in TERMINAL_STATES:
        assert allowed_transitions(terminal) == [], (
            f"Terminal state {terminal!r} should have no allowed transitions"
        )
    print("  OK allowed_transitions() correct for all states (OSM13)")


def test_all_states_returns_all_eight() -> None:
    result = all_states()
    assert len(result) == 8
    assert set(result) == set(STATES)
    print(f"  OK all_states() returns all {len(result)} states (OSM13)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- InvalidTransitionError context fields (OSM6, OSM12)
# ─────────────────────────────────────────────────────────────────────────────

def test_invalid_transition_error_context_fields() -> None:
    osm, _ = _make_osm()
    osm.register("ord_001")
    osm.transition("ord_001", "SUBMITTED")
    osm.transition("ord_001", "COMPLETE")   # terminal

    exc_captured = None
    try:
        osm.transition("ord_001", "OPEN")
    except InvalidTransitionError as exc:
        exc_captured = exc

    assert exc_captured is not None
    ctx = exc_captured.context
    assert ctx["order_id"] == "ord_001"
    assert ctx["from_state"] == "COMPLETE"
    assert ctx["to_state"] == "OPEN"
    assert ctx["allowed_next_states"] == []
    assert isinstance(ctx["allowed_next_states"], list)
    print("  OK InvalidTransitionError context has order_id, from/to_state, allowed_next_states (OSM6, OSM12)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- thread safety (OSM5)
# ─────────────────────────────────────────────────────────────────────────────

def test_thread_safety_concurrent_register_and_transition() -> None:
    """
    50 orders registered and driven through PENDING->SUBMITTED->COMPLETE
    from 5 threads (10 orders each). At the end, every order must be
    in COMPLETE state with no exceptions or race-corrupted state.
    """
    osm, _ = _make_osm()
    errors: list[Exception] = []

    def worker(start: int) -> None:
        for i in range(start, start + 10):
            oid = f"ord_{i:04d}"
            try:
                osm.register(oid)
                osm.transition(oid, "SUBMITTED")
                osm.transition(oid, "COMPLETE")
            except Exception as exc:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i * 10,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Thread errors: {errors}"

    # Verify every order is in COMPLETE
    for i in range(50):
        oid = f"ord_{i:04d}"
        state = osm.current_state(oid)
        assert state == "COMPLETE", f"{oid} is in {state!r}, expected COMPLETE"

    print("  OK 50 orders x 5 threads: all consistent, all COMPLETE (OSM5)")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        test_register_puts_order_in_pending,
        test_register_twice_raises_value_error,
        test_current_state_unknown_raises_value_error,
        test_pending_to_submitted_ok,
        test_submitted_to_open_ok,
        test_open_to_partial_ok,
        test_partial_self_loop_ok,
        test_partial_to_complete_ok,
        test_pending_to_open_raises_invalid_transition,
        test_complete_rejects_all_transitions,
        test_cancelled_rejects_all_transitions,
        test_failed_rejects_all_transitions,
        test_expired_rejects_all_transitions,
        test_all_four_terminal_states_reject_same_state,
        test_successful_transition_publishes_event,
        test_failed_transition_does_not_publish_event,
        test_bus_none_no_event_no_error,
        test_multiple_transitions_publish_multiple_events,
        test_is_terminal_correct_for_all_states,
        test_allowed_transitions_returns_correct_lists,
        test_all_states_returns_all_eight,
        test_invalid_transition_error_context_fields,
        test_thread_safety_concurrent_register_and_transition,
    ]

    print("=" * 70)
    print("order_state_machine.py -- Test Suite")
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
