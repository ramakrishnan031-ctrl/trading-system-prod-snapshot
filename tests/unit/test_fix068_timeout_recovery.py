"""
tests/unit/test_fix068_timeout_recovery.py — Trading System v2

Tests for FIX-068: BrokerTimeoutError recovery with UNKNOWN_IN_FLIGHT state.

Test scenarios:
1. BrokerTimeoutError during place_order → UNKNOWN_IN_FLIGHT, capital NOT released
2. Reconciler finds order at broker (FILLED) → local state updated
3. Order not found after 3 polls (45s) → FAILED, capital released
4. Broker unreachable during reconciliation → stays UNKNOWN_IN_FLIGHT
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
# Test 3: Reconciler CHECK_UNKNOWN_IN_FLIGHT - order found at broker
# ─────────────────────────────────────────────────────────────────────────────

def test_reconciler_unknown_in_flight_found_at_broker():
    """
    FIX-068: Reconciler finds UNKNOWN_IN_FLIGHT order at broker (FILLED) →
    updates local state, removes from recovery queue.
    """
    # Create mocks
    mock_store = Mock()
    mock_adapter = Mock()
    mock_fm = Mock()
    mock_ks = Mock()
    mock_notifier = Mock()
    mock_bus = Mock()
    mock_logger = Mock()
    mock_cfg = Mock(poll_interval_sec=15, capital_drift_tolerance=100.0)
    mock_quote_fn = Mock()
    mock_order_placer = Mock()

    # Mock order_placer.get_timeout_recovery_trades()
    mock_order_placer.get_timeout_recovery_trades.return_value = ["trade_timeout1"]

    # Mock adapter.get_open_orders() - order IS found
    mock_adapter.get_open_orders.return_value = [
        {"order_id": "broker_ord_123", "status": "COMPLETE", "symbol": "RELIANCE"}
    ]

    # Mock store.get_orders_for_trade() - local DB has this order
    mock_store.get_orders_for_trade.return_value = [
        {
            "order_id": "broker_ord_123",
            "trade_id": "trade_timeout1",
            "leg": "ENTRY",
            "status": "PENDING",
        }
    ]

    # Mock order_placer.remove_from_timeout_recovery()
    mock_order_placer.remove_from_timeout_recovery.return_value = {
        "signal_id": "sig_1",
        "reservation_id": "res_1",
        "symbol": "RELIANCE",
    }

    # Create reconciler
    reconciler = OrderReconciler(
        state_store=mock_store,
        adapter=mock_adapter,
        fund_manager=mock_fm,
        kill_switch=mock_ks,
        notifier=mock_notifier,
        bus=mock_bus,
        logger=mock_logger,
        cfg=mock_cfg,
        quote_fn=mock_quote_fn,
        broker_orders_fn=mock_adapter.get_open_orders,
        order_placer=mock_order_placer,
    )

    # FIX-165g: _order_mgr is constructed internally; mock its methods
    reconciler._order_mgr = Mock()
    reconciler._order_mgr.get_trade.return_value = {
        "trade_id": "trade_timeout1",
        "symbol": "RELIANCE",
        "status": "UNKNOWN_IN_FLIGHT",
    }

    # Run the check
    actions = reconciler._check_unknown_in_flight()

    # Verify action was created
    assert len(actions) == 1
    action = actions[0]
    assert action.check_name == "UNKNOWN_IN_FLIGHT_RESOLVED"
    assert action.tier == "RECOVERABLE"
    assert action.trade_id == "trade_timeout1"
    assert action.success is True

    # Verify trade was removed from recovery queue
    mock_order_placer.remove_from_timeout_recovery.assert_called_once_with("trade_timeout1")

    # FIX-165g: Verify update_trade_status on _order_mgr (not _store)
    reconciler._order_mgr.update_trade_status.assert_called_once_with("trade_timeout1", "PENDING_FILL")


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Reconciler CHECK_UNKNOWN_IN_FLIGHT - order NOT found after 3 polls
# ─────────────────────────────────────────────────────────────────────────────

def test_reconciler_unknown_in_flight_not_found_after_3_polls():
    """
    FIX-068: Reconciler doesn't find order after 3 polls (45s) →
    marks FAILED, releases capital.
    """
    mock_store = Mock()
    mock_adapter = Mock()
    mock_fm = Mock()
    mock_ks = Mock()
    mock_notifier = Mock()
    mock_bus = Mock()
    mock_logger = Mock()
    mock_cfg = Mock(poll_interval_sec=15, capital_drift_tolerance=100.0)
    mock_quote_fn = Mock()
    mock_order_placer = Mock()

    # Mock order_placer.get_timeout_recovery_trades()
    mock_order_placer.get_timeout_recovery_trades.return_value = ["trade_timeout2"]

    # Mock adapter.get_open_orders() - order NOT found
    mock_adapter.get_open_orders.return_value = []

    # Mock store.get_orders_for_trade() - local DB has order
    mock_store.get_orders_for_trade.return_value = [
        {
            "order_id": "broker_ord_999",
            "trade_id": "trade_timeout2",
            "leg": "ENTRY",
            "status": "PENDING",
        }
    ]

    # Mock order_placer.remove_from_timeout_recovery()
    mock_order_placer.remove_from_timeout_recovery.return_value = {
        "signal_id": "sig_2",
        "reservation_id": "res_2",
        "symbol": "TCS",
    }

    # Create reconciler
    reconciler = OrderReconciler(
        state_store=mock_store,
        adapter=mock_adapter,
        fund_manager=mock_fm,
        kill_switch=mock_ks,
        notifier=mock_notifier,
        bus=mock_bus,
        logger=mock_logger,
        cfg=mock_cfg,
        quote_fn=mock_quote_fn,
        broker_orders_fn=mock_adapter.get_open_orders,
        order_placer=mock_order_placer,
    )

    # FIX-165g: _order_mgr is constructed internally; mock its methods
    reconciler._order_mgr = Mock()
    reconciler._order_mgr.get_trade.return_value = {
        "trade_id": "trade_timeout2",
        "symbol": "TCS",
        "status": "UNKNOWN_IN_FLIGHT",
    }

    # Simulate 3 polls
    for poll_num in range(1, 4):
        actions = reconciler._check_unknown_in_flight()

        if poll_num < 3:
            # First 2 polls: still polling
            assert len(actions) == 1
            action = actions[0]
            assert action.check_name == "UNKNOWN_IN_FLIGHT_POLLING"
            assert action.tier == "COSMETIC"
            assert f"poll {poll_num}/3" in action.description

            # NOT removed from queue yet
            assert reconciler._timeout_poll_counts["trade_timeout2"] == poll_num
        else:
            # 3rd poll: timeout, mark FAILED
            assert len(actions) == 1
            action = actions[0]
            assert action.check_name == "UNKNOWN_IN_FLIGHT_TIMEOUT"
            assert action.tier == "UNRECOVERABLE"
            assert action.trade_id == "trade_timeout2"
            assert "marked FAILED" in action.description

            # FIX-165g: Verify update_trade_status on _order_mgr (not _store)
            reconciler._order_mgr.update_trade_status.assert_called_with("trade_timeout2", "FAILED")

            # Verify capital released
            mock_fm.release.assert_called_once()
            assert mock_fm.release.call_args[0][0] == "res_2"
            assert "timeout_not_found_after_3_polls" in mock_fm.release.call_args[0][1]

            # Verify removed from queue
            mock_order_placer.remove_from_timeout_recovery.assert_called_once_with("trade_timeout2")


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Reconciler CHECK_UNKNOWN_IN_FLIGHT - broker unreachable
# ─────────────────────────────────────────────────────────────────────────────

def test_reconciler_unknown_in_flight_broker_unreachable():
    """
    FIX-068: Broker unreachable during reconciliation →
    stays UNKNOWN_IN_FLIGHT, exception propagates to _reconcile() outer handler.
    """
    mock_store = Mock()
    mock_adapter = Mock()
    mock_fm = Mock()
    mock_ks = Mock()
    mock_notifier = Mock()
    mock_bus = Mock()
    mock_logger = Mock()
    mock_cfg = Mock(poll_interval_sec=15, capital_drift_tolerance=100.0)
    mock_quote_fn = Mock()
    mock_order_placer = Mock()

    # Mock order_placer.get_timeout_recovery_trades()
    mock_order_placer.get_timeout_recovery_trades.return_value = ["trade_timeout3"]

    # Mock adapter.get_open_orders() - raises BrokerTimeoutError
    mock_adapter.get_open_orders.side_effect = BrokerTimeoutError(
        "Broker unreachable",
        operation="get_open_orders",
    )

    # Create reconciler
    reconciler = OrderReconciler(
        state_store=mock_store,
        adapter=mock_adapter,
        fund_manager=mock_fm,
        kill_switch=mock_ks,
        notifier=mock_notifier,
        bus=mock_bus,
        logger=mock_logger,
        cfg=mock_cfg,
        quote_fn=mock_quote_fn,
        broker_orders_fn=mock_adapter.get_open_orders,
        order_placer=mock_order_placer,
    )

    # Run the check - should raise BrokerTimeoutError
    with pytest.raises(BrokerTimeoutError):
        reconciler._check_unknown_in_flight()

    # Verify trade was NOT removed from recovery queue
    mock_order_placer.remove_from_timeout_recovery.assert_not_called()

    # Verify capital was NOT released
    mock_fm.release.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Integration - full flow from timeout to recovery
# ─────────────────────────────────────────────────────────────────────────────

def test_full_timeout_recovery_flow():
    """
    FIX-068: Integration test for full timeout recovery flow:
    1. BrokerTimeoutError in order_placer
    2. Trade added to recovery queue
    3. Reconciler finds order at broker on 2nd poll
    4. Trade removed from recovery queue, status updated
    """
    # This is a simplified integration test - in a real scenario you'd set up
    # the full system with actual DB, but for unit tests we mock the key parts

    # Setup mocks
    mock_engine = Mock()
    mock_om = Mock()
    mock_fm = Mock()
    mock_bus = Mock()
    mock_logger = Mock()
    mock_order_monitor = Mock()
    mock_cost_calc = Mock()
    mock_store = Mock()
    mock_adapter = Mock()
    mock_ks = Mock()
    mock_notifier = Mock()
    mock_cfg = Mock(poll_interval_sec=15, capital_drift_tolerance=100.0)
    mock_quote_fn = Mock()

    # Step 1: OrderPlacer encounters timeout
    mock_engine.execute.side_effect = BrokerTimeoutError("timeout", operation="place_order")
    mock_om.create_trade.return_value = "trade_integration1"
    mock_om.link_signal_trade.return_value = None
    mock_fm.required_margin.return_value = 1000.0

    placer = OrderPlacer(
        entry_engine=mock_engine,
        order_manager=mock_om,
        fund_manager=mock_fm,
        bus=mock_bus,
        logger=mock_logger,
        order_monitor=mock_order_monitor,
        cost_calculator=mock_cost_calc,
    )

    # Attempt place (should timeout)
    with pytest.raises(BrokerTimeoutError):
        placer.place(
            symbol="INFY",
            side="BUY",
            qty=50,
            entry_price=1500.0,
            sl_price=1475.0,
            intent="INTRADAY",
            signal_id="sig_int1",
            reservation_id="res_int1",
        )

    # Verify in recovery queue
    assert "trade_integration1" in placer.get_timeout_recovery_trades()

    # Step 2: Reconciler checks (poll 1 - not found)
    mock_adapter.get_open_orders.return_value = []
    mock_store.get_orders_for_trade.return_value = [
        {"order_id": "ord_int1", "trade_id": "trade_integration1", "leg": "ENTRY"}
    ]

    reconciler = OrderReconciler(
        state_store=mock_store,
        adapter=mock_adapter,
        fund_manager=mock_fm,
        kill_switch=mock_ks,
        notifier=mock_notifier,
        bus=mock_bus,
        logger=mock_logger,
        cfg=mock_cfg,
        quote_fn=mock_quote_fn,
        broker_orders_fn=mock_adapter.get_open_orders,
        order_placer=placer,
    )

    # FIX-165g: _order_mgr is constructed internally; mock its methods
    reconciler._order_mgr = Mock()
    reconciler._order_mgr.get_trade.return_value = {
        "trade_id": "trade_integration1",
        "symbol": "INFY",
        "status": "UNKNOWN_IN_FLIGHT",
    }

    actions = reconciler._check_unknown_in_flight()
    assert len(actions) == 1
    assert actions[0].check_name == "UNKNOWN_IN_FLIGHT_POLLING"
    assert "poll 1/3" in actions[0].description

    # Step 3: Poll 2 - order found!
    mock_adapter.get_open_orders.return_value = [
        {"order_id": "ord_int1", "status": "COMPLETE", "symbol": "INFY"}
    ]

    actions = reconciler._check_unknown_in_flight()
    assert len(actions) == 1
    assert actions[0].check_name == "UNKNOWN_IN_FLIGHT_RESOLVED"
    assert actions[0].tier == "RECOVERABLE"

    # Verify removed from recovery queue
    assert "trade_integration1" not in placer.get_timeout_recovery_trades()

    # FIX-165g: Verify update_trade_status on _order_mgr (not _store)
    reconciler._order_mgr.update_trade_status.assert_called_with("trade_integration1", "PENDING_FILL")
