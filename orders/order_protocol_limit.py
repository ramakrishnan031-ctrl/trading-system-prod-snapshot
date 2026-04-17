"""
orders/order_protocol_limit.py — Trading System v2

Purpose:
    LIMIT_TRIPLE order protocol. Places three separate orders:
      1. ENTRY — LIMIT order at entry_price (BUY for LONG, SELL for SHORT)
      2. SL    — SL-M order at sl_price (exit side; triggered at sl_price,
                 executes as MARKET)
      3. TGT   — LIMIT order at tgt_price (exit side; limit at tgt_price)

    This is the P8/P13 fallback protocol used when Cover Orders are not
    available for a stock, or as the v2 default before CO is validated.

Locked Design Decisions:
    OPL1 -- Three separate broker orders: ENTRY LIMIT + SL SL-M + TGT LIMIT.
    OPL2 -- ENTRY side = signal side. SL/TGT side = opposite of signal side
            (always closing orders).
    OPL3 -- If ENTRY placement fails: raise immediately, do not place SL/TGT.
            If SL placement fails after ENTRY: attempt to cancel ENTRY, then
            raise. If TGT placement fails: SL still stands (partial success);
            raise BrokerError so caller can decide.
    OPL4 -- tag = trade_id passed to zerodha_adapter for all 3 orders.
    OPL5 -- Layer 5 (orders/). Imports broker/zerodha_adapter, orders/entry_engine.
    OPL6 -- All 3 PlacedOrder objects returned via EntryResult fields.
            order_protocol = "LIMIT_TRIPLE".

What This Module Does NOT Do:
    - Does not manage DB rows (order_placer's job)
    - Does not monitor fills (order_monitor's job)
    - Does not cancel/timeout orders (order_timeout's job)
"""
from __future__ import annotations

import logging
from typing import Optional

from broker.zerodha_adapter import ZerodhaAdapter
from core.exceptions import BrokerError, OrderRejectedError
from core.logger import log_exception
from orders.entry_engine import EntryEngine, EntryResult


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _exit_side(entry_side: str) -> str:
    """Return the closing side for a position."""
    return "SELL" if entry_side == "BUY" else "BUY"


# ─────────────────────────────────────────────────────────────────────────────
# LimitTripleProtocol
# ─────────────────────────────────────────────────────────────────────────────

class LimitTripleProtocol(EntryEngine):
    """
    LIMIT_TRIPLE entry protocol (P8/P13 fallback).

    Places: ENTRY LIMIT → SL SL-M → TGT LIMIT.
    """

    def __init__(
        self,
        adapter: ZerodhaAdapter,
        logger: logging.Logger,
    ) -> None:
        self._adapter = adapter
        self._log = logger

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
        Place ENTRY + SL + TGT orders. (OPL1–OPL6)

        Order of placement: ENTRY → SL → TGT.
        Failure semantics per OPL3.
        """
        order_tag = tag or trade_id
        exit_side = _exit_side(side)

        # ── Step 1: ENTRY LIMIT ────────────────────────────────────────────
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

        # ── Step 2: SL SL-M ───────────────────────────────────────────────
        sl_placed = None
        try:
            sl_placed = self._adapter.place_order(
                symbol=symbol,
                side=exit_side,
                qty=qty,
                price=0.0,          # SL-M executes at market
                order_type="SL-M",
                intent=intent,
                tag=order_tag,
                trigger_price=sl_price,
            )
        except BrokerError as exc:
            # OPL3: SL failed after ENTRY placed → cancel ENTRY best-effort
            self._log.error(
                "limit_triple.sl_failed_cancelling_entry",
                extra={
                    "trade_id": trade_id,
                    "entry_broker_id": entry_placed.broker_order_id,
                },
            )
            try:
                self._adapter.cancel_order(entry_placed.broker_order_id)
            except Exception as cancel_exc:
                log_exception(self._log, cancel_exc)
            raise

        # OP-LM3: guard SL result
        if not sl_placed.broker_order_id:
            try:
                self._adapter.cancel_order(entry_placed.broker_order_id)
            except Exception as cancel_exc:
                log_exception(self._log, cancel_exc)
            raise OrderRejectedError(
                "adapter returned empty broker_order_id for SL order",
                symbol=symbol, trade_id=trade_id, leg="SL",
            )

        self._log.info(
            "limit_triple.sl_placed",
            extra={
                "trade_id": trade_id, "symbol": symbol,
                "broker_order_id": sl_placed.broker_order_id,
                "trigger_price": sl_price,
            },
        )

        # ── Step 3: TGT LIMIT ─────────────────────────────────────────────
        tgt_placed = None
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
            # OPL3: TGT failed; SL is still standing; raise so caller handles
            self._log.error(
                "limit_triple.tgt_failed",
                extra={
                    "trade_id": trade_id,
                    "sl_still_standing": sl_placed.broker_order_id,
                },
            )
            log_exception(self._log, exc)
            raise

        # OP-LM3: guard TGT result
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
            },
        )

        return EntryResult(
            success=True,
            entry_broker_order_id=entry_placed.broker_order_id,
            sl_broker_order_id=sl_placed.broker_order_id,
            tgt_broker_order_id=tgt_placed.broker_order_id,
            entry_internal_id=entry_placed.internal_order_id,
            sl_internal_id=sl_placed.internal_order_id,
            tgt_internal_id=tgt_placed.internal_order_id,
            order_protocol="LIMIT_TRIPLE",
        )
