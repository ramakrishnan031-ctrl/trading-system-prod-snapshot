"""
orders/full_entry_engine.py — Trading System v2

Purpose:
    Single-shot entry engine (G10). Routes execute() to either
    CoPlusTgtProtocol or LimitTripleProtocol based on the order_protocol
    argument. This is the v2 implementation; ScalingEntryEngine is deferred
    to v2.1.

Locked Design Decisions:
    FEE1 -- Concrete EntryEngine; delegates to a protocol object (G10).
    FEE2 -- Protocol selection: "CO_PLUS_TGT" → CoPlusTgtProtocol,
            "LIMIT_TRIPLE" → LimitTripleProtocol.
            Unknown protocol → ValueError at execute() time.
    FEE3 -- Both protocol objects injected at construction. Caller selects
            the protocol string per-call (allows per-strategy override).
    FEE4 -- execute() is a thin router. No business logic inside.
    FEE5 -- Layer 5 (orders/). Imports orders/entry_engine, orders/protocol_*.

What This Module Does NOT Do:
    - Does not compute prices (caller supplies entry/sl/tgt)
    - Does not create DB rows (order_placer's job)
    - Does not manage order lifecycle after placement
"""
from __future__ import annotations

import logging

from typing import Optional

from orders.cnc_gtt import CncGttPlacer
from orders.entry_engine import EntryEngine, EntryResult
from orders.order_protocol_co import CoPlusTgtProtocol
from orders.order_protocol_limit import ExitLegsResult, LimitTripleProtocol


# ─────────────────────────────────────────────────────────────────────────────
# FullEntryEngine
# ─────────────────────────────────────────────────────────────────────────────

class FullEntryEngine(EntryEngine):
    """
    Single-shot entry engine (G10 default). Routes to CO or LIMIT_TRIPLE.

    Usage::
        engine = FullEntryEngine(co_protocol=co, limit_protocol=limit,
                                 logger=log)
        result = engine.execute(
            symbol="RELIANCE", side="BUY", qty=10,
            entry_price=2500.0, sl_price=2450.0, tgt_price=2600.0,
            intent="INTRADAY", trade_id="trd_abc...",
            order_protocol="LIMIT_TRIPLE",
        )
    """

    def __init__(
        self,
        co_protocol: CoPlusTgtProtocol,
        limit_protocol: LimitTripleProtocol,
        logger: logging.Logger,
        default_protocol: str = "LIMIT_TRIPLE",
        cnc_gtt_placer: Optional[CncGttPlacer] = None,  # SLICE2.5-P1
    ) -> None:
        self._co = co_protocol
        self._limit = limit_protocol
        self._log = logger
        self._default_protocol = default_protocol
        # SLICE2.5-P1: places the OCO GTT for DELIVERY (CNC) trades. None → the CNC
        # gate is inert (no delivery; existing INTRADAY behaviour fully preserved).
        self._cnc_gtt = cnc_gtt_placer

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
        order_protocol: str = "",  # overrides default_protocol when provided
        entry_order_type: str = "LIMIT",  # SNR-V2: "MARKET" for the retest entry
    ) -> EntryResult:
        """
        Route to the appropriate protocol (FEE1–FEE4).

        Args:
            order_protocol: "CO_PLUS_TGT" | "LIMIT_TRIPLE" | "" (uses default)
        """
        protocol = order_protocol or self._default_protocol

        if protocol == "CO_PLUS_TGT":
            selected = self._co
        elif protocol == "LIMIT_TRIPLE":
            selected = self._limit
        else:
            raise ValueError(
                f"Unknown order_protocol {protocol!r}; "
                "expected 'CO_PLUS_TGT' or 'LIMIT_TRIPLE'"
            )

        self._log.info(
            "full_entry_engine.execute",
            extra={
                "trade_id": trade_id, "symbol": symbol, "side": side,
                "qty": qty, "entry_price": entry_price,
                "sl_price": sl_price, "tgt_price": tgt_price,
                "protocol": protocol,
            },
        )
        return selected.execute(
            symbol=symbol, side=side, qty=qty,
            entry_price=entry_price, sl_price=sl_price, tgt_price=tgt_price,
            intent=intent, trade_id=trade_id, tag=tag,
            entry_order_type=entry_order_type,
        )

    def place_deferred_exits(
        self,
        *,
        order_protocol: str,
        symbol: str,
        entry_side: str,
        qty: int,
        sl_price: float,
        tgt_price: float,
        intent: str,
        trade_id: str,
        tag: str = "",
        entry_fill: float = 0.0,  # actual entry fill -> LIMIT_TRIPLE placeability gate
    ) -> ExitLegsResult:
        """
        FIX-016: Place exit legs AFTER ENTRY fill (naked-short fix).

        Routes to protocol-specific place_exits():
          - LIMIT_TRIPLE: places both SL + TGT (two separate orders)
          - CO_PLUS_TGT: places TGT only (SL embedded in CO bracket)

        Raises:
            ValueError if order_protocol is not LIMIT_TRIPLE or CO_PLUS_TGT.
            BrokerError on broker-side failure (SL or TGT).
        """
        # SLICE2.5-P1: a DELIVERY (CNC) trade is protected by ONE broker-side OCO GTT
        # (survives EOD/overnight), NOT day-validity legs. This is the SINGLE
        # centralized gate — all order_placer call sites (full / partial / retry) flow
        # through here, so the MIS/CO path below is byte-for-byte unchanged. Gate is
        # inert when intent != DELIVERY or no GTT placer is wired.
        if intent == "DELIVERY" and self._cnc_gtt is not None:
            exit_side = "SELL" if entry_side == "BUY" else "BUY"
            res = self._cnc_gtt.place_for_fill(
                symbol=symbol, exit_side=exit_side, qty=qty,
                sl_price=sl_price, tgt_price=tgt_price, trade_id=trade_id, tag=tag,
            )
            self._log.info(
                "full_entry_engine.cnc_gtt_exit",
                extra={
                    "trade_id": trade_id, "symbol": symbol, "gtt_id": res.gtt_id,
                    "modified": res.modified, "qty": qty, "exit_side": exit_side,
                    "sl_trigger": res.sl_trigger, "tgt_trigger": res.tgt_trigger,
                },
            )
            return ExitLegsResult(
                sl_broker_order_id=f"gtt:{res.gtt_id}",
                sl_internal_id=f"gtt:{res.gtt_id}",
                sl_order_type="GTT",
                sl_trigger_price=res.sl_trigger,
                sl_price=res.sl_limit,
                tgt_broker_order_id=f"gtt:{res.gtt_id}",
                tgt_internal_id=f"gtt:{res.gtt_id}",
                tgt_price=res.tgt_trigger,
                tgt_placed=True,
                is_gtt=True,
                gtt_id=res.gtt_id,
            )

        if order_protocol == "LIMIT_TRIPLE":
            selected = self._limit
        elif order_protocol == "CO_PLUS_TGT":
            selected = self._co
        else:
            raise ValueError(
                f"place_deferred_exits only supports LIMIT_TRIPLE or CO_PLUS_TGT, "
                f"got {order_protocol!r}"
            )

        self._log.info(
            "full_entry_engine.place_deferred_exits",
            extra={
                "trade_id": trade_id, "symbol": symbol, "entry_side": entry_side,
                "qty": qty, "sl_price": sl_price, "tgt_price": tgt_price,
                "intent": intent, "protocol": order_protocol,
            },
        )

        # FIX-016: CO protocol doesn't need sl_price (embedded in bracket)
        if order_protocol == "CO_PLUS_TGT":
            return selected.place_exits(
                symbol=symbol, entry_side=entry_side, qty=qty,
                tgt_price=tgt_price,
                intent=intent, trade_id=trade_id, tag=tag,
            )
        else:  # LIMIT_TRIPLE
            return selected.place_exits(
                symbol=symbol, entry_side=entry_side, qty=qty,
                sl_price=sl_price, tgt_price=tgt_price,
                intent=intent, trade_id=trade_id, tag=tag,
                entry_fill=entry_fill,
            )

    def place_deferred_tgt_only(
        self,
        *,
        symbol: str,
        entry_side: str,
        qty: int,
        tgt_price: float,
        intent: str,
        trade_id: str,
        tag: str = "",
        entry_fill: float = 0.0,  # actual entry fill -> placeability gate
    ):
        """
        TGT retry (Task 2026-06-19): place ONLY the TGT LIMIT leg (LIMIT_TRIPLE)
        for a trade whose SL is already standing (FIX-190 Bug C SL-only). Routes
        to LimitTripleProtocol.place_tgt_only, which never raises and never
        touches the SL. Returns a TgtOnlyResult. CO_PLUS_TGT is out of scope —
        its TGT is placed alongside the CO bracket, not as a standalone retry.
        """
        return self._limit.place_tgt_only(
            symbol=symbol, entry_side=entry_side, qty=qty,
            tgt_price=tgt_price, intent=intent, trade_id=trade_id, tag=tag,
            entry_fill=entry_fill,
        )
