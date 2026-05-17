"""
orders/order_protocol_limit.py — Trading System v2

Purpose:
    LIMIT_TRIPLE order protocol. Two-phase placement:
      Phase 1 (execute):   ENTRY LIMIT at entry_price.
      Phase 2 (place_exits): SL + TGT on the exit side, sized to the ACTUAL
                             filled qty. Called by OrderPlacer on ENTRY fill.

    This is the P8/P13 fallback protocol used when Cover Orders are not
    available for a stock, or as the v2 default before CO is validated.

Naked-Short Fix (2026-04-24 / Audit finding 2.1):
    Pre-fix the protocol placed ENTRY + SL + TGT in sequence at FULL qty
    without waiting for ENTRY fill. If ENTRY partial-filled (e.g. 100/1000)
    and price spiked through TGT, TGT executed at qty=1000 → naked short
    of 900. Post-fix the protocol places ENTRY only; exits are placed by
    OrderPlacer on ENTRY fill using event.filled_qty. Partial fills are
    safe because SL/TGT are sized to the filled qty.

DELIVERY Protocol Mismatch Fix (2026-04-24 / Audit finding 3.4):
    Zerodha rejects overnight SL-M orders for CNC products. place_exits
    branches on intent: DELIVERY → SL (explicit price=trigger_price).
    INTRADAY → SL-M (trigger_price only, executes as MARKET).

Locked Design Decisions:
    OPL1 -- Two-phase placement. execute() = ENTRY. place_exits() = SL+TGT.
    OPL2 -- ENTRY side = signal side. SL/TGT side = opposite of signal side
            (always closing orders).
    OPL3 -- execute() failure: raise immediately; no cleanup needed (nothing
            else placed). place_exits() failure: SL-first, so if SL fails
            NO TGT is attempted — caller must escalate (position is live
            with no SL). If TGT fails after SL: SL still stands; raise.
    OPL4 -- tag = trade_id passed to zerodha_adapter for all orders.
    OPL5 -- Layer 5 (orders/). Imports broker/zerodha_adapter, orders/entry_engine.
    OPL6 -- order_protocol = "LIMIT_TRIPLE" on the EntryResult.
    OPL7 -- DELIVERY intent uses order_type="SL" with price = trigger_price
            (no buffer; tight fill). INTRADAY uses order_type="SL-M".
            Rationale: SL-M is not valid for CNC/delivery at Zerodha.

What This Module Does NOT Do:
    - Does not manage DB rows (order_placer's job)
    - Does not monitor fills (order_monitor's job)
    - Does not cancel/timeout orders (order_timeout's job)
    - Does not subscribe to events (OrderPlacer owns the fill→exits flow)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from broker.zerodha_adapter import ZerodhaAdapter
from core.exceptions import BrokerError, OrderRejectedError
from core.ids import truncate_tag_for_broker
from core.logger import log_exception
from orders.entry_engine import EntryEngine, EntryResult


# ─────────────────────────────────────────────────────────────────────────────
# ExitLegsResult — return type for place_exits (Phase 2)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ExitLegsResult:
    """
    Outcome of LimitTripleProtocol.place_exits().

    Both legs always succeed together on success. On failure the protocol
    raises; it does not return a partial ExitLegsResult.
    """
    sl_broker_order_id: str
    sl_internal_id: str
    sl_order_type: str          # "SL-M" for INTRADAY; "SL" for DELIVERY
    sl_trigger_price: float
    sl_price: float             # 0.0 for SL-M; = trigger_price for SL
    tgt_broker_order_id: str
    tgt_internal_id: str
    tgt_price: float


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _exit_side(entry_side: str) -> str:
    """Return the closing side for a position."""
    return "SELL" if entry_side == "BUY" else "BUY"


def _is_delivery(intent: str) -> bool:
    """OPL7: branch on intent for SL order type."""
    return (intent or "").upper() == "DELIVERY"


# ─────────────────────────────────────────────────────────────────────────────
# LimitTripleProtocol
# ─────────────────────────────────────────────────────────────────────────────

class LimitTripleProtocol(EntryEngine):
    """
    LIMIT_TRIPLE entry protocol (P8/P13 fallback).

    Two-phase:
      Phase 1: execute() places ENTRY LIMIT only.
      Phase 2: place_exits() places SL + TGT on ENTRY fill.
    """

    def __init__(
        self,
        adapter: ZerodhaAdapter,
        logger: logging.Logger,
    ) -> None:
        self._adapter = adapter
        self._log = logger

    # ── Phase 1: ENTRY ────────────────────────────────────────────────────────

    def execute(
        self,
        *,
        symbol: str,
        side: str,
        qty: int,
        entry_price: float,
        sl_price: float,
        tgt_price: float,
        intent: str,
        trade_id: str,
        tag: str = "",
    ) -> EntryResult:
        """
        Phase 1: place ENTRY LIMIT only (OPL1/OPL2/OPL6).

        SL and TGT are deferred to place_exits(), called by OrderPlacer on
        ENTRY fill. The EntryResult has sl_* / tgt_* fields empty by design;
        see module docstring for the naked-short fix rationale.

        `sl_price` and `tgt_price` are accepted here for signature symmetry
        with CoPlusTgtProtocol but are NOT used — the caller must forward
        them to place_exits() at fill time.
        """
        # FIX-093: Truncate tag to 16 chars for Kite API compliance
        order_tag = truncate_tag_for_broker(tag or trade_id)
        self._log.debug(
            "limit_triple.order_tag_truncated",
            extra={"trade_id": trade_id, "order_tag": order_tag},
        )

        try:
            entry_placed = self._adapter.place_order(
                symbol=symbol,
                side=side,
                qty=qty,
                price=entry_price,
                order_type="LIMIT",
                intent=intent,
                tag=order_tag,
            )
        except BrokerError:
            raise  # OPL3: entry failure → propagate immediately

        # OP-LM3: empty broker_order_id is a silent broker failure
        if not entry_placed.broker_order_id:
            raise OrderRejectedError(
                "adapter returned empty broker_order_id for ENTRY order",
                symbol=symbol, trade_id=trade_id, leg="ENTRY",
            )

        self._log.info(
            "limit_triple.entry_placed",
            extra={
                "trade_id": trade_id, "symbol": symbol,
                "broker_order_id": entry_placed.broker_order_id,
                "price": entry_price,
            },
        )

        return EntryResult(
            success=True,
            entry_broker_order_id=entry_placed.broker_order_id,
            sl_broker_order_id="",    # deferred to place_exits()
            tgt_broker_order_id="",   # deferred to place_exits()
            entry_internal_id=entry_placed.internal_order_id,
            sl_internal_id="",
            tgt_internal_id="",
            order_protocol="LIMIT_TRIPLE",
        )

    # ── Phase 2: SL + TGT ─────────────────────────────────────────────────────

    def place_exits(
        self,
        *,
        symbol: str,
        entry_side: str,        # ENTRY side; SL/TGT placed on the opposite side
        qty: int,               # the filled qty — sized to ACTUAL fill, not requested
        sl_price: float,
        tgt_price: float,
        intent: str,
        trade_id: str,
        tag: str = "",
    ) -> ExitLegsResult:
        """
        Phase 2: place SL + TGT on exit side at the ACTUAL filled qty (OPL1/OPL7).

        Called by OrderPlacer on ENTRY OrderFilled. SL-first order matters:
        if SL fails we do NOT attempt TGT (better to raise with no TGT than
        leave a naked TGT that could execute into a missing SL bracket).

        Args:
            qty: the actual filled qty from the OrderFilled event. Sizing
                 SL/TGT to qty ≤ entry_qty is the naked-short fix (2.1).

        Raises:
            BrokerError on any leg failure. Caller is responsible for
            escalating (position is live with no protection if SL failed;
            position has only SL if TGT failed).
        """
        # FIX-093: Truncate tag to 16 chars for Kite API compliance
        order_tag = truncate_tag_for_broker(tag or trade_id)
        exit_side = _exit_side(entry_side)
        is_delivery = _is_delivery(intent)

        # ── Step 1: SL (SL-M for INTRADAY / SL for DELIVERY) ───────────────
        if is_delivery:
            # OPL7: DELIVERY cannot use SL-M at Zerodha (CNC rejects SL-M).
            # Use SL with price = trigger_price (tight limit).
            sl_order_type = "SL"
            sl_limit_price = sl_price
        else:
            sl_order_type = "SL-M"
            sl_limit_price = 0.0  # SL-M ignores price

        try:
            sl_placed = self._adapter.place_order(
                symbol=symbol,
                side=exit_side,
                qty=qty,
                price=sl_limit_price,
                order_type=sl_order_type,
                intent=intent,
                tag=order_tag,
                trigger_price=sl_price,
            )
        except BrokerError as exc:
            # OPL3: SL failed. Caller must escalate — position is live, unprotected.
            self._log.critical(
                "limit_triple.sl_failed_position_unprotected",
                extra={
                    "trade_id": trade_id,
                    "symbol": symbol,
                    "qty": qty,
                    "sl_price": sl_price,
                    "sl_order_type": sl_order_type,
                    "intent": intent,
                    "detail": "ENTRY already filled; SL placement failed; "
                              "TGT not attempted; caller must hard_kill and cancel any live position",
                },
            )
            log_exception(self._log, exc)
            raise

        if not sl_placed.broker_order_id:
            raise OrderRejectedError(
                "adapter returned empty broker_order_id for SL order",
                symbol=symbol, trade_id=trade_id, leg="SL",
            )

        self._log.info(
            "limit_triple.sl_placed",
            extra={
                "trade_id": trade_id, "symbol": symbol,
                "broker_order_id": sl_placed.broker_order_id,
                "order_type": sl_order_type,
                "trigger_price": sl_price,
                "qty": qty,
                "intent": intent,
            },
        )

        # ── Step 2: TGT LIMIT ─────────────────────────────────────────────
        try:
            tgt_placed = self._adapter.place_order(
                symbol=symbol,
                side=exit_side,
                qty=qty,
                price=tgt_price,
                order_type="LIMIT",
                intent=intent,
                tag=order_tag,
            )
        except BrokerError as exc:
            # OPL3: TGT failed; SL still stands. Raise so caller can surface.
            self._log.error(
                "limit_triple.tgt_failed",
                extra={
                    "trade_id": trade_id,
                    "symbol": symbol,
                    "qty": qty,
                    "sl_still_standing": sl_placed.broker_order_id,
                },
            )
            log_exception(self._log, exc)
            raise

        if not tgt_placed.broker_order_id:
            raise OrderRejectedError(
                "adapter returned empty broker_order_id for TGT order",
                symbol=symbol, trade_id=trade_id, leg="TGT",
            )

        self._log.info(
            "limit_triple.tgt_placed",
            extra={
                "trade_id": trade_id, "symbol": symbol,
                "broker_order_id": tgt_placed.broker_order_id,
                "price": tgt_price,
                "qty": qty,
            },
        )

        return ExitLegsResult(
            sl_broker_order_id=sl_placed.broker_order_id,
            sl_internal_id=sl_placed.internal_order_id,
            sl_order_type=sl_order_type,
            sl_trigger_price=sl_price,
            sl_price=sl_limit_price,
            tgt_broker_order_id=tgt_placed.broker_order_id,
            tgt_internal_id=tgt_placed.internal_order_id,
            tgt_price=tgt_price,
        )
