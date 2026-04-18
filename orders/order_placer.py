"""
orders/order_placer.py — Trading System v2

Purpose:
    Orchestrates the full order placement lifecycle for a single trade.
    Called by signal_processor with (symbol, side, qty, entry_price,
    sl_price, intent, signal_id, reservation_id). Creates the trade row,
    places all entry orders, starts fill monitoring, and commits (or
    releases) the capital reservation on fill/failure.

Locked Design Decisions:
    OP1  -- place() is the only public method called by signal_processor.
            Signature: place(symbol, side, qty, entry_price, sl_price,
            intent, signal_id, reservation_id) → None.
    OP2  -- place() is synchronous: creates trade, places orders, registers
            with order_monitor, returns. Fill handling is async (event-driven).
    OP3  -- tgt_price computed internally: entry ± (entry-sl)*rr_ratio.
            Default rr_ratio=2.0 (configurable at construction).
            LONG: tgt = entry + (entry - sl) * rr
            SHORT: tgt = entry - (sl - entry) * rr
    OP4  -- trade_id assigned via core.ids.new_trade_id() inside place().
            DB row created before any broker call. On broker failure,
            trade status set to FAILED and capital reservation released.
    OP5  -- In-memory fill map: internal_order_id → {trade_id, reservation_id,
            symbol, qty, leg}. Cleaned up on OrderFilled or FAILED.
    OP6  -- Subscribes to OrderFilled event at construction. Handler:
            commit capital, record fill in DB, update trade status to OPEN.
    OP7  -- On entry placement failure: set trade=FAILED, release reservation,
            cancel any partially placed orders best-effort.
    OP8  -- Thread-safe: _fill_map guarded by threading.Lock.
    OP9  -- order_protocol determined by intent: "INTRADAY" → default_protocol
            (LIMIT_TRIPLE by default; overridable at construction).
            Future: per-strategy config via scanner_configs.
    OP10 -- Sector passed as None (data/symbol_validator not yet built).
    OP11 -- Layer 5 (orders/). Imports orders/, core/, capital/, broker.

Last-Mile Gaps (locked 2026-04-16):
    OP-LM1 -- Kill-switch last-mile check. Immediately before engine.execute(),
              if kill_switch.is_active("entry"): mark trade FAILED, release
              reservation, raise OrderRejectedError("kill_switch_active_last_mile").
              kill_switch injected at construction (optional; None = disabled).
    OP-LM2 -- Reservation release on placement failure. Every BrokerError and
              soft-failure path calls fund_manager.release(reservation_id, reason)
              before returning/raising. Implemented via _handle_placement_failure().
    OP-LM3 -- Empty broker_order_id treated as failure. Validated in protocol
              files (order_protocol_limit.py, order_protocol_co.py) immediately
              after each adapter.place_order() call. Raises OrderRejectedError.

What This Module Does NOT Do:
    - Does not implement SL modification (smart_tgt_manager's job)
    - Does not implement EOD exit (eod_squareoff's job)
    - Does not implement order timeout (order_timeout's job)
    - Does not reconcile with broker (order_reconciler's job)
    - Does not send Telegram alerts (alerts/ module's job)
"""
from __future__ import annotations

import logging
import threading
from typing import Dict, Final, Optional

from broker.order_monitor import OrderMonitor
from broker.product_resolver import ProductResolver
from capital.fund_manager import FundManager
from capital.kill_switch import KillSwitch
from core.config_loader import SmartTgtConfig
from core.events import EventBus, OrderFilled
from core.exceptions import BrokerError, OrderRejectedError
from core.ids import new_trade_id
from core.logger import log_exception
from core.time_authority import now_ist
from orders.entry_engine import EntryResult
from orders.full_entry_engine import FullEntryEngine
from orders.order_manager import OrderManager
from orders.smart_tgt_manager import SmartTgtManager


# ─────────────────────────────────────────────────────────────────────────────
# Leg taxonomy (BL-7a)
#
# The `leg` field on _FillEntry routes OrderFilled events to the correct
# handler inside _on_order_filled:
#
#     ENTRY       → _handle_entry_fill  (commit capital, open position)
#     SL/TGT/EOD  → _handle_exit_fill   (release capital, close position)
#
# (The split handler is wired in BL-7d; A.3.c guards non-ENTRY legs.)
# EOD is reserved for eod_squareoff-originated tracks; it is a valid value
# today even though eod_squareoff does not currently populate _fill_map.
# ─────────────────────────────────────────────────────────────────────────────

_LEG_ENTRY: Final[str] = "ENTRY"
_LEG_SL:    Final[str] = "SL"
_LEG_TGT:   Final[str] = "TGT"
_LEG_EOD:   Final[str] = "EOD"
_VALID_LEGS: frozenset[str] = frozenset({_LEG_ENTRY, _LEG_SL, _LEG_TGT, _LEG_EOD})


# ─────────────────────────────────────────────────────────────────────────────
# Internal fill-map entry
# ─────────────────────────────────────────────────────────────────────────────

class _FillEntry:
    """
    One row in OrderPlacer._fill_map, keyed by internal_order_id.

    `leg` drives routing in _on_order_filled (see Leg taxonomy above).
    `order_protocol` / `direction` are cached here so entry-fill handling
    can branch (e.g. register with SmartTgtManager only for CO_PLUS_TGT)
    without a DB round-trip on every fill.

    Invariants:
        leg ∈ _VALID_LEGS; constructor raises ValueError otherwise.
    """

    __slots__ = (
        "trade_id", "reservation_id", "symbol", "qty", "leg",
        "order_protocol", "direction",
    )

    def __init__(
        self,
        trade_id: str,
        reservation_id: str,
        symbol: str,
        qty: int,
        leg: str,
        order_protocol: str,
        direction: str,
    ) -> None:
        if leg not in _VALID_LEGS:
            raise ValueError(
                f"_FillEntry.leg must be one of {sorted(_VALID_LEGS)}, "
                f"got {leg!r}"
            )
        self.trade_id = trade_id
        self.reservation_id = reservation_id
        self.symbol = symbol
        self.qty = qty
        self.leg = leg
        self.order_protocol = order_protocol
        self.direction = direction


# ─────────────────────────────────────────────────────────────────────────────
# OrderPlacer
# ─────────────────────────────────────────────────────────────────────────────

class OrderPlacer:
    """
    Orchestrates trade creation, order placement, and fill handling (OP1–OP11).

    Usage::
        placer = OrderPlacer(
            entry_engine=full_entry_engine,
            order_manager=om,
            fund_manager=fm,
            bus=event_bus,
            logger=log,
            rr_ratio=2.0,
            default_order_protocol="LIMIT_TRIPLE",
        )
        # signal_processor calls:
        placer.place(symbol=…, side=…, qty=…, entry_price=…, sl_price=…,
                     intent=…, signal_id=…, reservation_id=…)
    """

    def __init__(
        self,
        entry_engine: FullEntryEngine,
        order_manager: OrderManager,
        fund_manager: FundManager,
        bus: EventBus,
        logger: logging.Logger,
        order_monitor: OrderMonitor,
        rr_ratio: float = 2.0,
        default_order_protocol: str = "LIMIT_TRIPLE",
        kill_switch: Optional[KillSwitch] = None,
        product_resolver: Optional[ProductResolver] = None,
        smart_tgt_manager: Optional[SmartTgtManager] = None,
        smart_tgt_config: Optional[SmartTgtConfig] = None,
    ) -> None:
        # BL-7b: CO_PLUS_TGT needs trigger/step fractions at fill time.
        if smart_tgt_manager is not None and smart_tgt_config is None:
            raise ValueError(
                "OrderPlacer: smart_tgt_manager was provided but smart_tgt_config "
                "was not. CO_PLUS_TGT protocol needs trigger_pct/step_pct at fill "
                "time; pass smart_tgt_config=<SmartTgtConfig(...)> or omit both."
            )
        self._engine = entry_engine
        self._om = order_manager
        self._fm = fund_manager
        self._bus = bus
        self._log = logger
        self._order_monitor = order_monitor  # BL-7b: required for A.3.c track() wiring
        self._rr_ratio = rr_ratio
        self._default_protocol = default_order_protocol
        self._kill_switch = kill_switch  # OP-LM1: may be None (disabled)
        self._product_resolver = product_resolver  # HIGH #7: use resolver for product codes
        self._smart_tgt_manager = smart_tgt_manager  # BL-7b: None = SmartTgt disabled
        self._smart_tgt_config = smart_tgt_config    # BL-7b: trigger_pct/step_pct source
        # IC8: injected by main.py after Module 38; None = no tick rounding
        self._instrument_cache = None  # set via set_instrument_cache()

        # OP5: internal_order_id → _FillEntry
        self._fill_map: Dict[str, _FillEntry] = {}
        self._fill_map_lock = threading.Lock()

        # OP6: subscribe to OrderFilled
        self._bus.subscribe(OrderFilled, self._on_order_filled)

    def set_instrument_cache(self, cache) -> None:
        """Wire InstrumentCache for IC8 tick-size rounding (called from main.py)."""
        self._instrument_cache = cache

    # ── public interface ──────────────────────────────────────────────────────

    def place(
        self,
        *,
        symbol: str,
        side: str,
        qty: int,
        entry_price: float,
        sl_price: float,
        intent: str,
        signal_id: str,
        reservation_id: str,
        tgt_price: Optional[float] = None,  # SPW6: provided by signal_processor; overrides OP3
    ) -> None:
        """
        Create trade, place entry orders, register fill tracking. (OP1–OP4)

        Raises BrokerError on hard broker failure (after cleanup).
        signal_processor catches this and marks signal PLACEMENT_FAILED.
        """
        # IC8: round LIMIT prices to tick_size if instrument_cache wired
        entry_price = self._round_to_tick(symbol, entry_price)
        sl_price    = self._round_to_tick(symbol, sl_price)
        if tgt_price is not None:
            tgt_price = self._round_to_tick(symbol, tgt_price)

        # OP3: use caller-supplied tgt_price if provided; else compute internally
        if tgt_price is None:
            tgt_price = self._compute_tgt(side, entry_price, sl_price)

        # OP9: choose protocol
        order_protocol = self._default_protocol

        # OP4: compute trade fields
        direction = "LONG" if side == "BUY" else "SHORT"
        risk_amount = abs(entry_price - sl_price) * qty
        margin_reserved = entry_price * qty * 0.20  # standard intraday margin

        # OP4: create trade row FIRST (status=PENDING_FILL)
        trade_id = self._om.create_trade(
            signal_id=signal_id,
            symbol=symbol,
            direction=direction,
            strategy="",        # OP10: strategy lookup not yet wired
            sector=None,        # OP10: symbol_validator not yet built
            qty=qty,
            entry_target_price=entry_price,
            sl_initial=sl_price,
            tgt_initial=tgt_price,
            order_protocol=order_protocol,
            margin_reserved=margin_reserved,
            risk_amount=risk_amount,
        )

        # Link signal → trade
        try:
            self._om.link_signal_trade(signal_id, trade_id)
        except Exception as exc:
            log_exception(self._log, exc)
            # Non-fatal: continue; reconciler can fix the link later

        self._log.info(
            "order_placer.place_start",
            extra={
                "signal_id": signal_id, "trade_id": trade_id,
                "symbol": symbol, "side": side, "qty": qty,
                "entry_price": entry_price, "sl_price": sl_price,
                "tgt_price": tgt_price, "protocol": order_protocol,
            },
        )

        # ── OP-LM1: last-mile kill_switch check ───────────────────────────
        if self._kill_switch is not None and self._kill_switch.is_active("entry"):
            ks_exc = OrderRejectedError(
                "kill_switch_active_last_mile",
                trade_id=trade_id, signal_id=signal_id, symbol=symbol,
            )
            self._handle_placement_failure(trade_id, reservation_id, signal_id, ks_exc)
            raise ks_exc

        # ── Place entry orders ─────────────────────────────────────────────
        result: Optional[EntryResult] = None
        try:
            result = self._engine.execute(
                symbol=symbol,
                side=side,
                qty=qty,
                entry_price=entry_price,
                sl_price=sl_price,
                tgt_price=tgt_price,
                intent=intent,
                trade_id=trade_id,
                order_protocol=order_protocol,
            )
        except BrokerError as exc:
            # OP7: hard broker failure → mark trade FAILED, release capital
            self._handle_placement_failure(
                trade_id, reservation_id, signal_id, exc
            )
            raise

        if not result.success:
            # Soft failure
            soft_err = BrokerError(
                f"Entry engine returned success=False: {result.rejection_reason}"
            )
            self._handle_placement_failure(
                trade_id, reservation_id, signal_id, soft_err
            )
            raise soft_err

        # ── Persist order rows ─────────────────────────────────────────────
        self._persist_entry_orders(trade_id, result, symbol, qty, side, intent)

        # ── Register for fill tracking (OP5) ───────────────────────────────
        entry_internal = result.entry_internal_id
        if entry_internal:
            fill_entry = _FillEntry(
                trade_id=trade_id,
                reservation_id=reservation_id,
                symbol=symbol,
                qty=qty,
                leg=_LEG_ENTRY,
                order_protocol=order_protocol,
                direction=direction,
            )
            with self._fill_map_lock:
                self._fill_map[entry_internal] = fill_entry

        self._log.info(
            "order_placer.place_complete",
            extra={
                "trade_id": trade_id,
                "entry_broker_id": result.entry_broker_order_id,
                "protocol": result.order_protocol,
            },
        )

    # ── event handler ─────────────────────────────────────────────────────────

    def _on_order_filled(self, event: OrderFilled) -> None:
        """
        Handle OrderFilled event (OP6). Commit capital, update trade status.

        Called from order_monitor's poll thread; must be thread-safe (OP8).
        """
        internal_id = event.internal_order_id
        with self._fill_map_lock:
            fill_entry = self._fill_map.pop(internal_id, None)

        if fill_entry is None:
            # Not our trade (could be from another component or already handled)
            return

        trade_id = fill_entry.trade_id
        reservation_id = fill_entry.reservation_id

        self._log.info(
            "order_placer.fill_received",
            extra={
                "trade_id": trade_id,
                "internal_order_id": internal_id,
                "broker_order_id": event.broker_order_id,
                "avg_fill_price": event.avg_fill_price,
                "filled_qty": event.filled_qty,
            },
        )

        # Commit capital reservation (reservation → used)
        try:
            self._fm.commit_to_used(
                reservation_id=reservation_id,
                actual_fill_price=event.avg_fill_price,
                actual_qty=event.filled_qty,
            )
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.error(
                "order_placer.commit_capital_failed",
                extra={"trade_id": trade_id, "reservation_id": reservation_id},
            )
            # Continue: trade is open, capital state may be wrong; reconciler will fix

        # Record fill in DB
        try:
            self._om.record_entry_fill(
                trade_id=trade_id,
                avg_fill_price=event.avg_fill_price,
                qty_filled=event.filled_qty,
                filled_at=event.filled_at or now_ist().isoformat(),
            )
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.error(
                "order_placer.record_fill_failed",
                extra={"trade_id": trade_id},
            )

    # ── helpers ───────────────────────────────────────────────────────────────

    def _round_to_tick(self, symbol: str, price: float) -> float:
        """
        IC8: Round price to the nearest valid tick for the instrument.

        Uses instrument_cache.tick_size(symbol) if cache is wired.
        Falls back to the original price if cache is None or symbol missing.
        """
        if self._instrument_cache is None:
            return price
        try:
            tick = self._instrument_cache.tick_size(symbol)
            if tick <= 0:
                return price
            import math as _math
            return round(_math.floor(price / tick) * tick, 10)
        except Exception:
            return price

    def _compute_tgt(self, side: str, entry_price: float, sl_price: float) -> float:
        """OP3: compute tgt_price using R:R ratio."""
        risk = abs(entry_price - sl_price)
        if side == "BUY":
            return entry_price + risk * self._rr_ratio
        else:
            return entry_price - risk * self._rr_ratio

    def _handle_placement_failure(
        self,
        trade_id: str,
        reservation_id: str,
        signal_id: str,
        exc: Exception,
    ) -> None:
        """OP7: mark trade FAILED, release capital reservation."""
        log_exception(self._log, exc)
        try:
            self._om.update_trade_status(trade_id, "FAILED")
        except Exception as db_exc:
            log_exception(self._log, db_exc)
        try:
            self._fm.release(reservation_id, f"placement_failed: {exc}")
        except Exception as cap_exc:
            log_exception(self._log, cap_exc)

    def _persist_entry_orders(
        self,
        trade_id: str,
        result: EntryResult,
        symbol: str,
        qty: int,
        side: str,
        intent: str,
    ) -> None:
        """Persist order rows to DB after successful placement."""
        exit_side = "SELL" if side == "BUY" else "BUY"

        # HIGH #7: resolve product code via injected resolver; fallback map if not injected
        if self._product_resolver is not None:
            try:
                product = self._product_resolver.resolve(intent)
            except Exception:
                product = "MIS" if intent == "INTRADAY" else "CNC"
        else:
            product = "MIS" if intent == "INTRADAY" else "CNC"
        co_variety = "co" if result.order_protocol == "CO_PLUS_TGT" else "regular"

        try:
            # ENTRY order
            if result.entry_broker_order_id:
                self._om.insert_order(
                    trade_id=trade_id,
                    broker_order_id=result.entry_broker_order_id,
                    leg="ENTRY",
                    transaction_type=side,
                    order_type="SL" if result.order_protocol == "CO_PLUS_TGT" else "LIMIT",
                    product=product,
                    variety=co_variety,
                    qty_requested=qty,
                    price=0.0,  # will be updated on fill
                    leg_index=0,
                )

            # SL order (LIMIT_TRIPLE only)
            if result.sl_broker_order_id:
                self._om.insert_order(
                    trade_id=trade_id,
                    broker_order_id=result.sl_broker_order_id,
                    leg="SL",
                    transaction_type=exit_side,
                    order_type="SL-M",
                    product=product,
                    variety="regular",
                    qty_requested=qty,
                    price=0.0,
                    leg_index=0,
                )

            # TGT order
            if result.tgt_broker_order_id:
                self._om.insert_order(
                    trade_id=trade_id,
                    broker_order_id=result.tgt_broker_order_id,
                    leg="TGT",
                    transaction_type=exit_side,
                    order_type="LIMIT",
                    product=product,
                    variety="regular",
                    qty_requested=qty,
                    price=0.0,  # will be updated on fill
                    leg_index=0,
                )
        except Exception as exc:
            # DB write failure is non-fatal for order placement;
            # reconciler will rebuild from broker state.
            log_exception(self._log, exc)
            self._log.error(
                "order_placer.persist_orders_failed",
                extra={"trade_id": trade_id},
            )
