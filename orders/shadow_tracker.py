"""
orders/shadow_tracker.py -- Trading System v2

Purpose:
    Tracks multi-inning price simulation after a real trade closes.
    Inning 1 = the actual trade (real broker order, persisted from PositionClosed
    event). Innings 2–3 = simulated: no real orders, pure price watching via
    live tick feed. Closes each simulated inning when SL or TGT is hit, then
    cascades to the next inning (if max_innings not reached and market is open).

    Fixes Concern 4: old system reported TGT when SL hit first then price
    recovered and hit TGT later. shadow_tracker gives full visibility into all
    innings and their actual outcomes.

Locked Design Decisions:
    SH1  -- Inning frozen dataclass: 15 fields including is_real flag.
    SH2  -- Constructor injected with state_store, bus, live_feed,
            market_windows, time_authority, notifier, logger. Optional
            strategies dict for SL/TGT derivation in innings 2+.
    SH3  -- Subscribe to PositionClosed; create inning 1 from trade record.
    SH4  -- _start_simulated_inning: derive new SL/TGT from strategy params
            (or fallback to effective-pct from trade record).
    SH5  -- on_tick(tick): resolve symbol from instrument_cache; check
            all active innings for that symbol.
    SH6  -- _check_hit: LONG SL when ltp <= sl_price; TGT when ltp >= tgt.
            SHORT SL when ltp >= sl_price; TGT when ltp <= tgt.
    SH7  -- _close_inning: compute pnl, update DB, cascade if conditions met.
    SH8  -- Subscribe to EodSquareoffComplete; close all active innings EOD.
    SH9  -- innings table in core/schema.sql (v9).
    SH10 -- 4 state_store helpers: insert_inning, update_inning_close,
            get_innings_for_trade, get_innings_for_date.
    SH11 -- SystemConfig.shadow_tracker section.
    SH12 -- Layer 5 (orders/). No direct broker calls.
    SH13 -- enabled=False: all public methods are no-ops.
    SH14 -- Thread safety: _active_innings guarded by threading.RLock.
    SH15 -- Test suite in tests/unit/test_shadow_tracker.py.

What This Module Does NOT Do:
    - Does not place real broker orders (innings 2-3 are pure simulation)
    - Does not modify the original trade record
    - Does not implement the daily report multi-inning section (Module 40)
    - Does not track position sizing or capital for simulated innings
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, replace as dc_replace
from datetime import datetime
from typing import Dict, List, Optional

from core.events import EodSquareoffComplete, EventBus, PositionClosed
from core.logger import log_exception
from core.time_authority import ist_timezone, now_ist, today_ist
from orders.order_reconciler import _PRODUCT_TO_INTENT  # noqa: F401 — exposed for cross-module consistency


# ─────────────────────────────────────────────────────────────────────────────
# Inning dataclass (SH1)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Inning:
    """
    Immutable snapshot of one inning for a trade (SH1).

    inning_number 1 = the real trade.
    inning_number 2–3 = simulated (no broker orders).
    is_real is True only for inning 1.
    """
    inning_number:  int
    trade_id:       str
    symbol:         str
    direction:      str              # "LONG" | "SHORT"
    entry_price:    float
    entry_ts:       datetime
    sl_price:       float
    tgt_price:      float
    exit_price:     Optional[float]
    exit_ts:        Optional[datetime]
    exit_reason:    Optional[str]    # "SL" | "TGT" | "EOD" | None
    duration_sec:   Optional[int]
    pnl_pct:        Optional[float]
    pnl_per_share:  Optional[float]
    is_real:        bool             # True ONLY for inning 1


# ─────────────────────────────────────────────────────────────────────────────
# ShadowTracker
# ─────────────────────────────────────────────────────────────────────────────

class ShadowTracker:
    """
    Tracks multi-inning price simulation after a real trade closes (SH1-SH15).

    Lifecycle:
        1. On PositionClosed: create inning 1, persist, cascade to inning 2 if
           exit_reason was SL or TGT and market is still open.
        2. on_tick(tick): for each active simulated inning, check SL/TGT hit;
           close and cascade if hit.
        3. On EodSquareoffComplete: close all remaining active innings EOD.

    Constructor injects:
        state_store     -- for DB reads and innings persistence
        bus             -- subscribed to PositionClosed + EodSquareoffComplete
        live_feed       -- used for last-price fallback in EOD handler
        market_windows  -- for is_market_open() cascade guard
        time_authority  -- for now_ist() calls
        notifier        -- optional Telegram notifier for per-inning alerts
        logger          -- optional structured logger
        strategies      -- optional dict[str, StrategySchema] for SL/TGT derivation
        max_innings     -- int 1–5 (default 3)
        alert_per_inning -- bool (default True)
        enabled         -- bool (default True); False = all no-ops
    """

    def __init__(
        self,
        state_store,
        bus: EventBus,
        live_feed,
        market_windows,
        time_authority,
        notifier=None,
        logger=None,
        strategies: Optional[Dict] = None,
        max_innings: int = 3,
        alert_per_inning: bool = True,
        enabled: bool = True,
        mode: str = "LIVE",                # session mode label for alert title
    ) -> None:
        self._store = state_store
        self._bus = bus
        self._live_feed = live_feed
        self._market_windows = market_windows
        self._time_authority = time_authority
        self._notifier = notifier
        self._log = logger or logging.getLogger(__name__)
        self._strategies: Optional[Dict] = strategies
        self._max_innings = max_innings
        self._alert_per_inning = alert_per_inning
        self._enabled = enabled
        self._mode = mode

        # Active simulated innings (inning_number >= 2). Keyed by trade_id.
        # Only one active inning per trade at any time (SH14).
        self._active_innings: Dict[str, Inning] = {}
        self._lock = threading.RLock()

        # Last seen price per symbol (maintained by on_tick for EOD fallback).
        self._last_price: Dict[str, float] = {}

        # Set to True when EodSquareoffComplete fires; no new innings after EOD.
        # BL-13: _eod_fired_date holds the YYYY-MM-DD string of the day the
        # bool was set. Persisted across restarts by reading the eod_squareoff_log
        # row for today on construction. Protects the 13-min post-EOD-pre-close
        # restart window from a stale PositionClosed cascading to a simulated
        # second inning after the market has already squared off.
        self._eod_fired: bool = False
        self._eod_fired_date: Optional[str] = None

        # BL-13: restore _eod_fired from eod_squareoff_log if EOD already ran today.
        # Graceful-degrade on query failure: log and run with _eod_fired=False.
        # Mark-before-fire ordering is enforced upstream in eod_squareoff.py:
        # the log row is inserted BEFORE EodSquareoffComplete is published, so a
        # restart that sees the row is guaranteed to have missed the event.
        today_iso = self._today_ist()
        try:
            log_row = self._store.get_eod_squareoff_log_for_date(today_iso)
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.error(
                "shadow_tracker: eod_squareoff_log query failed on startup; "
                "running in degraded mode (_eod_fired=False) for %s",
                today_iso,
            )
            log_row = None

        if log_row is not None:
            self._eod_fired = True
            self._eod_fired_date = today_iso
            self._log.info(
                "shadow_tracker: restored _eod_fired from "
                "eod_squareoff_log for %s",
                today_iso,
            )

        # Subscribe to events (SH3, SH8)
        bus.subscribe(PositionClosed, self._on_position_closed)
        bus.subscribe(EodSquareoffComplete, self._on_eod_complete)

    # ── public interface ──────────────────────────────────────────────────────

    def set_instrument_cache(self, cache) -> None:
        """Wire InstrumentCache for token->symbol lookup in on_tick (SH5)."""
        self._instrument_cache = cache

    def is_tracking(self, symbol: str) -> bool:
        """
        B.5 / Audit 5.1: True if a simulated inning (>=2) is currently active
        for `symbol`. Used by signal_processor to skip new entries on a
        symbol whose previous inning is still simulating, preventing
        overlapping real + shadow positions on the same instrument.

        Cheap O(N) over active innings (N is bounded by max open trades,
        typically <= 30). No I/O, no broker calls.
        """
        if not self._enabled:
            return False
        with self._lock:
            for ing in self._active_innings.values():
                if ing.symbol == symbol:
                    return True
        return False

    def on_tick(self, tick: dict) -> None:
        """
        Process one live tick. Called on live_feed consumer thread (SH5).

        tick must have keys: instrument_token (int), last_price (float).
        Unknown tokens are silently skipped with a WARNING log.
        """
        if not self._enabled:
            return

        # Resolve symbol via instrument_cache
        cache = getattr(self, "_instrument_cache", None)
        if cache is None:
            return
        try:
            row = cache.get_by_token(tick["instrument_token"])
            symbol = row.symbol
        except Exception:
            self._log.warning(
                "shadow_tracker.on_tick: unknown instrument_token=%s",
                tick.get("instrument_token"),
            )
            return

        ltp: float = float(tick["last_price"])
        # Audit #20: bid/ask when present; 0.0 means "not available" →
        # _check_hit falls back to LTP.
        bid: float = float(tick.get("bid", 0.0) or 0.0)
        ask: float = float(tick.get("ask", 0.0) or 0.0)

        # Track last price for EOD fallback
        with self._lock:
            self._last_price[symbol] = ltp

            # Find all active innings for this symbol
            matching = [
                ing for ing in self._active_innings.values()
                if ing.symbol == symbol
            ]

        # Process hits outside lock to avoid holding lock during DB writes
        for ing in matching:
            hit = _check_hit(ing, ltp, bid=bid, ask=ask)
            if hit:
                self._close_inning(ing, ltp, hit)

    # ── event handlers ────────────────────────────────────────────────────────

    def _on_position_closed(self, event: PositionClosed) -> None:
        """
        Handle PositionClosed event. Create inning 1 from trade record (SH3).
        """
        if not self._enabled:
            return

        trade_id = event.trade_id
        exit_price = event.exit_price

        # Look up trade details from DB
        rows = self._store.fetch_all(
            "SELECT * FROM trades WHERE trade_id = ?",
            (trade_id,),
        )
        if not rows:
            self._log.error(
                "shadow_tracker: trade_id=%s not found in DB for PositionClosed",
                trade_id,
            )
            return

        trade = rows[0]
        direction: str = trade["direction"]              # LONG | SHORT
        entry_price: float = (
            float(trade["entry_actual_price"]) if trade["entry_actual_price"]
            else float(trade["entry_target_price"])
        )
        sl_initial: float = float(trade["sl_initial"])
        tgt_initial: float = float(trade["tgt_initial"])
        db_exit_reason: str = trade["exit_reason"] or "EOD"

        # Normalise DB exit_reason to inning convention
        reason_map = {
            "SL_HIT": "SL",
            "TGT_HIT": "TGT",
            "EOD": "EOD",
        }
        exit_reason: str = reason_map.get(db_exit_reason, "EOD")

        # Timestamps
        entry_ts_raw = trade["entry_time"] or trade["created_at"]
        entry_ts = _parse_ts(entry_ts_raw)
        exit_ts_raw = trade["exit_time"] or ""
        exit_ts = _parse_ts(exit_ts_raw) if exit_ts_raw else self._now()

        duration_sec = max(0, int((exit_ts - entry_ts).total_seconds()))

        # PnL for inning 1
        pnl_per_share, pnl_pct = _calc_pnl(direction, entry_price, exit_price)

        inning1 = Inning(
            inning_number=1,
            trade_id=trade_id,
            symbol=trade["symbol"],
            direction=direction,
            entry_price=entry_price,
            entry_ts=entry_ts,
            sl_price=sl_initial,
            tgt_price=tgt_initial,
            exit_price=exit_price,
            exit_ts=exit_ts,
            exit_reason=exit_reason,
            duration_sec=duration_sec,
            pnl_pct=pnl_pct,
            pnl_per_share=pnl_per_share,
            is_real=True,
        )

        # Persist inning 1
        try:
            self._store.insert_inning(inning1)
        except Exception as exc:
            self._log.error(
                "shadow_tracker: failed to insert inning 1 for trade_id=%s: %s",
                trade_id, exc,
            )
            return

        # Alert for inning 1 closure
        if self._alert_per_inning:
            self._send_alert(inning1)

        # Cascade to inning 2 if conditions are met
        if (
            exit_reason in ("SL", "TGT")
            and 1 < self._max_innings
            and not self._eod_fired
            and self._market_windows.is_market_open(self._now())
        ):
            self._start_simulated_inning(
                prev_inning=inning1,
                strategy_name=trade["strategy"],
                trade_entry=entry_price,
                trade_sl=sl_initial,
                trade_tgt=tgt_initial,
            )

    def _on_eod_complete(self, event: EodSquareoffComplete) -> None:
        """
        Handle EodSquareoffComplete: close all remaining active innings (SH8).

        BL-13: Idempotent per IST date. If _eod_fired_date already equals
        today, this is a duplicate event (either in-process re-publish or
        a post-restart replay) and we skip without closing innings again.
        """
        if not self._enabled:
            return

        today_iso = self._today_ist()
        if self._eod_fired_date is not None and self._eod_fired_date == today_iso:
            self._log.info(
                "shadow_tracker: EOD already fired today (%s), skipping",
                today_iso,
            )
            return

        with self._lock:
            # Mark first (belt-and-braces: even if close loop raises below,
            # a later duplicate event still hits the guard above).
            self._eod_fired = True
            self._eod_fired_date = today_iso
            active_copy = list(self._active_innings.values())

        for ing in active_copy:
            # Use last seen price or fallback to entry_price
            with self._lock:
                ltp = self._last_price.get(ing.symbol, ing.entry_price)
            self._close_inning(ing, ltp, "EOD")

    # ── private: simulated inning management ──────────────────────────────────

    def _start_simulated_inning(
        self,
        prev_inning: Inning,
        strategy_name: str,
        trade_entry: float,
        trade_sl: float,
        trade_tgt: float,
    ) -> None:
        """
        Create and register the next simulated inning (SH4).

        Derives new SL/TGT from strategy object if available; otherwise uses
        effective percentages computed from trade record (fallback).
        """
        entry_price = prev_inning.exit_price
        entry_ts = prev_inning.exit_ts or self._now()
        direction = prev_inning.direction
        inning_number = prev_inning.inning_number + 1

        # Derive SL and TGT for new entry price
        sl_price, tgt_price = self._compute_sl_tgt(
            entry_price=entry_price,
            direction=direction,
            strategy_name=strategy_name,
            trade_entry=trade_entry,
            trade_sl=trade_sl,
            trade_tgt=trade_tgt,
        )

        new_inning = Inning(
            inning_number=inning_number,
            trade_id=prev_inning.trade_id,
            symbol=prev_inning.symbol,
            direction=direction,
            entry_price=entry_price,
            entry_ts=entry_ts,
            sl_price=sl_price,
            tgt_price=tgt_price,
            exit_price=None,
            exit_ts=None,
            exit_reason=None,
            duration_sec=None,
            pnl_pct=None,
            pnl_per_share=None,
            is_real=False,
        )

        # Persist to DB
        try:
            self._store.insert_inning(new_inning)
        except Exception as exc:
            self._log.error(
                "shadow_tracker: failed to insert inning %d for trade_id=%s: %s",
                inning_number, prev_inning.trade_id, exc,
            )
            return

        # Register as active
        with self._lock:
            self._active_innings[prev_inning.trade_id] = new_inning

        self._log.info(
            "shadow_tracker: started inning %d for %s entry=%.2f sl=%.2f tgt=%.2f",
            inning_number, new_inning.symbol, entry_price, sl_price, tgt_price,
        )

    def _close_inning(
        self,
        inning: Inning,
        exit_price: float,
        exit_reason: str,
    ) -> None:
        """
        Close an active inning: compute PnL, update DB, cascade (SH7).
        """
        now = self._now()

        pnl_per_share, pnl_pct = _calc_pnl(inning.direction, inning.entry_price, exit_price)

        entry_ts = inning.entry_ts
        if entry_ts is None:
            entry_ts = now
        duration_sec = max(0, int((now - _make_naive(now, entry_ts)).total_seconds()))

        exit_ts_str = now.isoformat()

        # Update DB + remove from active under lock.
        # H-13: on DB failure we do NOT pop and we do NOT cascade. The inning
        # stays in _active_innings so a later tick (or the reconciler) can
        # pick it up and try again. Progressing state on a failed DB write
        # would leave memory and DB divergent and could double-cascade.
        with self._lock:
            try:
                self._store.update_inning_close(
                    trade_id=inning.trade_id,
                    inning_number=inning.inning_number,
                    exit_price=exit_price,
                    exit_ts=exit_ts_str,
                    exit_reason=exit_reason,
                    duration_sec=duration_sec,
                    pnl_pct=pnl_pct,
                    pnl_per_share=pnl_per_share,
                )
            except Exception as exc:
                self._log.error(
                    "SHADOW_INNING_CLOSE_DB_FAILED: leaving inning in active "
                    "set for reconciler recovery. trade_id=%s inning=%d "
                    "exit_reason=%s error=%s",
                    inning.trade_id, inning.inning_number, exit_reason, exc,
                )
                return
            self._active_innings.pop(inning.trade_id, None)

        self._log.info(
            "shadow_tracker: closed inning %d for %s exit=%.2f reason=%s "
            "pnl_pct=%.2f%%",
            inning.inning_number, inning.symbol, exit_price, exit_reason, pnl_pct,
        )

        # Build closed version for alert + cascade
        closed_inning = dc_replace(
            inning,
            exit_price=exit_price,
            exit_ts=now,
            exit_reason=exit_reason,
            duration_sec=duration_sec,
            pnl_pct=pnl_pct,
            pnl_per_share=pnl_per_share,
        )

        # Alert outside lock (can be slow)
        if self._alert_per_inning:
            self._send_alert(closed_inning)

        # Cascade to next inning
        if (
            exit_reason in ("SL", "TGT")
            and inning.inning_number < self._max_innings
            and not self._eod_fired
            and self._market_windows.is_market_open(self._now())
        ):
            # Look up trade params for cascade
            rows = self._store.fetch_all(
                "SELECT strategy, entry_actual_price, entry_target_price, "
                "sl_initial, tgt_initial FROM trades WHERE trade_id = ?",
                (inning.trade_id,),
            )
            if rows:
                t = rows[0]
                trade_entry = (
                    float(t["entry_actual_price"]) if t["entry_actual_price"]
                    else float(t["entry_target_price"])
                )
                self._start_simulated_inning(
                    prev_inning=closed_inning,
                    strategy_name=t["strategy"],
                    trade_entry=trade_entry,
                    trade_sl=float(t["sl_initial"]),
                    trade_tgt=float(t["tgt_initial"]),
                )
            else:
                self._log.error(
                    "shadow_tracker: trade_id=%s not found for cascade from "
                    "inning %d",
                    inning.trade_id, inning.inning_number,
                )

    # ── private: SL/TGT derivation ────────────────────────────────────────────

    def _compute_sl_tgt(
        self,
        entry_price: float,
        direction: str,
        strategy_name: str,
        trade_entry: float,
        trade_sl: float,
        trade_tgt: float,
    ) -> tuple:
        """
        Derive sl_price and tgt_price for a new entry_price (SH4).

        Primary path: look up StrategySchema from self._strategies dict by name;
        apply sl_method and tgt_method (FIXED_PCT / RISK_REWARD).

        Fallback: compute effective percentages from original trade record and
        apply them to new entry_price.
        """
        if self._strategies and strategy_name in self._strategies:
            strategy = self._strategies[strategy_name]
            return _derive_sl_tgt_from_strategy(entry_price, direction, strategy)

        # Fallback: effective-pct from original trade
        if trade_entry and trade_entry > 0:
            sl_pct = abs(trade_entry - trade_sl) / trade_entry
            tgt_pct = abs(trade_tgt - trade_entry) / trade_entry
        else:
            sl_pct = 0.01
            tgt_pct = 0.02

        if direction == "LONG":
            sl = entry_price * (1.0 - sl_pct)
            tgt = entry_price * (1.0 + tgt_pct)
        else:
            sl = entry_price * (1.0 + sl_pct)
            tgt = entry_price * (1.0 - tgt_pct)

        return sl, tgt

    # ── private: alerts ───────────────────────────────────────────────────────

    def _send_alert(self, inning: Inning) -> None:
        """Send a Telegram INFO alert for an inning close (SH7)."""
        if self._notifier is None:
            return
        try:
            reason = (inning.exit_reason or "CLOSED").upper()
            if reason == "TGT":
                emoji = "🎯"
            elif reason == "SL":
                emoji = "🔴"
            else:
                emoji = "🔵"

            # Total ₹ P&L requires qty_filled — best-effort lookup
            total_pnl = None
            try:
                rows = self._store.fetch_all(
                    "SELECT qty_filled FROM trades WHERE trade_id = ?",
                    (inning.trade_id,),
                )
                if rows and inning.pnl_per_share is not None:
                    qty = int(rows[0]["qty_filled"] or 0)
                    total_pnl = float(inning.pnl_per_share) * qty
            except Exception:  # noqa: BLE001
                total_pnl = None

            if total_pnl is not None:
                sign = "+" if total_pnl >= 0 else "-"
                pnl_rupees = f"{sign}₹{abs(total_pnl):,.2f}"
            else:
                pnl_rupees = "n/a"
            pnl_pct_str = (
                f"{inning.pnl_pct:+.2f}%"
                if inning.pnl_pct is not None else "n/a"
            )
            exit_price_str = (
                f"₹{float(inning.exit_price):,.2f}"
                if inning.exit_price is not None else "n/a"
            )

            title = (
                f"[{self._mode}] {emoji} {reason} — {inning.symbol}"
            )
            body = (
                f"Exit: {exit_price_str} | Direction: {inning.direction}\n"
                f"P&L: {pnl_rupees} ({pnl_pct_str}) | "
                f"Inning: {inning.inning_number}"
            )
            self._notifier.send(
                severity="INFO",
                title=title,
                body=body,
                source_module="shadow_tracker",
            )
        except Exception as exc:
            self._log.error(
                "shadow_tracker: alert failed for inning %d %s: %s",
                inning.inning_number, inning.symbol, exc,
            )

    # ── private: time helpers ─────────────────────────────────────────────────

    def _now(self) -> datetime:
        """Return current IST datetime via time_authority (or direct now_ist)."""
        if hasattr(self._time_authority, "now_ist"):
            return self._time_authority.now_ist()
        return now_ist()

    def _today_ist(self) -> str:
        """Return today's date as YYYY-MM-DD in IST (BL-13)."""
        if hasattr(self._time_authority, "today_ist"):
            return self._time_authority.today_ist()
        return today_ist()


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def _check_hit(
    inning: Inning,
    ltp: float,
    *,
    bid: float = 0.0,
    ask: float = 0.0,
) -> Optional[str]:
    """
    Return "SL", "TGT", or None based on current price vs. inning thresholds (SH6).

    Audit #20: SL simulation uses the fill-side of the book when depth is
    available. A LONG position exits on the bid (where we'd sell); SHORT
    exits on the ask (where we'd cover). Using LTP over-optimistically
    delays simulated stops. When bid/ask are 0.0 (unavailable) we fall
    back to LTP -- preserves prior behaviour.

    LONG:  SL when (bid or ltp) <= sl_price; TGT when ltp >= tgt_price.
    SHORT: SL when (ask or ltp) >= sl_price; TGT when ltp <= tgt_price.
    Boundary: == threshold counts as a hit.
    """
    if inning.direction == "LONG":
        sl_ref = bid if bid > 0.0 else ltp
        if sl_ref <= inning.sl_price:
            return "SL"
        if ltp >= inning.tgt_price:
            return "TGT"
    else:  # SHORT
        sl_ref = ask if ask > 0.0 else ltp
        if sl_ref >= inning.sl_price:
            return "SL"
        if ltp <= inning.tgt_price:
            return "TGT"
    return None


def _calc_pnl(direction: str, entry: float, exit_p: float) -> tuple:
    """Compute (pnl_per_share, pnl_pct) for a closed inning."""
    if direction == "LONG":
        pnl_per_share = exit_p - entry
    else:
        pnl_per_share = entry - exit_p
    pnl_pct = (pnl_per_share / entry) * 100.0 if entry else 0.0
    return pnl_per_share, pnl_pct


def _derive_sl_tgt_from_strategy(
    entry_price: float,
    direction: str,
    strategy,
) -> tuple:
    """
    Apply strategy sl_method and tgt_method to new entry_price (SH4).

    Mirrors signal_processor._derive_prices and _derive_target logic.
    ATR falls back to FIXED_PCT (same as signal_processor).
    """
    # ── SL ──
    sl_method = strategy.sl_method
    if sl_method == "ATR":
        sl_method = "FIXED_PCT"  # ATR not implemented; fallback

    if sl_method == "FIXED_PCT":
        sl_pct = float(strategy.sl_pct)
        if direction == "LONG":
            sl = entry_price * (1.0 - sl_pct)
        else:
            sl = entry_price * (1.0 + sl_pct)
    else:
        sl = entry_price  # unknown method; use entry as fallback

    # Bounds enforcement
    sl_dist_pct = abs(entry_price - sl) / entry_price if entry_price else 0.0
    if sl_dist_pct < strategy.sl_min_pct:
        adj = entry_price * strategy.sl_min_pct
        sl = entry_price - adj if direction == "LONG" else entry_price + adj
    elif sl_dist_pct > strategy.sl_max_pct:
        adj = entry_price * strategy.sl_max_pct
        sl = entry_price - adj if direction == "LONG" else entry_price + adj

    # ── TGT ──
    tgt_method = strategy.tgt_method
    if tgt_method == "ATR":
        tgt_method = "FIXED_PCT"  # fallback

    if tgt_method == "FIXED_PCT":
        tgt_pct = float(strategy.tgt_pct)
        if direction == "LONG":
            tgt = entry_price * (1.0 + tgt_pct)
        else:
            tgt = entry_price * (1.0 - tgt_pct)
    elif tgt_method == "RISK_REWARD":
        sl_dist = abs(entry_price - sl)
        ratio = float(strategy.tgt_risk_reward)
        if direction == "LONG":
            tgt = entry_price + sl_dist * ratio
        else:
            tgt = entry_price - sl_dist * ratio
    else:
        # Unknown method; use 2x sl_distance as fallback
        sl_dist = abs(entry_price - sl)
        tgt = (entry_price + sl_dist * 2) if direction == "LONG" else (entry_price - sl_dist * 2)

    return sl, tgt


def _parse_ts(ts_str: str) -> datetime:
    """Parse ISO-8601 string to datetime. Returns naive IST if tz-aware."""
    if not ts_str:
        return now_ist()
    try:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is not None:
            dt = dt.astimezone(ist_timezone()).replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return now_ist().replace(tzinfo=None)


def _make_naive(now: datetime, ts: datetime) -> datetime:
    """
    Return ts as a naive datetime comparable to now.

    Strips tzinfo from ts if now is naive; converts to naive IST if ts is aware.
    """
    if ts.tzinfo is not None:
        # ts is aware — convert to IST naive
        return ts.astimezone(ist_timezone()).replace(tzinfo=None)
    return ts
