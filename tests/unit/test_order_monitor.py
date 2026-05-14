"""
tests/unit/test_order_monitor.py

Validates broker/order_monitor.py against OM1-OM16.
Uses MockAdapter with scripted get_order_history responses.
All timeout tests use a fake monotonic/time provider -- no real sleeps.

Run: python -m pytest tests/unit/test_order_monitor.py -v
Or:  python tests/unit/test_order_monitor.py  (standalone mode)
"""

from __future__ import annotations

import sys
import logging
import threading
import time
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from broker.order_monitor import OrderMonitor, _calc_slippage_pct
from broker.order_state_machine import OrderStateMachine
from broker.zerodha_adapter import CancelResult, OrderHistoryEntry
from core.events import EventBus, OrderFilled
from core.exceptions import BrokerAuthError, BrokerTimeoutError
from core.ids import new_signal_id, new_trade_id
from core.state_store import StateStore
from core.time_authority import now_ist


# ─────────────────────────────────────────────────────────────────────────────
# MockAdapter
# ─────────────────────────────────────────────────────────────────────────────

class MockAdapter:
    """
    Scripted adapter for order_monitor tests.
    history_responses: list of lists of OrderHistoryEntry -- one list per poll call.
    cancel_success: True (default) means cancel_order returns success=True.
    history_exc: if set, raise this exception instead of returning responses.
    """

    def __init__(self) -> None:
        self.history_responses: list[list[OrderHistoryEntry]] = []
        self._history_call_count = 0
        self.cancel_success = True
        self.cancel_reason = ""
        self.history_exc: Exception | None = None
        self.cancel_calls: list[str] = []
        # H-15 mock parity: get_open_orders is called for orphan verification.
        # Default (None) returns empty list -> broker confirms absent ->
        # confirmed orphan. Override by setting open_orders_response to a list
        # of dicts or open_orders_exc to an Exception.
        self.open_orders_response: list[dict] | None = None
        self.open_orders_exc: Exception | None = None
        self.open_orders_calls: int = 0

    def get_order_history(self, broker_order_id: str) -> list[OrderHistoryEntry]:
        if self.history_exc is not None:
            raise self.history_exc
        if self._history_call_count < len(self.history_responses):
            result = self.history_responses[self._history_call_count]
        else:
            result = self.history_responses[-1] if self.history_responses else []
        self._history_call_count += 1
        return result

    def cancel_order(self, broker_order_id: str) -> CancelResult:
        self.cancel_calls.append(broker_order_id)
        return CancelResult(
            broker_order_id=broker_order_id,
            success=self.cancel_success,
            reason=self.cancel_reason,
        )

    def get_open_orders(self) -> list[dict]:
        """H-15 mock parity: mirrors ZerodhaAdapter.get_open_orders."""
        self.open_orders_calls += 1
        if self.open_orders_exc is not None:
            raise self.open_orders_exc
        return list(self.open_orders_response or [])


def _entry(status: str, filled_qty: int = 0, avg_price: float = 0.0) -> OrderHistoryEntry:
    return OrderHistoryEntry(
        broker_order_id="KITE001",
        status=status,
        filled_qty=filled_qty,
        avg_price=avg_price,
        rejection_reason="",
        ts=now_ist(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helper: StateStore for rehydration tests (FIX-024)
# ─────────────────────────────────────────────────────────────────────────────

def _make_store(tmp_path: Path) -> StateStore:
    """Create a StateStore with schema initialized."""
    db = tmp_path / "test.db"
    schema = Path("core/schema.sql")
    store = StateStore(db_path=db, schema_path=schema)
    return store


def _seed_signal(store: StateStore, signal_id: str | None = None, symbol: str = "RELIANCE") -> str:
    """Insert a minimal signal row so FK constraints pass."""
    sig_id = signal_id or new_signal_id()
    now = now_ist().isoformat()
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO signals (
                signal_id, symbol, scanner, strategy,
                triggered_at, received_at, expires_at,
                status, fingerprint, fingerprint_date
            ) VALUES (?, ?, 'test_scanner', 'test_strategy',
                      ?, ?, ?,
                      'PROCESSED', 'fp_test_001', ?)
            """,
            (sig_id, symbol, now, now, now, now[:10]),
        )
    return sig_id


def _seed_trade_with_order(
    store: StateStore,
    signal_id: str,
    trade_id: str | None = None,
    order_id: str = "KITE001",
    order_status: str = "PENDING",
    symbol: str = "RELIANCE",
    transaction_type: str = "BUY",
    qty: int = 10,
    price: float = 2500.0,
    leg: str = "ENTRY",
) -> str:
    """
    Seed a trade with a single order in the specified status.
    Returns the trade_id.
    """
    tid = trade_id or new_trade_id()
    now = now_ist().isoformat()

    with store.transaction() as cur:
        # Insert trade with all required fields
        cur.execute(
            """
            INSERT INTO trades (
                trade_id, signal_id, symbol, direction, strategy, sector,
                qty_planned, qty_filled, entry_target_price, entry_actual_price,
                sl_initial, tgt_initial, margin_reserved, risk_amount,
                created_at, entry_time, status, order_protocol, updated_at
            )
            VALUES (?, ?, ?, 'LONG', 'test_strategy', NULL,
                    ?, 0, ?, NULL,
                    2450.0, 2600.0, 25000.0, 500.0,
                    ?, NULL, 'PENDING_FILL', 'LIMIT_TRIPLE', ?)
            """,
            (tid, signal_id, symbol, qty, price, now, now),
        )

        # Insert order
        cur.execute(
            """
            INSERT INTO orders (
                order_id, trade_id, leg, transaction_type, order_type,
                product, variety, qty_requested, price, status, placed_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'LIMIT', 'MIS', 'regular', ?, ?, ?, ?, ?)
            """,
            (order_id, tid, leg, transaction_type, qty, price, order_status, now, now),
        )

    return tid


# ─────────────────────────────────────────────────────────────────────────────
# Helper: build monitor + register order in OSM
# ─────────────────────────────────────────────────────────────────────────────

def _make_monitor(
    adapter: MockAdapter | None = None,
    poll_interval_sec: int = 1,
    fill_timeout_sec: int = 60,
    on_orphan=None,
    on_critical=None,
) -> tuple[OrderMonitor, MockAdapter, OrderStateMachine, EventBus]:
    adapter = adapter or MockAdapter()
    osm = OrderStateMachine()
    bus = EventBus()
    logger = logging.getLogger("test_monitor")
    monitor = OrderMonitor(
        adapter=adapter,
        state_machine=osm,
        bus=bus,
        logger=logger,
        poll_interval_sec=poll_interval_sec,
        fill_timeout_sec=fill_timeout_sec,
        on_orphan_callback=on_orphan,
        on_critical_failure=on_critical,
    )
    return monitor, adapter, osm, bus


def _register_and_track(
    monitor: OrderMonitor,
    osm: OrderStateMachine,
    internal_id: str = "ord_aaa",
    broker_id: str = "KITE001",
    symbol: str = "RELIANCE",
    side: str = "BUY",
    qty: int = 10,
    expected_price: float = 2500.0,
    placed_at=None,
) -> None:
    """Register in OSM and add to monitor watch list."""
    osm.register(internal_id)
    osm.transition(internal_id, "SUBMITTED")   # adapter already did PENDING->SUBMITTED
    monitor.track(
        internal_order_id=internal_id,
        broker_order_id=broker_id,
        symbol=symbol,
        side=side,
        qty=qty,
        expected_price=expected_price,
        placed_at=placed_at or now_ist(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- track / untrack / helpers (OM3, OM15, OM16)
# ─────────────────────────────────────────────────────────────────────────────

def test_track_adds_to_watched() -> None:
    monitor, _, osm, _ = _make_monitor()
    osm.register("ord_aaa")
    osm.transition("ord_aaa", "SUBMITTED")
    monitor.track("ord_aaa", "KITE001", "RELIANCE", "BUY", 10, 2500.0, now_ist())
    assert monitor.is_watching("ord_aaa")
    assert monitor.watched_count() == 1
    print("  OK track() adds to _watched (OM3, OM16)")


def test_track_duplicate_raises_value_error() -> None:
    monitor, _, osm, _ = _make_monitor()
    _register_and_track(monitor, osm, "ord_aaa")
    raised = False
    try:
        monitor.track("ord_aaa", "KITE001", "RELIANCE", "BUY", 10, 2500.0, now_ist())
    except ValueError:
        raised = True
    assert raised, "Expected ValueError on duplicate track"
    print("  OK track() duplicate -> ValueError (OM3)")


def test_untrack_removes() -> None:
    monitor, _, osm, _ = _make_monitor()
    _register_and_track(monitor, osm, "ord_aaa")
    monitor.untrack("ord_aaa")
    assert not monitor.is_watching("ord_aaa")
    assert monitor.watched_count() == 0
    print("  OK untrack() removes from _watched (OM15)")


def test_untrack_unknown_is_noop() -> None:
    monitor, _, _, _ = _make_monitor()
    monitor.untrack("ord_unknown")  # must not raise
    print("  OK untrack(unknown) is no-op (OM15)")


def test_watched_count_correct() -> None:
    monitor, _, osm, _ = _make_monitor()
    _register_and_track(monitor, osm, "ord_001")
    _register_and_track(monitor, osm, "ord_002")
    _register_and_track(monitor, osm, "ord_003")
    assert monitor.watched_count() == 3
    monitor.untrack("ord_002")
    assert monitor.watched_count() == 2
    print("  OK watched_count() correct (OM16)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- status processing (OM5)
# ─────────────────────────────────────────────────────────────────────────────

def test_status_complete_transitions_and_emits_order_filled() -> None:
    adapter = MockAdapter()
    adapter.history_responses = [[_entry("COMPLETE", filled_qty=10, avg_price=2510.0)]]

    monitor, _, osm, bus = _make_monitor(adapter=adapter)
    received: list[OrderFilled] = []
    bus.subscribe(OrderFilled, received.append)  # type: ignore[arg-type]

    _register_and_track(monitor, osm, "ord_aaa", expected_price=2500.0)
    monitor._poll_cycle()

    assert osm.current_state("ord_aaa") == "COMPLETE"
    assert len(received) == 1
    evt = received[0]
    assert evt.internal_order_id == "ord_aaa"
    assert evt.broker_order_id == "KITE001"
    assert evt.symbol == "RELIANCE"
    assert evt.side == "BUY"
    assert evt.filled_qty == 10
    assert evt.avg_fill_price == 2510.0
    assert evt.expected_price == 2500.0
    assert not monitor.is_watching("ord_aaa")   # removed after COMPLETE
    print("  OK COMPLETE -> state COMPLETE, OrderFilled published, removed (OM5, OM6)")


def test_status_open_transitions_no_event() -> None:
    adapter = MockAdapter()
    adapter.history_responses = [[_entry("OPEN")]]

    monitor, _, osm, bus = _make_monitor(adapter=adapter)
    received: list[OrderFilled] = []
    bus.subscribe(OrderFilled, received.append)  # type: ignore[arg-type]

    _register_and_track(monitor, osm, "ord_aaa")
    monitor._poll_cycle()

    assert osm.current_state("ord_aaa") == "OPEN"
    assert len(received) == 0
    assert monitor.is_watching("ord_aaa")
    print("  OK OPEN -> state OPEN, no event (OM5)")


def test_status_partial_tracks_filled_qty_no_event() -> None:
    adapter = MockAdapter()
    adapter.history_responses = [[_entry("PARTIAL", filled_qty=5, avg_price=2505.0)]]

    monitor, _, osm, bus = _make_monitor(adapter=adapter)
    received: list[OrderFilled] = []
    bus.subscribe(OrderFilled, received.append)  # type: ignore[arg-type]

    _register_and_track(monitor, osm, "ord_aaa")
    monitor._poll_cycle()

    assert osm.current_state("ord_aaa") == "PARTIAL"
    assert len(received) == 0, "No OrderFilled on partial (OM8)"
    assert monitor.is_watching("ord_aaa")
    # Check filled_qty tracked internally
    with monitor._lock:
        assert monitor._watched["ord_aaa"].filled_qty == 5
    print("  OK PARTIAL -> state PARTIAL, no OrderFilled, filled_qty tracked (OM5, OM8)")


def test_partial_then_complete_emits_order_filled_once() -> None:
    """PARTIAL fill followed by COMPLETE: only one OrderFilled emitted (OM8)."""
    adapter = MockAdapter()
    adapter.history_responses = [
        [_entry("PARTIAL", filled_qty=5, avg_price=2505.0)],
        [_entry("COMPLETE", filled_qty=10, avg_price=2508.0)],
    ]

    monitor, _, osm, bus = _make_monitor(adapter=adapter)
    received: list[OrderFilled] = []
    bus.subscribe(OrderFilled, received.append)  # type: ignore[arg-type]

    _register_and_track(monitor, osm, "ord_aaa")
    monitor._poll_cycle()   # PARTIAL
    assert len(received) == 0
    monitor._poll_cycle()   # COMPLETE
    assert len(received) == 1
    assert received[0].filled_qty == 10
    assert received[0].avg_fill_price == 2508.0
    assert not monitor.is_watching("ord_aaa")
    print("  OK PARTIAL then COMPLETE: OrderFilled emitted exactly once (OM8)")


def test_status_cancelled_transitions_and_removes() -> None:
    adapter = MockAdapter()
    adapter.history_responses = [[_entry("CANCELLED")]]

    monitor, _, osm, bus = _make_monitor(adapter=adapter)
    _register_and_track(monitor, osm, "ord_aaa")
    monitor._poll_cycle()

    assert osm.current_state("ord_aaa") == "CANCELLED"
    assert not monitor.is_watching("ord_aaa")
    print("  OK CANCELLED -> state CANCELLED, removed (OM5)")


def test_status_rejected_transitions_to_failed_and_removes() -> None:
    adapter = MockAdapter()
    adapter.history_responses = [[_entry("REJECTED")]]

    monitor, _, osm, bus = _make_monitor(adapter=adapter)
    _register_and_track(monitor, osm, "ord_aaa")
    monitor._poll_cycle()

    assert osm.current_state("ord_aaa") == "FAILED"
    assert not monitor.is_watching("ord_aaa")
    print("  OK REJECTED -> state FAILED, removed (OM5)")


def test_unknown_status_logs_warning_no_transition() -> None:
    adapter = MockAdapter()
    adapter.history_responses = [[_entry("SOME_FUTURE_STATUS")]]

    log_records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log_records.append(record)

    handler = Capture()
    test_logger = logging.getLogger("test_monitor")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.DEBUG)

    monitor, _, osm, _ = _make_monitor(adapter=adapter)
    _register_and_track(monitor, osm, "ord_aaa")
    initial_state = osm.current_state("ord_aaa")
    monitor._poll_cycle()

    assert osm.current_state("ord_aaa") == initial_state   # no transition
    assert monitor.is_watching("ord_aaa")
    warning_msgs = [r.getMessage() for r in log_records if r.levelno == logging.WARNING]
    assert any("unknown_status" in m or "SOME_FUTURE_STATUS" in m for m in warning_msgs), (
        f"Expected unknown_status WARNING, got: {warning_msgs}"
    )

    test_logger.removeHandler(handler)
    print("  OK Unknown status -> WARNING logged, no transition (OM5)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- slippage calculation (OM9)
# ─────────────────────────────────────────────────────────────────────────────

def test_slippage_buy_fill_above_expected_positive() -> None:
    """BUY filled at 2510 vs expected 2500 -> unfavorable -> positive slippage."""
    slip = _calc_slippage_pct("BUY", avg_fill=2510.0, expected=2500.0)
    assert abs(slip - 0.4) < 1e-9, f"Expected 0.4, got {slip}"
    print("  OK BUY slippage: fill > expected -> positive slippage_pct (OM9)")


def test_slippage_buy_fill_below_expected_negative() -> None:
    """BUY filled at 2490 -> favorable -> negative slippage."""
    slip = _calc_slippage_pct("BUY", avg_fill=2490.0, expected=2500.0)
    assert abs(slip - (-0.4)) < 1e-9
    print("  OK BUY slippage: fill < expected -> negative slippage_pct (OM9)")


def test_slippage_sell_fill_below_expected_positive() -> None:
    """SELL filled at 2490 vs expected 2500 -> received less -> positive slippage."""
    slip = _calc_slippage_pct("SELL", avg_fill=2490.0, expected=2500.0)
    assert abs(slip - 0.4) < 1e-9
    print("  OK SELL slippage: fill < expected -> positive slippage_pct (OM9)")


def test_slippage_sell_fill_above_expected_negative() -> None:
    """SELL filled at 2510 -> received more -> negative slippage (favorable)."""
    slip = _calc_slippage_pct("SELL", avg_fill=2510.0, expected=2500.0)
    assert abs(slip - (-0.4)) < 1e-9
    print("  OK SELL slippage: fill > expected -> negative slippage_pct (OM9)")


def test_slippage_in_order_filled_event() -> None:
    """OrderFilled.slippage_pct computed correctly on COMPLETE (OM9)."""
    adapter = MockAdapter()
    adapter.history_responses = [[_entry("COMPLETE", filled_qty=10, avg_price=2510.0)]]

    monitor, _, osm, bus = _make_monitor(adapter=adapter)
    received: list[OrderFilled] = []
    bus.subscribe(OrderFilled, received.append)  # type: ignore[arg-type]

    _register_and_track(monitor, osm, "ord_aaa", side="BUY", expected_price=2500.0)
    monitor._poll_cycle()

    assert len(received) == 1
    assert abs(received[0].slippage_pct - 0.4) < 1e-9
    print("  OK OrderFilled.slippage_pct computed correctly (OM9)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- fill timeout (OM7)
# ─────────────────────────────────────────────────────────────────────────────

def test_fill_timeout_cancel_success_transitions_to_cancelled() -> None:
    """Order in OPEN for > fill_timeout_sec: cancel called, state -> CANCELLED."""
    adapter = MockAdapter()
    adapter.history_responses = [[_entry("OPEN")]]
    adapter.cancel_success = True

    monitor, _, osm, _ = _make_monitor(adapter=adapter, fill_timeout_sec=30)
    # Place order 40s in the past
    placed_at = now_ist() - timedelta(seconds=40)
    _register_and_track(monitor, osm, "ord_aaa", placed_at=placed_at)
    monitor._poll_cycle()

    assert osm.current_state("ord_aaa") == "CANCELLED"
    assert not monitor.is_watching("ord_aaa")
    assert "KITE001" in adapter.cancel_calls
    print("  OK fill timeout + cancel success -> CANCELLED, removed (OM7)")


def test_fill_timeout_cancel_fails_transitions_to_failed_fires_callback() -> None:
    """Cancel fails on timeout: state -> FAILED, on_orphan_callback called."""
    adapter = MockAdapter()
    adapter.history_responses = [[_entry("OPEN")]]
    adapter.cancel_success = False
    adapter.cancel_reason = "order not found"

    orphan_calls: list[tuple[str, str]] = []

    def on_orphan(internal_id: str, broker_id: str) -> None:
        orphan_calls.append((internal_id, broker_id))

    monitor, _, osm, _ = _make_monitor(
        adapter=adapter, fill_timeout_sec=30, on_orphan=on_orphan
    )
    placed_at = now_ist() - timedelta(seconds=40)
    _register_and_track(monitor, osm, "ord_aaa", placed_at=placed_at)
    monitor._poll_cycle()

    assert osm.current_state("ord_aaa") == "FAILED"
    assert not monitor.is_watching("ord_aaa")
    assert orphan_calls == [("ord_aaa", "KITE001")]
    print("  OK fill timeout + cancel fails -> FAILED, on_orphan called (OM7)")


def test_fill_timeout_not_triggered_before_deadline() -> None:
    """Order in OPEN but placed recently: no cancel, still in OPEN."""
    adapter = MockAdapter()
    adapter.history_responses = [[_entry("OPEN")]]

    monitor, _, osm, _ = _make_monitor(adapter=adapter, fill_timeout_sec=60)
    placed_at = now_ist() - timedelta(seconds=10)   # only 10s ago
    _register_and_track(monitor, osm, "ord_aaa", placed_at=placed_at)
    monitor._poll_cycle()

    assert osm.current_state("ord_aaa") == "OPEN"
    assert adapter.cancel_calls == []
    print("  OK fill timeout not triggered before deadline (OM7)")


def test_fill_timeout_skipped_for_sl_leg() -> None:
    """SL exit orders are exempt from fill timeout -- they stay open."""
    adapter = MockAdapter()
    adapter.history_responses = [[_entry("OPEN")]]
    adapter.cancel_success = True

    monitor, _, osm, _ = _make_monitor(adapter=adapter, fill_timeout_sec=30)
    placed_at = now_ist() - timedelta(seconds=120)  # well past timeout
    osm.register("ord_sl")
    osm.transition("ord_sl", "SUBMITTED")
    monitor.track(
        internal_order_id="ord_sl", broker_order_id="KITE001",
        symbol="RELIANCE", side="SELL", qty=10,
        expected_price=2450.0, placed_at=placed_at, leg="SL",
    )
    monitor._poll_cycle()

    assert osm.current_state("ord_sl") == "OPEN"
    assert adapter.cancel_calls == []
    assert monitor.is_watching("ord_sl")
    print("  OK SL leg exempt from fill timeout")


def test_fill_timeout_skipped_for_tgt_leg() -> None:
    """TGT exit orders are exempt from fill timeout -- they stay open."""
    adapter = MockAdapter()
    adapter.history_responses = [[_entry("OPEN")]]
    adapter.cancel_success = True

    monitor, _, osm, _ = _make_monitor(adapter=adapter, fill_timeout_sec=30)
    placed_at = now_ist() - timedelta(seconds=120)
    osm.register("ord_tgt")
    osm.transition("ord_tgt", "SUBMITTED")
    monitor.track(
        internal_order_id="ord_tgt", broker_order_id="KITE001",
        symbol="RELIANCE", side="SELL", qty=10,
        expected_price=2600.0, placed_at=placed_at, leg="TGT",
    )
    monitor._poll_cycle()

    assert osm.current_state("ord_tgt") == "OPEN"
    assert adapter.cancel_calls == []
    assert monitor.is_watching("ord_tgt")
    print("  OK TGT leg exempt from fill timeout")


def test_fill_timeout_skipped_for_eod_leg() -> None:
    """EOD squareoff orders are exempt from fill timeout."""
    adapter = MockAdapter()
    adapter.history_responses = [[_entry("OPEN")]]
    adapter.cancel_success = True

    monitor, _, osm, _ = _make_monitor(adapter=adapter, fill_timeout_sec=30)
    placed_at = now_ist() - timedelta(seconds=120)
    osm.register("ord_eod")
    osm.transition("ord_eod", "SUBMITTED")
    monitor.track(
        internal_order_id="ord_eod", broker_order_id="KITE001",
        symbol="RELIANCE", side="SELL", qty=10,
        expected_price=2500.0, placed_at=placed_at, leg="EOD",
    )
    monitor._poll_cycle()

    assert osm.current_state("ord_eod") == "OPEN"
    assert adapter.cancel_calls == []
    print("  OK EOD leg exempt from fill timeout")


def test_fill_timeout_still_applies_to_entry_leg() -> None:
    """Entry orders still get timed out as before."""
    adapter = MockAdapter()
    adapter.history_responses = [[_entry("OPEN")]]
    adapter.cancel_success = True

    monitor, _, osm, _ = _make_monitor(adapter=adapter, fill_timeout_sec=30)
    placed_at = now_ist() - timedelta(seconds=40)
    osm.register("ord_entry")
    osm.transition("ord_entry", "SUBMITTED")
    monitor.track(
        internal_order_id="ord_entry", broker_order_id="KITE001",
        symbol="RELIANCE", side="BUY", qty=10,
        expected_price=2500.0, placed_at=placed_at, leg="ENTRY",
    )
    monitor._poll_cycle()

    assert osm.current_state("ord_entry") == "CANCELLED"
    assert not monitor.is_watching("ord_entry")
    print("  OK ENTRY leg still subject to fill timeout")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- error tolerance (OM11)
# ─────────────────────────────────────────────────────────────────────────────

def test_broker_timeout_skips_order_retries_next_cycle() -> None:
    """BrokerTimeoutError: order skipped this cycle, watchlist unchanged."""
    adapter = MockAdapter()
    adapter.history_exc = BrokerTimeoutError("timeout", endpoint="/order/history")

    monitor, _, osm, _ = _make_monitor(adapter=adapter)
    _register_and_track(monitor, osm, "ord_aaa")
    initial_state = osm.current_state("ord_aaa")
    monitor._poll_cycle()

    # Order still watched, state unchanged
    assert monitor.is_watching("ord_aaa")
    assert osm.current_state("ord_aaa") == initial_state
    print("  OK BrokerTimeoutError: order skipped, still watched (OM11)")


def test_broker_auth_error_3_consecutive_fires_critical_callback_stops_monitor() -> None:
    """3 consecutive BrokerAuthError -> on_critical_failure called, stop_event set."""
    adapter = MockAdapter()
    adapter.history_exc = BrokerAuthError("token expired")

    critical_calls: list[str] = []

    def on_critical(reason: str) -> None:
        critical_calls.append(reason)

    monitor, _, osm, _ = _make_monitor(adapter=adapter, on_critical=on_critical)
    _register_and_track(monitor, osm, "ord_aaa")

    # 3 poll cycles with auth error
    monitor._poll_cycle()
    monitor._poll_cycle()
    monitor._poll_cycle()

    assert len(critical_calls) == 1, f"Expected 1 critical call, got {len(critical_calls)}"
    assert monitor._stop_event.is_set(), "stop_event should be set after 3 auth failures"
    print("  OK BrokerAuthError x3 -> on_critical_failure, stop_event set (OM11)")


def test_broker_auth_error_resets_on_success() -> None:
    """Auth fail count resets when a successful poll occurs."""
    adapter = MockAdapter()
    adapter.history_responses = [
        [],   # success (empty but no exception)
        [],
    ]
    adapter.history_exc = None

    critical_calls: list[str] = []
    monitor, _, osm, _ = _make_monitor(adapter=adapter, on_critical=critical_calls.append)

    # Manually set consecutive fails to 2
    monitor._consecutive_auth_fails = 2
    _register_and_track(monitor, osm, "ord_aaa")
    monitor._poll_cycle()   # success -> resets counter

    assert monitor._consecutive_auth_fails == 0
    assert not monitor._stop_event.is_set()
    print("  OK Auth fail counter resets on successful poll (OM11)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- idempotency (OM12)
# ─────────────────────────────────────────────────────────────────────────────

def test_complete_order_polled_again_no_crash() -> None:
    """Polling an already-COMPLETE order: InvalidTransitionError caught, no crash."""
    adapter = MockAdapter()
    adapter.history_responses = [
        [_entry("COMPLETE", filled_qty=10, avg_price=2510.0)],
        [_entry("COMPLETE", filled_qty=10, avg_price=2510.0)],  # polled again
    ]

    monitor, _, osm, bus = _make_monitor(adapter=adapter)
    received: list[OrderFilled] = []
    bus.subscribe(OrderFilled, received.append)  # type: ignore[arg-type]

    _register_and_track(monitor, osm, "ord_aaa")

    # First cycle: completes properly
    monitor._poll_cycle()
    assert osm.current_state("ord_aaa") == "COMPLETE"
    assert len(received) == 1

    # Manually re-add to _watched to simulate a second poll of same order
    with monitor._lock:
        from broker.order_monitor import _WatchEntry
        monitor._watched["ord_aaa"] = _WatchEntry(
            internal_order_id="ord_aaa",
            broker_order_id="KITE001",
            symbol="RELIANCE",
            side="BUY",
            qty=10,
            expected_price=2500.0,
            placed_at=now_ist(),
        )

    # Second cycle: InvalidTransitionError is caught, no crash (OM12)
    monitor._poll_cycle()
    assert len(received) == 1   # no duplicate event
    print("  OK Idempotent poll: COMPLETE->COMPLETE InvalidTransitionError caught (OM12)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- start/stop lifecycle (OM4)
# ─────────────────────────────────────────────────────────────────────────────

def test_start_stop_lifecycle() -> None:
    """start() launches thread; stop() joins within 5s."""
    adapter = MockAdapter()
    adapter.history_responses = []   # no orders to process

    monitor, _, osm, _ = _make_monitor(adapter=adapter, poll_interval_sec=1)
    monitor.start()
    assert monitor._thread is not None
    assert monitor._thread.is_alive()

    monitor.stop()
    assert not monitor._thread.is_alive()
    print("  OK start() / stop() lifecycle: thread alive then joined (OM4)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests -- thread safety (OM10)
# ─────────────────────────────────────────────────────────────────────────────

def test_thread_safety_concurrent_track_untrack() -> None:
    """20 track() + untrack() from 5 threads: no exceptions, consistent count."""
    monitor, _, osm, _ = _make_monitor()

    errors: list[Exception] = []

    def worker(start: int) -> None:
        for i in range(start, start + 4):
            oid = f"ord_{i:04d}"
            bid = f"KITE{i:04d}"
            try:
                osm.register(oid)
                osm.transition(oid, "SUBMITTED")
                monitor.track(oid, bid, "RELIANCE", "BUY", 10, 2500.0, now_ist())
                monitor.untrack(oid)
            except Exception as exc:
                errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i * 4,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Thread errors: {errors}"
    assert monitor.watched_count() == 0
    print("  OK 20 track()/untrack() from 5 threads: consistent, no exceptions (OM10)")


# ─────────────────────────────────────────────────────────────────────────────
# Regression tests: audit blocker fixes
# ─────────────────────────────────────────────────────────────────────────────

def test_empty_history_three_consecutive_fires_orphan_fresh() -> None:
    """
    BLOCKER #8 regression (clean variant): run 3 full _process_order cycles
    with a fresh monitor. After the 3rd, orphan callback fires and order removed.
    """
    orphan_calls: list[tuple[str, str]] = []

    def _on_orphan(internal_id: str, broker_id: str) -> None:
        orphan_calls.append((internal_id, broker_id))

    class _EmptyAdapter(MockAdapter):
        def get_order_history(self, broker_order_id):
            return []

    adapter = _EmptyAdapter()
    monitor, _, osm, _ = _make_monitor(adapter=adapter, on_orphan=_on_orphan)
    _register_and_track(monitor, osm, internal_id="ord_e2", broker_id="KITE_E2")

    for _ in range(3):
        entry = monitor._watched.get("ord_e2")
        if entry is None:
            break
        monitor._process_order(entry)

    assert len(orphan_calls) == 1, (
        f"Expected 1 orphan call after 3 empties, got {len(orphan_calls)}"
    )
    assert orphan_calls[0] == ("ord_e2", "KITE_E2")
    assert "ord_e2" not in monitor._watched, "Order should be untracked after orphan"
    print("  OK empty_history x3 fires orphan and untracks (BLOCKER #8)")


def test_empty_history_resets_on_non_empty_response() -> None:
    """
    BLOCKER #8: counter resets to 0 when a non-empty history is returned.
    """
    call_count = {"n": 0}

    class _FlickerAdapter(MockAdapter):
        def get_order_history(self, broker_order_id):
            call_count["n"] += 1
            if call_count["n"] <= 2:
                return []   # first 2 calls: empty
            # 3rd call: valid OPEN status
            entry = type("H", (), {
                "status": "OPEN",
                "filled_qty": 0,
                "avg_price": 0.0,
            })()
            return [entry]

    adapter = _FlickerAdapter()
    monitor, _, osm, _ = _make_monitor(adapter=adapter)
    _register_and_track(monitor, osm, internal_id="ord_flicker", broker_id="KITE_FL")

    entry = monitor._watched["ord_flicker"]
    monitor._process_order(entry)  # 1st empty
    assert entry.empty_history_count == 1

    monitor._process_order(entry)  # 2nd empty
    assert entry.empty_history_count == 2

    monitor._process_order(entry)  # 3rd: non-empty -> counter resets
    assert entry.empty_history_count == 0, (
        "Counter should reset to 0 after receiving a valid history response"
    )
    print("  OK empty_history counter resets on non-empty response (BLOCKER #8)")


# ─────────────────────────────────────────────────────────────────────────────
# FIX-024: rehydrate_from_store tests
# ─────────────────────────────────────────────────────────────────────────────

def test_fix024_rehydrate_two_pending_orders() -> None:
    """
    FIX-024 test 1: Simulate restart with 2 PENDING orders in DB.
    Assert both appear in _watched after rehydrate_from_store().
    """
    with TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        sig_id = _seed_signal(store)

        # Seed two PENDING orders
        _seed_trade_with_order(
            store, sig_id, trade_id="trade_001", order_id="KITE_001",
            order_status="PENDING", symbol="RELIANCE", qty=10
        )
        _seed_trade_with_order(
            store, sig_id, trade_id="trade_002", order_id="KITE_002",
            order_status="PENDING", symbol="INFY", qty=5
        )

        monitor, _, _, _ = _make_monitor()
        count = monitor.rehydrate_from_store(store)

        assert count == 2, f"Expected 2 rehydrated orders, got {count}"
        assert monitor.watched_count() == 2, "Both orders should be in _watched"
        # Verify both orders are present
        assert monitor.is_watching("KITE_001"), "KITE_001 should be watched"
        assert monitor.is_watching("KITE_002"), "KITE_002 should be watched"

        store.close()
        print("  OK FIX-024: rehydrate 2 PENDING orders from DB")


def test_fix024_rehydrate_filled_order_fires_event() -> None:
    """
    FIX-024 test 2: Simulate one order filled at broker during downtime.
    Assert OrderFilled event fires on first poll after rehydrate.
    """
    with TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        sig_id = _seed_signal(store, symbol="TCS")

        # Seed SUBMITTED order that filled during downtime
        # (SUBMITTED->COMPLETE is a valid transition; PENDING->COMPLETE is not)
        _seed_trade_with_order(
            store, sig_id, trade_id="trade_001", order_id="KITE_DOWN",
            order_status="SUBMITTED", symbol="TCS", qty=8, price=3500.0
        )

        # Adapter returns COMPLETE history (simulates filled during downtime)
        adapter = MockAdapter()
        adapter.history_responses = [
            [OrderHistoryEntry(
                broker_order_id="KITE_DOWN",
                status="COMPLETE",
                filled_qty=8,
                avg_price=3505.0,
                rejection_reason="",
                ts=now_ist()
            )]
        ]

        monitor, _, _, bus = _make_monitor(adapter=adapter, poll_interval_sec=1)
        monitor.rehydrate_from_store(store)

        # Capture OrderFilled events
        filled_events: list[OrderFilled] = []
        def capture_filled(event: OrderFilled) -> None:
            filled_events.append(event)
        bus.subscribe(OrderFilled, capture_filled)

        # Run one poll cycle
        entry = monitor._watched["KITE_DOWN"]
        monitor._process_order(entry)

        # Assert OrderFilled event was published
        assert len(filled_events) == 1, f"Expected 1 OrderFilled event, got {len(filled_events)}"
        evt = filled_events[0]
        assert evt.internal_order_id == "KITE_DOWN"
        assert evt.broker_order_id == "KITE_DOWN"
        assert evt.filled_qty == 8
        assert evt.avg_fill_price == 3505.0

        store.close()
        print("  OK FIX-024: rehydrated filled order fires OrderFilled event")


def test_fix024_rehydrate_empty_db_no_error() -> None:
    """
    FIX-024 test 3: Assert rehydrate_from_store() with empty DB completes without error.
    """
    with TemporaryDirectory() as tmp:
        store = _make_store(Path(tmp))
        # No signals, no trades, no orders

        monitor, _, _, _ = _make_monitor()
        count = monitor.rehydrate_from_store(store)

        assert count == 0, f"Expected 0 rehydrated orders, got {count}"
        assert monitor.watched_count() == 0, "No orders should be watched"

        store.close()
        print("  OK FIX-024: rehydrate from empty DB completes without error")


# ─────────────────────────────────────────────────────────────────────────────
# FIX-028: OrderPartiallyTerminated event on partial-fill terminal state
# ─────────────────────────────────────────────────────────────────────────────

def test_fix028_partial_cancelled_emits_partially_terminated() -> None:
    """
    FIX-028 test 1: Partial fill (50/100) then CANCELLED emits OrderPartiallyTerminated.
    Assert event contains correct qty_filled and reason.
    """
    adapter = MockAdapter()
    # First poll: PARTIAL with 50/100 filled
    # Second poll: CANCELLED with 50 filled (terminal)
    adapter.history_responses = [
        [_entry("PARTIAL", filled_qty=50, avg_price=2505.0)],
        [_entry("CANCELLED", filled_qty=50, avg_price=2505.0)],
    ]

    monitor, _, osm, bus = _make_monitor(adapter=adapter)

    # Subscribe to catch OrderPartiallyTerminated events
    from core.events import OrderPartiallyTerminated
    received_partial: list[OrderPartiallyTerminated] = []
    bus.subscribe(OrderPartiallyTerminated, received_partial.append)  # type: ignore[arg-type]

    # Register and track an entry order for 100 shares
    _register_and_track(monitor, osm, "ord_entry", "KITE001", "RELIANCE", "BUY", 100, 2500.0)

    # First poll: PARTIAL status (50/100 filled at 2505.0)
    monitor._poll_cycle()

    # Second poll: CANCELLED status (50 filled, 50 cancelled)
    monitor._poll_cycle()

    # Assert OrderPartiallyTerminated was published
    assert len(received_partial) == 1, f"Expected 1 OrderPartiallyTerminated, got {len(received_partial)}"

    event = received_partial[0]
    assert event.internal_order_id == "ord_entry", f"Expected ord_entry, got {event.internal_order_id}"
    assert event.filled_qty == 50, f"Expected filled_qty=50, got {event.filled_qty}"
    assert event.avg_fill_price == 2505.0, f"Expected avg_fill_price=2505.0, got {event.avg_fill_price}"
    assert event.reason == "CANCELLED", f"Expected reason=CANCELLED, got {event.reason}"

    # Assert OSM transitioned to CANCELLED
    assert osm.current_state("ord_entry") == "CANCELLED", "Order should be in CANCELLED state"

    # Assert order was untracked
    assert not monitor.is_watching("ord_entry"), "Order should be untracked"

    print("  OK FIX-028: partial fill (50/100) then CANCELLED emits OrderPartiallyTerminated")


def test_fix028_zero_fill_cancelled_no_partially_terminated() -> None:
    """
    FIX-028 test 2: Zero fill (0/100) then CANCELLED does NOT emit OrderPartiallyTerminated.
    Only OrderStatusChanged should be emitted.
    """
    adapter = MockAdapter()
    adapter.history_responses = [[_entry("CANCELLED", filled_qty=0, avg_price=0.0)]]

    monitor, _, osm, bus = _make_monitor(adapter=adapter)

    # Subscribe to catch events
    from core.events import OrderPartiallyTerminated, OrderStatusChanged
    received_partial: list[OrderPartiallyTerminated] = []
    received_status: list[OrderStatusChanged] = []
    bus.subscribe(OrderPartiallyTerminated, received_partial.append)  # type: ignore[arg-type]
    bus.subscribe(OrderStatusChanged, received_status.append)  # type: ignore[arg-type]

    # Register and track
    _register_and_track(monitor, osm, "ord_entry", "KITE001", "RELIANCE", "BUY", 100, 2500.0)

    # Simulate CANCELLED with zero fill
    monitor._poll_cycle()

    # Assert NO OrderPartiallyTerminated was published
    assert len(received_partial) == 0, f"Expected 0 OrderPartiallyTerminated, got {len(received_partial)}"

    # Assert OrderStatusChanged was published (from _safe_transition)
    assert len(received_status) > 0, "Expected OrderStatusChanged to be published"

    print("  OK FIX-028: zero fill (0/100) then CANCELLED does NOT emit OrderPartiallyTerminated")


def test_fix028_complete_fill_no_partially_terminated() -> None:
    """
    FIX-028 test 3: Complete fill (100/100) emits OrderFilled, NOT OrderPartiallyTerminated.
    """
    adapter = MockAdapter()
    adapter.history_responses = [[_entry("COMPLETE", filled_qty=100, avg_price=2505.0)]]

    monitor, _, osm, bus = _make_monitor(adapter=adapter)

    # Subscribe to catch events
    from core.events import OrderFilled, OrderPartiallyTerminated
    received_filled: list[OrderFilled] = []
    received_partial: list[OrderPartiallyTerminated] = []
    bus.subscribe(OrderFilled, received_filled.append)  # type: ignore[arg-type]
    bus.subscribe(OrderPartiallyTerminated, received_partial.append)  # type: ignore[arg-type]

    # Register and track
    _register_and_track(monitor, osm, "ord_entry", "KITE001", "RELIANCE", "BUY", 100, 2500.0)

    # Simulate COMPLETE status (100/100 filled)
    monitor._poll_cycle()

    # Assert OrderFilled was published
    assert len(received_filled) == 1, f"Expected 1 OrderFilled, got {len(received_filled)}"

    # Assert NO OrderPartiallyTerminated was published
    assert len(received_partial) == 0, f"Expected 0 OrderPartiallyTerminated, got {len(received_partial)}"

    print("  OK FIX-028: complete fill (100/100) emits OrderFilled, NOT OrderPartiallyTerminated")


def test_fix028_partial_failed_emits_partially_terminated() -> None:
    """
    FIX-028 test 4: Partial fill then FAILED (not just CANCELLED) also emits OrderPartiallyTerminated.
    """
    adapter = MockAdapter()
    # First poll: PARTIAL with 30/100 filled
    # Second poll: REJECTED with 30 filled (maps to FAILED state)
    adapter.history_responses = [
        [_entry("PARTIAL", filled_qty=30, avg_price=2502.0)],
        [_entry("REJECTED", filled_qty=30, avg_price=2502.0)],
    ]

    monitor, _, osm, bus = _make_monitor(adapter=adapter)

    # Subscribe to catch OrderPartiallyTerminated events
    from core.events import OrderPartiallyTerminated
    received_partial: list[OrderPartiallyTerminated] = []
    bus.subscribe(OrderPartiallyTerminated, received_partial.append)  # type: ignore[arg-type]

    # Register and track
    _register_and_track(monitor, osm, "ord_entry", "KITE001", "RELIANCE", "BUY", 100, 2500.0)

    # First poll: PARTIAL
    monitor._poll_cycle()

    # Second poll: REJECTED (maps to FAILED)
    monitor._poll_cycle()

    # Assert OrderPartiallyTerminated was published with reason=FAILED
    assert len(received_partial) == 1, f"Expected 1 OrderPartiallyTerminated, got {len(received_partial)}"

    event = received_partial[0]
    assert event.filled_qty == 30, f"Expected filled_qty=30, got {event.filled_qty}"
    assert event.reason == "FAILED", f"Expected reason=FAILED, got {event.reason}"

    # Assert OSM transitioned to FAILED
    assert osm.current_state("ord_entry") == "FAILED", "Order should be in FAILED state"

    print("  OK FIX-028: partial fill then REJECTED emits OrderPartiallyTerminated with reason=FAILED")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        test_track_adds_to_watched,
        test_track_duplicate_raises_value_error,
        test_untrack_removes,
        test_untrack_unknown_is_noop,
        test_watched_count_correct,
        test_status_complete_transitions_and_emits_order_filled,
        test_status_open_transitions_no_event,
        test_status_partial_tracks_filled_qty_no_event,
        test_partial_then_complete_emits_order_filled_once,
        test_status_cancelled_transitions_and_removes,
        test_status_rejected_transitions_to_failed_and_removes,
        test_unknown_status_logs_warning_no_transition,
        test_slippage_buy_fill_above_expected_positive,
        test_slippage_buy_fill_below_expected_negative,
        test_slippage_sell_fill_below_expected_positive,
        test_slippage_sell_fill_above_expected_negative,
        test_slippage_in_order_filled_event,
        test_fill_timeout_cancel_success_transitions_to_cancelled,
        test_fill_timeout_cancel_fails_transitions_to_failed_fires_callback,
        test_fill_timeout_not_triggered_before_deadline,
        test_broker_timeout_skips_order_retries_next_cycle,
        test_broker_auth_error_3_consecutive_fires_critical_callback_stops_monitor,
        test_broker_auth_error_resets_on_success,
        test_complete_order_polled_again_no_crash,
        test_start_stop_lifecycle,
        test_thread_safety_concurrent_track_untrack,
        test_fix024_rehydrate_two_pending_orders,
        test_fix024_rehydrate_filled_order_fires_event,
        test_fix024_rehydrate_empty_db_no_error,
        test_fix028_partial_cancelled_emits_partially_terminated,
        test_fix028_zero_fill_cancelled_no_partially_terminated,
        test_fix028_complete_fill_no_partially_terminated,
        test_fix028_partial_failed_emits_partially_terminated,
    ]

    print("=" * 70)
    print("order_monitor.py -- Test Suite")
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
