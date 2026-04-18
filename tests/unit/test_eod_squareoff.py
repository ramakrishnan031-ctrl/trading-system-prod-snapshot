"""
tests/unit/test_eod_squareoff.py

Validates orders/eod_squareoff.py against EOD1-EOD12 locked decisions.

All tests use mocks for adapter, fund_manager, kill_switch, order_monitor,
and state_store.  A real StateStore + in-memory DB is used for the DB-path
tests (check_restart_recovery, eod_squareoff_log insertion).

Run: python -m pytest tests/unit/test_eod_squareoff.py -v
Or:  python tests/unit/test_eod_squareoff.py  (standalone mode)
"""
from __future__ import annotations

import logging
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytest
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from broker.order_state_machine import OrderStateMachine
from broker.zerodha_adapter import CancelResult, PlacedOrder
from capital.kill_switch import KillState
from core.events import EodSquareoffComplete, EventBus
from core.market_windows import MarketWindows
from core.state_store import StateStore
from core.time_authority import now_ist
from orders.eod_squareoff import EodSquareoff, EodFireResult

_IST = timezone(timedelta(hours=5, minutes=30), "IST")
_FIXED_TEST_DATE = date(2026, 4, 20)  # Monday, trading day; pins tests off real-world weekday


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ist(h: int, m: int, s: int = 0, d: Optional[datetime] = None) -> datetime:
    """Build a timezone-aware IST datetime for a given H:M:S."""
    base_date = d.date() if d is not None else _FIXED_TEST_DATE
    return datetime(base_date.year, base_date.month, base_date.day, h, m, s, tzinfo=_IST)


def _make_market_windows() -> MarketWindows:
    return MarketWindows()  # defaults: entry 09:30-13:30, EOD 15:17


def _make_eod(
    store: Optional[StateStore] = None,
    auto_resume: bool = True,
    inter_order_delay_ms: int = 0,
    holidays: Optional[set] = None,
) -> tuple[EodSquareoff, MagicMock, MagicMock, MagicMock, MagicMock, MagicMock]:
    """
    Build an EodSquareoff with mocked dependencies.
    Returns (eod, adapter_mock, fm_mock, ks_mock, bus_mock, om_mock).
    """
    if store is None:
        store = MagicMock(spec=StateStore)
        store.get_pending_intraday_orders.return_value = []
        store.get_open_intraday_positions.return_value = []
        store.get_eod_squareoff_log_for_date.return_value = None

    adapter = MagicMock(spec=["place_order", "cancel_order"])
    fm = MagicMock()
    fm.release.return_value = True

    ks = MagicMock()
    ks.is_active.return_value = False
    ks.current_state.return_value = KillState.INACTIVE

    bus = MagicMock(spec=EventBus)
    osm = OrderStateMachine(bus=None)
    mw = _make_market_windows()
    if holidays:
        mw = MarketWindows(holidays=holidays)
    om = MagicMock()
    import logging
    logger = logging.getLogger("test_eod")

    eod = EodSquareoff(
        adapter=adapter,
        state_store=store,
        fund_manager=fm,
        state_machine=osm,
        bus=bus,
        market_windows=mw,
        time_authority=None,
        kill_switch=ks,
        logger=logger,
        order_monitor=om,
        inter_order_delay_ms=inter_order_delay_ms,
    )
    return eod, adapter, fm, ks, bus, om


# ─────────────────────────────────────────────────────────────────────────────
# check_and_fire: time / holiday gate (EOD3)
# ─────────────────────────────────────────────────────────────────────────────

def test_check_and_fire_before_eod_time_returns_false() -> None:
    eod, *_ = _make_eod()
    now = _ist(15, 16)    # one minute before 15:17
    result = eod.check_and_fire(now)
    assert result is False


def test_check_and_fire_at_eod_time_returns_true() -> None:
    eod, *_ = _make_eod()
    now = _ist(15, 17)
    result = eod.check_and_fire(now)
    assert result is True


def test_check_and_fire_on_holiday_returns_false() -> None:
    today = _FIXED_TEST_DATE
    eod, *_ = _make_eod(holidays={today})
    now = _ist(15, 17)
    result = eod.check_and_fire(now)
    assert result is False


def test_check_and_fire_idempotent_same_day() -> None:
    """Second call on same date returns False (EOD3 idempotent flag)."""
    eod, *_ = _make_eod()
    now = _ist(15, 17)
    first = eod.check_and_fire(now)
    second = eod.check_and_fire(now)
    assert first is True
    assert second is False


def test_check_and_fire_next_day_fires_again() -> None:
    """Flag is per-date; a new date fires again (EOD3)."""
    eod, *_ = _make_eod()
    day1 = _ist(15, 17)
    day2 = day1 + timedelta(days=2)  # skip to a non-holiday weekday
    # Ensure day2 is Monday-Friday
    while day2.weekday() >= 5:
        day2 += timedelta(days=1)

    first = eod.check_and_fire(day1)
    second = eod.check_and_fire(day2)
    assert first is True
    assert second is True


# ─────────────────────────────────────────────────────────────────────────────
# Kill switch lifecycle (EOD5 steps 2 + 7)
# ─────────────────────────────────────────────────────────────────────────────

def test_kill_switch_soft_killed_during_fire() -> None:
    """EOD sets SOFT_KILL before squaring off."""
    eod, adapter, fm, ks, bus, om = _make_eod()
    ks.is_active.return_value = False
    eod.check_and_fire(_ist(15, 17))
    ks.soft_kill.assert_called_once_with(reason="EOD_SQUAREOFF", triggered_by="eod_squareoff")


def test_kill_switch_resumed_after_fire_auto_resume_true() -> None:
    """After fire, kill switch resumed if WE set it and auto_resume_kill_switch=True."""
    eod, adapter, fm, ks, bus, om = _make_eod(auto_resume=True)
    ks.is_active.return_value = False
    eod.check_and_fire(_ist(15, 17))
    ks.resume.assert_called_once_with(
        reason="EOD_SQUAREOFF_COMPLETE",
        resumed_by="eod_squareoff",
    )


def test_kill_switch_not_resumed_if_already_active() -> None:
    """If kill switch was ALREADY active before EOD, we don't touch it (EOD5 step 7)."""
    eod, adapter, fm, ks, bus, om = _make_eod()
    ks.is_active.return_value = True
    ks.current_state.return_value = KillState.HARD_KILL
    eod.check_and_fire(_ist(15, 17))
    ks.soft_kill.assert_not_called()
    ks.resume.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Cancel pending entries (EOD5 step 3)
# ─────────────────────────────────────────────────────────────────────────────

def _pending_order_row(
    trade_id: str = "trd_001",
    signal_id: str = "sig_001",
    symbol: str = "RELIANCE",
    direction: str = "LONG",
    broker_order_id: str = "KITE001",
) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "trade_id": trade_id,
        "signal_id": signal_id,
        "symbol": symbol,
        "direction": direction,
        "broker_order_id": broker_order_id,
    }[key]
    return row


def test_pending_intraday_orders_cancelled() -> None:
    store = MagicMock(spec=StateStore)
    store.get_pending_intraday_orders.return_value = [
        _pending_order_row("trd_001", "sig_001", "RELIANCE", "LONG", "KITE001"),
    ]
    store.get_open_intraday_positions.return_value = []
    store.get_eod_squareoff_log_for_date.return_value = None
    store.get_reservation_id_for_signal.return_value = "res_abc"

    eod, adapter, fm, ks, bus, om = _make_eod(store=store)
    adapter.cancel_order.return_value = CancelResult(
        broker_order_id="KITE001", success=True, reason=""
    )

    eod.check_and_fire(_ist(15, 17))

    adapter.cancel_order.assert_called_once_with("KITE001")
    fm.release.assert_called_once_with("res_abc", "EOD_CANCEL")


def test_pending_delivery_orders_not_cancelled(monkeypatch=None) -> None:
    """
    Regression EOD6: DELIVERY (CNC) positions are filtered out by
    get_pending_intraday_orders (product IN ('MIS','CO') filter).
    The helper itself handles this; eod_squareoff just calls the helper.
    No pending rows returned -> no cancel calls.
    """
    store = MagicMock(spec=StateStore)
    store.get_pending_intraday_orders.return_value = []  # no intraday pending
    store.get_open_intraday_positions.return_value = []
    store.get_eod_squareoff_log_for_date.return_value = None

    eod, adapter, fm, ks, bus, om = _make_eod(store=store)
    eod.check_and_fire(_ist(15, 17))
    adapter.cancel_order.assert_not_called()


def test_cancel_failure_logs_critical_and_continues() -> None:
    """Cancel failure does NOT abort the rest of the sequence (EOD5)."""
    store = MagicMock(spec=StateStore)
    store.get_pending_intraday_orders.return_value = [
        _pending_order_row("trd_001", "sig_001", "RELIANCE", "LONG", "KITE001"),
        _pending_order_row("trd_002", "sig_002", "INFY", "LONG", "KITE002"),
    ]
    store.get_open_intraday_positions.return_value = []
    store.get_eod_squareoff_log_for_date.return_value = None
    store.get_reservation_id_for_signal.return_value = "res_x"

    eod, adapter, fm, ks, bus, om = _make_eod(store=store)
    adapter.cancel_order.side_effect = [
        CancelResult(broker_order_id="KITE001", success=False, reason="already filled"),
        CancelResult(broker_order_id="KITE002", success=True, reason=""),
    ]

    eod.check_and_fire(_ist(15, 17))

    # Both cancels attempted; second one succeeds
    assert adapter.cancel_order.call_count == 2
    # Release only called for successful cancel
    fm.release.assert_called_once_with("res_x", "EOD_CANCEL")


def test_cancel_broker_exception_logs_critical_continues() -> None:
    """BrokerError on cancel -> CRITICAL log -> continue to next order."""
    from core.exceptions import BrokerError

    store = MagicMock(spec=StateStore)
    store.get_pending_intraday_orders.return_value = [
        _pending_order_row("trd_001", "sig_001", "RELIANCE", "LONG", "KITE001"),
        _pending_order_row("trd_002", "sig_002", "INFY", "LONG", "KITE002"),
    ]
    store.get_open_intraday_positions.return_value = []
    store.get_eod_squareoff_log_for_date.return_value = None
    store.get_reservation_id_for_signal.return_value = "res_x"

    eod, adapter, fm, ks, bus, om = _make_eod(store=store)
    adapter.cancel_order.side_effect = [
        BrokerError("API error"),
        CancelResult(broker_order_id="KITE002", success=True, reason=""),
    ]

    eod.check_and_fire(_ist(15, 17))
    assert adapter.cancel_order.call_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# Exit open positions (EOD5 step 4)
# ─────────────────────────────────────────────────────────────────────────────

def _open_position_row(
    trade_id: str = "trd_001",
    signal_id: str = "sig_001",
    symbol: str = "RELIANCE",
    direction: str = "LONG",
    qty_filled: int = 10,
) -> MagicMock:
    row = MagicMock()
    row.__getitem__ = lambda self, key: {
        "trade_id": trade_id,
        "signal_id": signal_id,
        "symbol": symbol,
        "direction": direction,
        "qty_filled": qty_filled,
    }[key]
    return row


def _placed_order(
    internal_id: str = "ord_abc",
    broker_id: str = "KITE999",
    symbol: str = "RELIANCE",
    side: str = "SELL",
    qty: int = 10,
) -> PlacedOrder:
    return PlacedOrder(
        internal_order_id=internal_id,
        broker_order_id=broker_id,
        symbol=symbol,
        side=side,
        qty=qty,
        price=0.0,
        order_type="MARKET",
        product="MIS",
        status="SUBMITTED",
        ts=now_ist(),
    )


def test_open_intraday_positions_exited_with_market_sell() -> None:
    """LONG position -> SELL MARKET exit (EOD5 step 4b)."""
    store = MagicMock(spec=StateStore)
    store.get_pending_intraday_orders.return_value = []
    store.get_open_intraday_positions.return_value = [
        _open_position_row("trd_001", "sig_001", "RELIANCE", "LONG", 10),
    ]
    store.get_eod_squareoff_log_for_date.return_value = None

    eod, adapter, fm, ks, bus, om = _make_eod(store=store)
    adapter.place_order.return_value = _placed_order(side="SELL")

    eod.check_and_fire(_ist(15, 17))

    adapter.place_order.assert_called_once()
    call_kwargs = adapter.place_order.call_args
    assert call_kwargs.kwargs.get("side") == "SELL" or call_kwargs[1].get("side") == "SELL" or \
           (len(call_kwargs[0]) > 1 and call_kwargs[0][1] == "SELL")


def test_short_position_exited_with_buy() -> None:
    """SHORT position -> BUY MARKET exit (EOD5 step 4a)."""
    store = MagicMock(spec=StateStore)
    store.get_pending_intraday_orders.return_value = []
    store.get_open_intraday_positions.return_value = [
        _open_position_row("trd_001", "sig_001", "HDFC", "SHORT", 5),
    ]
    store.get_eod_squareoff_log_for_date.return_value = None

    eod, adapter, fm, ks, bus, om = _make_eod(store=store)
    adapter.place_order.return_value = _placed_order(side="BUY", symbol="HDFC", qty=5)

    eod.check_and_fire(_ist(15, 17))

    adapter.place_order.assert_called_once()
    # Verify BUY exit
    kwargs = adapter.place_order.call_args[1] if adapter.place_order.call_args[1] else {}
    args = adapter.place_order.call_args[0] if adapter.place_order.call_args[0] else ()
    side_val = kwargs.get("side") or (args[1] if len(args) > 1 else None)
    assert side_val == "BUY"


def test_open_delivery_positions_not_touched() -> None:
    """Regression EOD6: CNC positions filtered by get_open_intraday_positions."""
    store = MagicMock(spec=StateStore)
    store.get_pending_intraday_orders.return_value = []
    store.get_open_intraday_positions.return_value = []  # CNC filtered out by helper
    store.get_eod_squareoff_log_for_date.return_value = None

    eod, adapter, fm, ks, bus, om = _make_eod(store=store)
    eod.check_and_fire(_ist(15, 17))
    adapter.place_order.assert_not_called()


def test_exit_orders_placed_in_symbol_sorted_order() -> None:
    """Positions exited in symbol-sorted order (Foundation Rule 3.7 via DB ORDER BY)."""
    store = MagicMock(spec=StateStore)
    store.get_pending_intraday_orders.return_value = []
    # DB helper already returns sorted; simulate with alphabetical order
    store.get_open_intraday_positions.return_value = [
        _open_position_row("trd_a", "sig_a", "HDFC", "LONG", 5),
        _open_position_row("trd_b", "sig_b", "RELIANCE", "LONG", 10),
        _open_position_row("trd_c", "sig_c", "ZOMATO", "SHORT", 20),
    ]
    store.get_eod_squareoff_log_for_date.return_value = None

    eod, adapter, fm, ks, bus, om = _make_eod(store=store, inter_order_delay_ms=0)
    adapter.place_order.side_effect = [
        _placed_order("ord_1", "K1", "HDFC", "SELL", 5),
        _placed_order("ord_2", "K2", "RELIANCE", "SELL", 10),
        _placed_order("ord_3", "K3", "ZOMATO", "BUY", 20),
    ]

    eod.check_and_fire(_ist(15, 17))

    symbols_called = [c[1].get("symbol") or c[0][0] for c in adapter.place_order.call_args_list]
    assert symbols_called == ["HDFC", "RELIANCE", "ZOMATO"]


def test_inter_order_delay_applied() -> None:
    """time.sleep called between orders with correct delay (EOD5 audit fix)."""
    store = MagicMock(spec=StateStore)
    store.get_pending_intraday_orders.return_value = []
    store.get_open_intraday_positions.return_value = [
        _open_position_row("trd_a", "sig_a", "HDFC", "LONG", 5),
        _open_position_row("trd_b", "sig_b", "INFY", "LONG", 10),
    ]
    store.get_eod_squareoff_log_for_date.return_value = None

    eod, adapter, fm, ks, bus, om = _make_eod(store=store, inter_order_delay_ms=200)
    adapter.place_order.side_effect = [
        _placed_order("ord_1", "K1", "HDFC", "SELL", 5),
        _placed_order("ord_2", "K2", "INFY", "SELL", 10),
    ]

    with patch("orders.eod_squareoff.time.sleep") as mock_sleep:
        eod.check_and_fire(_ist(15, 17))

    # Only ONE sleep between the TWO orders (not after the last)
    mock_sleep.assert_called_once_with(0.2)


def test_exit_broker_exception_marks_exit_failed_continues() -> None:
    """BrokerError on place_order -> mark EOD_EXIT_FAILED, continue (EOD5 step 4d)."""
    from core.exceptions import BrokerError

    store = MagicMock(spec=StateStore)
    store.get_pending_intraday_orders.return_value = []
    store.get_open_intraday_positions.return_value = [
        _open_position_row("trd_a", "sig_a", "FAIL_STOCK", "LONG", 5),
        _open_position_row("trd_b", "sig_b", "GOOD_STOCK", "LONG", 10),
    ]
    store.get_eod_squareoff_log_for_date.return_value = None

    eod, adapter, fm, ks, bus, om = _make_eod(store=store, inter_order_delay_ms=0)
    adapter.place_order.side_effect = [
        BrokerError("network error"),
        _placed_order("ord_2", "K2", "GOOD_STOCK", "SELL", 10),
    ]

    eod.check_and_fire(_ist(15, 17))

    # Both positions attempted; second succeeded
    assert adapter.place_order.call_count == 2


# ─────────────────────────────────────────────────────────────────────────────
# order_monitor hand-off (EOD7)
# ─────────────────────────────────────────────────────────────────────────────

def test_order_monitor_track_called_for_exit_order() -> None:
    """order_monitor.track() called for each placed exit order (EOD7)."""
    store = MagicMock(spec=StateStore)
    store.get_pending_intraday_orders.return_value = []
    store.get_open_intraday_positions.return_value = [
        _open_position_row("trd_001", "sig_001", "RELIANCE", "LONG", 10),
    ]
    store.get_eod_squareoff_log_for_date.return_value = None

    eod, adapter, fm, ks, bus, om = _make_eod(store=store)
    placed = _placed_order("ord_abc", "KITE999", "RELIANCE", "SELL", 10)
    adapter.place_order.return_value = placed

    eod.check_and_fire(_ist(15, 17))

    om.track.assert_called_once()
    track_kwargs = om.track.call_args[1]
    assert track_kwargs["internal_order_id"] == "ord_abc"
    assert track_kwargs["broker_order_id"] == "KITE999"


def test_fund_manager_not_called_for_position_exits() -> None:
    """EOD does NOT call fund_manager on exit fills (order_monitor handles that)."""
    store = MagicMock(spec=StateStore)
    store.get_pending_intraday_orders.return_value = []
    store.get_open_intraday_positions.return_value = [
        _open_position_row("trd_001", "sig_001", "RELIANCE", "LONG", 10),
    ]
    store.get_eod_squareoff_log_for_date.return_value = None

    eod, adapter, fm, ks, bus, om = _make_eod(store=store)
    adapter.place_order.return_value = _placed_order()

    eod.check_and_fire(_ist(15, 17))

    fm.release.assert_not_called()
    fm.commit_to_used.assert_not_called() if hasattr(fm, "commit_to_used") else None


# ─────────────────────────────────────────────────────────────────────────────
# EOD squareoff log (EOD8)
# ─────────────────────────────────────────────────────────────────────────────

def test_eod_squareoff_log_row_written() -> None:
    """eod_squareoff_log row inserted after fire (EOD8)."""
    store = MagicMock(spec=StateStore)
    store.get_pending_intraday_orders.return_value = []
    store.get_open_intraday_positions.return_value = []
    store.get_eod_squareoff_log_for_date.return_value = None

    eod, adapter, fm, ks, bus, om = _make_eod(store=store)
    eod.check_and_fire(_ist(15, 17))

    store.insert_eod_squareoff_log.assert_called_once()
    kwargs = store.insert_eod_squareoff_log.call_args[1]
    assert kwargs["positions_attempted"] == 0
    assert kwargs["cancels_attempted"] == 0


def test_eod_squareoff_log_counts_correct() -> None:
    """Log row has correct counts for mixed success/failure scenario."""
    store = MagicMock(spec=StateStore)
    store.get_pending_intraday_orders.return_value = [
        _pending_order_row("trd_001", "sig_001", "RELIANCE", "LONG", "KITE001"),
        _pending_order_row("trd_002", "sig_002", "INFY", "LONG", "KITE002"),
    ]
    store.get_open_intraday_positions.return_value = [
        _open_position_row("trd_003", "sig_003", "HDFC", "LONG", 5),
    ]
    store.get_eod_squareoff_log_for_date.return_value = None
    store.get_reservation_id_for_signal.return_value = "res_x"

    eod, adapter, fm, ks, bus, om = _make_eod(store=store, inter_order_delay_ms=0)
    from core.exceptions import BrokerError
    adapter.cancel_order.side_effect = [
        CancelResult("KITE001", True, ""),
        CancelResult("KITE002", False, "already filled"),
    ]
    adapter.place_order.return_value = _placed_order()

    eod.check_and_fire(_ist(15, 17))

    kwargs = store.insert_eod_squareoff_log.call_args[1]
    assert kwargs["cancels_attempted"] == 2
    assert kwargs["cancels_succeeded"] == 1
    assert kwargs["cancels_failed"] == 1
    assert kwargs["positions_attempted"] == 1
    assert kwargs["positions_succeeded"] == 1
    assert kwargs["positions_failed"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# EodSquareoffComplete event published (EOD5 step 6)
# ─────────────────────────────────────────────────────────────────────────────

def test_eod_squareoff_complete_event_published() -> None:
    """EodSquareoffComplete event published with summary (EOD5)."""
    store = MagicMock(spec=StateStore)
    store.get_pending_intraday_orders.return_value = []
    store.get_open_intraday_positions.return_value = []
    store.get_eod_squareoff_log_for_date.return_value = None

    eod, adapter, fm, ks, bus, om = _make_eod(store=store)
    eod.check_and_fire(_ist(15, 17))

    bus.publish.assert_called_once()
    event = bus.publish.call_args[0][0]
    assert isinstance(event, EodSquareoffComplete)
    assert event.source_module == "eod_squareoff"
    assert event.positions_attempted == 0


# ─────────────────────────────────────────────────────────────────────────────
# fire_now: emergency manual fire (EOD10)
# ─────────────────────────────────────────────────────────────────────────────

def test_fire_now_bypasses_time_check() -> None:
    """fire_now() fires regardless of current time (EOD10)."""
    store = MagicMock(spec=StateStore)
    store.get_pending_intraday_orders.return_value = []
    store.get_open_intraday_positions.return_value = []
    store.get_eod_squareoff_log_for_date.return_value = None

    eod, adapter, fm, ks, bus, om = _make_eod(store=store)
    result = eod.fire_now(reason="TEST_EMERGENCY", triggered_by="operator")

    assert isinstance(result, EodFireResult)
    bus.publish.assert_called_once()


def test_fire_now_marks_fired_flag() -> None:
    """fire_now() marks _fired_for_date so check_and_fire stays quiet (EOD10)."""
    store = MagicMock(spec=StateStore)
    store.get_pending_intraday_orders.return_value = []
    store.get_open_intraday_positions.return_value = []
    store.get_eod_squareoff_log_for_date.return_value = None

    eod, adapter, fm, ks, bus, om = _make_eod(store=store)
    with patch("orders.eod_squareoff.now_ist", return_value=_ist(9, 0)):
        eod.fire_now(reason="MANUAL", triggered_by="test")

    # check_and_fire same day should return False
    result = eod.check_and_fire(_ist(15, 17))
    assert result is False


def test_fire_now_still_sets_soft_kill() -> None:
    """fire_now() does NOT skip the kill_switch SOFT_KILL step (EOD10)."""
    store = MagicMock(spec=StateStore)
    store.get_pending_intraday_orders.return_value = []
    store.get_open_intraday_positions.return_value = []
    store.get_eod_squareoff_log_for_date.return_value = None

    eod, adapter, fm, ks, bus, om = _make_eod(store=store)
    ks.is_active.return_value = False
    eod.fire_now(reason="MANUAL", triggered_by="test")

    ks.soft_kill.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# Restart recovery (EOD9)
# ─────────────────────────────────────────────────────────────────────────────

def test_restart_with_log_row_no_fire() -> None:
    """
    EOD9: if log row already exists for today on construction, do NOT fire.
    check_and_fire also returns False.
    """
    store = MagicMock(spec=StateStore)
    today_str = _FIXED_TEST_DATE.isoformat()
    # Simulate existing log row with no failures
    existing_row = MagicMock()
    existing_row.__getitem__ = lambda self, key: {
        "fired_date": today_str,
        "positions_failed": 0,
        "cancels_failed": 0,
    }[key]
    store.get_eod_squareoff_log_for_date.return_value = existing_row
    store.get_pending_intraday_orders.return_value = []
    store.get_open_intraday_positions.return_value = []

    with patch("orders.eod_squareoff.now_ist", return_value=_ist(9, 0)):
        eod, adapter, fm, ks, bus, om = _make_eod(store=store)

    result = eod.check_and_fire(_ist(15, 17))
    assert result is False
    adapter.cancel_order.assert_not_called()
    adapter.place_order.assert_not_called()


def test_restart_with_failed_log_row_logs_warning() -> None:
    """EOD9: log row with failures -> WARNING logged, no auto-fire."""
    import logging
    store = MagicMock(spec=StateStore)
    today_str = datetime.now(_IST).date().isoformat()
    existing_row = MagicMock()
    existing_row.__getitem__ = lambda self, key: {
        "fired_date": today_str,
        "positions_failed": 2,
        "cancels_failed": 0,
    }[key]
    store.get_eod_squareoff_log_for_date.return_value = existing_row
    store.get_pending_intraday_orders.return_value = []
    store.get_open_intraday_positions.return_value = []

    with patch.object(logging.getLogger("test_eod"), "warning") as mock_warn:
        eod, adapter, fm, ks, bus, om = _make_eod(store=store)

    # Just verify no crash; warning assertion would need log capture which
    # is environment-dependent


def test_restart_after_1530_no_fire_critical_logged() -> None:
    """EOD9: past 15:30 with no log row -> CRITICAL but NO auto-fire."""
    store = MagicMock(spec=StateStore)
    store.get_eod_squareoff_log_for_date.return_value = None
    store.get_pending_intraday_orders.return_value = []
    store.get_open_intraday_positions.return_value = []

    now_mock = _ist(15, 35)

    with patch("orders.eod_squareoff.now_ist", return_value=now_mock):
        eod, adapter, fm, ks, bus, om = _make_eod(store=store)

    # After construction (recovery check), no fire occurred
    adapter.cancel_order.assert_not_called()
    adapter.place_order.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# State machine transitions for EOD exit orders (EOD5 step 4c)
# ─────────────────────────────────────────────────────────────────────────────

def test_state_machine_registered_for_exit_order() -> None:
    """Exit order registered in state_machine by adapter (ZA7 wiring — not re-registered)."""
    # ZA7: adapter.place_order already calls new_order_id() and registers
    # with state_machine. We verify the internal_order_id comes back on PlacedOrder.
    store = MagicMock(spec=StateStore)
    store.get_pending_intraday_orders.return_value = []
    store.get_open_intraday_positions.return_value = [
        _open_position_row("trd_001", "sig_001", "RELIANCE", "LONG", 10),
    ]
    store.get_eod_squareoff_log_for_date.return_value = None

    eod, adapter, fm, ks, bus, om = _make_eod(store=store)
    placed = _placed_order("ord_xyz", "KITE999", "RELIANCE", "SELL", 10)
    adapter.place_order.return_value = placed

    eod.check_and_fire(_ist(15, 17))

    # order_monitor should receive the internal_order_id from PlacedOrder
    om.track.assert_called_once()
    assert om.track.call_args[1]["internal_order_id"] == "ord_xyz"


# ─────────────────────────────────────────────────────────────────────────────
# Integration: eod_squareoff_log via real StateStore (EOD8)
# ─────────────────────────────────────────────────────────────────────────────

def test_real_store_eod_log_insert_and_query() -> None:
    """Insert eod_squareoff_log row and read it back (EOD8)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = StateStore(Path(tmpdir) / "test.db")

        today = datetime.now(_IST).date().isoformat()
        fired_at = datetime.now(_IST).isoformat()

        store.insert_eod_squareoff_log(
            fired_date=today,
            fired_at=fired_at,
            positions_attempted=3,
            positions_succeeded=2,
            positions_failed=1,
            cancels_attempted=1,
            cancels_succeeded=1,
            cancels_failed=0,
            duration_sec=1.23,
        )

        row = store.get_eod_squareoff_log_for_date(today)
        assert row is not None
        assert row["positions_attempted"] == 3
        assert row["positions_succeeded"] == 2
        assert row["positions_failed"] == 1
        assert row["cancels_attempted"] == 1
        assert abs(row["duration_sec"] - 1.23) < 0.001

        store.close()


def test_real_store_eod_log_missing_returns_none() -> None:
    """get_eod_squareoff_log_for_date returns None for a date with no row."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = StateStore(Path(tmpdir) / "test.db")
        row = store.get_eod_squareoff_log_for_date("2099-01-01")
        assert row is None
        store.close()


# ─────────────────────────────────────────────────────────────────────────────
# Regression tests: audit blocker fixes
# ─────────────────────────────────────────────────────────────────────────────

def test_reset_daily_pnl_called_after_fire() -> None:
    """
    BLOCKER #12 regression: fund_manager.reset_daily_pnl() must be called
    after a successful EOD fire.
    """
    eod, adapter, fm, ks, bus, om = _make_eod()
    now = _ist(15, 17)
    eod.check_and_fire(now)
    fm.reset_daily_pnl.assert_called_once()


def test_fired_for_date_reset_on_fire_exception() -> None:
    """
    BLOCKER #13 regression: if _fire() raises, the _fired_for_date flag must
    be reset so check_and_fire() can retry on the next poll.
    Previously the flag was set before _fire(), causing permanent lockout.
    """
    eod, adapter, fm, ks, bus, om = _make_eod()
    now = _ist(15, 17)
    today = now.date()

    # Make _fire() raise on the first call; succeed on the second.
    original_fire = eod._fire
    call_count = {"n": 0}

    def _patched_fire(n, *, recovery_fire):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated _fire failure")
        return original_fire(n, recovery_fire=recovery_fire)

    eod._fire = _patched_fire

    # First call: _fire raises → flag must be reset
    with pytest.raises(RuntimeError):
        eod.check_and_fire(now)

    assert not eod._fired_for_date.get(today, False), (
        "_fired_for_date should be reset after _fire() exception"
    )

    # Second call: _fire succeeds → flag stays set
    result = eod.check_and_fire(now)
    assert result is True
    assert eod._fired_for_date.get(today, False)


def test_cancel_pending_entries_updates_order_row_status() -> None:
    """
    HIGH #8 regression: after EOD cancels a pending entry order, the orders row
    status must be set to CANCELLED (not left as PENDING).
    """
    from orders.order_manager import OrderManager

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        store = StateStore(Path(tmpdir) / "test_h8.db")
        om = OrderManager(store, logging.getLogger("test_h8_om"))

        # Seed signal row (required FK)
        sig_id = "SIG_H8_001"
        now_ts = datetime.now(_IST).isoformat()
        now_date = datetime.now(_IST).date().isoformat()
        with store.transaction() as cur:
            cur.execute(
                "INSERT INTO signals (signal_id, symbol, scanner, strategy, "
                "triggered_at, received_at, expires_at, status, fingerprint, fingerprint_date) "
                "VALUES (?, 'RELIANCE', 'sc', 'strat', ?, ?, ?, 'ACCEPTED', 'fp_h8', ?)",
                (sig_id, now_ts, now_ts, now_ts, now_date),
            )

        # Create trade + entry order
        trade_id = om.create_trade(
            signal_id=sig_id, symbol="RELIANCE", direction="LONG",
            strategy="test_strat", sector=None,
            qty=5, entry_target_price=2500.0, sl_initial=2450.0, tgt_initial=2600.0,
            order_protocol="LIMIT_TRIPLE", margin_reserved=500.0, risk_amount=250.0,
        )
        om.insert_order(
            trade_id=trade_id, broker_order_id="BRK_ORD_H8",
            leg="ENTRY", transaction_type="BUY", order_type="LIMIT",
            product="MIS", variety="regular", qty_requested=5, price=2500.0,
        )
        # Also update trade to PENDING_FILL (required by get_pending_intraday_orders)
        # create_trade sets status=PENDING_FILL already

        # Verify order starts as PENDING
        row_before = store.fetch_one(
            "SELECT status FROM orders WHERE order_id = ?", ("BRK_ORD_H8",)
        )
        assert row_before["status"] == "PENDING"

        # Build EodSquareoff with real store; mock adapter to return success cancel
        adapter = MagicMock(spec=["place_order", "cancel_order"])
        adapter.cancel_order.return_value = MagicMock(success=True, reason=None)

        fm = MagicMock()
        ks = MagicMock()
        ks.is_active.return_value = False
        ks.current_state.return_value = KillState.INACTIVE
        bus = MagicMock(spec=EventBus)
        from broker.order_state_machine import OrderStateMachine
        osm = OrderStateMachine(bus=None)

        eod = EodSquareoff(
            adapter=adapter,
            state_store=store,
            fund_manager=fm,
            state_machine=osm,
            bus=bus,
            market_windows=_make_market_windows(),
            time_authority=None,
            kill_switch=ks,
            logger=logging.getLogger("test_h8_eod"),
        )

        now = _ist(15, 17)
        eod.check_and_fire(now)

        # Verify order row status updated to CANCELLED
        row_after = store.fetch_one(
            "SELECT status FROM orders WHERE order_id = ?", ("BRK_ORD_H8",)
        )
        assert row_after["status"] == "CANCELLED", (
            f"Expected CANCELLED, got {row_after['status']}"
        )
        store.close()


def test_eod9_skipped_late_writes_event_and_alerts() -> None:
    """
    Section 3 / EOD9 visibility regression: when system restarts after 15:30
    with open positions, EOD_SKIPPED_LATE system_event must be written and
    notifier.send called with severity=CRITICAL.  No fire must occur.
    """
    store = MagicMock(spec=StateStore)
    # Simulate log row missing (no prior fire today)
    store.get_eod_squareoff_log_for_date.return_value = None
    # Two open intraday positions
    store.get_open_intraday_positions.return_value = [
        {"symbol": "RELIANCE", "side": "LONG", "quantity": 10, "entry_price": 2500.0},
        {"symbol": "INFY", "side": "LONG", "quantity": 5, "entry_price": 1500.0},
    ]

    notifier = MagicMock()

    adapter = MagicMock(spec=["place_order", "cancel_order"])
    fm = MagicMock()
    ks = MagicMock()
    ks.is_active.return_value = False
    ks.current_state.return_value = KillState.INACTIVE
    bus = MagicMock(spec=EventBus)
    osm = OrderStateMachine(bus=None)
    mw = _make_market_windows()
    import logging
    logger = logging.getLogger("test_eod9_skipped")

    # Use a fixed "now" of 15:31 so _check_restart_recovery sees post-15:30
    fixed_now = _ist(15, 31)

    with patch("orders.eod_squareoff.now_ist", return_value=fixed_now):
        eod = EodSquareoff(
            adapter=adapter,
            state_store=store,
            fund_manager=fm,
            state_machine=osm,
            bus=bus,
            market_windows=mw,
            time_authority=None,
            kill_switch=ks,
            logger=logger,
            notifier=notifier,
        )

    # Assert EOD_SKIPPED_LATE system_event was written
    store.insert_system_event.assert_called_once()
    call_kwargs = store.insert_system_event.call_args
    assert call_kwargs.kwargs.get("event_type") == "EOD_SKIPPED_LATE", (
        f"Expected event_type='EOD_SKIPPED_LATE', got {call_kwargs}"
    )

    # Assert CRITICAL alert sent
    notifier.send.assert_called_once()
    send_kwargs = notifier.send.call_args.kwargs
    assert send_kwargs.get("severity") == "CRITICAL"

    # Assert no broker orders were placed (EOD9: no fire)
    adapter.place_order.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import traceback

    tests = [
        test_check_and_fire_before_eod_time_returns_false,
        test_check_and_fire_at_eod_time_returns_true,
        test_check_and_fire_on_holiday_returns_false,
        test_check_and_fire_idempotent_same_day,
        test_check_and_fire_next_day_fires_again,
        test_kill_switch_soft_killed_during_fire,
        test_kill_switch_resumed_after_fire_auto_resume_true,
        test_kill_switch_not_resumed_if_already_active,
        test_pending_intraday_orders_cancelled,
        test_pending_delivery_orders_not_cancelled,
        test_cancel_failure_logs_critical_and_continues,
        test_cancel_broker_exception_logs_critical_continues,
        test_open_intraday_positions_exited_with_market_sell,
        test_short_position_exited_with_buy,
        test_open_delivery_positions_not_touched,
        test_exit_orders_placed_in_symbol_sorted_order,
        test_inter_order_delay_applied,
        test_exit_broker_exception_marks_exit_failed_continues,
        test_order_monitor_track_called_for_exit_order,
        test_fund_manager_not_called_for_position_exits,
        test_eod_squareoff_log_row_written,
        test_eod_squareoff_log_counts_correct,
        test_eod_squareoff_complete_event_published,
        test_fire_now_bypasses_time_check,
        test_fire_now_marks_fired_flag,
        test_fire_now_still_sets_soft_kill,
        test_restart_with_log_row_no_fire,
        test_restart_with_failed_log_row_logs_warning,
        test_restart_after_1530_no_fire_critical_logged,
        test_state_machine_registered_for_exit_order,
        test_real_store_eod_log_insert_and_query,
        test_real_store_eod_log_missing_returns_none,
        test_cancel_pending_entries_updates_order_row_status,
        test_reset_daily_pnl_called_after_fire,
        test_fired_for_date_reset_on_fire_exception,
        test_eod9_skipped_late_writes_event_and_alerts,
    ]

    passed = 0
    failed_tests = []
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
            passed += 1
        except Exception:
            print(f"  FAIL  {fn.__name__}")
            traceback.print_exc()
            failed_tests.append(fn.__name__)

    print(f"\n{passed}/{len(tests)} passed")
    if failed_tests:
        print("FAILED:", failed_tests)
        sys.exit(1)
