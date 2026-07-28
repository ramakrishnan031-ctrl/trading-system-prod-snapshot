"""
tests/unit/test_order_placer_fix061.py — FIX-061 SL-M Retry on First Valid LTP

Tests for LTP validation error retry mechanism in OrderPlacer.
"""
from unittest.mock import MagicMock, Mock, patch

import pytest

from core.exceptions import BrokerError, OrderRejectedError
from orders.order_placer import OrderPlacer, _ExitRetryParams, _FillEntry


@pytest.fixture
def mock_deps():
    """Minimal OrderPlacer dependencies for testing."""
    return {
        "entry_engine": Mock(),
        "order_manager": Mock(),
        "fund_manager": Mock(),
        "bus": Mock(),
        "logger": Mock(),
        "order_monitor": Mock(),
        "cost_calculator": Mock(),
        "kill_switch": Mock(),
        "product_resolver": Mock(resolve=Mock(return_value="MIS")),
        "live_feed": Mock(),
        "mode": "PAPER",
    }


@pytest.fixture
def placer(mock_deps):
    """OrderPlacer instance with live_feed and instrument_cache mocked."""
    placer = OrderPlacer(**mock_deps)
    # Mock instrument_cache
    mock_cache = Mock()
    mock_row = Mock(instrument_token=12345, symbol="TESTSTOCK")
    mock_cache.get_by_symbol = Mock(return_value=mock_row)
    placer.set_instrument_cache(mock_cache)

    # ── H-3 fixture repair (28-Jul-2026) ────────────────────────────────────
    # H-3 (`a254a82`, 05-Jul-2026) added a re-read at the top of
    # `_retry_limit_triple_exits` (order_placer.py:3512-3513):
    #     trade = store.get_trade_for_tgt_retry(trade_id)
    #     if trade is None or trade["status"] not in ("OPEN", "PARTIAL"): return
    # `store` is `self._om._store`, and `order_manager` is a bare Mock here, so
    # that returned a Mock and `trade["status"]` raised
    #     TypeError: 'Mock' object is not subscriptable
    # ⇒ FOUR tests below died INSIDE production code before reaching a single
    # assertion, and sat in the "known PC-env failures" bucket for 22+ days
    # while covering NOTHING on the LTP-retry -> emergency-exit -> HARD_KILL path.
    #
    # ⭐ This supplies what the guard REQUIRES; it weakens no assertion. The real
    # `get_trade_for_tgt_retry` returns an `sqlite3.Row` (state_store.py:1543);
    # a dict is a faithful stand-in, and the keys below are exactly that query's
    # SELECT columns, valued to match `fill_entry` (TESTSTOCK / 100 @ 100.0 /
    # SL 95 / TGT 105 / LONG / MIS). Status is OPEN so the guard PASSES and
    # execution proceeds to the assertions -- the point is to REACH them, not to
    # make them green.
    placer._om._store.get_trade_for_tgt_retry = Mock(return_value={
        "trade_id": "T001",
        "signal_id": "S001",
        "symbol": "TESTSTOCK",
        "direction": "LONG",
        "qty_filled": 100,
        "entry_actual_price": 100.0,
        "sl_initial": 95.0,
        "tgt_initial": 105.0,
        "status": "OPEN",
        "needs_tgt_retry": 1,
        "tgt_retry_count": 0,
        "tgt_risk_reward_applied": None,
        "product": "MIS",
        "leg": "ENTRY",
    })
    # ⛔ AND `get_sl_order_for_trade` MUST RETURN None -- this one is load-bearing
    # and easy to get backwards. H-3 added at order_placer.py:3524:
    #     if store.get_sl_order_for_trade(trade_id) is not None:
    #         ... self.retry_tgt_for_trade(trade_id); return
    # i.e. a NON-None SL sends execution down the "an SL already exists, place the
    # TGT only" branch and RETURNS -- `place_deferred_exits` is never reached and
    # neither is the hard_kill these tests assert. FIX-061's scenario is the
    # opposite: the exits were never placed, so there is NO SL yet and the whole
    # LIMIT_TRIPLE is being retried. A bare Mock is non-None, which is why the
    # default silently took the wrong branch.
    placer._om._store.get_sl_order_for_trade = Mock(return_value=None)
    # Idempotency scan inside retry_tgt_for_trade (iterated at :2687); empty = no
    # live TGT. Kept for the tests in this file that DO route through that path.
    placer._om._store.get_orders_for_trade = Mock(return_value=[])
    return placer


@pytest.fixture
def fill_entry():
    """Sample _FillEntry for testing."""
    return _FillEntry(
        trade_id="T001",
        reservation_id="R001",
        symbol="TESTSTOCK",
        qty=100,
        leg="ENTRY",
        order_protocol="LIMIT_TRIPLE",
        direction="LONG",
        side="BUY",
        sl_price=95.0,
        tgt_price=105.0,
        intent="INTRADAY",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Error 16418 received → assert added to _pending_exit_retry, not abandoned
# ─────────────────────────────────────────────────────────────────────────────

def test_ltp_error_16418_added_to_retry_queue(placer, fill_entry):
    """
    FIX-061 Test 1: Error code 16418 → added to _pending_exit_retry, not hard_kill.
    """
    # Simulate error 16418 from broker
    exc = OrderRejectedError(
        "Trigger price validation failed",
        kite_status_code=16418,
        rejection_reason="Trigger price cannot be validated",
        symbol="TESTSTOCK",
    )

    # Mock place_deferred_exits to raise the exception
    placer._engine.place_deferred_exits = Mock(side_effect=exc)

    # Call _place_limit_triple_exits
    placer._place_limit_triple_exits(
        trade_id="T001",
        fill_entry=fill_entry,
        qty_filled=100,
        avg_fill_price=100.0,
        reason="entry_fill",
    )

    # Assert added to _pending_exit_retry
    assert "T001" in placer._pending_exit_retry
    params = placer._pending_exit_retry["T001"]
    assert params.trade_id == "T001"
    assert params.fill_entry.symbol == "TESTSTOCK"
    assert params.retry_count == 0

    # Assert hard_kill was NOT called
    placer._kill_switch.hard_kill.assert_not_called()

    # Assert subscribed to LiveFeed
    placer._live_feed.subscribe.assert_called_once_with([12345])
    placer._live_feed.register_callback.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: First valid LTP tick → assert SL-M re-submitted
# ─────────────────────────────────────────────────────────────────────────────

def test_first_valid_ltp_triggers_retry(placer, fill_entry):
    """
    FIX-061 Test 2: First valid LTP tick → SL-M re-submitted.
    """
    # Pre-populate _pending_exit_retry
    params = _ExitRetryParams(
        trade_id="T001",
        fill_entry=fill_entry,
        qty_filled=100,
        avg_fill_price=100.0,
        reason="entry_fill",
        retry_count=0,
    )
    placer._pending_exit_retry["T001"] = params

    # Mock successful retry
    mock_legs = Mock(
        sl_broker_order_id="SL123",
        sl_internal_id="ISL123",
        sl_order_type="SL-M",
        sl_price=95.0,
        sl_trigger_price=95.0,
        tgt_broker_order_id="TGT123",
        tgt_internal_id="ITGT123",
        tgt_price=105.0,
    )
    placer._engine.place_deferred_exits = Mock(return_value=mock_legs)
    placer._om.insert_orders_atomic = Mock()

    # Simulate tick with valid LTP
    ticks = [{"instrument_token": 12345, "last_price": 100.5}]
    placer._on_ltp_tick_for_retry(ticks)

    # Assert _place_limit_triple_exits was called (retry happened)
    placer._engine.place_deferred_exits.assert_called_once()
    call_args = placer._engine.place_deferred_exits.call_args
    assert call_args.kwargs["symbol"] == "TESTSTOCK"
    assert call_args.kwargs["qty"] == 100

    # Assert removed from _pending_exit_retry after success
    assert "T001" not in placer._pending_exit_retry


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Successful re-submission → assert removed from dict
# ─────────────────────────────────────────────────────────────────────────────

def test_successful_retry_removes_from_queue(placer, fill_entry):
    """
    FIX-061 Test 3: Successful re-submission → removed from _pending_exit_retry.
    """
    # Pre-populate _pending_exit_retry
    params = _ExitRetryParams(
        trade_id="T001",
        fill_entry=fill_entry,
        qty_filled=100,
        avg_fill_price=100.0,
        reason="entry_fill",
        retry_count=0,
    )
    placer._pending_exit_retry["T001"] = params

    # Mock successful placement
    mock_legs = Mock(
        sl_broker_order_id="SL123",
        sl_internal_id="ISL123",
        sl_order_type="SL-M",
        sl_price=95.0,
        sl_trigger_price=95.0,
        tgt_broker_order_id="TGT123",
        tgt_internal_id="ITGT123",
        tgt_price=105.0,
    )
    placer._engine.place_deferred_exits = Mock(return_value=mock_legs)
    placer._om.insert_orders_atomic = Mock()

    # Trigger retry via tick
    ticks = [{"instrument_token": 12345, "last_price": 100.5}]
    placer._on_ltp_tick_for_retry(ticks)

    # Assert removed from queue
    assert "T001" not in placer._pending_exit_retry


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: 3 failed retries → assert soft_kill triggered
# ─────────────────────────────────────────────────────────────────────────────

def test_exhausted_retries_triggers_emergency_exit_and_hard_kill(placer, fill_entry):
    """
    FIX-061/FIX-148: After MAX_RETRIES (3) failed attempts → emergency market exit
    + hard_kill triggered.
    """
    # Pre-populate with retry_count = 2 (next will be 3rd and final)
    params = _ExitRetryParams(
        trade_id="T001",
        fill_entry=fill_entry,
        qty_filled=100,
        avg_fill_price=100.0,
        reason="entry_fill",
        retry_count=2,  # Already tried twice
    )
    placer._pending_exit_retry["T001"] = params

    # Mock continued LTP error
    exc = OrderRejectedError(
        "LTP cannot be validated",
        kite_status_code=16418,
        rejection_reason="LTP cannot be validated",
        symbol="TESTSTOCK",
    )
    placer._engine.place_deferred_exits = Mock(side_effect=exc)

    # Mock the adapter for emergency market exit.
    # Bug B (P0 2026-06-15): emergency exit uses self._adapter, not
    # self._engine.adapter (FullEntryEngine has no `adapter` attribute).
    mock_placed = Mock(broker_order_id="EMG_001", internal_order_id="INT_EMG_001")
    placer._adapter = Mock()
    placer._adapter.place_order = Mock(return_value=mock_placed)

    # Trigger retry (this will be 3rd attempt)
    ticks = [{"instrument_token": 12345, "last_price": 100.5}]
    placer._on_ltp_tick_for_retry(ticks)

    # FIX-148: hard_kill triggered (was soft_kill before)
    placer._kill_switch.hard_kill.assert_called_once()

    # FIX-148: Emergency market exit was attempted
    placer._adapter.place_order.assert_called_once()
    call_args = placer._adapter.place_order.call_args
    assert call_args.kwargs["order_type"] == "MARKET"
    assert call_args.kwargs["side"] == "SELL"  # LONG trade → SELL exit


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Non-LTP error (500) → assert FIX-012 path used, not this
# ─────────────────────────────────────────────────────────────────────────────

def test_non_ltp_error_uses_existing_path(placer, fill_entry):
    """
    FIX-061 Test 5: Non-LTP error (e.g., 500) → uses existing hard_kill path, not retry.
    """
    # Simulate non-LTP error (generic BrokerError)
    exc = BrokerError(
        "Internal server error",
        http_status=500,
        symbol="TESTSTOCK",
    )

    # Mock place_deferred_exits to raise the exception
    placer._engine.place_deferred_exits = Mock(side_effect=exc)

    # Call _place_limit_triple_exits
    placer._place_limit_triple_exits(
        trade_id="T001",
        fill_entry=fill_entry,
        qty_filled=100,
        avg_fill_price=100.0,
        reason="entry_fill",
    )

    # Assert NOT added to _pending_exit_retry
    assert "T001" not in placer._pending_exit_retry

    # Assert hard_kill WAS called (existing path)
    placer._kill_switch.hard_kill.assert_called_once()

    # Assert did NOT subscribe to LiveFeed
    placer._live_feed.subscribe.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Additional tests: Message-based LTP detection
# ─────────────────────────────────────────────────────────────────────────────

def test_ltp_error_trigger_price_message(placer, fill_entry):
    """
    FIX-061: Message containing "Trigger price" → detected as LTP error.
    """
    exc = OrderRejectedError(
        "Trigger price must be above current market price",
        rejection_reason="Trigger price must be above current market price",
        symbol="TESTSTOCK",
    )

    placer._engine.place_deferred_exits = Mock(side_effect=exc)

    placer._place_limit_triple_exits(
        trade_id="T001",
        fill_entry=fill_entry,
        qty_filled=100,
        avg_fill_price=100.0,
        reason="entry_fill",
    )

    # Assert added to retry queue
    assert "T001" in placer._pending_exit_retry
    placer._kill_switch.hard_kill.assert_not_called()


def test_ltp_error_cannot_validate_message(placer, fill_entry):
    """
    FIX-061: Message containing "LTP cannot be validated" → detected as LTP error.
    """
    exc = BrokerError(
        "LTP cannot be validated at this time",
        symbol="TESTSTOCK",
    )

    placer._engine.place_deferred_exits = Mock(side_effect=exc)

    placer._place_limit_triple_exits(
        trade_id="T001",
        fill_entry=fill_entry,
        qty_filled=100,
        avg_fill_price=100.0,
        reason="entry_fill",
    )

    # Assert added to retry queue
    assert "T001" in placer._pending_exit_retry
    placer._kill_switch.hard_kill.assert_not_called()


def test_zero_ltp_tick_ignored(placer, fill_entry):
    """
    FIX-061: Tick with LTP = 0 → ignored, no retry triggered.
    """
    # Pre-populate _pending_exit_retry
    params = _ExitRetryParams(
        trade_id="T001",
        fill_entry=fill_entry,
        qty_filled=100,
        avg_fill_price=100.0,
        reason="entry_fill",
        retry_count=0,
    )
    placer._pending_exit_retry["T001"] = params

    # Simulate tick with LTP = 0
    ticks = [{"instrument_token": 12345, "last_price": 0}]
    placer._on_ltp_tick_for_retry(ticks)

    # Assert retry was NOT triggered
    placer._engine.place_deferred_exits.assert_not_called()

    # Assert still in queue
    assert "T001" in placer._pending_exit_retry


def test_non_ltp_error_on_retry_triggers_hard_kill(placer, fill_entry):
    """
    FIX-061: If retry encounters non-LTP error → hard_kill (not soft_kill).
    """
    # Pre-populate _pending_exit_retry
    params = _ExitRetryParams(
        trade_id="T001",
        fill_entry=fill_entry,
        qty_filled=100,
        avg_fill_price=100.0,
        reason="entry_fill",
        retry_count=0,
    )
    placer._pending_exit_retry["T001"] = params

    # Mock non-LTP error on retry
    exc = BrokerError("Network timeout", symbol="TESTSTOCK")
    placer._engine.place_deferred_exits = Mock(side_effect=exc)

    # Trigger retry
    ticks = [{"instrument_token": 12345, "last_price": 100.5}]
    placer._on_ltp_tick_for_retry(ticks)

    # Assert hard_kill was triggered
    placer._kill_switch.hard_kill.assert_called_once()

    # Assert soft_kill was NOT called
    placer._kill_switch.soft_kill.assert_not_called()
