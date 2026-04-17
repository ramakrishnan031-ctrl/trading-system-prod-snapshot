"""
broker/order_monitor.py -- Trading System v2

Purpose:
    Polls Zerodha for live order fill status, drives the state machine
    through intermediate and terminal states, emits OrderFilled on COMPLETE,
    and cancels orders that exceed the fill timeout (audit Issue #14).

Locked Design Decisions:
    OM1  -- Polls broker, drives OSM, emits OrderFilled on COMPLETE.
    OM2  -- Constructor: adapter, state_machine, bus, logger,
            poll_interval_sec, fill_timeout_sec, on_orphan_callback,
            on_critical_failure callback.
    OM3  -- track(internal_order_id, broker_order_id, symbol, side,
            qty, expected_price, placed_at) adds to _watched.
    OM4  -- Single daemon polling thread. start()/stop() lifecycle.
    OM5  -- Zerodha status -> OSM state mapping.
    OM6  -- OrderFilled event with OM6 payload on COMPLETE.
    OM7  -- Fill timeout: cancel, then CANCELLED or FAILED + callback.
    OM8  -- Partial fill tracking; OrderFilled only on COMPLETE.
    OM9  -- Slippage formula: BUY/SELL signed per lock.
    OM10 -- Thread-safe _watched dict with snapshot polling.
    OM11 -- BrokerTimeoutError: skip+retry; BrokerAuthError x3: stop.
    OM12 -- Idempotent: InvalidTransitionError caught, DEBUG logged.
    OM13 -- Layer 3. Imports: stdlib + core.* + broker layer 2/3.
    OM14 -- SystemConfig.order_monitor added.
    OM15 -- untrack(internal_order_id) idempotent.
    OM16 -- is_watching(), watched_count() status helpers.

What This Module Does NOT Do:
    - Does not place orders (adapter's job)
    - Does not emit PositionClosed (order_reconciler's job)
    - Does not persist state across restarts
    - Does not retry cancelled orders
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from broker.order_state_machine import TERMINAL_STATES, OrderStateMachine
from broker.zerodha_adapter import ZerodhaAdapter
from core.events import EventBus, OrderFilled
from core.exceptions import BrokerAuthError, BrokerTimeoutError, InvalidTransitionError
from core.logger import log_exception
from core.time_authority import now_ist

# ─────────────────────────────────────────────────────────────────────────────
# Internal watch record
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _WatchEntry:
    internal_order_id: str
    broker_order_id: str
    symbol: str
    side: str            # "BUY" | "SELL"
    qty: int
    expected_price: float
    placed_at: datetime
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    # Track consecutive auth failures for OM11
    auth_fail_count: int = field(default=0, compare=False)
    # Track consecutive empty-history responses; after threshold triggers orphan
    empty_history_count: int = field(default=0, compare=False)


# ─────────────────────────────────────────────────────────────────────────────
# Zerodha -> system status mapping (OM5)
# ─────────────────────────────────────────────────────────────────────────────

# These are the exact Zerodha order status strings as documented in the SDK.
_KITE_STATUS_OPEN = {"OPEN", "TRIGGER PENDING"}
_KITE_STATUS_PARTIAL = {"PARTIAL"}
_KITE_STATUS_COMPLETE = {"COMPLETE"}
_KITE_STATUS_CANCELLED = {"CANCELLED"}
_KITE_STATUS_REJECTED = {"REJECTED"}


# ─────────────────────────────────────────────────────────────────────────────
# Slippage calculation (OM9)
# ─────────────────────────────────────────────────────────────────────────────

def _calc_slippage_pct(side: str, avg_fill: float, expected: float) -> float:
    """
    Positive slippage_pct = unfavorable fill (paid more / received less).
    BUY:  slippage_pct = (avg_fill - expected) / expected * 100
    SELL: slippage_pct = (expected - avg_fill) / expected * 100
    Returns 0.0 if expected == 0 to avoid ZeroDivisionError.
    """
    if expected == 0.0:
        return 0.0
    if side == "BUY":
        return (avg_fill - expected) / expected * 100.0
    else:  # SELL
        return (expected - avg_fill) / expected * 100.0


# ─────────────────────────────────────────────────────────────────────────────
# OrderMonitor
# ─────────────────────────────────────────────────────────────────────────────

class OrderMonitor:
    """
    Background daemon that polls Zerodha for order fill status.

    Lifecycle::
        monitor = OrderMonitor(adapter, state_machine, bus, logger,
                               poll_interval_sec=2, fill_timeout_sec=60)
        monitor.track(internal_id, broker_id, "RELIANCE", "BUY", 10, 2500.0, placed_at)
        monitor.start()
        # ... system running ...
        monitor.stop()
    """

    def __init__(
        self,
        adapter: ZerodhaAdapter,
        state_machine: OrderStateMachine,
        bus: EventBus,
        logger: logging.Logger,
        poll_interval_sec: int = 2,
        fill_timeout_sec: int = 60,
        on_orphan_callback: Optional[Callable[[str, str], None]] = None,
        on_critical_failure: Optional[Callable[[str], None]] = None,
    ) -> None:
        """
        Args:
            adapter:              ZerodhaAdapter for get_order_history / cancel_order.
            state_machine:        OrderStateMachine to drive transitions.
            bus:                  EventBus to publish OrderFilled events.
            logger:               Logger from get_logger.
            poll_interval_sec:    Seconds between poll cycles (>= 1). (OM14)
            fill_timeout_sec:     Seconds before unfilled order is cancelled. (OM14)
            on_orphan_callback:   Called with (internal_id, broker_id) when cancel
                                  fails and order is orphaned. Optional. (OM7)
            on_critical_failure:  Called with (reason) when auth fails 3x. (OM11)
        """
        self._adapter = adapter
        self._osm = state_machine
        self._bus = bus
        self._log = logger
        self._poll_interval = poll_interval_sec
        self._fill_timeout = fill_timeout_sec
        self._on_orphan = on_orphan_callback
        self._on_critical = on_critical_failure

        self._watched: dict[str, _WatchEntry] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # OM11: consecutive auth fail counter (global across all orders)
        self._consecutive_auth_fails = 0

    # ── public API ────────────────────────────────────────────────────────────

    def track(
        self,
        internal_order_id: str,
        broker_order_id: str,
        symbol: str,
        side: str,
        qty: int,
        expected_price: float,
        placed_at: datetime,
    ) -> None:
        """
        Add an order to the watch list (OM3).

        Raises:
            ValueError: internal_order_id already being watched.
        """
        with self._lock:
            if internal_order_id in self._watched:
                raise ValueError(
                    f"Order {internal_order_id!r} is already being watched"
                )
            self._watched[internal_order_id] = _WatchEntry(
                internal_order_id=internal_order_id,
                broker_order_id=broker_order_id,
                symbol=symbol,
                side=side,
                qty=qty,
                expected_price=expected_price,
                placed_at=placed_at,
            )
        self._log.info(
            "order_monitor.track",
            extra={"internal_order_id": internal_order_id,
                   "broker_order_id": broker_order_id,
                   "symbol": symbol},
        )

    def untrack(self, internal_order_id: str) -> None:
        """Remove an order from the watch list (OM15). No-op if not present."""
        with self._lock:
            self._watched.pop(internal_order_id, None)

    def is_watching(self, internal_order_id: str) -> bool:
        """Return True if the order is currently being watched (OM16)."""
        with self._lock:
            return internal_order_id in self._watched

    def watched_count(self) -> int:
        """Return number of orders currently being monitored (OM16)."""
        with self._lock:
            return len(self._watched)

    def start(self) -> None:
        """Launch the daemon polling thread (OM4)."""
        if self._thread is not None and self._thread.is_alive():
            return  # already running
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="order-monitor",
            daemon=True,
        )
        self._thread.start()
        self._log.info("order_monitor.start", extra={"poll_interval_sec": self._poll_interval})

    def stop(self) -> None:
        """Signal the polling thread to exit and join with 5s timeout (OM4)."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._log.info("order_monitor.stop")

    # ── polling loop ──────────────────────────────────────────────────────────

    def _poll_loop(self) -> None:
        """Main loop: sleep poll_interval_sec, then process all watched orders."""
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._poll_interval)
            if self._stop_event.is_set():
                break
            self._poll_cycle()

    def _poll_cycle(self) -> None:
        """One polling cycle: snapshot _watched, process each order (OM10)."""
        with self._lock:
            snapshot = dict(self._watched)   # copy under lock (OM10)

        for internal_id, entry in snapshot.items():
            # Skip if already removed (concurrent untrack)
            with self._lock:
                if internal_id not in self._watched:
                    continue
            self._process_order(entry)

    def _process_order(self, entry: _WatchEntry) -> None:
        """Fetch order history, apply status transition, handle timeouts."""
        try:
            history = self._adapter.get_order_history(entry.broker_order_id)
        except BrokerAuthError as exc:
            self._consecutive_auth_fails += 1
            self._log.critical(
                "order_monitor.auth_error",
                extra={"broker_order_id": entry.broker_order_id,
                       "consecutive": self._consecutive_auth_fails},
            )
            log_exception(self._log, exc)
            if self._consecutive_auth_fails >= 3:
                reason = f"BrokerAuthError x{self._consecutive_auth_fails} -- monitor stopping"
                self._log.critical("order_monitor.critical_failure", extra={"reason": reason})
                if self._on_critical is not None:
                    self._on_critical(reason)
                self._stop_event.set()
            return
        except BrokerTimeoutError as exc:
            # OM11: skip this order this cycle, retry next
            self._log.warning(
                "order_monitor.timeout_skipped",
                extra={"broker_order_id": entry.broker_order_id},
            )
            log_exception(self._log, exc)
            return
        except Exception as exc:
            log_exception(self._log, exc)
            return

        # Reset auth fail counter on successful poll (OM11)
        self._consecutive_auth_fails = 0

        if not history:
            # OM11 extension: empty history is unexpected for a tracked order.
            # After 3 consecutive empties treat as orphan — broker may have lost it.
            entry.empty_history_count += 1
            if entry.empty_history_count >= 3:
                self._log.warning(
                    "order_monitor.empty_history_orphan",
                    extra={
                        "broker_order_id": entry.broker_order_id,
                        "symbol": entry.symbol,
                        "consecutive_empty": entry.empty_history_count,
                    },
                )
                if self._on_orphan is not None:
                    self._on_orphan(entry.internal_order_id, entry.broker_order_id)
                self.untrack(entry.internal_order_id)
            return
        # Non-empty history: reset the empty counter
        entry.empty_history_count = 0

        latest = history[-1]   # most recent status entry
        kite_status = latest.status.upper()
        filled_qty = latest.filled_qty
        avg_price = latest.avg_price if latest.avg_price else 0.0

        now = now_ist()

        if kite_status in _KITE_STATUS_OPEN:
            self._handle_open(entry, now)

        elif kite_status in _KITE_STATUS_PARTIAL:
            self._handle_partial(entry, filled_qty, avg_price)

        elif kite_status in _KITE_STATUS_COMPLETE:
            self._handle_complete(entry, filled_qty, avg_price)

        elif kite_status in _KITE_STATUS_CANCELLED:
            self._handle_terminal(entry, "CANCELLED")

        elif kite_status in _KITE_STATUS_REJECTED:
            self._handle_terminal(entry, "FAILED")

        else:
            self._log.warning(
                "order_monitor.unknown_status",
                extra={"broker_order_id": entry.broker_order_id,
                       "kite_status": kite_status},
            )

    # ── status handlers ───────────────────────────────────────────────────────

    def _handle_open(self, entry: _WatchEntry, now: datetime) -> None:
        """Transition to OPEN; check fill timeout."""
        self._safe_transition(entry.internal_order_id, "OPEN")
        self._check_fill_timeout(entry, now)

    def _handle_partial(
        self,
        entry: _WatchEntry,
        filled_qty: int,
        avg_price: float,
    ) -> None:
        """Update partial fill tracking; transition to PARTIAL (self-loop OK) (OM8)."""
        if filled_qty > entry.filled_qty:
            entry.filled_qty = filled_qty
            entry.avg_fill_price = avg_price
            self._log.info(
                "order_monitor.partial_fill",
                extra={"internal_order_id": entry.internal_order_id,
                       "filled_qty": filled_qty, "avg_price": avg_price},
            )
        self._safe_transition(entry.internal_order_id, "PARTIAL")
        # No OrderFilled on PARTIAL -- only on COMPLETE (OM8)

    def _handle_complete(
        self,
        entry: _WatchEntry,
        filled_qty: int,
        avg_price: float,
    ) -> None:
        """Transition to COMPLETE, emit OrderFilled (OM6), remove from watch."""
        # Update fill data with final values
        final_qty = filled_qty if filled_qty > 0 else entry.qty
        final_price = avg_price if avg_price > 0 else entry.expected_price

        transitioned = self._safe_transition(entry.internal_order_id, "COMPLETE")
        if not transitioned:
            return   # already terminal; idempotent (OM12)

        slippage = _calc_slippage_pct(entry.side, final_price, entry.expected_price)
        filled_at = now_ist()

        self._bus.publish(
            OrderFilled(
                source_module="order_monitor",
                internal_order_id=entry.internal_order_id,
                broker_order_id=entry.broker_order_id,
                symbol=entry.symbol,
                side=entry.side,
                filled_qty=final_qty,
                avg_fill_price=final_price,
                expected_price=entry.expected_price,
                slippage_pct=slippage,
                filled_at=filled_at.isoformat(),
            )
        )

        self._log.info(
            "order_monitor.complete",
            extra={"internal_order_id": entry.internal_order_id,
                   "avg_fill_price": final_price,
                   "slippage_pct": round(slippage, 4)},
        )
        self.untrack(entry.internal_order_id)

    def _handle_terminal(self, entry: _WatchEntry, osm_state: str) -> None:
        """Transition to a terminal state and remove from watch."""
        self._safe_transition(entry.internal_order_id, osm_state)
        self.untrack(entry.internal_order_id)

    # ── fill timeout (OM7) ────────────────────────────────────────────────────

    def _check_fill_timeout(self, entry: _WatchEntry, now: datetime) -> None:
        """
        If order has been open longer than fill_timeout_sec, cancel it (OM7).
        placed_at is timezone-aware (IST); now is also IST-aware.
        """
        elapsed = (now - entry.placed_at).total_seconds()
        if elapsed <= self._fill_timeout:
            return

        self._log.warning(
            "order_monitor.fill_timeout",
            extra={"internal_order_id": entry.internal_order_id,
                   "broker_order_id": entry.broker_order_id,
                   "elapsed_sec": round(elapsed, 1)},
        )

        result = self._adapter.cancel_order(entry.broker_order_id)
        if result.success:
            self._log.info(
                "order_monitor.timeout_cancelled",
                extra={"internal_order_id": entry.internal_order_id},
            )
            self._safe_transition(entry.internal_order_id, "CANCELLED")
            self.untrack(entry.internal_order_id)
        else:
            # Cancel failed -- orphaned order (OM7 CRITICAL path)
            self._log.critical(
                "order_monitor.orphan_detected",
                extra={"internal_order_id": entry.internal_order_id,
                       "broker_order_id": entry.broker_order_id,
                       "cancel_reason": result.reason},
            )
            self._safe_transition(entry.internal_order_id, "FAILED")
            self.untrack(entry.internal_order_id)
            if self._on_orphan is not None:
                self._on_orphan(entry.internal_order_id, entry.broker_order_id)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _safe_transition(self, internal_order_id: str, to_state: str) -> bool:
        """
        Attempt OSM transition. Returns True on success, False if already
        in that state or terminal (OM12: InvalidTransitionError caught).
        """
        try:
            self._osm.transition(internal_order_id, to_state)
            return True
        except InvalidTransitionError as exc:
            # Already COMPLETE, or illegal transition -- not an error (OM12)
            self._log.debug(
                "order_monitor.transition_skipped",
                extra={"internal_order_id": internal_order_id,
                       "to_state": to_state,
                       "reason": str(exc)},
            )
            return False
        except ValueError:
            # order_id not in OSM (untracked race) -- ignore
            return False
