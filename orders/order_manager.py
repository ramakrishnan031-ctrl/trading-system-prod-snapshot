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
    OMgr9  -- Layer 5 (orders/). Imports: core/state_store, core/ids,
              core/time_authority, core/logger.
    OMgr10 -- Optional EventBus subscription (BL-12). When constructed with
              bus=<EventBus>, subscribes to OrderStatusChanged and persists
              the broker-reported snapshot via update_order_status(). Pass
              bus=None for standalone instances (e.g. inside reconciler)
              that should not react to events.
    OMgr11 -- insert_orders_atomic(trade_id, specs) (BL-8). Wraps an entire
              entry-sequence (ENTRY + SL + TGT) in ONE state_store.transaction.
              Either every row commits or none do. Used by order_placer to
              close the silent-DB-failure window where partial persistence
              left the broker and the DB inconsistent. Per
              StateStore.transaction docstring, nested transactions are NOT
              supported -- callers must not wrap this method in their own
              transaction block.

What This Module Does NOT Do:
    - Does not compute tgt_price, sl_price, margin, or risk amounts
    - Does not call fund_manager or any capital module
    - Does not emit events
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Final, List, Optional, Sequence

from core.events import EventBus, OrderStatusChanged
from core.ids import new_trade_id
from core.state_store import StateStore
from core.time_authority import now_ist


# ─────────────────────────────────────────────────────────────────────────────
# OrderInsertSpec (BL-8 / OMgr11)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class OrderInsertSpec:
    """
    One row spec for OrderManager.insert_orders_atomic (BL-8).

    Mirrors the kwargs of insert_order; bundled so the caller can hand
    the atomic-batch method a single list whose semantics are obvious.
    """
    broker_order_id: str
    leg: str               # "ENTRY" | "SL" | "TGT" | "EOD" | "CANCEL"
    transaction_type: str  # "BUY" | "SELL"
    order_type: str        # "LIMIT" | "MARKET" | "SL-M" | "SL"
    product: str           # broker product code e.g. "MIS"
    variety: str           # "regular" | "co"
    qty_requested: int
    price: float = 0.0
    trigger_price: float = 0.0
    leg_index: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# exit_reason taxonomy (BL-10a)
#
# Written to trades.exit_reason by close_trade(). Shadow_tracker and the EOD
# reports consume this field; unknown values collapse to "EOD" in
# shadow_tracker's reason_map. The frozenset below is the canonical set
# any new caller must use.
# ─────────────────────────────────────────────────────────────────────────────

_VALID_EXIT_REASONS: Final[frozenset[str]] = frozenset({
    "TGT_HIT", "SL_HIT", "MANUAL_CLOSE", "EOD_SQUAREOFF",
})


# ─────────────────────────────────────────────────────────────────────────────
# OrderManager
# ─────────────────────────────────────────────────────────────────────────────

class OrderManager:
    """
    Manages the DB lifecycle of trades and their orders (OMgr1–OMgr9).

    All public methods use state_store.transaction() for atomicity.
    Callers supply every value; this class does no arithmetic.
    """

    def __init__(
        self,
        state_store: StateStore,
        logger: logging.Logger,
        bus: Optional[EventBus] = None,
    ) -> None:
        """
        Args:
            state_store: StateStore for DB access.
            logger:      Module logger.
            bus:         If provided, subscribes to OrderStatusChanged and
                         updates the orders table on every broker-reported
                         status change (OMgr10 / BL-12). Pass None for
                         standalone instances (e.g. inside reconciler) that
                         should not react to events.
        """
        self._store = state_store
        self._log = logger
        self._bus = bus
        if bus is not None:
            bus.subscribe(OrderStatusChanged, self._on_order_status_changed)

    # ── event handlers (OMgr10 / BL-12) ───────────────────────────────────────

    def _on_order_status_changed(self, event: OrderStatusChanged) -> None:
        """
        Persist the broker-reported status snapshot to the orders table.
        Swallows exceptions — a DB write failure must not crash order_monitor
        (EventBus propagates subscriber exceptions back to the publisher).
        """
        try:
            filled_at = now_ist().isoformat() if event.status == "COMPLETE" else None
            self.update_order_status(
                broker_order_id=event.broker_order_id,
                status=event.status,
                qty_filled=event.qty_filled,
                avg_fill_price=event.avg_fill_price,
                rejection_reason=getattr(event, "rejection_reason", None),
                filled_at=filled_at,
            )
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "order_manager.on_order_status_changed_failed",
                extra={"internal_order_id": event.internal_order_id,
                       "broker_order_id": event.broker_order_id,
                       "status": event.status,
                       "error": str(exc)},
            )

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
        reservation_id: Optional[str] = None,  # EF-5
        mode: Optional[str] = None,  # v14: PAPER | LIVE
        tolerance_fraction_used: Optional[float] = None,  # Phase 3a: resolved sl_fraction
        tolerance_source: Optional[str] = None,           # Phase 3a: which override rule won
        sizing_breakdown: Optional[dict] = None,          # Diary #4: PositionSizer.breakdown (sizing audit)
        tgt_risk_reward_applied: Optional[float] = None,  # Slice 1: strategy R:R frozen at placement
    ) -> str:
        """
        Insert a new trade row with status=PENDING_FILL. Returns trade_id.

        OMgr2: trade_id is assigned here via new_trade_id().
        EF-5: reservation_id is the fm_ledger reservation that funded this
        trade. Nullable for callers predating the column (recovered trades
        via reconciler). Populated by order_placer from the signal pipeline.
        Phase 3a: tolerance_fraction_used / tolerance_source record which entry-
        slippage override rule applied (Symbol > Strategy > Band > Global); both
        nullable (NULL for non-sl_fraction modes / recovered trades).
        """
        trade_id = new_trade_id()
        now = now_ist().isoformat()
        bd = sizing_breakdown or {}  # Diary #4: sizing-audit fields (NULL if absent)
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
                    status, entry_mode, order_protocol, recovered_flag,
                    reservation_id, mode,
                    tolerance_fraction_used, tolerance_source,
                    tier_multiplier_mode, tier_weight_applied, perf_weight_applied,
                    flat_value_rs_used, qty_by_risk, qty_by_capital,
                    qty_by_concentration, qty_by_flat, binding_constraint,
                    actual_position_value_rs,
                    tgt_risk_reward_applied
                ) VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, 0,
                    ?, NULL,
                    ?, ?,
                    ?, ?,
                    ?, ?,
                    'PENDING_FILL', 'FULL', ?, 0,
                    ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?,
                    ?
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
                    reservation_id, mode,
                    tolerance_fraction_used, tolerance_source,
                    bd.get("tier_multiplier_mode"), bd.get("tier_weight_applied"), bd.get("perf_weight_applied"),
                    bd.get("flat_value_rs_used"), bd.get("qty_by_risk"), bd.get("qty_by_capital"),
                    bd.get("qty_by_concentration"), bd.get("qty_by_flat"), bd.get("binding_constraint"),
                    bd.get("actual_position_value_rs"),
                    tgt_risk_reward_applied,
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

    def record_exits_verification(
        self, trade_id: str, verified: int, detail: str
    ) -> None:
        """Slice 1 Part B: persist the SL/TGT placement after-check verdict.

        verified = 1 (SL+TGT landed ok) | 0 (mismatch); detail = 'ok' or a
        human-readable description of what mismatched. Unlike the write-only v34
        sizing-audit columns, these are READ-BACK by the after-check and are
        available for audit/reports. Best-effort UPDATE (the fill path must not
        break if this write fails)."""
        with self._store.transaction() as cur:
            cur.execute(
                "UPDATE trades SET exits_verified = ?, exits_verify_detail = ?, "
                "updated_at = ? WHERE trade_id = ?",
                (int(verified), detail, now_ist().isoformat(), trade_id),
            )

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

    def insert_orders_atomic(
        self,
        trade_id: str,
        order_specs: Sequence[OrderInsertSpec],
    ) -> None:
        """
        BL-8 / OMgr11: atomically insert all order rows for an entry sequence.

        Either every row commits or none do. If any INSERT raises, the
        transaction rolls back and the exception propagates so the caller
        (order_placer) can cancel the corresponding broker orders.

        Use insert_order() for single-row inserts on non-entry-sequence paths.
        This method is specifically for the entry-sequence case where partial
        persistence leaves the broker and the DB inconsistent.

        StateStore.transaction does NOT support nesting -- callers MUST NOT
        wrap this method in their own transaction block.
        """
        if not order_specs:
            return
        now = now_ist().isoformat()
        with self._store.transaction() as cur:
            for spec in order_specs:
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
                        spec.broker_order_id, trade_id,
                        spec.leg, spec.leg_index,
                        spec.transaction_type, spec.order_type,
                        spec.product, spec.variety,
                        spec.qty_requested,
                        spec.price if spec.price > 0 else None,
                        spec.trigger_price if spec.trigger_price > 0 else None,
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

        FIX-130 (Item 5): also computes and stores signal-to-fill latency metrics
        (signal_to_order_ms, order_to_fill_ms, total_latency_ms) best-effort.
        Any failure is logged at DEBUG level and silently skipped.
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

        # FIX-130 (Item 5): compute latency metrics best-effort (no schema knowledge in OMgr)
        self._compute_and_store_latency(trade_id, filled_at)

    def _compute_and_store_latency(self, trade_id: str, filled_at: str) -> None:
        """
        FIX-130 (Item 5): Compute signal-to-fill latency metrics and store in trades.

        signal_to_order_ms: signal.received_at → ENTRY order.placed_at
        order_to_fill_ms:   ENTRY order.placed_at → filled_at (entry fill time)
        total_latency_ms:   signal.received_at → filled_at

        All best-effort — any DB read failure is silently skipped.
        """
        from datetime import datetime

        def _parse(ts: Optional[str]) -> Optional[datetime]:
            if not ts:
                return None
            try:
                return datetime.fromisoformat(ts)
            except Exception:
                return None

        def _ms(t_start: Optional[datetime], t_end: Optional[datetime]) -> Optional[int]:
            if t_start is None or t_end is None:
                return None
            delta = (t_end - t_start).total_seconds() * 1000
            return max(0, int(delta))  # clamp to 0 (NTP skew can give small negatives)

        try:
            trade_row = self._store.fetch_one(
                "SELECT signal_id FROM trades WHERE trade_id = ?", (trade_id,)
            )
            signal_id = trade_row["signal_id"] if trade_row else None

            sig_received_at: Optional[str] = None
            if signal_id:
                sig_row = self._store.fetch_one(
                    "SELECT received_at FROM signals WHERE signal_id = ?", (signal_id,)
                )
                sig_received_at = sig_row["received_at"] if sig_row else None

            order_row = self._store.fetch_one(
                "SELECT placed_at FROM orders WHERE trade_id = ? AND leg = 'ENTRY'", (trade_id,)
            )
            order_placed_at: Optional[str] = order_row["placed_at"] if order_row else None

            t_signal = _parse(sig_received_at)
            t_placed = _parse(order_placed_at)
            t_filled = _parse(filled_at)

            sig_to_order = _ms(t_signal, t_placed)
            order_to_fill = _ms(t_placed, t_filled)
            total_lat = _ms(t_signal, t_filled)

            if any(v is not None for v in (sig_to_order, order_to_fill, total_lat)):
                with self._store.transaction() as cur:
                    cur.execute(
                        """UPDATE trades
                           SET signal_to_order_ms = ?,
                               order_to_fill_ms   = ?,
                               total_latency_ms   = ?
                           WHERE trade_id = ?""",
                        (sig_to_order, order_to_fill, total_lat, trade_id),
                    )
                self._log.debug(
                    "latency_recorded",
                    extra={
                        "trade_id": trade_id,
                        "signal_to_order_ms": sig_to_order,
                        "order_to_fill_ms": order_to_fill,
                        "total_latency_ms": total_lat,
                    },
                )
        except Exception as exc:
            self._log.debug("latency_compute_failed: %s", exc)

    def close_trade(
        self,
        *,
        trade_id: str,
        exit_price: float,
        exit_qty: int,
        exit_reason: str,
        gross_pnl: float,
        charges: float,
        cost_breakdown: Optional[object] = None,
    ) -> Dict:
        """
        Finalize a trade on exit fill (BL-10a).

        Writes exit_time, exit_price, exit_reason, gross_pnl, charges,
        net_pnl=gross_pnl-charges, and sets status='CLOSED' in one transaction.

        Args:
            trade_id:    trade to close.
            exit_price:  fill price of the closing order.
            exit_qty:    shares closed (for ledger consistency; schema stores
                         qty_filled from entry, not from exit).
            exit_reason: must be in _VALID_EXIT_REASONS.
            gross_pnl:   direction-aware gross PnL computed by the caller.
            charges:     total round-trip broker costs.

        Returns:
            The updated trades row as a dict (read back after UPDATE, so the
            return value reflects persisted state, not caller input).

        Raises:
            ValueError: exit_reason not in taxonomy; trade_id not found; or
                        trade is already CLOSED (double-close guard).
        """
        if exit_reason not in _VALID_EXIT_REASONS:
            raise ValueError(
                f"close_trade.exit_reason must be one of "
                f"{sorted(_VALID_EXIT_REASONS)}, got {exit_reason!r}"
            )

        existing = self.get_trade(trade_id)
        if existing is None:
            raise ValueError(f"close_trade: trade {trade_id!r} not found")
        if existing.get("status") not in ("OPEN", "PARTIAL"):
            raise ValueError(
                f"close_trade: trade {trade_id!r} has terminal status "
                f"{existing.get('status')!r} "
                f"(exit_time={existing.get('exit_time')!r}); "
                f"refusing to overwrite"
            )

        if exit_qty != existing.get("qty_filled", 0):
            self._log.warning(
                "close_trade: partial exit (exit_qty=%d, entry_qty=%d)",
                exit_qty, existing.get("qty_filled", 0)
            )

        # E.2 (2026-04-25): sanity-bound gross_pnl. A bug in _handle_exit_fill
        # (or upstream cost computation) producing gross_pnl ~= 1e9 would
        # corrupt fund_manager.daily_realized_pnl, trip the daily-loss
        # invariant on a phantom magnitude, and cascade hard_kill / on_critical.
        # 10x entry_value is a loose-but-finite ceiling: ±100% intraday
        # (notional) is a tail outcome; ±1000% is computationally impossible
        # in a single trade and indicates either a unit-confusion (pct vs
        # rupees) or an integer overflow upstream. Raise rather than write
        # poisoned PnL into the trades table.
        entry_actual_price = existing.get("entry_actual_price")
        entry_qty_filled = existing.get("qty_filled") or 0
        if entry_actual_price is not None and entry_qty_filled > 0:
            entry_value = float(entry_actual_price) * float(entry_qty_filled)
            ceiling = entry_value * 10.0
            if entry_value > 0 and abs(gross_pnl) > ceiling:
                self._log.critical(
                    "close_trade.gross_pnl_implausible",
                    extra={
                        "trade_id": trade_id,
                        "gross_pnl": gross_pnl,
                        "entry_value": entry_value,
                        "ceiling_10x": ceiling,
                        "exit_price": exit_price,
                        "exit_qty": exit_qty,
                        "exit_reason": exit_reason,
                    },
                )
                raise ValueError(
                    f"close_trade: gross_pnl={gross_pnl:.2f} exceeds 10x "
                    f"entry_value={entry_value:.2f} (ceiling={ceiling:.2f}). "
                    f"Refusing to write poisoned PnL for trade {trade_id!r}; "
                    f"upstream cost / fill computation likely broken."
                )

        net_pnl = gross_pnl - charges
        now = now_ist().isoformat()
        cb = cost_breakdown
        with self._store.transaction() as cur:
            cur.execute(
                """
                UPDATE trades
                SET status          = 'CLOSED',
                    exit_time       = ?,
                    exit_price      = ?,
                    exit_reason     = ?,
                    gross_pnl       = ?,
                    charges         = ?,
                    net_pnl         = ?,
                    cost_brokerage  = ?,
                    cost_stt        = ?,
                    cost_exchange_txn = ?,
                    cost_sebi       = ?,
                    cost_gst        = ?,
                    cost_stamp_duty = ?,
                    updated_at      = ?
                WHERE trade_id = ?
                """,
                (now, exit_price, exit_reason,
                 gross_pnl, charges, net_pnl,
                 cb.brokerage if cb else None,
                 cb.stt if cb else None,
                 cb.exchange_txn if cb else None,
                 cb.sebi if cb else None,
                 cb.gst if cb else None,
                 cb.stamp_duty if cb else None,
                 now, trade_id),
            )
        self._log.info(
            "trade_closed",
            extra={
                "trade_id": trade_id,
                "exit_price": exit_price,
                "exit_qty": exit_qty,
                "exit_reason": exit_reason,
                "gross_pnl": gross_pnl,
                "charges": charges,
                "net_pnl": net_pnl,
            },
        )
        return self.get_trade(trade_id)  # read-back for caller (persisted truth)

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
        rejection_reason: Optional[str] = None,
        filled_at: Optional[str] = None,
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
                    rejection_reason = COALESCE(?, rejection_reason),
                    filled_at = COALESCE(?, filled_at),
                    updated_at = ?
                WHERE order_id = ?
                """,
                (status, qty_filled, avg_fill_price,
                 rejection_reason, filled_at,
                 now, broker_order_id),
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
