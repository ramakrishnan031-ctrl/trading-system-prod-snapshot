"""
orders/structure_exit_manager.py -- Trading System v2 · S&R V2 Phase B (SNR-V2)

Purpose:
    Structure-aware exit for MIS / LIMIT_TRIPLE static-leg trades. On every
    1-minute candle close (push feed via CandleStore.register_on_candle_close)
    for a held symbol it:

      ACTION B (evaluated first) — CONFIRMED STRUCTURE BREAK → EXIT.
        LONG  : a 1m candle CLOSE below the nearest HIGH support band AND a strong
                LOWER close (bearish). SHORT mirror: CLOSE above the nearest HIGH
                resistance band AND a strong UPPER close (bullish).
        On a confirmed break the position is flattened in the MANDATORY order
        (inherits the reconciler/G5b race protection):
            1. mark trade status = EXITING  (BEFORE anything else),
            2. cancel the resting SL + TGT legs,
            3. reverse-aware marketable-LIMIT flatten (MARKET fallback).

      ACTION A (else) — TRAIL THE SL TO STRUCTURE (tighten-only, once per candle).
        LONG  SL → support.band_low  × (1 − sl_buffer_pct/100), tick-snapped.
        SHORT SL → resistance.band_high × (1 + sl_buffer_pct/100), tick-snapped.
        Two pre-modify guards (root-cause, NOT broker-rejection reliance):
            ONLY-TIGHTEN : LONG new_sl > current_sl · SHORT new_sl < current_sl.
            WRONG-SIDE   : LONG ltp > new_sl · SHORT ltp < new_sl (else skip).
        Debounce: at most ONE SL move per (trade, candle) — Action A only.

Locked Design Decisions (SNR-V2 Phase B):
    SE1  -- MIS / LIMIT_TRIPLE static-leg trades ONLY. CO (SL in broker bracket)
            and CNC (SL in OCO GTT) are NEVER touched; delivery is off.
    SE2  -- HIGH-confidence zones only (min_zone_confidence). No valid zone →
            DO NOTHING (leave the current SL).
    SE3  -- Break = a 1-MINUTE candle CLOSE beyond the band (+ optional strong
            close). NEVER an LTP touch / wick.
    SE4  -- Single SL owner: the structure-exit controller is the ONLY writer of
            the SL leg. Do NOT co-enable strategy.trailing_sl_enabled.
    SE5  -- EXITING marked BEFORE cancel + flatten, ALWAYS. If the EXITING write
            fails the exit is aborted this cycle (the original SL still protects)
            and retried next candle — the invariant is never violated.
    SE6  -- Tighten-only (never widens risk); at most one SL move per candle.
    SE7  -- No DB schema change. Stateless across restart: the EXITING status
            covers in-flight exits durably; the per-candle debounce map is
            in-memory and naturally re-derives on boot.
    SE8  -- Parity: modify / cancel / flatten all use the adapter's paper/live
            split, so structure-exit behaves identically in PAPER.
    SE9  -- Dormant when structure_exit_enabled=false: not constructed, no
            subscribe, no action (existing flows byte-identical).

What This Module Does NOT Do:
    - Does not place or move CO/CNC exits (modify_order static legs only).
    - Does not change DB schema (no new table/column).
    - Does not trail continuously — one move per 1m candle, tighten-only.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from core.ids import truncate_tag_for_broker
from orders.price_math import (
    DEFAULT_SL_LIMIT_OFFSET_PCT,
    DEFAULT_TICK,
    EMERGENCY_EXIT_BUFFER_PCT,
    calc_sl_limit_price,
    marketable_limit_price,
    round_to_tick,
)
# SE: reuse Phase A's pure strong-close predicate (sr_detector/retest_confirm.py)
# rather than re-implement it — keeps the break-confirmation byte-identical to the
# entry-side confirm (only the is_long polarity flips for the exit side).
from sr_detector.retest_confirm import _strong_close

# Status sets (mirror the existing call sites verbatim so behaviour is identical).
#   SL-leg lookup live set  → breakeven_manager.py:382-398
#   resting-exit cancel set → kill_switch.py:976-981
_SL_LIVE_EXCLUDE = ("CANCELLED", "COMPLETE", "REJECTED", "FAILED")
_RESTING_EXIT_EXCLUDE = ("CANCELLED", "FAILED", "EXPIRED", "COMPLETE")

_LONG = ("LONG", "BUY")
_SHORT = ("SHORT", "SELL")


class StructureExitManager:
    """
    Structure-aware exit controller (SE1-SE9). Push-driven by CandleStore 1m
    closes; lifecycle modelled on screening/retest_monitor.py (start/stop +
    RLock). Dormant unless ``config.structure_exit_enabled`` is True.

    Usage::
        sem = StructureExitManager(adapter=..., state_store=..., zone_cache=...,
                                   config=app_config.system.structure_exit,
                                   candle_store=candle_store, ...)
        sem.start()    # subscribes on_1m_close to CandleStore
        ...
        sem.stop()     # unsubscribes
    """

    def __init__(
        self,
        *,
        adapter: Any,            # ZerodhaAdapter: modify_order/cancel_order/get_quote/get_positions/place_order
        state_store: Any,        # StateStore: fetch_one/fetch_all/transaction/get_open_intraday_positions
        zone_cache: Any,         # sr_detector.ZoneCache: get(symbol) -> ZoneSet | None
        config: Any,             # StructureExitConfig
        logger: Any,
        now_fn: Any,             # time_authority.now_ist
        instrument_cache: Any = None,   # tick_size(symbol); DEFAULT_TICK fallback
        order_monitor: Any = None,      # track() the flatten exit leg (optional)
        candle_store: Any = None,       # register/unregister_on_candle_close (optional in tests)
        notifier: Any = None,           # TelegramNotifier (optional)
        mode: str = "LIVE",
        sl_limit_offset_pct: float = DEFAULT_SL_LIMIT_OFFSET_PCT,
        enabled: Optional[bool] = None,
    ) -> None:
        self._adapter = adapter
        self._store = state_store
        self._zone_cache = zone_cache
        self._cfg = config
        self._log = logger
        self._now_fn = now_fn
        self._instrument_cache = instrument_cache
        self._order_monitor = order_monitor
        self._candle_store = candle_store
        self._notifier = notifier
        self._mode = mode
        self._sl_limit_offset_pct = float(sl_limit_offset_pct)

        self._enabled = bool(
            getattr(config, "structure_exit_enabled", False) if enabled is None else enabled
        )
        self._conf = str(getattr(config, "min_zone_confidence", "HIGH")).upper()
        self._sl_buffer_pct = float(getattr(config, "sl_buffer_pct", 0.2))
        self._break_buffer_pct = float(getattr(config, "break_buffer_pct", 0.0))
        self._require_strong = bool(getattr(config, "require_strong_close", True))
        self._break_frac = float(getattr(config, "break_strong_close_frac", 0.6))

        self._lock = threading.RLock()
        # SE6/SE7: per-candle debounce (Action A only). trade_id -> candle.ts iso.
        self._last_sl_move_candle_ts: Dict[str, str] = {}
        self._subscribed = False
        self._running = False

    # ── lifecycle (RetestMonitor shape) ──────────────────────────────────────

    def start(self) -> None:
        """SE9: subscribe on_1m_close to CandleStore. No-op when disabled."""
        if not self._enabled:
            return
        self._running = True
        if self._candle_store is not None and not self._subscribed:
            self._candle_store.register_on_candle_close(self.on_1m_close)
            self._subscribed = True
        self._log.info(
            "structure_exit_manager.started",
            extra={
                "subscribed": self._subscribed,
                "min_zone_confidence": self._conf,
                "require_strong_close": self._require_strong,
                "sl_buffer_pct": self._sl_buffer_pct,
                "mode": self._mode,
            },
        )

    def stop(self) -> None:
        """Unsubscribe from CandleStore. Idempotent; best-effort."""
        self._running = False
        if self._candle_store is not None and self._subscribed:
            try:
                self._candle_store.unregister_on_candle_close(self.on_1m_close)
            except Exception as exc:  # noqa: BLE001
                self._log.warning("structure_exit_manager.unsubscribe_failed: %s", exc)
        self._subscribed = False

    # ── feed callback ────────────────────────────────────────────────────────

    def on_1m_close(self, candle: Any) -> None:
        """CandleStore 1m-close callback. NEVER raises (would break sibling cbs)."""
        if not (self._enabled and self._running):
            return
        try:
            self._process_close(candle)
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "structure_exit_manager.on_close_error",
                extra={"symbol": getattr(candle, "symbol", "?"), "error": str(exc)},
            )

    def _process_close(self, candle: Any) -> None:
        symbol = getattr(candle, "symbol", None)
        if not symbol:
            return
        # STEP 2.1 — the OPEN MIS LIMIT_TRIPLE trade for this symbol (or nothing).
        trade = self._find_open_limit_triple_trade(symbol)
        if trade is None:
            return
        direction = str(trade["direction"] or "").upper()
        if direction in _LONG:
            is_long = True
        elif direction in _SHORT:
            is_long = False
        else:
            return  # unknown direction — leave the SL alone

        # STEP 2.2 — zones (synchronous cache read; miss/expiry → leave SL).
        zs = self._zone_cache.get(symbol)
        if zs is None:
            return

        # STEP 2.3 — zone selection (HIGH only). Reference the candle EXTREME, not
        # the close: a LONG support break is a candle whose HIGH was above the
        # support but whose CLOSE is below it — selecting on close would exclude
        # the very zone being broken (close < band_low), so ACTION B could never
        # fire. high (LONG) / low (SHORT) keeps trail (close still inside) and
        # break (close beyond) coherent on the same selected zone.
        price_ref = float(candle.high) if is_long else float(candle.low)
        zone = self._select_zone(zs, is_long, price_ref)
        if zone is None:
            return

        # STEP 4 — ACTION B (break) first; if it fires, exit and return.
        if self._is_confirmed_break(candle, zone, is_long):
            self._exit_on_break(trade, candle, zone, is_long)
            return

        # STEP 5 — ACTION A (reposition / trail), subject to the per-candle debounce.
        self._trail_sl(trade, candle, zone, is_long)

    # ── trade + zone selection ───────────────────────────────────────────────

    def _find_open_limit_triple_trade(self, symbol: str) -> Optional[Any]:
        """The OPEN/PARTIAL MIS LIMIT_TRIPLE trade for ``symbol``.

        get_open_intraday_positions() already scopes to product IN ('MIS','CO');
        filtering order_protocol == 'LIMIT_TRIPLE' drops CO_PLUS_TGT (CO) and
        guarantees a static local SL leg (SE1). CNC is excluded by the product
        scope. Returns None when there is no such trade.
        """
        try:
            rows = self._store.get_open_intraday_positions()
        except Exception as exc:  # noqa: BLE001
            self._log.error("structure_exit.trade_lookup_failed: %s", exc)
            return None
        matches = [
            r for r in (rows or [])
            if r["symbol"] == symbol
            and str(r["order_protocol"] or "").upper() == "LIMIT_TRIPLE"
        ]
        if not matches:
            return None
        if len(matches) > 1:
            self._log.warning(
                "structure_exit: %d open LIMIT_TRIPLE trades for %s; using first",
                len(matches), symbol,
            )
        return matches[0]

    def _select_zone(self, zone_set: Any, is_long: bool, ref: float) -> Optional[Any]:
        """Nearest min-confidence zone the candle traded into, in the protective
        direction. ``ref`` is the candle EXTREME (high for LONG, low for SHORT).
        LONG → the highest SUPPORT whose band_low is below ``ref`` (a support the
        whole candle sits under — already broken & left behind — has band_low > high
        → excluded). SHORT → the lowest RESISTANCE whose band_high is above ``ref``.
        """
        if is_long:
            cands = [
                z for z in zone_set.support
                if str(z.confidence).upper() == self._conf and z.band_low < ref
            ]
            if not cands:
                return None
            return max(cands, key=lambda z: z.band_high)   # nearest support below
        cands = [
            z for z in zone_set.resistance
            if str(z.confidence).upper() == self._conf and z.band_high > ref
        ]
        if not cands:
            return None
        return min(cands, key=lambda z: z.band_low)        # nearest resistance above

    # ── ACTION B: confirmed-break exit ───────────────────────────────────────

    def _is_confirmed_break(self, candle: Any, zone: Any, is_long: bool) -> bool:
        """A 1m CLOSE beyond the band (+ optional strong close). NOTE the polarity
        flip vs the entry-side confirm: a LONG SUPPORT break is bearish → strong
        close near the candle LOW (_strong_close is_long=False); a SHORT RESISTANCE
        break is bullish → strong close near the HIGH (is_long=True)."""
        close = float(candle.close)
        buf = self._break_buffer_pct / 100.0
        if is_long:
            broke = close < zone.band_low * (1.0 - buf)
            strong = _strong_close(candle, self._break_frac, is_long=False)
        else:
            broke = close > zone.band_high * (1.0 + buf)
            strong = _strong_close(candle, self._break_frac, is_long=True)
        if not broke:
            return False
        if self._require_strong and not strong:
            return False
        return True

    def _exit_on_break(self, trade: Any, candle: Any, zone: Any, is_long: bool) -> None:
        """SE5: EXITING → cancel resting SL+TGT → reverse-aware flatten. STRICT
        order; abort (and retry next candle) if EXITING cannot be marked."""
        trade_id = trade["trade_id"]
        symbol = trade["symbol"]
        self._log.warning(
            "structure_exit.confirmed_break",
            extra={
                "trade_id": trade_id, "symbol": symbol,
                "direction": "LONG" if is_long else "SHORT",
                "close": float(candle.close),
                "band_low": zone.band_low, "band_high": zone.band_high,
            },
        )

        # 1. EXITING FIRST. Without it the reconciler's G5b/CHECK1 could re-arm an
        #    SL mid-exit; if the write fails we keep the original SL and bail.
        if not self._mark_exiting(trade_id):
            self._log.error(
                "structure_exit.exit_aborted_no_exiting_mark",
                extra={"trade_id": trade_id, "symbol": symbol},
            )
            return

        # 2. cancel resting SL + TGT (so a late leg-fill can't re-open a naked pos).
        self._cancel_trade_resting_exits(trade_id)

        # 3. reverse-aware flatten.
        self._flatten_one(trade, symbol, is_long)

        # best-effort alert.
        if self._notifier is not None:
            try:
                self._notifier.send(
                    severity="WARNING",
                    title=f"[{self._mode}] STRUCTURE BREAK EXIT — {symbol}",
                    body=(
                        f"Trade: {trade_id}\n"
                        f"Direction: {'LONG' if is_long else 'SHORT'}\n"
                        f"1m close {float(candle.close):.2f} broke "
                        f"{'support' if is_long else 'resistance'} "
                        f"[{zone.band_low:.2f}, {zone.band_high:.2f}]\n"
                        f"EXITING → cancel SL/TGT → marketable flatten"
                    ),
                    source_module="structure_exit_manager",
                )
            except Exception:  # noqa: BLE001
                pass

    def _mark_exiting(self, trade_id: str) -> bool:
        """Mark the trade EXITING (mirror kill_switch.py:956-963). Returns success."""
        try:
            with self._store.transaction() as cur:
                cur.execute(
                    "UPDATE trades SET status = ?, updated_at = ? WHERE trade_id = ?",
                    ("EXITING", self._now_iso(), trade_id),
                )
            return True
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "structure_exit.mark_exiting_failed",
                extra={"trade_id": trade_id, "error": str(exc)},
            )
            return False

    def _cancel_trade_resting_exits(self, trade_id: str) -> None:
        """Cancel the trade's resting SL + TGT before flatten (mirror
        kill_switch.py:971-1017). order_id is the broker-assigned PK."""
        try:
            rows = self._store.fetch_all(
                "SELECT order_id, leg FROM orders "
                "WHERE trade_id = ? AND leg IN ('SL','TGT') "
                "AND status NOT IN ('CANCELLED','FAILED','EXPIRED','COMPLETE')",
                (trade_id,),
            )
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                "structure_exit: could not query resting exits for %s: %s", trade_id, exc
            )
            return
        cancelled = 0
        for r in rows or []:
            try:
                oid = r["order_id"]
            except (KeyError, IndexError, TypeError):
                continue
            if not oid:
                continue
            try:
                self._adapter.cancel_order(oid)
            except Exception as exc:  # noqa: BLE001
                self._log.warning(
                    "structure_exit: cancel resting %s order %s failed: %s",
                    r["leg"], oid, exc,
                )
                continue
            try:
                with self._store.transaction() as cur:
                    cur.execute(
                        "UPDATE orders SET status = 'CANCELLED', updated_at = ? "
                        "WHERE order_id = ?",
                        (self._now_iso(), oid),
                    )
            except Exception:  # noqa: BLE001
                pass  # broker cancel is what matters; reconciler finalizes DB
            cancelled += 1
        if cancelled:
            self._log.info(
                "structure_exit: cancelled %d resting exit order(s) for %s before flatten",
                cancelled, trade_id,
            )

    def _flatten_one(self, trade: Any, symbol: str, is_long: bool) -> None:
        """Reverse-aware single-position flatten: marketable LIMIT (LTP ± emergency
        buffer), MARKET fallback. Parity-safe — exit_side is derived from the local
        trade direction (broker qty sign differs paper vs live), qty prefers broker
        truth, and a broker-flat symbol is skipped (gap backstop, no double-exit).
        Mirrors order_reconciler._flatten_broker_position / eod._place_marketable_
        limit_exit. Best-effort — never raises."""
        trade_id = trade["trade_id"]
        held_qty = self._broker_mis_qty(symbol)
        if held_qty is not None and held_qty == 0:
            self._log.info(
                "structure_exit: %s already flat at broker — skipping flatten "
                "(no double-exit)", symbol,
            )
            return

        local_qty = abs(int(trade["qty_filled"] or 0))
        use_qty = held_qty if (held_qty is not None and held_qty > 0) else local_qty
        if use_qty <= 0:
            self._log.warning("structure_exit: no qty to flatten for %s", symbol)
            return

        exit_side = "SELL" if is_long else "BUY"
        tick = self._tick_for(symbol)
        ltp = self._ltp(symbol, fallback=0.0)
        if ltp and ltp > 0:
            price = marketable_limit_price(exit_side, ltp, EMERGENCY_EXIT_BUFFER_PCT, tick)
            order_type = "LIMIT"
        else:
            price = 0.0
            order_type = "MARKET"

        try:
            placed = self._adapter.place_order(
                symbol=symbol,
                side=exit_side,
                qty=use_qty,
                price=price,
                order_type=order_type,
                intent="INTRADAY",
                tag=truncate_tag_for_broker("STRUCT_EXIT"),
            )
        except Exception as exc:  # noqa: BLE001
            self._log.critical(
                "structure_exit: flatten place_order failed for %s: %s", symbol, exc
            )
            return

        # Hand off to order_monitor (leg='EOD') so the fill flows through to close
        # the trade — identical to the EOD squareoff exit path.
        if self._order_monitor is not None:
            try:
                self._order_monitor.track(
                    internal_order_id=placed.internal_order_id,
                    broker_order_id=placed.broker_order_id,
                    symbol=symbol,
                    side=exit_side,
                    qty=use_qty,
                    expected_price=placed.price,
                    placed_at=placed.ts,
                    leg="EOD",
                )
            except Exception as exc:  # noqa: BLE001
                self._log.warning(
                    "structure_exit: order_monitor.track failed for %s: %s", symbol, exc
                )

        # Persist the EOD exit leg (best-effort; mirrors eod_squareoff).
        try:
            db_price = price if order_type == "LIMIT" else None
            ts_iso = placed.ts.isoformat() if hasattr(placed.ts, "isoformat") else str(placed.ts)
            with self._store.transaction() as cur:
                cur.execute(
                    """
                    INSERT OR IGNORE INTO orders
                      (order_id, trade_id, leg, leg_index,
                       transaction_type, order_type, product, variety,
                       qty_requested, price, trigger_price,
                       status, qty_filled, avg_fill_price,
                       placed_at, updated_at)
                    VALUES (?, ?, 'EOD', 0, ?, ?, 'MIS', 'regular',
                            ?, ?, NULL, 'OPEN', 0, NULL, ?, ?)
                    """,
                    (
                        placed.broker_order_id, trade_id, exit_side, order_type,
                        use_qty, db_price, ts_iso, ts_iso,
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            self._log.warning("structure_exit: EOD leg insert failed for %s: %s", symbol, exc)

        self._log.warning(
            "structure_exit.flattened",
            extra={
                "trade_id": trade_id, "symbol": symbol, "side": exit_side,
                "qty": use_qty, "order_type": order_type, "price": price,
                "broker_order_id": getattr(placed, "broker_order_id", None),
            },
        )

    # ── ACTION A: trail SL to structure (tighten-only, once per candle) ───────

    def _trail_sl(self, trade: Any, candle: Any, zone: Any, is_long: bool) -> None:
        trade_id = trade["trade_id"]
        symbol = trade["symbol"]
        candle_ts = self._candle_ts(candle)

        # SE6: per-candle debounce (Action A only).
        with self._lock:
            if self._last_sl_move_candle_ts.get(trade_id) == candle_ts:
                return

        tick = self._tick_for(symbol)
        if is_long:
            new_sl_raw = zone.band_low * (1.0 - self._sl_buffer_pct / 100.0)
        else:
            new_sl_raw = zone.band_high * (1.0 + self._sl_buffer_pct / 100.0)
        new_sl = round_to_tick(new_sl_raw, tick, mode="nearest")

        sl_row = self._get_sl_leg(trade_id)
        if sl_row is None:
            return  # no live static SL leg — nothing to trail (defensive; SE1)
        order_id = sl_row["order_id"]
        current_sl = float(sl_row["trigger_price"] or sl_row["price"] or 0.0)
        if current_sl <= 0:
            return

        # SE6: ONLY-TIGHTEN — never widen risk.
        if is_long and not (new_sl > current_sl):
            return
        if (not is_long) and not (new_sl < current_sl):
            return

        # WRONG-SIDE pre-check (root-cause; mirror smart_tgt_manager.py:861-878).
        # A LONG SL must sit below LTP, a SHORT SL above it — else the move would
        # be an instant stop-out. Pre-check; do NOT send-and-let-broker-reject.
        ltp = self._ltp(symbol, fallback=float(candle.close))
        if is_long and ltp <= new_sl:
            self._log.warning(
                "structure_exit.skip_wrong_side",
                extra={"trade_id": trade_id, "symbol": symbol, "side": "LONG",
                       "ltp": ltp, "new_sl": new_sl},
            )
            return
        if (not is_long) and ltp >= new_sl:
            self._log.warning(
                "structure_exit.skip_wrong_side",
                extra={"trade_id": trade_id, "symbol": symbol, "side": "SHORT",
                       "ltp": ltp, "new_sl": new_sl},
            )
            return

        exit_side = "SELL" if is_long else "BUY"
        new_limit = calc_sl_limit_price(
            exit_side, new_sl, self._sl_limit_offset_pct, tick_size=tick,
        )
        try:
            result = self._adapter.modify_order(
                order_id, price=new_limit, trigger_price=new_sl, symbol=symbol,
            )
        except Exception as exc:  # noqa: BLE001
            self._log.warning(
                "structure_exit.modify_raised",
                extra={"trade_id": trade_id, "symbol": symbol, "error": str(exc)},
            )
            return

        if getattr(result, "success", False):
            with self._lock:
                self._last_sl_move_candle_ts[trade_id] = candle_ts
            self._log.info(
                "structure_exit.sl_trailed",
                extra={
                    "trade_id": trade_id, "symbol": symbol,
                    "direction": "LONG" if is_long else "SHORT",
                    "old_sl": round(current_sl, 2), "new_sl": round(new_sl, 2),
                },
            )
        else:
            # Leave the prior SL active; do not crash, do not mark the debounce.
            self._log.warning(
                "structure_exit.modify_failed",
                extra={
                    "trade_id": trade_id, "symbol": symbol, "new_sl": new_sl,
                    "reason": getattr(result, "reason", ""),
                },
            )

    # ── helpers ──────────────────────────────────────────────────────────────

    def _get_sl_leg(self, trade_id: str) -> Optional[Any]:
        """The live static SL leg row (order_id + current trigger/limit). order_id
        is the broker-assigned PK (the adapter's modify target)."""
        try:
            return self._store.fetch_one(
                "SELECT order_id, trigger_price, price FROM orders "
                "WHERE trade_id = ? AND leg = 'SL' "
                "AND status NOT IN ('CANCELLED','COMPLETE','REJECTED','FAILED') "
                "ORDER BY placed_at LIMIT 1",
                (trade_id,),
            )
        except Exception as exc:  # noqa: BLE001
            self._log.error(
                "structure_exit.sl_lookup_failed",
                extra={"trade_id": trade_id, "error": str(exc)},
            )
            return None

    def _broker_mis_qty(self, symbol: str) -> Optional[int]:
        """Absolute MIS qty for ``symbol`` at the broker, or None on fetch failure
        (caller errs toward flattening). 0 = broker reports flat. Parity: live qty
        is signed, paper qty already abs — abs() covers both."""
        try:
            positions = self._adapter.get_positions()
        except Exception as exc:  # noqa: BLE001
            self._log.warning("structure_exit: get_positions failed (flatten gate): %s", exc)
            return None
        for p in positions or []:
            if getattr(p, "symbol", "") == symbol and getattr(p, "product", "") == "MIS":
                return abs(int(getattr(p, "qty", 0) or 0))
        return 0

    def _ltp(self, symbol: str, fallback: float) -> float:
        """Current LTP via adapter.get_quote (bare-symbol key). Fallback on any
        miss so a quote hiccup never blocks a guard."""
        try:
            quotes = self._adapter.get_quote([symbol])
        except Exception:  # noqa: BLE001
            return fallback
        q = (quotes or {}).get(symbol)
        if q is None:
            return fallback
        lp = float(getattr(q, "last_price", 0.0) or 0.0)
        return lp if lp > 0 else fallback

    def _tick_for(self, symbol: str) -> float:
        """Instrument tick for ``symbol`` (DEFAULT_TICK fallback) — mirror
        breakeven_manager.py:104-112."""
        if self._instrument_cache is None:
            return DEFAULT_TICK
        try:
            t = self._instrument_cache.tick_size(symbol)
            return t if t and t > 0 else DEFAULT_TICK
        except Exception:  # noqa: BLE001
            return DEFAULT_TICK

    def _candle_ts(self, candle: Any) -> str:
        ts = getattr(candle, "ts", None)
        if ts is None:
            return ""
        try:
            return ts.isoformat()
        except Exception:  # noqa: BLE001
            return str(ts)

    def _now_iso(self) -> str:
        try:
            return self._now_fn().isoformat()
        except Exception:  # noqa: BLE001
            from core.time_authority import now_ist
            return now_ist().isoformat()
