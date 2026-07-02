"""
tests/unit/test_fix068_timeout_recovery.py — Trading System v2

Tests for FIX-068: BrokerTimeoutError recovery with UNKNOWN_IN_FLIGHT state.

Scope (post A-1/E-1, 2026-07-02): this file still covers the ORDER-PLACER side of
FIX-068 — a place_order timeout transitions the trade to UNKNOWN_IN_FLIGHT WITHOUT
releasing capital and enqueues it for recovery (unchanged, still correct).

The RECONCILER side changed: the old OrderReconciler._check_unknown_in_flight
(timeout-only; keyed on a broker_order_id a timed-out entry never learned; blind
FAILED + capital release after 3 polls) was the A-1 naked-orphan bug and has been
REPLACED by the unified tag-correlation recovery (_recover_in_flight_entries /
_adopt_or_fail). Those obsolete reconciler-method tests were removed from here;
the new recovery is covered end-to-end in tests/unit/test_a1e1_orphan_recovery.py.
"""
from __future__ import annotations

import threading
from unittest.mock import Mock, patch

import pytest

from broker.order_state_machine import OrderStateMachine, STATES
from core.exceptions import BrokerTimeoutError, OrderRejectedError
from core.time_authority import now_ist
from orders.order_placer import OrderPlacer
from orders.order_reconciler import OrderReconciler, ReconciliationAction


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: UNKNOWN_IN_FLIGHT state exists in OrderStateMachine
# ─────────────────────────────────────────────────────────────────────────────

def test_unknown_in_flight_state_exists():
    """FIX-068: UNKNOWN_IN_FLIGHT is a valid order state."""
    assert "UNKNOWN_IN_FLIGHT" in STATES


def test_unknown_in_flight_transitions():
    """FIX-068: UNKNOWN_IN_FLIGHT has correct transitions."""
    from broker.order_state_machine import allowed_transitions, is_terminal

    # UNKNOWN_IN_FLIGHT is not terminal
    assert not is_terminal("UNKNOWN_IN_FLIGHT")

    # Can transition from PENDING to UNKNOWN_IN_FLIGHT
    assert "UNKNOWN_IN_FLIGHT" in allowed_transitions("PENDING")

    # Can transition from UNKNOWN_IN_FLIGHT to OPEN, PARTIAL, COMPLETE, FAILED
    allowed = allowed_transitions("UNKNOWN_IN_FLIGHT")
    assert "OPEN" in allowed
    assert "PARTIAL" in allowed
    assert "COMPLETE" in allowed
    assert "FAILED" in allowed


def test_state_machine_pending_to_unknown_in_flight():
    """FIX-068: State machine allows PENDING → UNKNOWN_IN_FLIGHT transition."""
    osm = OrderStateMachine()
    osm.register("ord_test123")
    assert osm.current_state("ord_test123") == "PENDING"

    osm.transition("ord_test123", "UNKNOWN_IN_FLIGHT")
    assert osm.current_state("ord_test123") == "UNKNOWN_IN_FLIGHT"


def test_state_machine_unknown_in_flight_to_complete():
    """FIX-068: State machine allows UNKNOWN_IN_FLIGHT → COMPLETE transition."""
    osm = OrderStateMachine()
    osm.register("ord_test456")
    osm.transition("ord_test456", "UNKNOWN_IN_FLIGHT")
    osm.transition("ord_test456", "COMPLETE")
    assert osm.current_state("ord_test456") == "COMPLETE"


def test_state_machine_unknown_in_flight_to_failed():
    """FIX-068: State machine allows UNKNOWN_IN_FLIGHT → FAILED transition."""
    osm = OrderStateMachine()
    osm.register("ord_test789")
    osm.transition("ord_test789", "UNKNOWN_IN_FLIGHT")
    osm.transition("ord_test789", "FAILED")
    assert osm.current_state("ord_test789") == "FAILED"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: order_placer catches BrokerTimeoutError and transitions correctly
# ─────────────────────────────────────────────────────────────────────────────

def test_order_placer_timeout_error_handling():
    """
    FIX-068: BrokerTimeoutError during place_order → UNKNOWN_IN_FLIGHT,
    capital NOT released, added to timeout recovery queue.
    """
    # Create mocks
    mock_engine = Mock()
    mock_order_manager = Mock()
    mock_fund_manager = Mock()
    mock_bus = Mock()
    mock_logger = Mock()
    mock_order_monitor = Mock()
    mock_cost_calculator = Mock()

    # Mock engine.execute() to raise BrokerTimeoutError
    mock_engine.execute.side_effect = BrokerTimeoutError(
        "ReadTimeout during place_order",
        operation="place_order",
        symbol="RELIANCE",
    )

    # Mock order_manager.create_trade() to return a trade_id
    mock_order_manager.create_trade.return_value = "trade_timeout_test"
    mock_order_manager.link_signal_trade.return_value = None

    # Mock fund_manager.required_margin()
    mock_fund_manager.required_margin.return_value = 1000.0

    # Create OrderPlacer
    placer = OrderPlacer(
        entry_engine=mock_engine,
        order_manager=mock_order_manager,
        fund_manager=mock_fund_manager,
        bus=mock_bus,
        logger=mock_logger,
        order_monitor=mock_order_monitor,
        cost_calculator=mock_cost_calculator,
        rr_ratio=2.0,
        default_order_protocol="LIMIT_TRIPLE",
    )

    # Attempt to place order (should raise BrokerTimeoutError)
    with pytest.raises(BrokerTimeoutError):
        placer.place(
            symbol="RELIANCE",
            side="BUY",
            qty=100,
            entry_price=2500.0,
            sl_price=2450.0,
            intent="INTRADAY",
            signal_id="sig_test123",
            reservation_id="res_test456",
        )

    # Verify trade status was updated to UNKNOWN_IN_FLIGHT
    # FIX-071 Part A adds PENDING update before broker call, so we expect 2 calls
    assert mock_order_manager.update_trade_status.call_count == 2
    calls = mock_order_manager.update_trade_status.call_args_list
    assert calls[0][0] == ("trade_timeout_test", "PENDING")  # FIX-071 Part A
    assert calls[1][0] == ("trade_timeout_test", "UNKNOWN_IN_FLIGHT")  # FIX-068

    # Verify capital was NOT released (fund_manager.release NOT called)
    mock_fund_manager.release.assert_not_called()

    # Verify trade was added to timeout recovery queue
    timeout_trades = placer.get_timeout_recovery_trades()
    assert "trade_timeout_test" in timeout_trades


def test_order_placer_timeout_recovery_queue_methods():
    """FIX-068: Test timeout recovery queue get/remove methods."""
    mock_engine = Mock()
    mock_order_manager = Mock()
    mock_fund_manager = Mock()
    mock_bus = Mock()
    mock_logger = Mock()
    mock_order_monitor = Mock()
    mock_cost_calculator = Mock()

    placer = OrderPlacer(
        entry_engine=mock_engine,
        order_manager=mock_order_manager,
        fund_manager=mock_fund_manager,
        bus=mock_bus,
        logger=mock_logger,
        order_monitor=mock_order_monitor,
        cost_calculator=mock_cost_calculator,
    )

    # Initially empty
    assert placer.get_timeout_recovery_trades() == []

    # Manually add an entry (simulating timeout)
    with placer._timeout_recovery_lock:
        placer._timeout_recovery_queue["trade_test1"] = {
            "signal_id": "sig_1",
            "reservation_id": "res_1",
            "symbol": "RELIANCE",
            "added_at": now_ist().isoformat(),
        }

    # Verify get
    timeout_trades = placer.get_timeout_recovery_trades()
    assert "trade_test1" in timeout_trades

    # Verify remove
    entry = placer.remove_from_timeout_recovery("trade_test1")
    assert entry is not None
    assert entry["signal_id"] == "sig_1"
    assert entry["reservation_id"] == "res_1"

    # Verify removed
    assert placer.get_timeout_recovery_trades() == []

    # Remove non-existent returns None
    assert placer.remove_from_timeout_recovery("nonexistent") is None

# ─────────────────────────────────────────────────────────────────────────────
# Reconciler-side recovery: MOVED. The old _check_unknown_in_flight tests
# (order-found / not-found-after-3-polls / broker-unreachable / full-flow) tested
# a method that no longer exists — the A-1/E-1 unified tag-correlation recovery
# (_recover_in_flight_entries) supersedes it. See tests/unit/test_a1e1_orphan_recovery.py
# for the full matrix (adopt-filled / defer-resting / confirmed-absent-FAILED /
# rejected / HARD_KILL flatten / poll-fail-defer / idempotent / paper-parity).
# ─────────────────────────────────────────────────────────────────────────────
