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
    ZA17 -- place_order accepts optional trigger_price (required for SL/SL-M)
            and optional variety (default "regular"; use "co" for Cover Orders).
            Both are backward-compatible optional params.

What This Module Does NOT Do:
    - Does not retry failed calls (ZA11 -- caller owns retry)
    - Does not publish OrderFilled events (order_monitor's job)
    - Does not read config files directly (all deps injected)
    - Does not import kiteconnect in any other module
"""
from __future__ import annotations

import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from kiteconnect import exceptions as kex

from broker.cost_calculator import CostCalculator
from broker.order_state_machine import OrderStateMachine
from broker.product_resolver import ProductResolver
from broker.rate_limiter import RateLimiter
from core.exceptions import (
    BrokerAuthError,
    BrokerError,
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
            raise _translate_kite_exception(exc, context, self._log) from exc

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

    def cancel_order(self, broker_order_id: str) -> CancelResult:
        """
        Cancel an open order. Returns CancelResult; does not raise on
        kite-level rejection (returns success=False instead). State machine
        transition is the CALLER's responsibility after inspecting the result.
        """
        t0 = time.monotonic()
        self._log.info(
            "cancel_order call_start",
            extra={"method": "cancel_order",
                   "broker_order_id": broker_order_id},
        )

        if self._paper:
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
                variety="regular",
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
            entries = [
                OrderHistoryEntry(
                    broker_order_id=broker_order_id,
                    status="SUBMITTED",
                    filled_qty=0,
                    avg_price=0.0,
                    rejection_reason="",
                    ts=now_ist(),
                )
            ]
            ms = int((time.monotonic() - t0) * 1000)
            self._log.info(
                "get_order_history call_end",
                extra={"method": "get_order_history", "duration_ms": ms,
                       "result_summary": "PAPER 1 entry"},
            )
            return entries

        self._rl.acquire(_CATEGORY_MAP["get_order_history"])

        try:
            raw = self._kite.order_history(order_id=broker_order_id)
        except Exception as exc:
            raise _translate_kite_exception(
                exc, {"broker_order_id": broker_order_id}, self._log
            ) from exc

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
            ms = int((time.monotonic() - t0) * 1000)
            self._log.info(
                "get_positions call_end",
                extra={"method": "get_positions", "duration_ms": ms,
                       "result_summary": "PAPER 0 positions"},
            )
            return []

        self._rl.acquire(_CATEGORY_MAP["get_positions"])

        try:
            raw = self._kite.positions()
        except Exception as exc:
            raise _translate_kite_exception(exc, {}, self._log) from exc

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
            raise _translate_kite_exception(exc, {}, self._log) from exc

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

    def get_server_time(self) -> datetime:
        """
        Return an approximate broker server timestamp for clock-skew checks (G4).

        Paper mode: returns now_ist() directly (local clock is the reference).
        Live mode:  makes a lightweight margins API call to verify connectivity,
                    then returns now_ist() after the round-trip.  This gives an
                    approximation of the broker server time (within one RTT).
                    A future improvement would parse response headers or use NTP.

        Raises a broker exception if the live API call fails, so the caller
        (check_clock_skew) can return passed=False instead of swallowing the
        connectivity error.
        """
        if self._paper:
            return now_ist()
        # Live: ping broker to validate connectivity; raises on failure
        try:
            self._rl.acquire(_CATEGORY_MAP["get_margins"])
            self._kite.margins(segment="equity")
        except Exception as exc:
            raise _translate_kite_exception(exc, {}, self._log) from exc
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
            raise _translate_kite_exception(
                exc, {"symbols": symbols}, self._log
            ) from exc

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
            quotes[symbol] = Quote(
                symbol=symbol,
                last_price=float(data.get("last_price", 0.0)),
                bid=bid,
                ask=ask,
                volume=int(data.get("volume", 0)),
                ts=ts,
            )

        ms = int((time.monotonic() - t0) * 1000)
        self._log.info(
            "get_quote call_end",
            extra={"method": "get_quote", "duration_ms": ms,
                   "result_summary": f"{len(quotes)} quotes"},
        )
        return quotes

    def get_open_orders(self) -> list[dict]:
        """
        Return all broker-side orders that are OPEN or TRIGGER PENDING.

        Used by order_reconciler CHECK 6 (ORPHAN_ORDER) to detect orders that
        exist at the broker but have no corresponding local trade row.

        In paper mode returns [] (no real broker orders).

        Returns:
            List of dicts with at minimum: {"order_id": str, "symbol": str,
            "status": str, "transaction_type": str}.
        """
        if self._paper:
            return []
        try:
            self._rl.acquire(_CATEGORY_MAP["get_margins"])  # reuse quota bucket
            all_orders = self._kite.orders()
        except Exception as exc:
            raise _translate_kite_exception(exc, {}, self._log) from exc

        open_statuses = {"OPEN", "TRIGGER PENDING"}
        return [
            {
                "order_id": o.get("order_id", ""),
                "symbol": o.get("tradingsymbol", ""),
                "status": o.get("status", ""),
                "transaction_type": o.get("transaction_type", ""),
                "quantity": o.get("quantity", 0),
                "price": o.get("price", 0.0),
            }
            for o in (all_orders or [])
            if o.get("status", "").upper() in open_statuses
        ]

    # ── private helpers ───────────────────────────────────────────────────────

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
        """Simulate order placement in paper mode (ZA10)."""
        fake_broker_id = "PAPER_" + uuid.uuid4().hex[:12].upper()
        self._osm.transition(internal_id, "SUBMITTED")
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
