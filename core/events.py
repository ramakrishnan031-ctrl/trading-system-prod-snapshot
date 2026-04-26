"""
core/events.py — Trading System v2

Purpose:
    Minimal synchronous EventBus reserved ONLY for the 4 fan-out events
    listed in G9: OrderFilled, PositionClosed, KillSwitchActivated,
    CapitalDriftDetected. All other inter-module calls use direct imports.

Locked Design Decisions:
    EV1 — Synchronous in-process dispatch. Publisher calls bus.publish(event);
           bus invokes all subscribers in the same thread and returns when all
           are done. No queue, no async, no background thread.
    EV2 — Explicit registration: bus.subscribe(EventType, handler).
           No decorator — makes startup wiring grep-able and testable.
    EV3 — Full event envelope. Every event is a dataclass subclassing Event:
           event_id (uuid4 hex, auto-generated), ts (IST, auto via now_ist()),
           source_module (caller-provided), payload (dict, default empty).
    EV4 — Collect-all-errors-then-raise. Bus invokes every subscriber;
           captures exceptions, logs each, then raises EventDispatchError
           if any failed. No subscriber is skipped due to a sibling failing.
    EV5 — No ordering guarantee across subscribers. Invoked in registration
           order, but this is NOT part of the contract. Subscribers must not
           depend on sibling subscriber side effects.
    EV6 — Four event types seeded: OrderFilled, PositionClosed,
           KillSwitchActivated, CapitalDriftDetected. No others added
           speculatively.
    EV7 — RETIRED. OrderStateChanged was published on every OSM transition
           but had no production subscriber after Audit #12 disabled the
           order_reconciler subscription. 2026-04-26 audit DEAD-2 removed
           the event class and the OSM publish call. Re-add only alongside
           a real subscriber.
    EV8 — EodSquareoffComplete added for eod_squareoff (EOD5).
           Published after the full EOD fire sequence completes.
    EV9 — OrderStatusChanged added for broker-authoritative status snapshots
           (BL-12). Published by order_monitor on every successful OSM
           transition; consumed by order_manager to update the `orders`
           table.

What This Module Does NOT Do:
    - Does not use queues, threads, or async (see EV1)
    - Does not add event types beyond EV6's four (EV7 extends this)
    - Does not import from any module above core/
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from core.exceptions import EventDispatchError
from core.time_authority import now_ist

_log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Base event envelope (EV3)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Event:
    """
    Base for every event. Subclasses add typed payload fields (EV6).
    event_id and ts are auto-populated; source_module is caller-provided.
    payload is available for unstructured extras but typed fields are preferred.
    """
    source_module: str
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: datetime = field(default_factory=now_ist)
    payload: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Seeded event types (EV6)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class OrderFilled(Event):
    """
    Broker order transitioned to COMPLETE state (OM6).

    Core OM6 fields (set by order_monitor):
        internal_order_id, broker_order_id, symbol, side, filled_qty,
        avg_fill_price, expected_price, slippage_pct, filled_at.

    Legacy fields kept for callers that set them:
        order_id, trade_id, signal_id, fill_price.
    """
    # OM6 fields
    internal_order_id: str = ""    # ord_<hex32> from core.ids
    broker_order_id: str = ""      # kite order_id
    symbol: str = ""
    side: str = ""                 # "BUY" | "SELL"
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    expected_price: float = 0.0
    slippage_pct: float = 0.0      # positive = unfavorable (OM9)
    filled_at: str = ""            # ISO-8601 IST string
    # Legacy fields (keep; used by other callers)
    order_id: str = ""
    trade_id: str = ""
    signal_id: str = ""
    fill_price: float = 0.0


@dataclass
class PositionClosed(Event):
    """Position fully closed (exit filled or SL triggered)."""
    symbol: str = ""
    trade_id: str = ""
    signal_id: str = ""
    exit_price: float = 0.0
    realized_pnl: float = 0.0


@dataclass
class KillSwitchActivated(Event):
    """
    Kill switch state changed (triggered, or resumed) (KS8).

    Fields:
        kill_type:      "soft" | "hard" | "resume"
        reason:         Human-readable reason for the state change.
        previous_state: KillState value before this event (KS8).
        new_state:      KillState value after this event (KS8).
        triggered_by:   Module or actor that initiated the change (KS8).
    """
    kill_type: str = ""        # "soft" | "hard" | "resume"
    reason: str = ""
    previous_state: str = ""   # KS8: e.g. "INACTIVE", "SOFT_KILL", "HARD_KILL"
    new_state: str = ""        # KS8
    triggered_by: str = ""     # KS8


@dataclass
class CapitalDriftDetected(Event):
    """Broker capital deviates beyond reconciliation tolerance."""
    expected: float = 0.0
    actual: float = 0.0
    delta: float = 0.0


@dataclass
class EodSquareoffComplete(Event):
    """
    Published after the EOD square-off sequence finishes (EOD5 step 6).

    Fields:
        fired_date:          YYYY-MM-DD IST date of the EOD fire
        positions_attempted: number of open intraday positions targeted
        positions_succeeded: MARKET exit orders placed successfully
        positions_failed:    positions that could not be exited (see EOD5)
        cancels_attempted:   number of pending entry orders targeted for cancel
        cancels_succeeded:   entry orders successfully cancelled
        cancels_failed:      entry orders that could not be cancelled
    """
    fired_date: str = ""
    positions_attempted: int = 0
    positions_succeeded: int = 0
    positions_failed: int = 0
    cancels_attempted: int = 0
    cancels_succeeded: int = 0
    cancels_failed: int = 0


@dataclass
class OrderStatusChanged(Event):
    """
    Broker-authoritative order status snapshot (EV9, BL-12).

    Published by order_monitor on every successful OSM transition; consumed
    by order_manager to persist the broker-reported snapshot into the
    `orders` table (status/qty_filled/avg_fill_price columns).

    Fields:
        internal_order_id: ord_<hex32> from core.ids
        broker_order_id:   broker-assigned id; PK of the orders table row
        status:            orders.status value (OPEN/PARTIAL/COMPLETE/
                           CANCELLED/REJECTED/FAILED)
        qty_filled:        cumulative fill qty at time of transition (0 if
                           no fill yet, e.g. on initial OPEN)
        avg_fill_price:    broker-reported avg fill price; None if no fill
                           yet (e.g. on OPEN / CANCELLED-before-fill)
    """
    internal_order_id: str = ""
    broker_order_id: str = ""
    status: str = ""
    qty_filled: int = 0
    avg_fill_price: Optional[float] = None


# ─────────────────────────────────────────────────────────────────────────────
# EventBus (EV1, EV2, EV4, EV5)
# ─────────────────────────────────────────────────────────────────────────────

class EventBus:
    """
    Synchronous in-process event bus. Pass as a dependency; do not make a
    global singleton — callers own the instance and wire subscriptions at
    startup.

    Example::

        bus = EventBus()
        bus.subscribe(OrderFilled, on_order_filled)
        bus.publish(OrderFilled(source_module="order_placer", symbol="RELIANCE"))
    """

    def __init__(self) -> None:
        self._subscribers: dict[type, list[Callable[[Event], None]]] = {}

    def subscribe(self, event_type: type, handler: Callable[[Event], None]) -> None:
        """
        Register handler for event_type.

        Raises:
            TypeError: if event_type is not a subclass of Event.
        """
        if not (isinstance(event_type, type) and issubclass(event_type, Event)):
            raise TypeError(
                f"event_type must be a subclass of Event, got {event_type!r}"
            )
        self._subscribers.setdefault(event_type, []).append(handler)

    def publish(self, event: Event) -> None:
        """
        Dispatch event to all registered subscribers synchronously (EV1).
        Every subscriber is invoked even if a sibling raises (EV4).
        Collects all exceptions, logs each, then raises EventDispatchError
        if any failed. No-op when no subscribers registered for this type.
        """
        handlers = self._subscribers.get(type(event), [])
        errors: list[Exception] = []

        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:  # noqa: BLE001 — intentional collect-all
                _log.error(
                    "Subscriber %s failed for event %s(id=%s): %r",
                    getattr(handler, "__qualname__", repr(handler)),
                    type(event).__name__,
                    event.event_id,
                    exc,
                )
                errors.append(exc)

        if errors:
            raise EventDispatchError(
                f"{len(errors)} subscriber(s) failed for {type(event).__name__}",
                event_type=type(event).__name__,
                event_id=event.event_id,
                error_count=len(errors),
                errors=str(errors),
            )
