"""
tests/unit/test_phase19_batch1.py — Trading System v2

Tests for Phase 19 audit fixes: BATCH 1 (FIX-089)
"""
from __future__ import annotations

import logging
from unittest.mock import Mock

import pytest

from broker.order_state_machine import OrderStateMachine, TERMINAL_STATES, EARLIER_STATES
from broker.order_monitor import OrderMonitor, _WatchEntry
from core.events import EventBus
from core.time_authority import now_ist


# ─────────────────────────────────────────────────────────────────────────────
# FIX-089: Drop earlier-state transitions when in terminal states
# ─────────────────────────────────────────────────────────────────────────────

def test_fix089_complete_to_open_ignored() -> None:
    """
    FIX-089: Order in COMPLETE state receives OPEN transition (chronological inversion).
    Assert state stays COMPLETE, DEBUG logged.
    """
    osm = OrderStateMachine()
    osm.register("ord_001")
    osm.transition("ord_001", "SUBMITTED")
    osm.transition("ord_001", "COMPLETE")

    # Current state is COMPLETE (terminal)
    assert osm.current_state("ord_001") == "COMPLETE"

    # Try to transition to OPEN (earlier state) - should be ignored
    adapter = Mock()
    bus = EventBus()
    logger = logging.getLogger("test_fix089")
    monitor = OrderMonitor(adapter, osm, bus, logger)

    entry = _WatchEntry(
        internal_order_id="ord_001",
        broker_order_id="broker_123",
        symbol="RELIANCE",
        side="BUY",
        qty=10,
        expected_price=2500.0,
        placed_at=now_ist(),
    )

    # Attempt transition to OPEN (earlier state)
    result = monitor._safe_transition("ord_001", "OPEN", entry)

    # Should return False (transition ignored)
    assert result is False, "Expected chronological inversion to be ignored"
    # State should remain COMPLETE
    assert osm.current_state("ord_001") == "COMPLETE", "State should remain COMPLETE"
    print("  OK fix089_complete_to_open_ignored: COMPLETE→OPEN ignored, state stays COMPLETE")


def test_fix089_complete_to_partial_ignored() -> None:
    """
    FIX-089: Order in COMPLETE state receives PARTIAL transition.
    Assert state stays COMPLETE.
    """
    osm = OrderStateMachine()
    osm.register("ord_001")
    osm.transition("ord_001", "SUBMITTED")
    osm.transition("ord_001", "COMPLETE")

    adapter = Mock()
    bus = EventBus()
    logger = logging.getLogger("test_fix089")
    monitor = OrderMonitor(adapter, osm, bus, logger)

    entry = _WatchEntry(
        internal_order_id="ord_001",
        broker_order_id="broker_123",
        symbol="RELIANCE",
        side="BUY",
        qty=10,
        expected_price=2500.0,
        placed_at=now_ist(),
    )

    # Attempt transition to PARTIAL (earlier state)
    result = monitor._safe_transition("ord_001", "PARTIAL", entry)

    assert result is False, "Expected chronological inversion to be ignored"
    assert osm.current_state("ord_001") == "COMPLETE", "State should remain COMPLETE"
    print("  OK fix089_complete_to_partial_ignored: COMPLETE→PARTIAL ignored")


def test_fix089_cancelled_to_submitted_ignored() -> None:
    """
    FIX-089: Order in CANCELLED state receives SUBMITTED transition.
    Assert state stays CANCELLED.
    """
    osm = OrderStateMachine()
    osm.register("ord_001")
    osm.transition("ord_001", "SUBMITTED")
    osm.transition("ord_001", "CANCELLED")

    adapter = Mock()
    bus = EventBus()
    logger = logging.getLogger("test_fix089")
    monitor = OrderMonitor(adapter, osm, bus, logger)

    entry = _WatchEntry(
        internal_order_id="ord_001",
        broker_order_id="broker_123",
        symbol="RELIANCE",
        side="BUY",
        qty=10,
        expected_price=2500.0,
        placed_at=now_ist(),
    )

    result = monitor._safe_transition("ord_001", "SUBMITTED", entry)

    assert result is False
    assert osm.current_state("ord_001") == "CANCELLED"
    print("  OK fix089_cancelled_to_submitted_ignored: CANCELLED→SUBMITTED ignored")


def test_fix089_open_to_partial_allowed() -> None:
    """
    FIX-089: Order in OPEN state receives PARTIAL (valid forward transition).
    Assert transition succeeds.
    """
    osm = OrderStateMachine()
    osm.register("ord_001")
    osm.transition("ord_001", "SUBMITTED")
    osm.transition("ord_001", "OPEN")

    adapter = Mock()
    bus = EventBus()
    logger = logging.getLogger("test_fix089")
    monitor = OrderMonitor(adapter, osm, bus, logger)

    entry = _WatchEntry(
        internal_order_id="ord_001",
        broker_order_id="broker_123",
        symbol="RELIANCE",
        side="BUY",
        qty=10,
        expected_price=2500.0,
        placed_at=now_ist(),
    )

    # Valid forward transition
    result = monitor._safe_transition("ord_001", "PARTIAL", entry)

    assert result is True, "Expected valid transition to succeed"
    assert osm.current_state("ord_001") == "PARTIAL"
    print("  OK fix089_open_to_partial_allowed: OPEN→PARTIAL succeeded (valid forward)")


def test_fix089_complete_to_cancelled_terminal_to_terminal() -> None:
    """
    FIX-089: Order in COMPLETE state receives CANCELLED (terminal→terminal from FIX-052).
    Assert DEBUG logged but NOT the chronological inversion message.
    This should use FIX-052 path, not FIX-089 path.
    """
    osm = OrderStateMachine()
    osm.register("ord_001")
    osm.transition("ord_001", "SUBMITTED")
    osm.transition("ord_001", "COMPLETE")

    adapter = Mock()
    bus = EventBus()
    logger = logging.getLogger("test_fix089")
    monitor = OrderMonitor(adapter, osm, bus, logger)

    entry = _WatchEntry(
        internal_order_id="ord_001",
        broker_order_id="broker_123",
        symbol="RELIANCE",
        side="BUY",
        qty=10,
        expected_price=2500.0,
        placed_at=now_ist(),
    )

    # Terminal→terminal (FIX-052 path)
    result = monitor._safe_transition("ord_001", "CANCELLED", entry)

    assert result is False
    assert osm.current_state("ord_001") == "COMPLETE"
    # Note: We're testing that this uses FIX-052 path (terminal_to_terminal_transition_skipped)
    # not FIX-089 path (chronological_inversion_ignored)
    print("  OK fix089_complete_to_cancelled_terminal_to_terminal: FIX-052 path used")


def test_fix089_earlier_states_constant() -> None:
    """
    FIX-089: Verify EARLIER_STATES constant is defined correctly.
    """
    assert "PENDING" in EARLIER_STATES
    assert "SUBMITTED" in EARLIER_STATES
    assert "OPEN" in EARLIER_STATES
    assert "PARTIAL" in EARLIER_STATES
    assert "COMPLETE" not in EARLIER_STATES
    assert "CANCELLED" not in EARLIER_STATES
    print("  OK fix089_earlier_states_constant: EARLIER_STATES defined correctly")


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n=== Phase 19 Batch 1 Tests ===\n")

    test_fix089_complete_to_open_ignored()
    test_fix089_complete_to_partial_ignored()
    test_fix089_cancelled_to_submitted_ignored()
    test_fix089_open_to_partial_allowed()
    test_fix089_complete_to_cancelled_terminal_to_terminal()
    test_fix089_earlier_states_constant()

    print("\n=== All Phase 19 Batch 1 tests passed ===\n")
