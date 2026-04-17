"""
orders/order_manager.py — Trading System v2

Purpose:
    Thin DB layer for the orders layer. Owns all INSERT/UPDATE SQL for
    the `trades` and `orders` tables. Uses state_store.transaction() for
    atomicity. Does NOT compute business logic — callers supply all values.

Locked Design Decisions:
    OMgr1 -- Owns all INSERT/UPDATE SQL for trades + orders tables.
             No business logic; callers supply every field value.
    OMgr2 -- create_trade(…) → trade_id (trd_<hex32>). Assigns trade_id
             via core.ids.new_trade_id(). Inserts with status=PENDING_FILL.
    OMgr3 -- insert_order(trade_id, leg, …) → None. broker_order_id is
             the PK for the orders table (schema v4). internal_order_id is
             NOT stored in DB (kept in-memory by order_placer for fill lookup).
    OMgr4 -- record_entry_fill(trade_id, avg_fill_price, qty_filled,
             filled_at) → None. Sets status=OPEN, entry_actual_price,
             entry_time, qty_filled in one transaction.
    OMgr5 -- update_trade_status(trade_id, status) → None. Only updates
             status + updated_at. No other columns touched.
    OMgr6 -- link_signal_trade(signal_id, trade_id) → None. Sets
             signals.trade_id = trade_id.
    OMgr7 -- get_trade(trade_id) → dict | None. Returns sqlite3.Row as dict.
    OMgr8 -- get_orders_for_trade(trade_id) → list[dict].
    OMgr9 -- Layer 5 (orders/). Imports: core/state_store, core/ids,
             core/time_authority, core/logger.

What This Module Does NOT Do:
    - Does not compute tgt_price, sl_price, margin, or risk amounts
    - Does not call fund_manager or any capital module
    - Does not emit events
    - Does not subscribe to events
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from core.ids import new_trade_id
from core.state_store import StateStore
from core.time_authority import now_ist


# ─────────────────────────────────────────────────────────────────────────────
# OrderManager
# ─────────────────────────────────────────────────────────────────────────────

class OrderManager:
    """
    Manages the DB lifecycle of trades and their orders (OMgr1–OMgr9).

    All public methods use state_store.transaction() for atomicity.
    Callers supply every value; this class does no arithmetic.
    """

    def __init__(self, state_store: StateStore, logger: logging.Logger) -> None:
        self._store = state_store
        self._log = logger

    # ── write methods ─────────────────────────────────────────────────────────

    def create_trade(
        self,
        *,
        signal_id: str,
        symbol: str,
        direction: str,         # "LONG" | "SHORT"
        strategy: str,
        sector: Optional[str],
        qty: int,
        entry_target_price: float,
        sl_initial: float,
        tgt_initial: float,
        order_protocol: str,    # "CO_PLUS_TGT" | "LIMIT_TRIPLE"
        margin_reserved: float,
        risk_amount: float,
    ) -> str:
        """
        Insert a new trade row with status=PENDING_FILL. Returns trade_id.

        OMgr2: trade_id is assigned here via new_trade_id().
        """
        trade_id = new_trade_id()
        now = now_ist().isoformat()
        with self._store.transaction() as cur:
            cur.execute(
                """
                INSERT INTO trades (
                    trade_id, signal_id, symbol, direction, strategy, sector,
                    qty_planned, qty_filled,
                    entry_target_price, entry_actual_price,
                    sl_initial, tgt_initial,
                    margin_reserved, risk_amount,
                    created_at, updated_at,
                    status, entry_mode, order_protocol, recovered_flag
                ) VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, 0,
                    ?, NULL,
                    ?, ?,
                    ?, ?,
                    ?, ?,
                    'PENDING_FILL', 'FULL', ?, 0
                )
                """,
                (
                    trade_id, signal_id, symbol, direction, strategy, sector,
                    qty,
                    entry_target_price,
                    sl_initial, tgt_initial,
                    margin_reserved, risk_amount,
                    now, now,
                    order_protocol,
                ),
            )
        self._log.info(
            "trade_created",
            extra={
                "trade_id": trade_id, "signal_id": signal_id,
                "symbol": symbol, "direction": direction,
            },
        )
        return trade_id

    def insert_order(
        self,
        *,
        trade_id: str,
        broker_order_id: str,   # PK in orders table
        leg: str,               # "ENTRY" | "SL" | "TGT" | "EOD" | "CANCEL"
        transaction_type: str,  # "BUY" | "SELL"
        order_type: str,        # "LIMIT" | "MARKET" | "SL-M" | "SL"
        product: str,           # broker product code e.g. "MIS"
        variety: str,           # "regular" | "co"
        qty_requested: int,
        price: float,
        trigger_price: float = 0.0,
        leg_index: int = 0,
    ) -> None:
        """
        Insert a new order row. broker_order_id is the PK (OMgr3).
        internal_order_id is NOT stored — kept in memory by order_placer.
        """
        now = now_ist().isoformat()
        with self._store.transaction() as cur:
            cur.execute(
                """
                INSERT INTO orders (
                    order_id, trade_id,
                    leg, leg_index,
                    transaction_type, order_type, product, variety,
                    qty_requested, price, trigger_price,
                    status, qty_filled, avg_fill_price,
                    placed_at, updated_at
                ) VALUES (
                    ?, ?,
                    ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    'PENDING', 0, NULL,
                    ?, ?
                )
                """,
                (
                    broker_order_id, trade_id,
                    leg, leg_index,
                    transaction_type, order_type, product, variety,
                    qty_requested,
                    price if price > 0 else None,
                    trigger_price if trigger_price > 0 else None,
                    now, now,
                ),
            )

    def record_entry_fill(
        self,
        *,
        trade_id: str,
        avg_fill_price: float,
        qty_filled: int,
        filled_at: str,         # ISO-8601 IST string
    ) -> None:
        """
        Record that the entry order filled. Sets status=OPEN. (OMgr4)
        """
        now = now_ist().isoformat()
        with self._store.transaction() as cur:
            cur.execute(
                """
                UPDATE trades
                SET status = 'OPEN',
                    entry_actual_price = ?,
                    entry_time = ?,
                    qty_filled = ?,
                    updated_at = ?
                WHERE trade_id = ?
                """,
                (avg_fill_price, filled_at, qty_filled, now, trade_id),
            )
        self._log.info(
            "entry_fill_recorded",
            extra={
                "trade_id": trade_id,
                "avg_fill_price": avg_fill_price,
                "qty_filled": qty_filled,
            },
        )

    def update_trade_status(self, trade_id: str, status: str) -> None:
        """Update trades.status + updated_at. (OMgr5)"""
        now = now_ist().isoformat()
        with self._store.transaction() as cur:
            cur.execute(
                "UPDATE trades SET status = ?, updated_at = ? WHERE trade_id = ?",
                (status, now, trade_id),
            )

    def link_signal_trade(self, signal_id: str, trade_id: str) -> None:
        """Set signals.trade_id for the given signal_id. (OMgr6)"""
        with self._store.transaction() as cur:
            cur.execute(
                "UPDATE signals SET trade_id = ? WHERE signal_id = ?",
                (trade_id, signal_id),
            )

    def update_order_status(
        self,
        broker_order_id: str,
        status: str,
        qty_filled: int = 0,
        avg_fill_price: Optional[float] = None,
    ) -> None:
        """Update an order row's status/fill info."""
        now = now_ist().isoformat()
        with self._store.transaction() as cur:
            cur.execute(
                """
                UPDATE orders
                SET status = ?,
                    qty_filled = ?,
                    avg_fill_price = ?,
                    updated_at = ?
                WHERE order_id = ?
                """,
                (status, qty_filled, avg_fill_price, now, broker_order_id),
            )

    # ── read methods ──────────────────────────────────────────────────────────

    def get_trade(self, trade_id: str) -> Optional[Dict]:
        """Return trades row as dict, or None if not found. (OMgr7)"""
        row = self._store.fetch_one(
            "SELECT * FROM trades WHERE trade_id = ?", (trade_id,)
        )
        return dict(row) if row else None

    def get_orders_for_trade(self, trade_id: str) -> List[Dict]:
        """Return all orders rows for a trade as list of dicts. (OMgr8)"""
        rows = self._store.fetch_all(
            "SELECT * FROM orders WHERE trade_id = ? ORDER BY placed_at",
            (trade_id,),
        )
        return [dict(r) for r in rows]
