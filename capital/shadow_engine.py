"""
capital/shadow_engine.py -- Trading System v2  FIX-135 Item 41

Purpose:
    Shadow paper engine that runs in parallel with live trading.
    Receives same signals as the live engine, simulates fills at the
    signal trigger price, and stores shadow trades in DB for regret
    analysis (live vs shadow comparison at EOD).

    Does NOT place real orders. Does NOT affect capital allocation.
    Only active when mode=LIVE (no shadow in paper mode).

Design:
    - Injected into signal_processor via set_shadow_engine().
    - Called after live pipeline completes (success or reject).
    - Simulates: entry at trigger_price, SL/TGT from strategy config.
    - Exit simulated at EOD or when SL/TGT would have been hit
      (checked via on_tick from live feed).
    - Results stored in shadow_trades table for daily report comparison.
"""
from __future__ import annotations

import logging
import math
import threading
import uuid
from typing import Dict, Optional

from core.time_authority import now_ist


class ShadowTrade:
    __slots__ = (
        "shadow_trade_id", "date", "signal_id", "symbol", "strategy",
        "direction", "entry_price", "qty", "sys_sl", "sys_tgt",
        "live_trade_id", "live_status", "created_at",
        "simulated_exit_price", "simulated_exit_reason", "simulated_pnl",
        "closed",
    )

    def __init__(
        self, *, signal_id: str, symbol: str, strategy: str,
        direction: str, entry_price: float, qty: int,
        sys_sl: float, sys_tgt: float,
        live_trade_id: Optional[str] = None,
        live_status: str = "UNKNOWN",
        date: Optional[str] = None,
    ):
        self.shadow_trade_id = str(uuid.uuid4())
        self.date = date or now_ist().strftime("%Y-%m-%d")
        self.signal_id = signal_id
        self.symbol = symbol
        self.strategy = strategy
        self.direction = direction
        self.entry_price = entry_price
        self.qty = qty
        self.sys_sl = sys_sl
        self.sys_tgt = sys_tgt
        self.live_trade_id = live_trade_id
        self.live_status = live_status
        self.created_at = now_ist().isoformat()
        self.simulated_exit_price: Optional[float] = None
        self.simulated_exit_reason: Optional[str] = None
        self.simulated_pnl: Optional[float] = None
        self.closed = False


class ShadowEngine:
    """Shadow paper engine for live vs paper regret analysis."""

    def __init__(
        self,
        state_store,
        logger: Optional[logging.Logger] = None,
        enabled: bool = True,
    ) -> None:
        self._store = state_store
        self._log = logger or logging.getLogger("shadow_engine")
        self._enabled = enabled
        self._active: Dict[str, ShadowTrade] = {}
        self._token_map: Dict[int, str] = {}
        self._lock = threading.Lock()

    def set_token_map(self, token_map: Dict[int, str]) -> None:
        self._token_map = token_map

    @property
    def active_count(self) -> int:
        return len(self._active)

    def record_shadow_trade(
        self,
        *,
        signal_id: str,
        symbol: str,
        strategy: str,
        direction: str,
        entry_price: float,
        qty: int,
        sys_sl: float,
        sys_tgt: float,
        live_trade_id: Optional[str] = None,
        live_status: str = "TRADED",
    ) -> Optional[str]:
        """
        Record a shadow trade for a signal that was processed by live engine.
        Returns shadow_trade_id or None if disabled.
        """
        if not self._enabled:
            return None

        st = ShadowTrade(
            signal_id=signal_id,
            symbol=symbol,
            strategy=strategy,
            direction=direction,
            entry_price=entry_price,
            qty=qty,
            sys_sl=sys_sl,
            sys_tgt=sys_tgt,
            live_trade_id=live_trade_id,
            live_status=live_status,
        )

        with self._lock:
            self._active[st.shadow_trade_id] = st

        self._persist_shadow_trade(st)

        self._log.info(
            "shadow_engine.trade_recorded",
            extra={
                "shadow_trade_id": st.shadow_trade_id,
                "symbol": symbol,
                "direction": direction,
                "entry_price": entry_price,
                "live_trade_id": live_trade_id,
            },
        )
        return st.shadow_trade_id

    def on_tick(self, tick: dict) -> None:
        """Check active shadow trades for SL/TGT hit."""
        if not self._enabled:
            return
        token = tick.get("instrument_token")
        ltp = tick.get("last_price", 0.0)
        if token is None or ltp <= 0:
            return

        symbol = self._token_map.get(token)
        if not symbol:
            return

        with self._lock:
            to_close = []
            for sid, st in self._active.items():
                if st.closed or st.symbol != symbol:
                    continue
                hit = self._check_sl_tgt(st, ltp)
                if hit:
                    to_close.append((sid, hit, ltp))

            for sid, reason, price in to_close:
                self._close_shadow_trade(sid, price, reason)

    def close_all_eod(self, eod_price_map: Optional[Dict[str, float]] = None) -> int:
        """Close all open shadow trades at EOD. Returns count closed."""
        if not self._enabled:
            return 0
        closed = 0
        with self._lock:
            for sid in list(self._active.keys()):
                st = self._active[sid]
                if st.closed:
                    continue
                price = (eod_price_map or {}).get(st.symbol, st.entry_price)
                self._close_shadow_trade(sid, price, "EOD")
                closed += 1
        return closed

    def get_daily_summary(self, date: Optional[str] = None) -> list[dict]:
        """Fetch shadow trades for a date from DB."""
        date = date or now_ist().strftime("%Y-%m-%d")
        rows = self._store.fetch_all(
            "SELECT * FROM shadow_trades WHERE date = ? ORDER BY created_at",
            (date,),
        )
        return [dict(r) for r in rows]

    def _check_sl_tgt(self, st: ShadowTrade, ltp: float) -> Optional[str]:
        if st.direction == "LONG":
            if ltp <= st.sys_sl:
                return "SL_HIT"
            if ltp >= st.sys_tgt:
                return "TGT_HIT"
        else:
            if ltp >= st.sys_sl:
                return "SL_HIT"
            if ltp <= st.sys_tgt:
                return "TGT_HIT"
        return None

    def _close_shadow_trade(
        self, shadow_trade_id: str, exit_price: float, reason: str
    ) -> None:
        st = self._active.get(shadow_trade_id)
        if st is None or st.closed:
            return

        st.closed = True
        st.simulated_exit_price = exit_price
        st.simulated_exit_reason = reason

        if st.direction == "LONG":
            st.simulated_pnl = (exit_price - st.entry_price) * st.qty
        else:
            st.simulated_pnl = (st.entry_price - exit_price) * st.qty

        self._update_shadow_trade_exit(st)
        self._log.info(
            "shadow_engine.trade_closed",
            extra={
                "shadow_trade_id": st.shadow_trade_id,
                "symbol": st.symbol,
                "reason": reason,
                "pnl": st.simulated_pnl,
            },
        )

    def _persist_shadow_trade(self, st: ShadowTrade) -> None:
        try:
            with self._store.transaction() as cur:
                cur.execute(
                    """
                    INSERT INTO shadow_trades
                      (shadow_trade_id, date, signal_id, symbol, strategy,
                       direction, entry_price, qty, sys_sl, sys_tgt,
                       live_trade_id, live_status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        st.shadow_trade_id, st.date, st.signal_id,
                        st.symbol, st.strategy, st.direction,
                        st.entry_price, st.qty, st.sys_sl, st.sys_tgt,
                        st.live_trade_id, st.live_status, st.created_at,
                    ),
                )
        except Exception as exc:
            self._log.error("shadow_engine.persist_failed: %s", exc)

    def _update_shadow_trade_exit(self, st: ShadowTrade) -> None:
        try:
            with self._store.transaction() as cur:
                cur.execute(
                    """
                    UPDATE shadow_trades
                    SET simulated_exit_price = ?,
                        simulated_exit_reason = ?,
                        simulated_pnl = ?
                    WHERE shadow_trade_id = ?
                    """,
                    (
                        st.simulated_exit_price,
                        st.simulated_exit_reason,
                        st.simulated_pnl,
                        st.shadow_trade_id,
                    ),
                )
        except Exception as exc:
            self._log.error("shadow_engine.update_exit_failed: %s", exc)
