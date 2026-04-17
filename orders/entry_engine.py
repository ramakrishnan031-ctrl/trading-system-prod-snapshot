"""
orders/entry_engine.py — Trading System v2

Purpose:
    Abstract base class for entry placement strategies (G10).
    Defines the interface that FullEntryEngine (and future ScalingEntryEngine)
    must implement. Callers depend only on this interface, not the concrete
    implementation.

Locked Design Decisions:
    EE1  -- Abstract base; no imports from this codebase (G10).
    EE2  -- EntryResult dataclass: success flag + three broker order IDs
            (entry, sl, tgt). sl_broker_order_id is empty-string for CO
            protocol (SL is handled by broker bracket). tgt is empty if
            TGT was not placed.
    EE3  -- execute(**kwargs) raises on hard failures (BrokerError subclass).
            Returns EntryResult(success=False) only for soft rejections
            (e.g. invalid params that don't warrant an exception).
    EE4  -- Protocol-agnostic interface: callers don't know whether CO or
            LIMIT_TRIPLE is used underneath.
    EE5  -- Layer 5 (orders/). This file: no imports from this codebase.

What This Module Does NOT Do:
    - Does not place orders (concrete subclasses do that)
    - Does not import broker or core modules (no deps to avoid cycles)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


# ─────────────────────────────────────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EntryResult:
    """
    Outcome of EntryEngine.execute().

    Attributes:
        success:               True if all required orders were placed.
        entry_broker_order_id: Broker ID for the entry order (or CO order).
        sl_broker_order_id:    Broker ID for the SL order.
                               Empty string for CO_PLUS_TGT (SL lives inside
                               the CO bracket at the broker level).
        tgt_broker_order_id:   Broker ID for the TGT order.
                               Empty string if no TGT was placed.
        entry_internal_id:     Internal ord_ ID for the entry order (used by
                               order_monitor for fill tracking).
        sl_internal_id:        Internal ord_ ID for SL order (empty for CO).
        tgt_internal_id:       Internal ord_ ID for TGT order.
        order_protocol:        "CO_PLUS_TGT" | "LIMIT_TRIPLE"
        rejection_reason:      Non-empty string on soft failure.
    """
    success: bool
    entry_broker_order_id: str = ""
    sl_broker_order_id: str = ""
    tgt_broker_order_id: str = ""
    entry_internal_id: str = ""
    sl_internal_id: str = ""
    tgt_internal_id: str = ""
    order_protocol: str = ""
    rejection_reason: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Abstract base
# ─────────────────────────────────────────────────────────────────────────────

class EntryEngine(ABC):
    """
    Abstract base for entry placement (G10).

    Subclasses implement execute() using a specific order protocol.
    """

    @abstractmethod
    def execute(
        self,
        *,
        symbol: str,
        side: str,          # "BUY" | "SELL"
        qty: int,
        entry_price: float,
        sl_price: float,
        tgt_price: float,
        intent: str,        # "INTRADAY" | "DELIVERY" | etc.
        trade_id: str,      # trd_<hex32> — used as order tag for traceability
        tag: str = "",
    ) -> EntryResult:
        """
        Place all entry-side orders for a trade.

        Args:
            symbol:       NSE trading symbol
            side:         "BUY" for LONG entry, "SELL" for SHORT entry
            qty:          Number of shares
            entry_price:  Target entry limit price
            sl_price:     Stop-loss trigger price
            tgt_price:    Take-profit limit price
            intent:       Semantic product intent (e.g. "INTRADAY")
            trade_id:     Internal trade identifier (passed as order tag)
            tag:          Extra tag string (optional)

        Returns:
            EntryResult — check .success before proceeding.

        Raises:
            BrokerError subclass on hard broker failure (network, auth, etc.)
        """
        ...
