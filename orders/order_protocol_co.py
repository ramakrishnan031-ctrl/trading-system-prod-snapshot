"""
orders/order_protocol_co.py — Trading System v2

Purpose:
    CO_PLUS_TGT order protocol. Places two broker orders:
      1. CO (Cover Order) — variety="co", order_type="SL". This is the entry
         order; the broker automatically maintains the SL bracket. The entry
         triggers when the SL trigger is not hit, i.e., price moves in the
         intended direction. The CO trigger_price = sl_price (built-in SL).
      2. TGT LIMIT — separate LIMIT order on the closing side at tgt_price.

    Per P8/P13: CO is the default protocol for intraday strategies.
    CO eliminates the entry-to-SL window race condition atomically.

    Kiteconnect CO mechanics:
      - variety="co"
      - order_type="SL" (the CO entry IS a stop-loss bracket order)
      - For a LONG entry (BUY CO): price=entry_price, trigger_price=sl_price
        The broker places a buy limit at entry_price with a built-in SL at
        sl_price. If price drops to sl_price before filling, the CO exits.
      - The SL bracket is managed entirely by Zerodha; we do NOT place a
        separate SL order.
      - TGT must be a separate LIMIT SELL order (INTRADAY/MIS).

Locked Design Decisions:
    OPC1 -- Two broker orders: CO entry + separate LIMIT TGT (P8/P13 default).
    OPC2 -- CO uses variety="co", order_type="SL", trigger_price=sl_price.
            Entry side = signal side. SL is embedded in CO (no separate SL order).
    OPC3 -- TGT side = opposite of signal side, order_type="LIMIT".
    OPC4 -- If CO placement fails: raise immediately, do not place TGT.
            If TGT fails after CO: log ERROR (position at risk, no TGT);
            return success=False surfacing the CO broker_order_id via
            entry_broker_order_id so the caller (OrderPlacer, BL-8) can
            cancel the live CO before marking the trade FAILED. Reconciler
            is the backstop, not the primary recovery path.
    OPC5 -- sl_broker_order_id="" in EntryResult (SL is inside CO bracket).
    OPC6 -- Layer 5 (orders/). Imports broker/zerodha_adapter, orders/entry_engine.
    OPC7 -- order_protocol = "CO_PLUS_TGT".

What This Module Does NOT Do:
    - Does not manage DB rows (order_placer's job)
    - Does not modify the CO SL after placement (smart_tgt_manager's job)
    - Does not cancel/exit positions (other modules handle that)
"""
from __future__ import annotations

import logging

from broker.zerodha_adapter import ZerodhaAdapter
from core.exceptions import BrokerError, OrderRejectedError
from core.ids import truncate_tag_for_broker
from core.logger import log_exception
from orders.entry_engine import EntryEngine, EntryResult


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _exit_side(entry_side: str) -> str:
    return "SELL" if entry_side == "BUY" else "BUY"


# ─────────────────────────────────────────────────────────────────────────────
# CoPlusTgtProtocol
# ─────────────────────────────────────────────────────────────────────────────

class CoPlusTgtProtocol(EntryEngine):
    """
    CO_PLUS_TGT entry protocol (P8/P13 default).

    FIX-016: Two-phase placement to prevent naked short on early TGT fill.
      Phase 1: execute() places CO entry only (with built-in SL bracket).
      Phase 2: place_exits() places separate LIMIT TGT after entry fills.
    """

    def __init__(
        self,
        adapter: ZerodhaAdapter,
        logger: logging.Logger,
    ) -> None:
        self._adapter = adapter
        self._log = logger

    # ── Phase 1: CO ENTRY ONLY ────────────────────────────────────────────────

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
        FIX-016 Phase 1: Place CO entry only. TGT deferred to fill event.

        CO entry order has built-in SL bracket (trigger_price=sl_price).
        TGT is NOT placed here — caller must use place_exits() after entry fills.
        """
        # FIX-093: Truncate tag to 16 chars for Kite API compliance
        order_tag = truncate_tag_for_broker(tag or trade_id)
        self._log.debug(
            "co_plus_tgt.order_tag_truncated",
            extra={"trade_id": trade_id, "order_tag": order_tag},
        )

        # ── CO entry order (with built-in SL bracket) ──────────────────────
        try:
            co_placed = self._adapter.place_order(
                symbol=symbol,
                side=side,
                qty=qty,
                price=entry_price,
                order_type="SL",            # CO uses SL order_type
                intent=intent,
                tag=order_tag,
                trigger_price=sl_price,     # built-in SL bracket
                variety="co",               # ZA17: Cover Order variety
            )
        except BrokerError:
            raise  # OPC4: CO failure → propagate immediately

        # OP-LM3: empty broker_order_id from CO is a silent broker failure
        if not co_placed.broker_order_id:
            raise OrderRejectedError(
                "adapter returned empty broker_order_id for CO order",
                symbol=symbol, trade_id=trade_id, leg="CO",
            )

        self._log.info(
            "co_plus_tgt.co_placed phase1_only",
            extra={
                "trade_id": trade_id, "symbol": symbol,
                "broker_order_id": co_placed.broker_order_id,
                "entry_price": entry_price,
                "sl_price": sl_price,
                "tgt_deferred": True,  # FIX-016 marker
            },
        )

        # FIX-016: Return success with empty TGT fields (will be populated in phase 2)
        return EntryResult(
            success=True,
            entry_broker_order_id=co_placed.broker_order_id,
            sl_broker_order_id="",             # OPC5: SL inside CO bracket
            tgt_broker_order_id="",            # FIX-016: TGT not placed yet
            entry_internal_id=co_placed.internal_order_id,
            sl_internal_id="",
            tgt_internal_id="",                # FIX-016: will be set in phase 2
            order_protocol="CO_PLUS_TGT",
        )

    # ── Phase 2: TGT ON ENTRY FILL ───────────────────────────────────────────

    def place_exits(
        self,
        *,
        symbol: str,
        entry_side: str,
        qty: int,
        tgt_price: float,
        intent: str,
        trade_id: str,
        tag: str = "",
    ) -> "ExitLegsResult":
        """
        FIX-016 Phase 2: Place TGT LIMIT after CO entry fills.

        CO protocol has built-in SL (no separate SL order), so this only
        places the TGT leg. Called by OrderPlacer on CO entry fill event.

        Returns:
            ExitLegsResult with tgt_broker_order_id, tgt_order_type="LIMIT".
            sl_broker_order_id="" (SL embedded in CO bracket).

        Raises:
            BrokerError if TGT placement fails.
        """
        from orders.full_entry_engine import ExitLegsResult

        # FIX-093: Truncate tag to 16 chars for Kite API compliance
        order_tag = truncate_tag_for_broker(tag or trade_id)
        exit_side = _exit_side(entry_side)

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
            log_exception(self._log, exc)
            self._log.critical(
                "co_plus_tgt.tgt_failed_phase2 CO_TGT_PLACEMENT_FAILED",
                extra={
                    "trade_id": trade_id,
                    "symbol": symbol,
                    "tgt_price": tgt_price,
                    "error": str(exc),
                },
            )
            raise  # Propagate to OrderPlacer for hard_kill (position unprotected)

        # OP-LM3: empty tgt broker_order_id is a silent failure
        if not tgt_placed.broker_order_id:
            self._log.critical(
                "co_plus_tgt.tgt_empty_broker_id_phase2 CO_TGT_PLACEMENT_FAILED",
                extra={
                    "trade_id": trade_id,
                    "symbol": symbol,
                    "failure_details": "TGT returned empty broker_order_id",
                },
            )
            raise OrderRejectedError(
                "TGT leg returned empty broker_order_id",
                symbol=symbol, trade_id=trade_id, leg="TGT",
            )

        self._log.info(
            "co_plus_tgt.tgt_placed phase2_complete",
            extra={
                "trade_id": trade_id, "symbol": symbol,
                "broker_order_id": tgt_placed.broker_order_id,
                "tgt_price": tgt_price,
            },
        )

        return ExitLegsResult(
            sl_broker_order_id="",             # SL inside CO bracket
            sl_internal_id="",
            sl_order_type="",
            sl_trigger_price=0.0,
            sl_price=0.0,
            tgt_broker_order_id=tgt_placed.broker_order_id,
            tgt_internal_id=tgt_placed.internal_order_id,
            tgt_price=tgt_price,
        )
