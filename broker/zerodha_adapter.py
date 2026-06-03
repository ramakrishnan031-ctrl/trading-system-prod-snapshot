"""
broker/zerodha_adapter.py -- Trading System v2

Purpose:
    Single point of contact with the Zerodha kiteconnect SDK.
    No other module in the codebase imports kiteconnect directly.
    Provides a typed, testable interface over the raw kite API.

Locked Design Decisions:
    ZA1  -- Wraps kiteconnect.KiteConnect (injected). No direct kiteconnect
            import anywhere else in the codebase.
    ZA2  -- Methods: place_order, cancel_order, modify_order,
            get_order_history, get_positions, get_margins, get_quote.
            All return frozen dataclasses defined in this module.
    ZA3  -- rate_limiter.acquire(category) called AFTER validation,
            BEFORE every kite API call. Category map locked in ZA3.
    ZA4  -- product_resolver.resolve(intent, "zerodha") called in
            place_order to obtain broker product code.
    ZA5  -- Exception translation: kite exceptions -> system exceptions.
    ZA6  -- OrderRejectedError context: symbol, side, qty, price, intent,
            broker_code, rejection_reason, kite_status_code.
    ZA7  -- place_order calls new_order_id() and registers with state_machine
            in PENDING. Success -> SUBMITTED; exception -> FAILED.
    ZA8  -- State machine transitions on place_order only (ZA7).
            cancel_order does NOT auto-transition (caller's responsibility).
    ZA9  -- Logging: method entry/exit at INFO; exceptions via log_exception.
    ZA10 -- paper_mode=True: simulate all calls, never touch kite.
    ZA11 -- Adapter does NOT retry. Single attempt, raise and exit.
    ZA12 -- timeout passed to kiteconnect at construction as read_sec.
    ZA13 -- Input validation before rate_limiter.acquire.
    ZA14 -- Layer 3. Imports: kiteconnect, stdlib, core.*, broker layer 2.
    ZA15 -- kiteconnect>=5.1.0 installed in venv.
    ZA16 -- Adapter does NOT publish events (state machine does via OSM7).
            [LIVE MODE ONLY -- see ZA16a for the paper-mode carve-out.]
    ZA16a -- Paper-mode exception (H-20): in paper mode the adapter
            synthesizes BOTH the OSM SUBMITTED->COMPLETE transition AND
            the OrderFilled event publish, after a configurable delay.
            This is the ONE place the adapter publishes to the event bus.

            Rationale: paper mode mocks the entire broker + polling
            surface. There is no kite broker to return fill history and
            no order_monitor poll loop to drive OrderFilled. If the
            adapter did not synthesize the fill, paper mode would sit
            at SUBMITTED forever and the downstream pipeline
            (commit_to_used, close_trade, release_used, PositionClosed,
            shadow_tracker) would be silently untested. Pre-H-20 this
            made paper trials a false-positive green: the first leg
            placed and nothing downstream ever ran.

            Mechanics: _paper_place_order spawns a daemon thread that
            sleeps PaperConfig.auto_fill_delay_sec (default 0.5s),
            transitions OSM SUBMITTED->COMPLETE, then publishes
            OrderFilled with avg_fill_price = the limit price (slippage
            0). Delay mimics real broker fill latency.

            Live mode remains unchanged: order_monitor polls the real
            broker, detects COMPLETE, transitions OSM, and publishes
            OrderFilled as specified by OM1/OM6.

            Guard: the synth path fires ONLY when self._paper is True
            AND self._bus is not None. Live mode never takes this
            branch; any future refactor that allows the live path to
            publish is a regression against ZA16. A runtime assertion
            inside _synth_fill logs CRITICAL and returns without
            publishing if invoked with self._paper == False.
    ZA17 -- place_order accepts optional trigger_price (required for SL/SL-M)
            and optional variety (default "regular"; use "co" for Cover Orders).
            Both are backward-compatible optional params.

BL-6 (locked 2026-04-19, Phase D.1):
    Adapter detects broker HTTP 429 via getattr(exc, "code", None) == 429 and
    translates it to BrokerRateLimit429Error (distinct from client-side
    BrokerRateLimitError). Before raising, the adapter calls
    rate_limiter.penalize(category, delay) to freeze the bucket for an
    exponential delay derived from a per-category attempt counter
    (initial 0.2s, multiplier 2, max 5.0s, +/- 0.05s jitter). The counter
    resets after any successful call in the same category. ZA11 stays
    intact: the adapter does NOT retry, does NOT sleep -- it raises and
    exits. The caller (OrderPlacer, BL-19) owns retry. On the next
    attempt, rate_limiter.acquire() blocks until the bucket thaws, which
    is the backoff pacing.

    Detection branch: kiteconnect raises typed exceptions with a .code
    attribute set from the HTTP status. 429 can surface via
    kex.NetworkException, kex.GeneralException, or any other exception
    carrying .code == 429. _is_429() inspects the attribute without
    depending on the concrete exception class, so future SDK changes that
    add a dedicated type stay correctly classified.

What This Module Does NOT Do:
    - Does not retry failed calls (ZA11 -- caller owns retry)
    - Does not publish OrderFilled events in LIVE mode (order_monitor's job).
      Paper mode is the ZA16a carve-out; see above.
    - Does not read config files directly (all deps injected)
    - Does not import kiteconnect in any other module
"""
from __future__ import annotations

import email.utils
import random
import socket
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from kiteconnect import exceptions as kex

from broker.cost_calculator import CostCalculator
from broker.order_state_machine import OrderStateMachine
from broker.product_resolver import ProductResolver
from broker.rate_limiter import RateLimiter
from broker.slippage_engine import SlippageEngine
from core.config_loader import RateLimitBackoffConfig
from core.events import EventBus, OrderFilled
from core.exceptions import (
    BrokerAuthError,
    BrokerError,
    BrokerRateLimit429Error,
    BrokerTimeoutError,
    InvalidTransitionError,
    OrderRejectedError,
)
from core.ids import new_order_id
from core.logger import log_exception
from core.time_authority import now_ist

# ─────────────────────────────────────────────────────────────────────────────
# Return-type dataclasses (ZA2) -- frozen=True
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PlacedOrder:
    internal_order_id: str    # ord_<hex32> from core.ids
    broker_order_id: str      # kite order_id string
    symbol: str
    side: str                 # "BUY" | "SELL"
    qty: int
    price: float
    order_type: str           # "MARKET" | "LIMIT" | "SL" | "SL-M"
    product: str              # broker code e.g. "MIS"
    status: str               # "SUBMITTED" | "PENDING" (paper)
    ts: datetime
    trigger_price: float = 0.0   # ZA17: > 0 for SL / SL-M orders
    variety: str = "regular"     # ZA17: "regular" | "co"


@dataclass(frozen=True)
class CancelResult:
    broker_order_id: str
    success: bool
    reason: str               # empty string on success


@dataclass(frozen=True)
class ModifyResult:
    broker_order_id: str
    success: bool
    reason: str


@dataclass(frozen=True)
class OrderHistoryEntry:
    broker_order_id: str
    status: str
    filled_qty: int
    avg_price: float
    rejection_reason: str     # empty string if none
    ts: datetime


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: int
    avg_price: float
    product: str              # broker code
    side: str                 # "BUY" | "SELL" | "NONE"


@dataclass(frozen=True)
class MarginInfo:
    net: float
    available: float
    used: float
    ts: datetime


@dataclass(frozen=True)
class Quote:
    symbol: str
    last_price: float
    bid: float
    ask: float
    volume: int
    ts: datetime
    # v2.1 fields: OHLC, VWAP, circuit limits (optional for backward compat)
    vwap: float | None = None
    open_price: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    upper_circuit: float | None = None
    lower_circuit: float | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_VALID_SIDES: frozenset[str] = frozenset({"BUY", "SELL"})
_VALID_ORDER_TYPES: frozenset[str] = frozenset({"MARKET", "LIMIT", "SL", "SL-M"})

# ZA3: method -> rate_limiter category
_CATEGORY_MAP: dict[str, str] = {
    "place_order":      "order",
    "cancel_order":     "order",
    "modify_order":     "order",
    "get_order_history":"order",
    "get_positions":    "margins",
    "get_margins":      "margins",
    "get_quote":        "quote",
}

# kiteconnect order type strings
_KITE_ORDER_TYPES: dict[str, str] = {
    "MARKET": "MARKET",
    "LIMIT":  "LIMIT",
    "SL":     "SL",
    "SL-M":   "SL-M",
}

# kiteconnect transaction type strings
_KITE_TRANSACTION: dict[str, str] = {
    "BUY":  "BUY",
    "SELL": "SELL",
}


# ─────────────────────────────────────────────────────────────────────────────
# Exception translation (ZA5)
# ─────────────────────────────────────────────────────────────────────────────

def _translate_kite_exception(
    exc: Exception,
    context: dict[str, object],
    logger: Any,
) -> BrokerError:
    """
    Map a raw kiteconnect (or network) exception to a system BrokerError
    subclass. Logs the original exception before returning the translated one.
    """
    log_exception(logger, exc)

    if isinstance(exc, kex.TokenException):
        return BrokerAuthError(
            f"Zerodha token/auth failure: {exc}",
            **context,
            http_status=getattr(exc, "code", None),
        )
    if isinstance(exc, kex.NetworkException):
        return BrokerTimeoutError(
            f"Zerodha network error: {exc}",
            **context,
        )
    if isinstance(exc, (kex.InputException, kex.OrderException)):
        return OrderRejectedError(
            f"Zerodha rejected order: {exc}",
            rejection_reason=str(exc),
            kite_status_code=getattr(exc, "code", None),
            **context,
        )
    if isinstance(exc, kex.PermissionException):
        return BrokerAuthError(
            f"Zerodha permission denied: {exc}",
            **context,
            http_status=getattr(exc, "code", None),
        )
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return BrokerTimeoutError(
            f"Network timeout calling Zerodha: {exc}",
            **context,
        )
    if isinstance(exc, kex.GeneralException):
        return BrokerError(
            f"Zerodha general error: {exc}",
            **context,
        )
    # Unknown -- wrap in BrokerError
    return BrokerError(
        f"Unexpected error from Zerodha: {type(exc).__name__}: {exc}",
        **context,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Adapter
# ─────────────────────────────────────────────────────────────────────────────

class ZerodhaAdapter:
    """
    Typed adapter over kiteconnect.KiteConnect. All broker I/O flows
    through this class; no other module touches kiteconnect directly (ZA1).

    Constructed once at startup and injected wherever broker calls are
    needed. All dependencies are injected (ZA1), so the adapter is fully
    testable with mocks.
    """

    def __init__(
        self,
        kite_client: Any,                          # KiteConnect or mock
        rate_limiter: RateLimiter,
        product_resolver: ProductResolver,
        cost_calculator: CostCalculator,           # reserved for future cost tracking
        state_machine: OrderStateMachine,
        logger: Any,
        paper_mode: bool = False,
        paper_capital: float = 100_000.0,
        quote_provider: Optional[Callable[[list[str]], dict[str, Quote]]] = None,
        account_id: Optional[str] = None,          # IC9: reserved for v2.1 multi-account
        bus: Optional[EventBus] = None,            # ZA16a: paper mode publishes OrderFilled
        paper_auto_fill_delay_sec: float = 0.5,    # ZA16a: daemon-thread synth delay
        rate_limit_backoff: Optional[RateLimitBackoffConfig] = None,  # BL-6
        # Audit 6.2: paper LTP-gating (default OFF -- existing tests behave
        # as before; production paper YAML enables it).
        paper_ltp_gating_enabled: bool = False,
        paper_ltp_gating_max_wait_sec: float = 60.0,
        paper_ltp_gating_poll_sec: float = 0.5,
        # CFG-6 (2026-04-26 audit): paper-mode slippage applicator (P12).
        # None = no slippage (existing tests behave as before). main.py
        # late-binds via set_slippage_engine() once instrument_cache is
        # loaded. Live mode ignores this parameter -- broker fills are truth.
        slippage_engine: Optional[SlippageEngine] = None,
    ) -> None:
        self._kite = kite_client
        self._rl = rate_limiter
        self._pr = product_resolver
        self._cc = cost_calculator
        self._osm = state_machine
        self._log = logger
        self._paper = paper_mode
        self._paper_capital = paper_capital
        self._quote_provider = quote_provider
        self._account_id = account_id  # IC9: no-op for v2 single-account
        self._bus = bus
        self._paper_auto_fill_delay_sec = paper_auto_fill_delay_sec
        # Audit 6.2: paper LTP-gating settings (no-op when paper_mode=False)
        self._paper_ltp_gating_enabled = paper_ltp_gating_enabled
        self._paper_ltp_gating_max_wait_sec = paper_ltp_gating_max_wait_sec
        self._paper_ltp_gating_poll_sec = paper_ltp_gating_poll_sec
        # CFG-6: paper-mode slippage engine. May be set later via setter.
        self._slippage: Optional[SlippageEngine] = slippage_engine
        # BL-6: 429 backoff state. Per-category counter drives exponential delay;
        # resets when any call in the category succeeds. Lock guards increments
        # across threads (order_placer, order_monitor, reconciler can all race).
        self._rl_backoff: RateLimitBackoffConfig = (
            rate_limit_backoff or RateLimitBackoffConfig()
        )
        self._429_attempts: dict[str, int] = {}
        self._429_lock: threading.Lock = threading.Lock()
        # Paper order state tracker: broker_order_id -> {status, filled_qty, avg_price}.
        # Updated by _paper_place_order (SUBMITTED), _synth_fill (COMPLETE),
        # and cancel_order (CANCELLED). Read by get_order_history() so
        # order_monitor sees real state instead of a static SUBMITTED stub.
        self._paper_fills: dict[str, dict] = {}
        self._paper_fills_lock: threading.Lock = threading.Lock()
        self._paper_positions: dict[str, dict] = {}
        # FIX-009: capture the HTTP Date header from the most recent Kite API
        # response so get_server_time() can return the broker's actual clock
        # instead of the local RTT midpoint.
        self._last_response_date: Optional[datetime] = None
        self._install_date_header_hook()
        # FIX-072: TTL-cached margin requirements by (symbol, intent).
        # Cache entry: (margin_pct, fetched_at). TTL = 5 minutes.
        self._margin_cache: dict[tuple[str, str], tuple[float, datetime]] = {}
        self._margin_cache_lock: threading.Lock = threading.Lock()
        self._margin_cache_ttl_sec: float = 300.0  # 5 minutes

        # ZA16a: paper needs bus to publish synthesized OrderFilled. If paper
        # is on but bus is None we degrade safely (state reaches COMPLETE via
        # synth thread; no event) and log a warning. main.py wires bus in
        # non-degraded mode; tests can skip bus to exercise paper without
        # event plumbing.
        if self._paper and self._bus is None:
            self._log.warning(
                "zerodha_adapter paper_mode with bus=None -- OrderFilled "
                "will NOT be published (ZA16a synth degrades to OSM-only)"
            )

    # ── public methods ────────────────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        price: float,
        order_type: str,
        intent: str,
        tag: Optional[str] = None,
        trigger_price: float = 0.0,
        variety: str = "regular",
    ) -> PlacedOrder:
        """
        Place an order with Zerodha (or simulate in paper mode).

        Validates inputs, acquires rate limit token, resolves product code,
        registers with state machine, calls kite, transitions state.

        Args:
            symbol:        trading symbol e.g. "RELIANCE"
            side:          "BUY" or "SELL"
            qty:           number of shares (> 0)
            price:         limit price (> 0 for LIMIT/SL; may be 0 for MARKET)
            order_type:    "MARKET" | "LIMIT" | "SL" | "SL-M"
            intent:        semantic product intent e.g. "INTRADAY"
            tag:           optional order tag passed to kite
            trigger_price: stop trigger price (ZA17; required > 0 for SL/SL-M)
            variety:       kite variety string (ZA17; default "regular"; "co" for CO orders)

        Returns:
            PlacedOrder with internal_order_id and broker_order_id.

        Raises:
            ValueError:                invalid inputs (ZA13)
            ProductNotSupportedError:  intent not supported by zerodha (ZA4)
            BrokerAuthError:           token/auth failure (ZA5)
            BrokerTimeoutError:        network/timeout (ZA5)
            OrderRejectedError:        kite rejected the order (ZA5)
            BrokerError:               any other kite error (ZA5)
        """
        t0 = time.monotonic()
        self._log.info(
            "place_order call_start",
            extra={"method": "place_order", "symbol": symbol,
                   "side": side, "qty": qty, "order_type": order_type,
                   "intent": intent},
        )

        # ZA13: validate before burning rate-limit token
        self._validate_place_order(symbol, side, qty, price, order_type, trigger_price)

        # ZA4: resolve product intent -> broker code (may raise ProductNotSupportedError)
        broker_code = self._pr.resolve(intent, "zerodha")

        # ZA7: allocate internal ID and register with state machine in PENDING
        internal_id = new_order_id()
        self._osm.register(internal_id)

        if self._paper:
            result = self._paper_place_order(
                internal_id, symbol, side, qty, price, order_type, broker_code,
                trigger_price=trigger_price, variety=variety,
            )
            ms = int((time.monotonic() - t0) * 1000)
            self._log.info(
                "place_order call_end",
                extra={"method": "place_order", "duration_ms": ms,
                       "result_summary": f"PAPER broker_order_id={result.broker_order_id}"},
            )
            return result

        # Live path: acquire rate limit, then call kite
        self._rl.acquire(_CATEGORY_MAP["place_order"])

        context: dict[str, object] = {
            "symbol": symbol, "side": side, "qty": qty,
            "price": price, "intent": intent, "broker_code": broker_code,
        }
        try:
            kite_order_id = self._kite.place_order(
                variety=variety,
                exchange="NSE",
                tradingsymbol=symbol,
                transaction_type=_KITE_TRANSACTION[side],
                quantity=qty,
                product=broker_code,
                order_type=_KITE_ORDER_TYPES[order_type],
                price=price if order_type in ("LIMIT", "SL") else None,
                trigger_price=trigger_price if trigger_price > 0 else None,
                tag=tag,
            )
        except Exception as exc:
            # ZA7: transition to FAILED on any kite exception
            try:
                self._osm.transition(internal_id, "FAILED")
            except InvalidTransitionError:
                pass  # already failed; ignore double-fault
            raise self._translate_broker_exception(exc, context, "place_order") from exc

        # BL-6: success in "order" category -> reset its 429 attempt counter
        self._reset_429_attempts(_CATEGORY_MAP["place_order"])

        # ZA7: successful placement -> SUBMITTED
        self._osm.transition(internal_id, "SUBMITTED")

        result = PlacedOrder(
            internal_order_id=internal_id,
            broker_order_id=str(kite_order_id),
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            order_type=order_type,
            product=broker_code,
            status="SUBMITTED",
            ts=now_ist(),
            trigger_price=trigger_price,
            variety=variety,
        )
        ms = int((time.monotonic() - t0) * 1000)
        self._log.info(
            "place_order call_end",
            extra={"method": "place_order", "duration_ms": ms,
                   "result_summary": f"broker_order_id={kite_order_id}"},
        )
        return result

    def cancel_order(
        self,
        broker_order_id: str,
        variety: str = "regular",
    ) -> CancelResult:
        """
        Cancel an open order. Returns CancelResult; does not raise on
        kite-level rejection (returns success=False instead). State machine
        transition is the CALLER's responsibility after inspecting the result.

        Audit #5: accepts `variety` so callers can cancel CO bracket orders
        (variety="co") in addition to regular orders. Default stays "regular"
        for existing callers.
        """
        t0 = time.monotonic()
        self._log.info(
            "cancel_order call_start",
            extra={"method": "cancel_order",
                   "broker_order_id": broker_order_id,
                   "variety": variety},
        )

        if self._paper:
            with self._paper_fills_lock:
                if broker_order_id in self._paper_fills:
                    self._paper_fills[broker_order_id]["status"] = "CANCELLED"
            result = CancelResult(
                broker_order_id=broker_order_id, success=True, reason=""
            )
            ms = int((time.monotonic() - t0) * 1000)
            self._log.info(
                "cancel_order call_end",
                extra={"method": "cancel_order", "duration_ms": ms,
                       "result_summary": "PAPER success=True"},
            )
            return result

        self._rl.acquire(_CATEGORY_MAP["cancel_order"])

        try:
            self._kite.cancel_order(
                variety=variety,
                order_id=broker_order_id,
            )
            result = CancelResult(
                broker_order_id=broker_order_id, success=True, reason=""
            )
        except Exception as exc:
            log_exception(self._log, exc)
            result = CancelResult(
                broker_order_id=broker_order_id,
                success=False,
                reason=str(exc),
            )

        ms = int((time.monotonic() - t0) * 1000)
        self._log.info(
            "cancel_order call_end",
            extra={"method": "cancel_order", "duration_ms": ms,
                   "result_summary": f"success={result.success}"},
        )
        return result

    def modify_order(
        self,
        broker_order_id: str,
        price: Optional[float] = None,
        qty: Optional[int] = None,
        trigger_price: Optional[float] = None,
    ) -> ModifyResult:
        """Modify price/qty of a pending order. Returns ModifyResult."""
        t0 = time.monotonic()
        self._log.info(
            "modify_order call_start",
            extra={"method": "modify_order",
                   "broker_order_id": broker_order_id},
        )

        if self._paper:
            result = ModifyResult(
                broker_order_id=broker_order_id, success=True, reason=""
            )
            ms = int((time.monotonic() - t0) * 1000)
            self._log.info(
                "modify_order call_end",
                extra={"method": "modify_order", "duration_ms": ms,
                       "result_summary": "PAPER success=True"},
            )
            return result

        self._rl.acquire(_CATEGORY_MAP["modify_order"])

        try:
            self._kite.modify_order(
                variety="regular",
                order_id=broker_order_id,
                price=price,
                quantity=qty,
                trigger_price=trigger_price,
            )
            result = ModifyResult(
                broker_order_id=broker_order_id, success=True, reason=""
            )
        except Exception as exc:
            log_exception(self._log, exc)
            result = ModifyResult(
                broker_order_id=broker_order_id,
                success=False,
                reason=str(exc),
            )

        ms = int((time.monotonic() - t0) * 1000)
        self._log.info(
            "modify_order call_end",
            extra={"method": "modify_order", "duration_ms": ms,
                   "result_summary": f"success={result.success}"},
        )
        return result

    def get_order_history(self, broker_order_id: str) -> list[OrderHistoryEntry]:
        """Return the full history of a kite order as a list of entries."""
        t0 = time.monotonic()
        self._log.info(
            "get_order_history call_start",
            extra={"method": "get_order_history",
                   "broker_order_id": broker_order_id},
        )

        if self._paper:
            with self._paper_fills_lock:
                state = self._paper_fills.get(broker_order_id, {})
            status = state.get("status", "SUBMITTED")
            filled_qty = state.get("filled_qty", 0)
            avg_price = state.get("avg_price", 0.0)
            entries = [
                OrderHistoryEntry(
                    broker_order_id=broker_order_id,
                    status=status,
                    filled_qty=filled_qty,
                    avg_price=avg_price,
                    rejection_reason="",
                    ts=now_ist(),
                )
            ]
            ms = int((time.monotonic() - t0) * 1000)
            self._log.info(
                "get_order_history call_end",
                extra={"method": "get_order_history", "duration_ms": ms,
                       "result_summary": f"PAPER 1 entry status={status}"},
            )
            return entries

        self._rl.acquire(_CATEGORY_MAP["get_order_history"])

        try:
            raw = self._kite.order_history(order_id=broker_order_id)
        except Exception as exc:
            raise self._translate_broker_exception(
                exc, {"broker_order_id": broker_order_id}, "get_order_history"
            ) from exc

        # BL-6: success in category -> reset its 429 attempt counter
        self._reset_429_attempts(_CATEGORY_MAP["get_order_history"])

        entries = [
            OrderHistoryEntry(
                broker_order_id=broker_order_id,
                status=str(row.get("status", "")),
                filled_qty=int(row.get("filled_quantity", 0)),
                avg_price=float(row.get("average_price", 0.0)),
                rejection_reason=str(row.get("status_message", "") or ""),
                ts=now_ist(),
            )
            for row in (raw or [])
        ]
        ms = int((time.monotonic() - t0) * 1000)
        self._log.info(
            "get_order_history call_end",
            extra={"method": "get_order_history", "duration_ms": ms,
                   "result_summary": f"{len(entries)} entries"},
        )
        return entries

    def get_positions(self) -> list[Position]:
        """Return current open positions from kite."""
        t0 = time.monotonic()
        self._log.info(
            "get_positions call_start",
            extra={"method": "get_positions"},
        )

        if self._paper:
            with self._paper_fills_lock:
                positions = [
                    Position(
                        symbol=sym,
                        qty=abs(info["qty"]),
                        avg_price=info["avg_price"],
                        product=info.get("product", "MIS"),
                        side=info["side"],
                    )
                    for sym, info in self._paper_positions.items()
                    if info["qty"] != 0
                ]
            ms = int((time.monotonic() - t0) * 1000)
            self._log.info(
                "get_positions call_end",
                extra={"method": "get_positions", "duration_ms": ms,
                       "result_summary": f"PAPER {len(positions)} positions"},
            )
            return positions

        self._rl.acquire(_CATEGORY_MAP["get_positions"])

        try:
            raw = self._kite.positions()
        except Exception as exc:
            raise self._translate_broker_exception(exc, {}, "get_positions") from exc

        # BL-6: success in category -> reset its 429 attempt counter
        self._reset_429_attempts(_CATEGORY_MAP["get_positions"])

        # kite returns {"day": [...], "net": [...]} — use "net" for open positions
        net = raw.get("net", []) if isinstance(raw, dict) else []
        positions = [
            Position(
                symbol=str(row.get("tradingsymbol", "")),
                qty=int(row.get("quantity", 0)),
                avg_price=float(row.get("average_price", 0.0)),
                product=str(row.get("product", "")),
                side="BUY" if int(row.get("quantity", 0)) > 0 else "SELL",
            )
            for row in net
            if int(row.get("quantity", 0)) != 0
        ]
        ms = int((time.monotonic() - t0) * 1000)
        self._log.info(
            "get_positions call_end",
            extra={"method": "get_positions", "duration_ms": ms,
                   "result_summary": f"{len(positions)} positions"},
        )
        return positions

    def set_paper_capital(self, value: float) -> None:
        """
        EF-4: late-bind paper_capital once AccountRegistry has resolved the
        selected account.

        Must be called in paper mode before any code path that reads
        get_margins() (fund_manager.initialize, reconciler G3 check, banner
        display). No-op in live mode (live reads real broker margins).

        Replaces the pre-SU19 getattr(app_config.system, "paper_capital", ...)
        pattern at main.py that read a ghost config key absent from YAML and
        always defaulted to 500_000 while AccountRow.paper_capital (from
        accounts.csv, typically 5_000_000) was the authoritative value. This
        setter binds the adapter's paper_capital to AccountRow.paper_capital
        after account selection completes -- single source of truth.
        """
        if not self._paper:
            return
        if value <= 0:
            raise ValueError(
                f"paper_capital must be > 0, got {value!r}"
            )
        self._paper_capital = value
        self._log.info(
            "adapter.set_paper_capital bound",
            extra={"method": "set_paper_capital", "value": value},
        )

    def _install_date_header_hook(self) -> None:
        """
        FIX-009: attach a requests response hook to kite.reqsession to
        capture the HTTP Date header from each Kite API response.

        The kiteconnect SDK exposes its internal requests.Session as
        `kite.reqsession`.  If the attribute is absent (mocks, paper mode)
        the hook is silently skipped.
        """
        session = getattr(self._kite, "reqsession", None)
        if session is None:
            return
        hooks = getattr(session, "hooks", None)
        if hooks is None:
            return

        def _capture_date(response, *args, **kwargs):
            date_str = response.headers.get("Date", "")
            if date_str:
                try:
                    # email.utils.parsedate_to_datetime handles RFC 2822
                    parsed = email.utils.parsedate_to_datetime(date_str)
                    from core.time_authority import ist_timezone
                    self._last_response_date = parsed.astimezone(ist_timezone())
                except Exception:
                    pass

        hooks.setdefault("response", []).append(_capture_date)

    def set_slippage_engine(self, engine: SlippageEngine) -> None:
        """
        CFG-6 (2026-04-26 audit): late-bind paper-mode slippage engine after
        InstrumentCache is loaded. No-op in live mode (broker fills are
        truth). Main.py constructs adapter with slippage_engine=None
        because instrument_cache is loaded later in startup; this setter
        wires it once available.
        """
        if not self._paper:
            return
        self._slippage = engine
        self._log.info(
            "adapter.set_slippage_engine bound",
            extra={"method": "set_slippage_engine"},
        )

    def get_margins(self) -> MarginInfo:
        """Return equity margin info from kite."""
        t0 = time.monotonic()
        self._log.info(
            "get_margins call_start",
            extra={"method": "get_margins"},
        )

        if self._paper:
            info = MarginInfo(
                net=self._paper_capital,
                available=self._paper_capital,
                used=0.0,
                ts=now_ist(),
            )
            ms = int((time.monotonic() - t0) * 1000)
            self._log.info(
                "get_margins call_end",
                extra={"method": "get_margins", "duration_ms": ms,
                       "result_summary": f"PAPER net={self._paper_capital}"},
            )
            return info

        self._rl.acquire(_CATEGORY_MAP["get_margins"])

        try:
            raw = self._kite.margins(segment="equity")
        except Exception as exc:
            raise self._translate_broker_exception(exc, {}, "get_margins") from exc

        # BL-6: success in category -> reset its 429 attempt counter
        self._reset_429_attempts(_CATEGORY_MAP["get_margins"])

        equity = raw.get("equity", {}) if isinstance(raw, dict) else {}
        info = MarginInfo(
            net=float(equity.get("net", 0.0)),
            available=float(equity.get("available", {}).get("cash", 0.0)),
            used=float(equity.get("utilised", {}).get("debits", 0.0)),
            ts=now_ist(),
        )
        ms = int((time.monotonic() - t0) * 1000)
        self._log.info(
            "get_margins call_end",
            extra={"method": "get_margins", "duration_ms": ms,
                   "result_summary": f"net={info.net}"},
        )
        return info

    def get_live_margin_pct(self, symbol: str, intent: str) -> float:
        """
        FIX-072: Fetch live margin requirement percentage from broker API.

        Uses kite.order_margins() to query the exact margin required for
        placing 1 share of the given symbol with the specified intent (product).
        Returns margin_required / price as a percentage (e.g., 0.20 = 20% margin).

        Results are cached with a 5-minute TTL to avoid repeated API calls
        for the same (symbol, intent) pair. Use invalidate_margin_cache()
        to force a fresh fetch (e.g., after 16388 margin rejection).

        Paper mode: returns static leverage from _leverage_map (no broker call).
        Live mode: calls broker API with TTL caching.

        Args:
            symbol: Trading symbol (e.g., "RELIANCE").
            intent: Semantic product intent (e.g., "INTRADAY", "DELIVERY").

        Returns:
            Margin percentage as a decimal (0.20 = 20% margin, leverage = 5x).
            On API failure: raises BrokerError (caller should catch and fallback).

        Raises:
            BrokerError: if live API call fails.
            ValueError: if intent is invalid or broker_code not resolved.
        """
        # Paper mode: no broker API; raise error so caller falls back to static
        if self._paper:
            raise BrokerError(
                "get_live_margin_pct not available in paper mode; use static leverage"
            )

        cache_key = (symbol, intent)

        # Check cache first (thread-safe)
        with self._margin_cache_lock:
            if cache_key in self._margin_cache:
                margin_pct, fetched_at = self._margin_cache[cache_key]
                age_sec = (now_ist() - fetched_at).total_seconds()
                if age_sec < self._margin_cache_ttl_sec:
                    # Cache hit within TTL
                    return margin_pct

        # Cache miss or expired: fetch from broker
        t0 = time.monotonic()
        self._log.info(
            "get_live_margin_pct call_start",
            extra={"method": "get_live_margin_pct", "symbol": symbol, "intent": intent},
        )

        # Resolve intent -> broker product code
        try:
            broker_code = self._pr.resolve(intent, "zerodha")
        except Exception as exc:
            raise ValueError(
                f"Failed to resolve intent {intent!r} to broker code: {exc}"
            ) from exc

        # Acquire rate limit token (use "order" category as this is order-related)
        self._rl.acquire(_CATEGORY_MAP["place_order"])

        try:
            # Query broker for margin required for 1 share at market price
            # kite.order_margins() expects a list of order params
            raw = self._kite.order_margins([{
                "exchange": "NSE",
                "tradingsymbol": symbol,
                "transaction_type": "BUY",  # Use BUY; margin is symmetric
                "variety": "regular",
                "product": broker_code,
                "order_type": "MARKET",
                "quantity": 1,
            }])
        except Exception as exc:
            # Translate kite exception to system exception
            raise self._translate_broker_exception(
                exc, {"symbol": symbol, "intent": intent}, "order_margins"
            ) from exc

        # BL-6: success -> reset 429 attempt counter for this category
        self._reset_429_attempts(_CATEGORY_MAP["place_order"])

        # Parse response: raw is a list of margin objects
        # Each object has: {"total": <float>, "pnl": {...}, "span": {...}, ...}
        if not raw or not isinstance(raw, list) or len(raw) == 0:
            raise BrokerError(
                f"order_margins returned empty or invalid response for {symbol}/{intent}"
            )

        margin_obj = raw[0]
        margin_required = float(margin_obj.get("total", 0.0))

        # Get current LTP to compute margin percentage
        # We need price to calculate margin_pct = margin_required / (qty * price)
        # Since qty=1, margin_pct = margin_required / price
        try:
            quotes_dict = self.get_quote([symbol])  # get_quote takes a list
            # get_quote returns dict[symbol, Quote] (stripped of "NSE:" prefix)
            if symbol not in quotes_dict:
                raise BrokerError(f"Quote for {symbol} not in response")
            quote = quotes_dict[symbol]
            price = quote.last_price
        except Exception:
            # If quote fetch fails, cannot compute margin_pct reliably
            # Raise error so caller can fallback to static
            raise BrokerError(
                f"Failed to fetch quote for {symbol} (needed for margin_pct calc)"
            )

        if price <= 0:
            raise BrokerError(
                f"Invalid price {price} for {symbol} (cannot compute margin_pct)"
            )

        margin_pct = margin_required / price

        # Cache the result
        with self._margin_cache_lock:
            self._margin_cache[cache_key] = (margin_pct, now_ist())

        ms = int((time.monotonic() - t0) * 1000)
        self._log.info(
            "get_live_margin_pct call_end",
            extra={
                "method": "get_live_margin_pct",
                "symbol": symbol,
                "intent": intent,
                "margin_pct": margin_pct,
                "duration_ms": ms,
            },
        )

        return margin_pct

    def invalidate_margin_cache(self, symbol: str, intent: str) -> None:
        """
        FIX-072: Invalidate cached margin for (symbol, intent).

        Called by order_placer when a 16388 margin rejection occurs, forcing
        a fresh fetch on the next get_live_margin_pct() call.

        Args:
            symbol: Trading symbol.
            intent: Product intent.
        """
        cache_key = (symbol, intent)
        with self._margin_cache_lock:
            if cache_key in self._margin_cache:
                del self._margin_cache[cache_key]
                self._log.info(
                    "margin_cache invalidated",
                    extra={"symbol": symbol, "intent": intent},
                )

    def get_server_time(self) -> datetime:
        """
        Return an approximate broker server timestamp for clock-skew checks (G4).

        Paper mode: returns now_ist() directly (local clock is the reference).
        Live mode:  makes a lightweight quote API call to verify connectivity,
                    then returns now_ist() after the round-trip.  This gives an
                    approximation of the broker server time (within one RTT).
                    A future improvement would parse response headers or use NTP.

        D.3 (2026-04-25): switched from kite.margins() (consumes the
        "margins" rate-limit bucket alongside reconciler at 15s and
        order_monitor at 2s) to kite.quote(["NSE:NIFTY 50"]) which uses
        the "quote" bucket. Frees margins capacity (burst=1, 1/sec) for
        the reconciler/order_monitor hot paths and avoids starvation
        when the probe runs every 60s.

        Raises a broker exception if the live API call fails, so the caller
        (check_clock_skew) can return passed=False instead of swallowing the
        connectivity error.
        """
        if self._paper:
            return now_ist()
        # Live: ping broker to validate connectivity; raises on failure.
        # FIX-009: the response hook (_install_date_header_hook) captures the
        # HTTP Date header from this call. After the quote returns we use that
        # header as the broker clock, falling back to now_ist() if absent.
        self._last_response_date = None  # reset before call
        try:
            self._rl.acquire(_CATEGORY_MAP["get_quote"])
            # NIFTY 50 is the canonical liquid index quote, always available
            # during market hours and a tiny payload. Errors propagate.
            self._kite.quote(["NSE:NIFTY 50"])
        except Exception as exc:
            raise self._translate_broker_exception(exc, {}, "get_quote") from exc
        # BL-6: success in category -> reset its 429 attempt counter
        self._reset_429_attempts(_CATEGORY_MAP["get_quote"])
        # FIX-009: prefer broker Date header; fall back to local clock on miss
        if self._last_response_date is not None:
            return self._last_response_date
        return now_ist()

    def get_quote(self, symbols: list[str]) -> dict[str, Quote]:
        """
        Return quotes for a list of symbols.
        In paper mode, delegates to an injected quote_provider callable.
        Raises NotImplementedError if paper mode and no provider injected.
        """
        t0 = time.monotonic()
        self._log.info(
            "get_quote call_start",
            extra={"method": "get_quote", "symbol_count": len(symbols)},
        )

        if self._paper:
            if self._quote_provider is None:
                raise NotImplementedError(
                    "get_quote in paper mode requires a quote_provider "
                    "injected at construction"
                )
            result = self._quote_provider(symbols)
            ms = int((time.monotonic() - t0) * 1000)
            self._log.info(
                "get_quote call_end",
                extra={"method": "get_quote", "duration_ms": ms,
                       "result_summary": f"PAPER {len(result)} quotes"},
            )
            return result

        self._rl.acquire(_CATEGORY_MAP["get_quote"])

        # kite quote() takes positional instrument keys: "NSE:RELIANCE", ...
        instrument_keys = [f"NSE:{s}" for s in symbols]
        try:
            raw = self._kite.quote(*instrument_keys)
        except Exception as exc:
            raise self._translate_broker_exception(
                exc, {"symbols": symbols}, "get_quote"
            ) from exc

        # BL-6: success in category -> reset its 429 attempt counter
        self._reset_429_attempts(_CATEGORY_MAP["get_quote"])

        ts = now_ist()
        quotes: dict[str, Quote] = {}
        for key, data in (raw or {}).items():
            symbol = key.split(":", 1)[-1]   # "NSE:RELIANCE" -> "RELIANCE"
            depth = data.get("depth", {})
            bid = 0.0
            ask = 0.0
            if depth:
                bids = depth.get("buy", [])
                asks = depth.get("sell", [])
                bid = float(bids[0]["price"]) if bids else 0.0
                ask = float(asks[0]["price"]) if asks else 0.0
            # v2.1: extract OHLC, VWAP, circuit limits from Kite response
            ohlc = data.get("ohlc", {})
            quotes[symbol] = Quote(
                symbol=symbol,
                last_price=float(data.get("last_price", 0.0)),
                bid=bid,
                ask=ask,
                volume=int(data.get("volume", 0)),
                ts=ts,
                vwap=float(data["average_price"]) if data.get("average_price") else None,
                open_price=float(ohlc["open"]) if ohlc.get("open") else None,
                day_high=float(ohlc["high"]) if ohlc.get("high") else None,
                day_low=float(ohlc["low"]) if ohlc.get("low") else None,
                upper_circuit=float(data["upper_circuit_limit"]) if data.get("upper_circuit_limit") else None,
                lower_circuit=float(data["lower_circuit_limit"]) if data.get("lower_circuit_limit") else None,
            )

        ms = int((time.monotonic() - t0) * 1000)
        self._log.info(
            "get_quote call_end",
            extra={"method": "get_quote", "duration_ms": ms,
                   "result_summary": f"{len(quotes)} quotes"},
        )
        return quotes

    def get_trades(self) -> list[dict]:
        """
        FIX-148: Return today's executed trades from Kite trades() API.

        Used by order_reconciler CHECK 1 to find the actual exit price when a
        position was closed externally (RMS squareoff, manual close via terminal).

        In paper mode returns an empty list (paper adapter tracks fills internally).

        Returns:
            List of dicts with keys: trade_id, order_id, tradingsymbol,
            transaction_type, quantity, average_price, fill_timestamp.
        """
        if self._paper:
            return []
        try:
            self._rl.acquire(_CATEGORY_MAP["get_margins"])
            raw = self._kite.trades()
        except Exception as exc:
            raise self._translate_broker_exception(exc, {}, "get_margins") from exc

        self._reset_429_attempts(_CATEGORY_MAP["get_margins"])

        return [
            {
                "trade_id": t.get("trade_id", ""),
                "order_id": t.get("order_id", ""),
                "tradingsymbol": t.get("tradingsymbol", ""),
                "transaction_type": t.get("transaction_type", ""),
                "quantity": int(t.get("quantity", 0)),
                "average_price": float(t.get("average_price", 0.0)),
                "fill_timestamp": t.get("fill_timestamp", ""),
            }
            for t in (raw or [])
        ]

    def get_open_orders(self) -> list[dict]:
        """
        Return all broker-side orders that are OPEN or TRIGGER PENDING.

        Used by:
            - order_reconciler CHECK 6 (ORPHAN_ORDER): detect orders that
              exist at the broker but have no corresponding local trade row.
            - order_reconciler CHECK 8 (CO_SL_DRIFT, M-2): compare broker-side
              CO trigger_price against SmartTgtManager-tracked current_sl.
            - order_monitor orphan second-source check (H-15): verify tracked
              broker_order_ids still exist before firing orphan callbacks.

        In paper mode returns synthetic open-order entries from _paper_fills
        so the reconciler can distinguish pending from filled/cancelled orders.

        Returns:
            List of dicts with keys: ``order_id``, ``symbol``, ``status``,
            ``transaction_type``, ``quantity``, ``price``, ``trigger_price``.
        """
        if self._paper:
            with self._paper_fills_lock:
                return [
                    {
                        "order_id": bid,
                        "symbol": info.get("symbol", ""),
                        "status": "OPEN",
                        "transaction_type": info.get("side", ""),
                        "quantity": info.get("qty", 0),
                        "price": info.get("price", 0.0),
                        "trigger_price": info.get("trigger_price", 0.0),
                    }
                    for bid, info in self._paper_fills.items()
                    if info.get("status") == "SUBMITTED"
                ]
        try:
            self._rl.acquire(_CATEGORY_MAP["get_margins"])  # reuse quota bucket
            all_orders = self._kite.orders()
        except Exception as exc:
            # BL-6: tag operation as get_margins (the reused quota category)
            raise self._translate_broker_exception(exc, {}, "get_margins") from exc

        # BL-6: success in category -> reset its 429 attempt counter
        self._reset_429_attempts(_CATEGORY_MAP["get_margins"])

        open_statuses = {"OPEN", "TRIGGER PENDING"}
        return [
            {
                "order_id": o.get("order_id", ""),
                "symbol": o.get("tradingsymbol", ""),
                "status": o.get("status", ""),
                "transaction_type": o.get("transaction_type", ""),
                "quantity": o.get("quantity", 0),
                "price": o.get("price", 0.0),
                # M-2: trigger_price included so order_reconciler CHECK 8
                # can compare broker-side CO SL trigger against local current_sl.
                "trigger_price": o.get("trigger_price", 0.0),
            }
            for o in (all_orders or [])
            if o.get("status", "").upper() in open_statuses
        ]

    # ── private helpers ───────────────────────────────────────────────────────

    # ── BL-6: 429 handling ────────────────────────────────────────────────────
    #
    # kiteconnect surfaces HTTP 429 on its exception classes via .code (see
    # kex.KiteException.code -- set from the upstream HTTP response). Empirically
    # the SDK wraps 429 into either kex.NetworkException (transport-shaped) or
    # kex.GeneralException (API-shaped), so we inspect .code regardless of the
    # concrete type. If the SDK starts exposing a more specific type in a
    # future release, add it to the detection branch below.
    #
    # ZA11 (adapter does not retry) stays intact: _translate_broker_exception
    # computes the backoff delay, calls rate_limiter.penalize() to freeze the
    # bucket, and RAISES BrokerRateLimit429Error. It does NOT sleep and does
    # NOT loop. The caller (OrderPlacer, per BL-19) owns retry -- its next
    # acquire() call blocks until the bucket thaws, which is the backoff pacing.

    def _is_429(self, exc: Exception) -> bool:
        """BL-6: classify a kiteconnect exception as a broker-side HTTP 429."""
        return getattr(exc, "code", None) == 429

    def _compute_429_backoff_delay(self, category: str) -> tuple[float, int]:
        """
        BL-6: compute next penalize() duration for this category and increment
        the per-category 429 attempt counter.

        Returns (delay_sec, attempt_number_1_indexed).
        """
        cfg = self._rl_backoff
        with self._429_lock:
            prior = self._429_attempts.get(category, 0)
            self._429_attempts[category] = prior + 1
        base = cfg.initial_delay_sec * (cfg.multiplier ** prior)
        capped = min(base, cfg.max_delay_sec)
        jitter = (
            random.uniform(-cfg.jitter_sec, cfg.jitter_sec)
            if cfg.jitter_sec > 0 else 0.0
        )
        delay = max(0.0, capped + jitter)
        return delay, prior + 1

    def _reset_429_attempts(self, category: str) -> None:
        """BL-6: reset per-category 429 counter after a successful call."""
        with self._429_lock:
            self._429_attempts.pop(category, None)

    def _translate_broker_exception(
        self,
        exc: Exception,
        context: dict[str, object],
        operation: str,
    ) -> BrokerError:
        """
        BL-6: instance-aware wrapper over _translate_kite_exception.

        If the exception carries HTTP status 429, call rate_limiter.penalize()
        to freeze the bucket and return a typed BrokerRateLimit429Error.
        Otherwise fall through to the module-level generic translator
        (TokenException -> BrokerAuthError, NetworkException -> BrokerTimeout,
        etc. -- ZA5 taxonomy unchanged).

        ZA11 intact: this method does NOT retry, does NOT sleep.
        """
        if self._is_429(exc):
            category = _CATEGORY_MAP.get(operation, "order")
            delay, attempt = self._compute_429_backoff_delay(category)
            try:
                self._rl.penalize(category, delay)
            except ValueError as pz_exc:
                # unknown category from the operation map -- log and raise
                # without penalize; caller still sees BrokerRateLimit429Error
                self._log.error(
                    "zerodha_adapter.penalize_unknown_category",
                    extra={"operation": operation, "category": category,
                           "error": str(pz_exc)},
                )
            self._log.warning(
                "zerodha_adapter.broker_429",
                extra={"operation": operation, "category": category,
                       "attempt": attempt, "delay_sec": delay,
                       **context},
            )
            return BrokerRateLimit429Error(
                f"broker 429 on {operation}",
                operation=operation,
                category=category,
                delay_sec=delay,
                attempt=attempt,
            )
        return _translate_kite_exception(exc, context, self._log)

    def _validate_place_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        price: float,
        order_type: str,
        trigger_price: float = 0.0,
    ) -> None:
        """ZA13: raise ValueError before touching rate limiter or state machine."""
        if not symbol or not isinstance(symbol, str):
            raise ValueError("symbol must be a non-empty string")
        if side not in _VALID_SIDES:
            raise ValueError(
                f"side must be 'BUY' or 'SELL', got {side!r}"
            )
        if not isinstance(qty, int) or qty <= 0:
            raise ValueError(
                f"qty must be a positive integer, got {qty!r}"
            )
        if order_type not in _VALID_ORDER_TYPES:
            raise ValueError(
                f"order_type must be one of {sorted(_VALID_ORDER_TYPES)}, "
                f"got {order_type!r}"
            )
        if order_type in ("LIMIT", "SL") and price <= 0:
            raise ValueError(
                f"price must be > 0 for {order_type} orders, got {price!r}"
            )
        # ZA17: SL and SL-M orders require trigger_price > 0
        if order_type in ("SL", "SL-M") and trigger_price <= 0:
            raise ValueError(
                f"trigger_price must be > 0 for {order_type} orders, "
                f"got {trigger_price!r}"
            )

    def _paper_place_order(
        self,
        internal_id: str,
        symbol: str,
        side: str,
        qty: int,
        price: float,
        order_type: str,
        broker_code: str,
        trigger_price: float = 0.0,
        variety: str = "regular",
    ) -> PlacedOrder:
        """
        Simulate order placement in paper mode (ZA10 + ZA16a).

        Returns SUBMITTED immediately; spawns a daemon thread that
        transitions to COMPLETE and publishes OrderFilled after
        paper_auto_fill_delay_sec. See ZA16a rationale in module docstring.
        """
        fake_broker_id = "PAPER_" + uuid.uuid4().hex[:12].upper()
        self._osm.transition(internal_id, "SUBMITTED")

        with self._paper_fills_lock:
            self._paper_fills[fake_broker_id] = {
                "status": "SUBMITTED", "filled_qty": 0, "avg_price": 0.0,
                "symbol": symbol, "side": side, "qty": qty,
                "price": price, "trigger_price": trigger_price,
            }

        # ZA16a: paper mode synthesizes the broker fill that live mode
        # receives from order_monitor. Fire the synth off the thread so
        # place_order returns immediately like the live path does.
        thread = threading.Thread(
            target=self._synth_fill,
            name=f"paper_synth_{internal_id}",
            args=(internal_id, fake_broker_id, symbol, side, qty, price,
                  order_type, trigger_price),
            daemon=True,
        )
        thread.start()

        return PlacedOrder(
            internal_order_id=internal_id,
            broker_order_id=fake_broker_id,
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            order_type=order_type,
            product=broker_code,
            status="SUBMITTED",
            ts=now_ist(),
            trigger_price=trigger_price,
            variety=variety,
        )

    def _synth_fill(
        self,
        internal_id: str,
        broker_order_id: str,
        symbol: str,
        side: str,
        qty: int,
        price: float,
        order_type: str = "LIMIT",
        trigger_price: float = 0.0,
    ) -> None:
        """
        ZA16a: paper-mode fill synthesizer.

        Sleeps paper_auto_fill_delay_sec, transitions OSM SUBMITTED->COMPLETE,
        publishes OrderFilled. Runs on a daemon thread. Must NEVER fire in
        live mode -- runtime guard logs CRITICAL and returns if self._paper
        is False (belt-and-braces against a future refactor accidentally
        invoking this from live code; ZA16 regression guard).

        Audit 6.2 (LTP-gating): when paper_ltp_gating_enabled=True the
        synthesizer no longer fills LIMIT/SL/SL-M orders unconditionally.
        It polls quote_provider() up to ltp_gating_max_wait_sec and only
        fires OrderFilled when LTP has crossed the order condition:
          - LIMIT BUY  : LTP <= price          fill at min(LTP, price)
          - LIMIT SELL : LTP >= price          fill at max(LTP, price)
          - SL-M BUY   : LTP >= trigger_price  fill at LTP
          - SL-M SELL  : LTP <= trigger_price  fill at LTP
          - SL  BUY    : LTP >= trigger_price  fill at min(LTP, price)
          - SL  SELL   : LTP <= trigger_price  fill at max(LTP, price)
          - MARKET     : fill at LTP after delay (or `price` if LTP unavail)
        If max_wait elapses without a crossing, OSM stays SUBMITTED and
        no OrderFilled is published -- equivalent to a real broker leaving
        the order pending. order_timeout / EOD cleanup deals with stragglers.

        When ltp_gating_enabled=False (default for tests) behaviour is
        identical to pre-fix: sleep + always fill at `price`.
        """
        # ZA16a guard: live mode must never take this path.
        if not self._paper:
            self._log.critical(
                "zerodha_adapter._synth_fill invoked in live mode -- "
                "ZA16a violation, aborting without publish",
                extra={"internal_order_id": internal_id,
                       "broker_order_id": broker_order_id, "symbol": symbol},
            )
            return

        try:
            delay = max(0.0, self._paper_auto_fill_delay_sec)
            if delay > 0:
                time.sleep(delay)

            # Audit 6.2: LTP-gated fill computation.
            if self._paper_ltp_gating_enabled:
                fill_price = self._compute_ltp_gated_fill_price(
                    symbol=symbol,
                    side=side,
                    order_type=order_type,
                    price=price,
                    trigger_price=trigger_price,
                )
                if fill_price is None:
                    # No LTP crossing within the bounded poll horizon. Leave
                    # OSM at SUBMITTED so order_timeout/EOD handles cleanup.
                    self._log.info(
                        "paper_synth: LTP-gating timeout, no fill",
                        extra={"internal_order_id": internal_id,
                               "broker_order_id": broker_order_id,
                               "symbol": symbol, "order_type": order_type,
                               "price": price, "trigger_price": trigger_price},
                    )
                    return

                # Sanity guard: reject fills that deviate >50% from the
                # reference price. Catches stale/stub LTP leaking in.
                ref = trigger_price if trigger_price > 0 else price
                if ref > 0 and fill_price > 0:
                    deviation = abs(fill_price - ref) / ref
                    if deviation > 0.50:
                        self._log.warning(
                            "paper_synth: fill_price deviates >50%% from "
                            "reference, rejecting fill (likely stale LTP)",
                            extra={
                                "internal_order_id": internal_id,
                                "symbol": symbol,
                                "fill_price": fill_price,
                                "reference_price": ref,
                                "deviation_pct": round(deviation * 100, 1),
                            },
                        )
                        return
            else:
                # Legacy behaviour: always fill at the requested price.
                fill_price = price

            # CFG-6 (2026-04-26 audit): apply per-tier slippage to the synth
            # fill so paper P&L does not over-state vs live (P12). Engine is
            # injected; absent => zero slippage (back-compat for tests that
            # don't wire it). MARKET with no LTP can land here as price=0.0;
            # SlippageEngine.apply() short-circuits non-positive prices.
            if self._slippage is not None:
                fill_price = self._slippage.apply(symbol, side, fill_price)

            # OSM transition: SUBMITTED -> COMPLETE (legal per OSM2).
            try:
                self._osm.transition(internal_id, "COMPLETE")
            except InvalidTransitionError:
                # Already terminal (e.g., cancelled between place and synth).
                # Idempotent: do not publish a spurious fill.
                self._log.info(
                    "paper_synth: OSM already terminal, skip publish",
                    extra={"internal_order_id": internal_id,
                           "broker_order_id": broker_order_id},
                )
                return

            with self._paper_fills_lock:
                self._paper_fills[broker_order_id] = {
                    "status": "COMPLETE", "filled_qty": qty, "avg_price": fill_price,
                }
                pos = self._paper_positions.get(symbol, {"qty": 0, "avg_price": 0.0})
                if side == "BUY":
                    new_qty = pos["qty"] + qty
                else:
                    new_qty = pos["qty"] - qty
                if new_qty == 0:
                    self._paper_positions.pop(symbol, None)
                else:
                    self._paper_positions[symbol] = {
                        "qty": new_qty,
                        "avg_price": fill_price,
                        "side": "BUY" if new_qty > 0 else "SELL",
                        "product": "MIS",
                    }

            if self._bus is None:
                # Degraded mode warned at ctor time. State reached COMPLETE;
                # downstream will not see OrderFilled. Tests hit this path.
                return

            filled_at = now_ist()
            # expected_price is the order condition (limit/trigger), not LTP,
            # so slippage analytics measure (fill - expected) like live mode.
            expected_for_slippage = price if price > 0 else trigger_price
            slippage_pct = 0.0
            if expected_for_slippage > 0 and fill_price != expected_for_slippage:
                slippage_pct = (
                    (fill_price - expected_for_slippage) / expected_for_slippage
                )
            self._bus.publish(
                OrderFilled(
                    source_module="zerodha_adapter_paper",
                    internal_order_id=internal_id,
                    broker_order_id=broker_order_id,
                    symbol=symbol,
                    side=side,
                    filled_qty=qty,
                    avg_fill_price=fill_price,
                    expected_price=expected_for_slippage or fill_price,
                    slippage_pct=slippage_pct,
                    filled_at=filled_at.isoformat(),
                )
            )
            self._log.info(
                "paper_synth: OrderFilled published",
                extra={"internal_order_id": internal_id,
                       "broker_order_id": broker_order_id,
                       "symbol": symbol, "qty": qty, "fill_price": fill_price,
                       "order_type": order_type,
                       "ltp_gated": self._paper_ltp_gating_enabled},
            )
        except Exception as exc:  # noqa: BLE001 -- thread must not propagate
            log_exception(self._log, exc)
            self._log.error(
                "paper_synth: unhandled exception",
                extra={"internal_order_id": internal_id,
                       "broker_order_id": broker_order_id,
                       "error": str(exc)},
            )

    def _compute_ltp_gated_fill_price(
        self,
        symbol: str,
        side: str,
        order_type: str,
        price: float,
        trigger_price: float,
    ) -> Optional[float]:
        """
        Audit 6.2: poll LTP and return the synth fill price when the order
        condition is satisfied, or None if max_wait elapses with no crossing.

        MARKET fills immediately at LTP (or `price` if LTP unavailable).
        LIMIT/SL/SL-M poll quote_provider every ltp_gating_poll_sec for up
        to ltp_gating_max_wait_sec.

        Returns:
            fill price >= 0.0 on a synthesizable fill, or None to skip.
        """
        # MARKET: one-shot. Fall back to `price` if no LTP (test fixtures
        # without quote_provider; deviation from spec but safer than 0.0).
        if order_type == "MARKET":
            ltp = self._fetch_ltp(symbol)
            # FIX-001: _fetch_ltp returns None on failure; use price as fallback
            return ltp if ltp is not None else price

        deadline = time.monotonic() + max(0.0, self._paper_ltp_gating_max_wait_sec)
        poll = max(0.01, self._paper_ltp_gating_poll_sec)

        while True:
            ltp = self._fetch_ltp(symbol)
            # FIX-001: only evaluate condition when LTP is a valid positive float
            if ltp is not None:
                fill = self._ltp_satisfies_condition(
                    side=side, order_type=order_type,
                    price=price, trigger_price=trigger_price, ltp=ltp,
                )
                if fill is not None:
                    return fill
            if time.monotonic() >= deadline:
                return None
            time.sleep(poll)

    @staticmethod
    def _ltp_satisfies_condition(
        side: str,
        order_type: str,
        price: float,
        trigger_price: float,
        ltp: float,
    ) -> Optional[float]:
        """
        Audit 6.2 helper: if LTP satisfies the order condition, return the
        synth fill price; else None. Pure function -- ltp passed in.
        """
        if order_type == "LIMIT":
            if side == "BUY" and ltp <= price:
                return min(ltp, price)
            if side == "SELL" and ltp >= price:
                return max(ltp, price)
            return None
        if order_type == "SL-M":
            if side == "BUY" and ltp >= trigger_price:
                return ltp
            if side == "SELL" and ltp <= trigger_price:
                return ltp
            return None
        if order_type == "SL":
            if side == "BUY" and ltp >= trigger_price:
                return min(ltp, price) if price > 0 else ltp
            if side == "SELL" and ltp <= trigger_price:
                return max(ltp, price) if price > 0 else ltp
            return None
        # Unknown order_type -- do not gate; behave like legacy auto-fill.
        return price

    def _fetch_ltp(self, symbol: str) -> Optional[float]:
        """
        Audit 6.2 helper: return latest LTP for symbol, or None on failure.
        Returns None (not 0.0) so callers can distinguish "no data" from a
        genuine zero price, preventing false SL triggers (FIX-001).
        """
        if self._quote_provider is None:
            return None
        try:
            quotes = self._quote_provider([symbol])
        except Exception as exc:  # noqa: BLE001 -- best-effort LTP probe
            self._log.debug(
                "paper_synth.ltp_fetch_failed",
                extra={"symbol": symbol, "error": str(exc)},
            )
            return None
        quote = (quotes or {}).get(symbol)
        if quote is None:
            return None
        ltp = float(getattr(quote, "last_price", 0.0) or 0.0)
        return ltp if ltp > 0.0 else None
