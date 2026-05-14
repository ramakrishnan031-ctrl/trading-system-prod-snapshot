"""
orders/eod_squareoff.py -- Trading System v2

Purpose:
    Square off all open intraday positions and cancel pending intraday entry
    orders at the EOD square-off time (15:17 IST per P1). Places staggered
    MARKET exit orders. Publishes EodSquareoffComplete. Owns the per-date
    "already fired" flag (P1_market_windows_api).

Locked Design Decisions:
    EOD1  -- Purpose: cancel pending INTRADAY/CO entry orders, exit filled
             INTRADAY/CO positions with MARKET orders. Publishes
             PositionClosed via order_monitor fill flow (not directly).
    EOD2  -- Constructor: adapter, state_store, fund_manager, state_machine,
             bus, market_windows, time_authority, kill_switch, logger,
             order_monitor (optional), inter_order_delay_ms=500.
    EOD3  -- "Already fired" flag: _fired_for_date dict[date, bool].
             check_and_fire(now) is idempotent. Caller polls; module fires once.
    EOD4  -- No internal scheduler thread. Exposes check_and_fire() for
             external polling. Optional start_polling() daemon thread helper.
    EOD5  -- Fire sequence: soft_kill -> cancel entries -> exit positions ->
             log summary -> publish EodSquareoffComplete -> optional resume.
    EOD6  -- DELIVERY (CNC) positions NOT touched. Only INTRADAY (MIS) and
             COVER_ORDER (CO) products exited.
    EOD7  -- Fill confirmation out of scope. Hands off to order_monitor.
    EOD8  -- State persistence: eod_squareoff_log table. One row per fire.
    EOD9  -- Restart recovery: if log row exists with failures, log WARNING.
             Recovery-fire only if no log row AND now past EOD AND before 15:30.
    EOD10 -- fire_now(reason, triggered_by) for emergency/manual fire.
    EOD11 -- Layer 5 (orders/). Imports: stdlib, core.*, broker.*, capital.*.
    EOD12 -- Config: eod_squareoff section in SystemConfig.

What This Module Does NOT Do:
    - Does NOT touch DELIVERY (CNC) positions (EOD6)
    - Does NOT wait for fill confirmations (EOD7; order_monitor handles)
    - Does NOT own its own scheduler thread by default (EOD4)
    - Does NOT call fund_manager on exit fills (order_monitor's flow)
    - Does NOT import from signals/ or screening/
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional, TYPE_CHECKING

from broker.order_state_machine import OrderStateMachine
from broker.zerodha_adapter import ZerodhaAdapter
from capital.fund_manager import FundManager
from capital.kill_switch import KillSwitch, KillState
from core.events import EodSquareoffComplete, EventBus
from core.exceptions import BrokerError
from core.logger import log_exception
from core.market_windows import MarketWindows
from core.state_store import StateStore
from core.time_authority import now_ist

if TYPE_CHECKING:
    from broker.order_monitor import OrderMonitor

# ─────────────────────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EodFireResult:
    """Summary result from a single EOD fire (EOD5, EOD10)."""
    fired_date: str             # YYYY-MM-DD IST
    fired_at: str               # ISO-8601 IST
    positions_attempted: int
    positions_succeeded: int
    positions_failed: int
    cancels_attempted: int
    cancels_succeeded: int
    cancels_failed: int
    duration_sec: float
    recovery_fire: bool = False  # True if fired by EOD9 recovery path


# ─────────────────────────────────────────────────────────────────────────────
# EodSquareoff
# ─────────────────────────────────────────────────────────────────────────────

class EodSquareoff:
    """
    Squares off all open intraday positions at EOD time.

    Caller drives timing: poll check_and_fire(now_ist()) every few seconds.
    Fires at most once per trading day per instance (EOD3).

    Usage::
        eod = EodSquareoff(adapter, store, fund_manager, state_machine,
                           bus, market_windows, time_authority, kill_switch,
                           logger, order_monitor=order_monitor)
        # In main loop or scheduler:
        eod.check_and_fire(now_ist())
    """

    def __init__(
        self,
        adapter: ZerodhaAdapter,
        state_store: StateStore,
        fund_manager: FundManager,
        state_machine: OrderStateMachine,
        bus: EventBus,
        market_windows: MarketWindows,
        time_authority: object,  # module with now_ist(); injected for testability
        kill_switch: KillSwitch,
        logger: logging.Logger,
        order_monitor: Optional["OrderMonitor"] = None,
        inter_order_delay_ms: int = 500,
        notifier: Optional[object] = None,  # TelegramNotifier; for EOD9 SKIPPED_LATE alert
        market_close: str = "15:30",        # IST HH:MM; hard stop for recovery fire
        mode: str = "LIVE",                 # session mode label for alert titles
        # Audit 3.3 + 5.2 (Phase B / B.1): EOD exit protocol
        exit_protocol: str = "MARKET",
        limit_aggressive_pct: float = 0.01,
        limit_grace_sec: float = 120.0,
        # FIX-046: entry_gate for clearing stale gate state at EOD
        entry_gate: Optional[object] = None,
    ) -> None:
        self._adapter = adapter
        self._store = state_store
        self._fm = fund_manager
        self._state_machine = state_machine
        self._bus = bus
        self._mw = market_windows
        self._ta = time_authority
        self._ks = kill_switch
        self._log = logger
        self._order_monitor = order_monitor
        self._inter_order_delay_sec = inter_order_delay_ms / 1000.0
        self._notifier = notifier
        self._market_close_time = datetime.strptime(market_close, "%H:%M").time()
        self._mode = mode
        # Audit 3.3 + 5.2: EOD exit protocol
        self._exit_protocol = exit_protocol
        self._limit_aggressive_pct = limit_aggressive_pct
        self._limit_grace_sec = limit_grace_sec
        # FIX-046: entry_gate reference
        self._entry_gate = entry_gate

        # EOD3: per-date "already fired" flag
        self._fired_for_date: dict[date, bool] = {}
        self._lock = threading.Lock()

        # Track whether WE set soft_kill (so we can resume safely)
        self._we_set_soft_kill: bool = False

        # H-7: _check_restart_recovery() DEFERRED to post_wire_init(). It can
        # recovery-fire, which publishes EodSquareoffComplete on the bus; if
        # we fired in __init__ the bus would have no subscribers yet (main.py
        # wires bus.subscribe AFTER constructing EodSquareoff). Caller MUST
        # call post_wire_init() once all bus subscriptions are in place.

    # ── public API ────────────────────────────────────────────────────────────

    def post_wire_init(self) -> None:
        """
        H-7: Finalize init after the caller has wired all bus subscriptions.
        Runs the EOD9 restart-recovery check, which may publish
        EodSquareoffComplete; calling this before subscribers are registered
        would silently drop the event.
        """
        self._check_restart_recovery()

    def check_and_fire(self, now: datetime) -> bool:
        """
        Check if EOD square-off is due and fire if so (EOD3).

        Args:
            now: current IST datetime (from time_authority.now_ist()).

        Returns:
            True  -- EOD was triggered this call (first fire for the date).
            False -- Not yet due, already fired today, or trading holiday.

        Thread-safe: caller may poll from any thread.
        """
        if not self._mw.is_eod_squareoff_due(now):
            return False
        if self._mw.is_trading_holiday(now):
            return False

        today = now.date()
        with self._lock:
            if self._fired_for_date.get(today, False):
                return False
            # Claim the slot atomically to prevent concurrent double-fire.
            # If _fire() later raises, we reset the flag inside the except block
            # so check_and_fire() can retry on the next poll.
            self._fired_for_date[today] = True

        try:
            self._fire(now, recovery_fire=False)
        except Exception:
            # _fire() failed; reset flag so the next poll can retry.
            with self._lock:
                self._fired_for_date[today] = False
            raise
        return True

    def fire_now(self, reason: str, triggered_by: str) -> EodFireResult:
        """
        Emergency / manual fire. Bypasses time and holiday checks (EOD10).

        Logs CRITICAL with reason. Marks _fired_for_date[today] to prevent
        check_and_fire() from firing again the same day.

        Args:
            reason:       Human-readable reason for the manual fire.
            triggered_by: Module or operator that triggered this.

        Returns:
            EodFireResult with full summary.
        """
        now = now_ist()
        self._log.critical(
            "EOD fire_now called: reason=%s triggered_by=%s", reason, triggered_by
        )
        today = now.date()
        with self._lock:
            self._fired_for_date[today] = True

        return self._fire(now, recovery_fire=False)

    def start_polling(self, poll_interval_sec: int = 5) -> None:
        """
        Start a daemon thread that calls check_and_fire() every poll_interval_sec
        seconds (EOD4). Useful in environments without an external scheduler.
        Safe to call multiple times; only one polling thread is started.
        """
        with self._lock:
            if getattr(self, "_polling_thread", None) is not None:
                return  # Already started

        def _poll_loop() -> None:
            while True:
                try:
                    self.check_and_fire(now_ist())
                except Exception as exc:  # noqa: BLE001
                    log_exception(self._log, exc)
                time.sleep(poll_interval_sec)

        t = threading.Thread(target=_poll_loop, daemon=True, name="eod_squareoff_poll")
        with self._lock:
            self._polling_thread = t
        t.start()
        self._log.info("EOD squareoff polling thread started (interval=%ds)", poll_interval_sec)

    # ── internal implementation ───────────────────────────────────────────────

    def _fire(self, now: datetime, *, recovery_fire: bool) -> EodFireResult:
        """
        Execute the full EOD square-off sequence (EOD5).

        Called by check_and_fire() and fire_now() with the lock already ensuring
        single execution. Also called by _check_restart_recovery() for the
        recovery-fire path (EOD9).
        """
        fired_at = now_ist()
        fired_date_str = fired_at.date().isoformat()
        start_ts = time.monotonic()

        self._log.info("EOD square-off triggered for %s", fired_date_str)

        # Step 0: FIX-046 - Clear gate state to prevent stale signal rehydration
        if self._entry_gate is not None:
            try:
                cleared_count = self._entry_gate.clear_all()
                self._log.info(f"FIX-046: cleared {cleared_count} gate entries at EOD")
            except Exception as exc:  # noqa: BLE001
                self._log.error(f"FIX-046: gate clear_all() failed: {exc}")
                # Continue with EOD sequence even if gate clear fails

        # M-3 (write-ahead): mark IN_PROGRESS before doing anything. A crash
        # between here and the COMPLETE update leaves the row IN_PROGRESS,
        # which _check_restart_recovery() treats as "recover". Recovery of a
        # recovery that also fails stays IN_PROGRESS and alerts the operator
        # (no auto-retry loop).
        try:
            self._store.insert_eod_squareoff_log_start(
                fired_date=fired_date_str,
                fired_at=fired_at.isoformat(),
            )
        except Exception as exc:  # noqa: BLE001
            # Log but proceed: we'd rather do the squareoff than abort it
            # because we couldn't write the write-ahead row. The final
            # COMPLETE update will INSERT OR REPLACE via the helper below
            # (insert_eod_squareoff_log), so state is still durable.
            log_exception(self._log, exc)
            self._log.error(
                "EOD_WRITEAHEAD_FAILED: proceeding with squareoff; "
                "fired_date=%s error=%s",
                fired_date_str, exc,
            )

        # Step 2: soft_kill to block new entries during square-off (EOD5)
        self._we_set_soft_kill = False
        if not self._ks.is_active("any"):
            self._ks.soft_kill(
                reason="EOD_SQUAREOFF",
                triggered_by="eod_squareoff",
            )
            self._we_set_soft_kill = True
        else:
            self._log.info(
                "Kill switch already active (%s); skipping soft_kill",
                self._ks.current_state().value,
            )

        # Step 3: cancel pending intraday entry orders (EOD5)
        c_attempted, c_succeeded, c_failed = self._cancel_pending_entries()

        # Step 3b (Audit #6): cancel pending SL/TGT legs for open positions
        # BEFORE firing MARKET exits. If we don't, a late TGT/SL fill after
        # the MARKET squareoff re-opens a naked reverse position overnight.
        # Failures here are logged but do not abort Step 4 -- the MARKET
        # exit still runs so positions don't ride through the gap.
        ec_attempted, ec_succeeded, ec_failed = self._cancel_pending_exit_legs()
        c_attempted  += ec_attempted
        c_succeeded  += ec_succeeded
        c_failed     += ec_failed

        # Step 4: exit open intraday positions (EOD5)
        p_attempted, p_succeeded, p_failed = self._exit_open_positions(
            now, recovery_fire=recovery_fire,
        )

        duration_sec = time.monotonic() - start_ts

        # Step 5: log summary (EOD5)
        self._log.info(
            "EOD summary: positions_squared=%d/%d cancels=%d/%d duration=%.2fs",
            p_succeeded, p_attempted,
            c_succeeded, c_attempted,
            duration_sec,
        )

        # Step 8: persist final counts to eod_squareoff_log (EOD8)
        # M-3: transition the write-ahead IN_PROGRESS row to COMPLETE. If the
        # write-ahead INSERT at _fire() start failed for any reason, fall back
        # to insert_eod_squareoff_log (which INSERT OR REPLACE covers the gap).
        completed_at_iso = now_ist().isoformat()
        try:
            self._store.update_eod_squareoff_log_complete(
                fired_date=fired_date_str,
                positions_attempted=p_attempted,
                positions_succeeded=p_succeeded,
                positions_failed=p_failed,
                cancels_attempted=c_attempted,
                cancels_succeeded=c_succeeded,
                cancels_failed=c_failed,
                duration_sec=duration_sec,
                completed_at=completed_at_iso,
            )
        except Exception as exc:  # noqa: BLE001
            log_exception(self._log, exc)
            # Fall back: single-shot write if UPDATE failed (e.g., row missing
            # because write-ahead also failed). Guarantees we always have a
            # COMPLETE row for this date.
            try:
                self._store.insert_eod_squareoff_log(
                    fired_date=fired_date_str,
                    fired_at=fired_at.isoformat(),
                    positions_attempted=p_attempted,
                    positions_succeeded=p_succeeded,
                    positions_failed=p_failed,
                    cancels_attempted=c_attempted,
                    cancels_succeeded=c_succeeded,
                    cancels_failed=c_failed,
                    duration_sec=duration_sec,
                )
            except Exception as exc2:  # noqa: BLE001
                self._log.critical(
                    "EOD_LOG_PERSIST_FAILED: fired_date=%s error1=%s error2=%s",
                    fired_date_str, exc, exc2,
                )

        # Step 5c: FIX-047 - WAL checkpoint to reclaim disk space
        try:
            checkpoint_result = self._store.checkpoint()
            self._log.info(
                f"FIX-047: WAL checkpoint complete - "
                f"busy={checkpoint_result['busy']} "
                f"log={checkpoint_result['log']} "
                f"checkpointed={checkpoint_result['checkpointed']}"
            )
        except Exception as exc:  # noqa: BLE001
            self._log.error(f"FIX-047: WAL checkpoint failed: {exc}")
            # Continue - checkpoint failure should not abort EOD

        # Step 6: publish EodSquareoffComplete event (EOD5)
        try:
            self._bus.publish(
                EodSquareoffComplete(
                    source_module="eod_squareoff",
                    fired_date=fired_date_str,
                    positions_attempted=p_attempted,
                    positions_succeeded=p_succeeded,
                    positions_failed=p_failed,
                    cancels_attempted=c_attempted,
                    cancels_succeeded=c_succeeded,
                    cancels_failed=c_failed,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log_exception(self._log, exc)

        # Step 7: Reset daily PnL in fund_manager (FM14)
        try:
            self._fm.reset_daily_pnl()
            self._log.info("eod_squareoff: fund_manager daily PnL reset")
        except Exception as exc:  # noqa: BLE001
            self._log.error("eod_squareoff: reset_daily_pnl failed: %s", exc)

        # Step 8: resume kill_switch ONLY if WE set it (EOD5)
        if self._we_set_soft_kill:
            try:
                self._ks.resume(
                    reason="EOD_SQUAREOFF_COMPLETE",
                    resumed_by="eod_squareoff",
                )
            except Exception as exc:  # noqa: BLE001
                log_exception(self._log, exc)

        result = EodFireResult(
            fired_date=fired_date_str,
            fired_at=fired_at.isoformat(),
            positions_attempted=p_attempted,
            positions_succeeded=p_succeeded,
            positions_failed=p_failed,
            cancels_attempted=c_attempted,
            cancels_succeeded=c_succeeded,
            cancels_failed=c_failed,
            duration_sec=duration_sec,
            recovery_fire=recovery_fire,
        )

        # Telegram alert: EOD DAILY SUMMARY (optional; never crash).
        if self._notifier is not None:
            try:
                self._send_daily_summary(fired_date_str)
            except Exception as exc:  # noqa: BLE001
                log_exception(self._log, exc)
                self._log.error("EOD daily summary alert failed: %s", exc)

        return result

    # ------------------------------------------------------------------
    # Daily summary (new)
    # ------------------------------------------------------------------

    def _send_daily_summary(self, date_str: str) -> None:
        """Build and send the EOD daily summary Telegram alert.

        Pulls closed trades for date_str from the trades table and reports
        net P&L, win rate, best/worst trade, strategy breakdown, and a
        rough Smart TGT split derived from order_protocol.
        """
        try:
            all_trades = self._store.get_trades_for_date(date_str)
        except Exception as exc:  # noqa: BLE001
            self._log.error("EOD_DAILY_SUMMARY fetch failed: %s", exc)
            return

        closed = [
            t for t in all_trades
            if (t.get("status") == "CLOSED") and (t.get("net_pnl") is not None)
        ]

        if not closed:
            body = (
                "\nNo closed trades today.\n"
                f"Attempted today: {len(all_trades)}"
            )
            self._notifier.send(
                severity="INFO",
                title=f"[{self._mode}] 📊 DAILY SUMMARY — {date_str}",
                body=body,
                source_module="eod_squareoff",
            )
            return

        total_pnl = sum(float(t["net_pnl"]) for t in closed)
        wins = [t for t in closed if float(t["net_pnl"]) > 0]
        losses = [t for t in closed if float(t["net_pnl"]) <= 0]
        win_n = len(wins)
        loss_n = len(losses)
        total_n = len(closed)
        win_rate_pct = (win_n / total_n * 100.0) if total_n else 0.0

        best = max(closed, key=lambda t: float(t["net_pnl"]))
        worst = min(closed, key=lambda t: float(t["net_pnl"]))

        best_pnl = float(best["net_pnl"])
        worst_pnl = float(worst["net_pnl"])
        best_reason = best.get("exit_reason") or "—"
        worst_reason = worst.get("exit_reason") or "—"

        # Avg R = avg of (net_pnl / risk_amount) across closed trades.
        r_values = []
        for t in closed:
            risk = t.get("risk_amount")
            try:
                risk_f = float(risk) if risk is not None else 0.0
            except Exception:
                risk_f = 0.0
            if risk_f > 0:
                r_values.append(float(t["net_pnl"]) / risk_f)
        avg_r = (sum(r_values) / len(r_values)) if r_values else 0.0

        # Capital used = sum of margin_reserved for closed trades.
        capital_used = 0.0
        for t in closed:
            mr = t.get("margin_reserved")
            try:
                capital_used += float(mr) if mr is not None else 0.0
            except (TypeError, ValueError) as exc:
                # LOG-1 (2026-04-26 audit): never silent on data conversion.
                self._log.warning(
                    "eod_squareoff.margin_float_failed",
                    extra={
                        "trade_id": t.get("trade_id"),
                        "mr": mr,
                        "error": str(exc),
                    },
                )

        # Strategy breakdown.
        strat_stats: dict = {}
        for t in closed:
            name = t.get("strategy") or "unknown"
            entry = strat_stats.setdefault(
                name, {"trades": 0, "wins": 0, "pnl": 0.0}
            )
            entry["trades"] += 1
            if float(t["net_pnl"]) > 0:
                entry["wins"] += 1
            entry["pnl"] += float(t["net_pnl"])

        # Smart TGT bucket breakdown: CO_PLUS_TGT = TRAIL-eligible, rest = FIXED.
        trail_n = sum(1 for t in closed if t.get("order_protocol") == "CO_PLUS_TGT")
        fixed_n = total_n - trail_n
        defend_n = 0   # not implemented

        pnl_sign = "+" if total_pnl >= 0 else "-"
        best_sign = "+" if best_pnl >= 0 else "-"
        worst_sign = "+" if worst_pnl >= 0 else "-"

        lines = [
            "",
            f"P&L: {pnl_sign}₹{abs(total_pnl):,.2f} | "
            f"Win rate: {win_rate_pct:.1f}% ({win_n}W {loss_n}L)",
            f"Best:  {best.get('symbol', '?')} "
            f"{best_sign}₹{abs(best_pnl):,.2f} ({best_reason})",
            f"Worst: {worst.get('symbol', '?')} "
            f"{worst_sign}₹{abs(worst_pnl):,.2f} ({worst_reason})",
            f"Avg R: {avg_r:+.2f} | Capital used: ₹{capital_used:,.2f}",
            "",
            "Strategies:",
        ]
        for name, s in strat_stats.items():
            s_wr = (s["wins"] / s["trades"] * 100.0) if s["trades"] else 0.0
            s_sign = "+" if s["pnl"] >= 0 else "-"
            lines.append(
                f"  {name} {s['trades']}T {s_wr:.0f}%WR "
                f"{s_sign}₹{abs(s['pnl']):,.2f}"
            )
        lines.append("")
        lines.append(
            f"Smart TGT: FIXED={fixed_n} TRAIL={trail_n} DEFEND={defend_n}"
        )

        self._notifier.send(
            severity="INFO",
            title=f"[{self._mode}] 📊 DAILY SUMMARY — {date_str}",
            body="\n".join(lines),
            source_module="eod_squareoff",
        )

    def _cancel_pending_entries(self) -> tuple[int, int, int]:
        """
        Cancel all pending intraday entry orders. Returns (attempted, succeeded, failed).

        For each cancellation:
          - Call adapter.cancel_order(broker_order_id)
          - On success: update trade status to CANCELLED, release reserved capital
          - On failure: log CRITICAL, leave for reconciler (EOD5 step 3)
        """
        rows = self._store.get_pending_intraday_orders()
        attempted = len(rows)
        succeeded = 0
        failed = 0

        for row in rows:
            trade_id = row["trade_id"]
            signal_id = row["signal_id"]
            broker_order_id = row["broker_order_id"]
            symbol = row["symbol"]

            try:
                result = self._adapter.cancel_order(broker_order_id)
                if not result.success:
                    self._log.critical(
                        "EOD cancel failed: trade_id=%s symbol=%s broker_order_id=%s reason=%s",
                        trade_id, symbol, broker_order_id, result.reason,
                    )
                    failed += 1
                    continue

                # Update trade AND order row status to CANCELLED in DB (HIGH #8)
                now_ts = now_ist().isoformat()
                with self._store.transaction() as cur:
                    cur.execute(
                        "UPDATE trades SET status = 'CANCELLED', updated_at = ? WHERE trade_id = ?",
                        (now_ts, trade_id),
                    )
                    cur.execute(
                        "UPDATE orders SET status = 'CANCELLED', updated_at = ? "
                        "WHERE order_id = ?",
                        (now_ts, broker_order_id),
                    )

                # Release reserved capital
                reservation_id = self._store.get_reservation_id_for_signal(signal_id)
                if reservation_id:
                    released = self._fm.release(reservation_id, "EOD_CANCEL")
                    if not released:
                        self._log.warning(
                            "EOD cancel: reservation_id %s not found in fund_manager "
                            "(already released?): trade_id=%s",
                            reservation_id, trade_id,
                        )
                else:
                    self._log.warning(
                        "EOD cancel: no reservation_id found for signal_id=%s trade_id=%s; "
                        "capital release skipped",
                        signal_id, trade_id,
                    )

                succeeded += 1
                self._log.info(
                    "EOD cancel OK: trade_id=%s symbol=%s broker_order_id=%s",
                    trade_id, symbol, broker_order_id,
                )

            except BrokerError as exc:
                log_exception(self._log, exc)
                self._log.critical(
                    "EOD cancel BrokerError: trade_id=%s symbol=%s broker_order_id=%s",
                    trade_id, symbol, broker_order_id,
                )
                failed += 1
            except Exception as exc:  # noqa: BLE001
                log_exception(self._log, exc)
                self._log.critical(
                    "EOD cancel unexpected error: trade_id=%s symbol=%s",
                    trade_id, symbol,
                )
                failed += 1

        return attempted, succeeded, failed

    def _cancel_pending_exit_legs(self) -> tuple[int, int, int]:
        """
        Audit #6: cancel live SL/TGT legs for all open intraday positions.

        The MARKET squareoff in Step 4 only closes the position; it does not
        touch the exit-leg orders waiting at the broker. If one of those
        legs later triggers, it places a fresh order in the opposite
        direction of the (now closed) position, leaving a naked overnight
        position. Cancelling them here is the fix.

        Returns (attempted, succeeded, failed). Per-row failures are logged
        CRITICAL but do not abort the iteration or the EOD sequence.
        """
        try:
            rows = self._store.get_pending_exit_orders_for_open_positions()
        except Exception as exc:  # noqa: BLE001
            log_exception(self._log, exc)
            self._log.critical(
                "EOD exit-leg fetch failed; skipping pre-cancel step; error=%s",
                exc,
            )
            return (0, 0, 0)

        attempted = len(rows)
        succeeded = 0
        failed = 0

        for row in rows:
            trade_id = row["trade_id"]
            symbol = row["symbol"]
            leg = row["leg"]
            variety = row["variety"] or "regular"
            broker_order_id = row["order_id"]

            try:
                result = self._adapter.cancel_order(
                    broker_order_id, variety=variety
                )
                if not result.success:
                    self._log.critical(
                        "EOD exit-leg cancel failed: trade_id=%s symbol=%s "
                        "leg=%s broker_order_id=%s reason=%s",
                        trade_id, symbol, leg, broker_order_id, result.reason,
                    )
                    failed += 1
                    continue

                now_ts = now_ist().isoformat()
                with self._store.transaction() as cur:
                    cur.execute(
                        "UPDATE orders SET status = 'CANCELLED', updated_at = ? "
                        "WHERE order_id = ?",
                        (now_ts, broker_order_id),
                    )

                succeeded += 1
                self._log.info(
                    "EOD exit-leg cancel OK: trade_id=%s symbol=%s "
                    "leg=%s broker_order_id=%s",
                    trade_id, symbol, leg, broker_order_id,
                )
            except BrokerError as exc:
                log_exception(self._log, exc)
                self._log.critical(
                    "EOD exit-leg cancel BrokerError: trade_id=%s "
                    "symbol=%s leg=%s broker_order_id=%s",
                    trade_id, symbol, leg, broker_order_id,
                )
                failed += 1
            except Exception as exc:  # noqa: BLE001
                log_exception(self._log, exc)
                self._log.critical(
                    "EOD exit-leg cancel unexpected error: trade_id=%s "
                    "symbol=%s leg=%s",
                    trade_id, symbol, leg,
                )
                failed += 1

        return attempted, succeeded, failed

    def _exit_open_positions(
        self,
        now: datetime,
        *,
        recovery_fire: bool = False,
    ) -> tuple[int, int, int]:
        """
        Place exit orders for all open intraday positions.
        Returns (attempted, succeeded, failed).

        For each position (sorted by symbol per Foundation 3.7):
          - CO bracket: cancel_order(variety="co") (Audit 3.1).
          - MIS / LIMIT_TRIPLE:
              * exit_protocol == "MARKET" (legacy): place MARKET exit.
              * exit_protocol == "LIMIT_THEN_MARKET" (Audit 3.3, default):
                  Phase 1: aggressive LIMIT (LTP +/- limit_aggressive_pct).
                  Phase 2 (after limit_grace_sec): query broker for
                  still-open positions; cancel residual LIMIT and place
                  MARKET for the remaining qty. This caps the 15:17
                  liquidity-vacuum slippage at the configured pct while
                  the auto-square at 15:20 is still avoided.
          - Register exit order with state_machine
          - Hand off to order_monitor (if injected)
          - Delay inter_order_delay_sec between orders (audit EOD5)
          - On failure: log CRITICAL, mark EOD_EXIT_FAILED in DB (EOD5 step 4d)

        Audit 5.2 (broker-authoritative qty): regardless of recovery_fire,
        we fetch adapter.get_positions() upfront and override row qty with
        the broker's truth. The DB row may be stale because a partial fill
        (PARTIAL fills get committed via Audit #7) hasn't been ingested
        yet. The broker is authoritative; on broker-fetch failure we fall
        back to the DB qty (better to over-square than to ride overnight).

        Audit #14: recovery_fire=True trims rows to what the broker still
        reports as open. (RMS auto-square race; see prior comment.)
        """
        rows = self._store.get_open_intraday_positions()

        # Audit 5.2: fetch broker positions ONCE upfront. Used for
        # (a) authoritative qty (always) and (b) recovery-mode filter.
        # On failure: skip both filter and qty override (legacy behaviour).
        # broker_qty=None signals "fetch failed -> trust DB"; {} signals
        # "fetch ok, no positions -> all DB rows are stale-closed".
        broker_qty: Optional[dict[str, int]] = None
        try:
            broker_positions = self._adapter.get_positions()
            # FIX-015: Filter to intraday products only (MIS/CO). EOD6 design
            # mandates DELIVERY (CNC/NRML) positions are never touched. In live
            # mode, broker may report both intraday and delivery positions; we
            # must exclude delivery to avoid using their qty or symbol presence
            # in the position-filter logic below.
            broker_qty = {
                p.symbol: abs(int(p.qty))
                for p in broker_positions
                if int(p.qty) != 0 and p.product in ("MIS", "CO")
            }
        except Exception as exc:  # noqa: BLE001
            log_exception(self._log, exc)
            self._log.critical(
                "EOD: get_positions failed; falling back to DB view for "
                "qty + recovery filter; error=%s",
                exc,
            )

        # E.5 (2026-04-25): broker-position filter applies to ALL fires, not
        # just recovery_fire. If a position has been closed by RMS or by an
        # earlier exit fill we did not yet ingest, the DB row is stale and
        # firing a MARKET reverse on it creates a naked short. The recovery
        # comment below remains accurate for that path; the filter is now
        # also a guard against this stale-DB-row class on the regular fire.
        # Skip the filter only when fetch failed (broker_qty is None);
        # fetch-ok-with-no-open-symbols correctly trims to [].
        if broker_qty is not None:
            before = len(rows)
            skipped = [r["symbol"] for r in rows if r["symbol"] not in broker_qty]
            rows = [r for r in rows if r["symbol"] in broker_qty]
            for sym in skipped:
                self._log.warning(
                    "EOD: skipping symbol %s — broker reports zero/missing "
                    "position (likely closed by RMS or earlier exit fill); "
                    "MARKET reverse would create a naked short. "
                    "recovery_fire=%s",
                    sym, recovery_fire,
                )
            self._log.info(
                "EOD: broker-position filter kept %d/%d trades "
                "(broker open symbols=%d, recovery_fire=%s)",
                len(rows), before, len(broker_qty), recovery_fire,
            )

        attempted = len(rows)
        succeeded = 0
        failed = 0
        # Audit 3.3: track placed LIMITs so the post-grace MARKET sweep can
        # cancel them and re-fire as MARKET. {trade_id: (broker_order_id,
        # symbol, exit_side, requested_qty)}.
        pending_limits: dict[str, tuple[str, str, str, int]] = {}

        # Audit 3.3: batch-fetch LTPs for the LIMIT branch. One call covers
        # all MIS/LIMIT_TRIPLE symbols. CO symbols are present too -- harmless
        # extra symbols, the dict lookup just goes unused. On failure (whole
        # call raises or returns partial) we fall back per-symbol to MARKET.
        ltp_map: dict[str, float] = {}
        if self._exit_protocol == "LIMIT_THEN_MARKET" and rows:
            ltp_symbols = sorted({r["symbol"] for r in rows})
            try:
                quotes = self._adapter.get_quote(ltp_symbols)
                ltp_map = {
                    sym: float(q.last_price)
                    for sym, q in (quotes or {}).items()
                    if float(getattr(q, "last_price", 0.0)) > 0
                }
            except Exception as exc:  # noqa: BLE001
                log_exception(self._log, exc)
                self._log.critical(
                    "EOD: get_quote failed; falling back to MARKET for all "
                    "exits this fire; error=%s",
                    exc,
                )

        for i, row in enumerate(rows):
            trade_id = row["trade_id"]
            symbol = row["symbol"]
            direction = row["direction"]
            qty = row["qty_filled"]
            order_protocol = (row["order_protocol"] or "").upper()
            entry_broker_id = row["entry_broker_order_id"] or ""
            entry_variety = (row["entry_variety"] or "regular").lower()

            # Audit 3.1: CO positions cannot be squared off with a reverse
            # MARKET -- Zerodha rejects and auto-squares at 15:20 with a
            # ₹50+GST penalty. The correct path is cancel_order(variety="co")
            # on the CO entry bracket; the broker collapses the bracket and
            # closes the position at market.
            is_co = (order_protocol == "CO_PLUS_TGT") and (entry_variety == "co")

            # Determine exit side: LONG position -> SELL exit; SHORT -> BUY
            exit_side = "SELL" if direction == "LONG" else "BUY"

            try:
                if is_co:
                    if not entry_broker_id:
                        # Defensive: a CO trade with no ENTRY broker_order_id
                        # cannot be cancelled -- fall through to MARKET reverse
                        # (the broker will likely reject; we'll mark the trade
                        # EOD_EXIT_FAILED and let reconciler pick up the pieces).
                        self._log.critical(
                            "EOD CO cancel: no entry_broker_order_id for "
                            "CO_PLUS_TGT trade; CO_SQUAREOFF_NO_BROKER_ID "
                            "trade_id=%s symbol=%s",
                            trade_id, symbol,
                        )
                        raise BrokerError(
                            f"CO trade {trade_id} has no entry_broker_order_id"
                        )

                    # Cancel the CO bracket -- broker exits the position.
                    cancel_result = self._adapter.cancel_order(
                        entry_broker_id, variety="co",
                    )
                    if not getattr(cancel_result, "success", False):
                        reason = getattr(cancel_result, "reason", "") or "rejected"
                        self._log.critical(
                            "EOD CO cancel rejected by broker: "
                            "CO_SQUAREOFF_CANCEL_REJECTED "
                            "trade_id=%s symbol=%s broker_order_id=%s reason=%s",
                            trade_id, symbol, entry_broker_id, reason,
                        )
                        self._mark_exit_failed(trade_id)
                        failed += 1
                    else:
                        # Record the cancel as the EOD exit marker in orders
                        # table (single CANCEL leg row). order_monitor will
                        # pick up CANCELLED + qty_filled=0 on poll OR
                        # CANCELLED-with-partial-fill which flows through
                        # _on_order_status_changed (Audit #7) to release
                        # capital.
                        now_str = now_ist().isoformat()
                        try:
                            with self._store.transaction() as cur:
                                cur.execute(
                                    """
                                    INSERT OR IGNORE INTO orders
                                      (order_id, trade_id, leg, leg_index,
                                       transaction_type, order_type, product, variety,
                                       qty_requested, price, trigger_price,
                                       status, qty_filled, avg_fill_price,
                                       placed_at, updated_at)
                                    VALUES (?, ?, 'EOD', 0, ?, 'CANCEL', 'CO', 'co',
                                            ?, NULL, NULL, 'OPEN', 0, NULL, ?, ?)
                                    """,
                                    (
                                        f"{entry_broker_id}_CO_CANCEL", trade_id,
                                        exit_side, qty, now_str, now_str,
                                    ),
                                )
                        except Exception as db_exc:  # noqa: BLE001
                            log_exception(self._log, db_exc)
                            self._log.warning(
                                "EOD CO cancel: DB record insert failed "
                                "(cancel already issued to broker) trade_id=%s",
                                trade_id,
                            )
                        succeeded += 1
                        self._log.info(
                            "EOD CO cancel OK: trade_id=%s symbol=%s "
                            "broker_order_id=%s (variety=co)",
                            trade_id, symbol, entry_broker_id,
                        )
                    # Fall through to inter-order delay.

                else:
                    # MIS / LIMIT_TRIPLE branch.
                    #
                    # Audit 5.2: prefer broker truth for qty. The DB row may
                    # reflect a stale qty if a partial fill hasn't been
                    # ingested yet; broker positions are authoritative.
                    # broker_qty is None only on fetch-failure; in that case
                    # we fall back to the DB qty.
                    if broker_qty is None:
                        use_qty = qty
                    else:
                        use_qty = broker_qty.get(symbol, 0)
                    if use_qty <= 0:
                        # Broker shows position already closed -- nothing to
                        # exit. Skip without marking failed.
                        self._log.info(
                            "EOD exit skip: trade_id=%s symbol=%s broker shows "
                            "qty=0 (already closed)",
                            trade_id, symbol,
                        )
                        # Don't increment succeeded/failed; just skip.
                        if i < len(rows) - 1 and self._inter_order_delay_sec > 0:
                            time.sleep(self._inter_order_delay_sec)
                        continue

                    # Audit 3.3: select order_type/price by protocol.
                    if self._exit_protocol == "LIMIT_THEN_MARKET":
                        ltp = ltp_map.get(symbol, 0.0)
                        if ltp > 0:
                            if exit_side == "SELL":
                                limit_px = round(
                                    ltp * (1.0 - self._limit_aggressive_pct), 2
                                )
                            else:  # BUY
                                limit_px = round(
                                    ltp * (1.0 + self._limit_aggressive_pct), 2
                                )
                            order_type = "LIMIT"
                            price = limit_px
                        else:
                            # No LTP -> fall back to MARKET for this symbol
                            self._log.warning(
                                "EOD: no LTP for %s, falling back to MARKET",
                                symbol,
                            )
                            order_type = "MARKET"
                            price = 0.0
                    else:
                        order_type = "MARKET"
                        price = 0.0

                    placed = self._adapter.place_order(
                        symbol=symbol,
                        side=exit_side,
                        qty=use_qty,
                        price=price,
                        order_type=order_type,
                        intent="INTRADAY",
                        tag="EOD_SQUAREOFF",
                    )

                    internal_oid = placed.internal_order_id

                    # Hand off to order_monitor for fill tracking (EOD7)
                    if self._order_monitor is not None:
                        self._order_monitor.track(
                            internal_order_id=internal_oid,
                            broker_order_id=placed.broker_order_id,
                            symbol=symbol,
                            side=exit_side,
                            qty=use_qty,
                            expected_price=placed.price,
                            placed_at=placed.ts,
                            leg="EOD",
                        )

                    # Audit 3.3: track LIMITs for the post-grace promotion sweep.
                    if order_type == "LIMIT":
                        pending_limits[trade_id] = (
                            placed.broker_order_id, symbol, exit_side, use_qty,
                        )

                    # Record EOD exit order in orders table
                    db_price = price if order_type == "LIMIT" else None
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
                                placed.broker_order_id, trade_id,
                                exit_side, order_type, use_qty, db_price,
                                placed.ts.isoformat(), placed.ts.isoformat(),
                            ),
                        )

                    succeeded += 1
                    self._log.info(
                        "EOD exit OK: trade_id=%s symbol=%s side=%s qty=%d "
                        "type=%s price=%s broker_order_id=%s",
                        trade_id, symbol, exit_side, use_qty,
                        order_type, price, placed.broker_order_id,
                    )

            except BrokerError as exc:
                log_exception(self._log, exc)
                self._log.critical(
                    "EOD exit BrokerError: trade_id=%s symbol=%s qty=%d is_co=%s",
                    trade_id, symbol, qty, is_co,
                )
                # Mark position as EOD_EXIT_FAILED in state_store (EOD5 step 4d)
                self._mark_exit_failed(trade_id)
                failed += 1
            except Exception as exc:  # noqa: BLE001
                log_exception(self._log, exc)
                self._log.critical(
                    "EOD exit unexpected error: trade_id=%s symbol=%s",
                    trade_id, symbol,
                )
                self._mark_exit_failed(trade_id)
                failed += 1

            # Staggered delay between orders (EOD5 audit fix; not after last order)
            if i < len(rows) - 1 and self._inter_order_delay_sec > 0:
                time.sleep(self._inter_order_delay_sec)

        # Audit 3.3 phase-2: promote unfilled LIMITs to MARKET after grace.
        # Single sleep (not per-symbol) so the grace window is bounded by
        # limit_grace_sec rather than N * grace. Skip if no LIMITs were
        # placed (e.g., legacy MARKET protocol or get_quote failed).
        if pending_limits:
            self._log.info(
                "EOD phase-2: %d LIMIT order(s) pending; sleeping %.1fs grace "
                "before MARKET promotion sweep",
                len(pending_limits), self._limit_grace_sec,
            )
            if self._limit_grace_sec > 0:
                time.sleep(self._limit_grace_sec)

            promoted, promote_failed = self._promote_limits_to_market(
                pending_limits
            )
            if promote_failed:
                # Each promote-failure means the LIMIT was cancelled but the
                # MARKET fallback could not be placed. Move those trades from
                # succeeded -> failed for the summary.
                succeeded -= promote_failed
                failed += promote_failed
            self._log.info(
                "EOD phase-2 sweep done: promoted=%d failed=%d",
                promoted, promote_failed,
            )

        return attempted, succeeded, failed

    def _promote_limits_to_market(
        self,
        pending_limits: dict[str, tuple[str, str, str, int]],
    ) -> tuple[int, int]:
        """
        Audit 3.3 phase-2: for each trade_id whose phase-1 LIMIT is still
        open at the broker, cancel the LIMIT and place a MARKET for the
        remaining qty.

        Returns (promoted_count, failed_count).
          - promoted_count: trades where MARKET fallback was placed OK.
          - failed_count: trades where MARKET fallback could not be placed
            (LIMIT cancel issued; trade marked EOD_EXIT_FAILED).

        Trades whose LIMIT fully filled during the grace window are skipped
        (broker shows qty=0); they do not count as promoted or failed.
        """
        # Re-fetch broker positions to discover what is still open.
        try:
            broker_positions = self._adapter.get_positions()
            current_qty = {
                p.symbol: abs(int(p.qty))
                for p in broker_positions
                if int(p.qty) != 0
            }
        except Exception as exc:  # noqa: BLE001
            log_exception(self._log, exc)
            self._log.critical(
                "EOD phase-2: get_positions failed; cannot promote unfilled "
                "LIMITs; error=%s",
                exc,
            )
            # Cannot tell which are still open; leave LIMITs in place. The
            # 15:20 RMS auto-square will close them (with the ~50 INR penalty)
            # but capital safety is preserved. Don't reclassify counts.
            return 0, 0

        promoted = 0
        promote_failed = 0
        items = list(pending_limits.items())
        for j, (trade_id, (broker_oid, symbol, exit_side, requested_qty)) in enumerate(items):
            remaining = current_qty.get(symbol, 0)
            if remaining <= 0:
                # LIMIT fully filled during grace window. Nothing to do.
                self._log.info(
                    "EOD phase-2: trade_id=%s symbol=%s LIMIT filled in "
                    "grace window (broker qty=0)",
                    trade_id, symbol,
                )
                continue

            # Cancel the residual LIMIT, then place MARKET for what's left.
            try:
                cancel_res = self._adapter.cancel_order(broker_oid)
                if not getattr(cancel_res, "success", False):
                    reason = getattr(cancel_res, "reason", "") or "rejected"
                    self._log.warning(
                        "EOD phase-2: cancel residual LIMIT rejected "
                        "trade_id=%s broker_oid=%s reason=%s -- proceeding "
                        "with MARKET anyway",
                        trade_id, broker_oid, reason,
                    )
            except Exception as exc:  # noqa: BLE001
                log_exception(self._log, exc)
                self._log.warning(
                    "EOD phase-2: cancel raised trade_id=%s broker_oid=%s "
                    "error=%s -- proceeding with MARKET anyway",
                    trade_id, broker_oid, exc,
                )

            try:
                placed = self._adapter.place_order(
                    symbol=symbol,
                    side=exit_side,
                    qty=remaining,
                    price=0.0,
                    order_type="MARKET",
                    intent="INTRADAY",
                    tag="EOD_SQUAREOFF",
                )
                if self._order_monitor is not None:
                    self._order_monitor.track(
                        internal_order_id=placed.internal_order_id,
                        broker_order_id=placed.broker_order_id,
                        symbol=symbol,
                        side=exit_side,
                        qty=remaining,
                        expected_price=placed.price,
                        placed_at=placed.ts,
                        leg="EOD",
                    )
                with self._store.transaction() as cur:
                    cur.execute(
                        """
                        INSERT OR IGNORE INTO orders
                          (order_id, trade_id, leg, leg_index,
                           transaction_type, order_type, product, variety,
                           qty_requested, price, trigger_price,
                           status, qty_filled, avg_fill_price,
                           placed_at, updated_at)
                        VALUES (?, ?, 'EOD', 1, ?, 'MARKET', 'MIS', 'regular',
                                ?, NULL, NULL, 'OPEN', 0, NULL, ?, ?)
                        """,
                        (
                            placed.broker_order_id, trade_id,
                            exit_side, remaining,
                            placed.ts.isoformat(), placed.ts.isoformat(),
                        ),
                    )
                promoted += 1
                self._log.info(
                    "EOD phase-2: promoted to MARKET trade_id=%s symbol=%s "
                    "side=%s qty=%d broker_oid=%s",
                    trade_id, symbol, exit_side, remaining, placed.broker_order_id,
                )
            except Exception as exc:  # noqa: BLE001
                log_exception(self._log, exc)
                self._log.critical(
                    "EOD phase-2: MARKET fallback FAILED trade_id=%s "
                    "symbol=%s qty=%d -- position will be RMS-auto-squared "
                    "at 15:20; error=%s",
                    trade_id, symbol, remaining, exc,
                )
                self._mark_exit_failed(trade_id)
                promote_failed += 1

            # Inter-order delay between MARKET promotions.
            if j < len(items) - 1 and self._inter_order_delay_sec > 0:
                time.sleep(self._inter_order_delay_sec)

        return promoted, promote_failed

    def _mark_exit_failed(self, trade_id: str) -> None:
        """Update exit_reason to EOD_EXIT_FAILED for a trade that could not be exited."""
        try:
            with self._store.transaction() as cur:
                cur.execute(
                    "UPDATE trades SET exit_reason = 'EOD_EXIT_FAILED', "
                    "updated_at = ? WHERE trade_id = ?",
                    (now_ist().isoformat(), trade_id),
                )
        except Exception as exc:  # noqa: BLE001
            log_exception(self._log, exc)

    def _check_restart_recovery(self) -> None:
        """
        EOD9 (post_wire_init): inspect today's eod_squareoff_log.

        M-3 write-ahead semantics:
          - status=COMPLETE  -> already done today; skip.
          - status=IN_PROGRESS -> prior fire crashed; recover (fire again).
          - no row AND past EOD time AND before 15:30 -> recovery fire.
          - no row AND past 15:30 -> CRITICAL alert, do not fire.
        """
        now = now_ist()
        today_str = now.date().isoformat()
        today_date = now.date()

        row = self._store.get_eod_squareoff_log_for_date(today_str)

        if row is not None:
            # Row-key schema_meta pragma: 'status' column exists in v11+. For
            # backwards compat with rows written pre-v11, default to COMPLETE
            # if the column is missing or NULL.
            try:
                status = row["status"]
            except (IndexError, KeyError):
                status = "COMPLETE"
            if status is None:
                status = "COMPLETE"

            if status == "COMPLETE":
                # EOD already finished today; mark fired so check_and_fire stays quiet
                self._fired_for_date[today_date] = True
                if row["positions_failed"] > 0 or row["cancels_failed"] > 0:
                    self._log.warning(
                        "EOD ran today (%s) but had failures "
                        "(positions_failed=%d cancels_failed=%d); "
                        "reconciler should handle residual positions",
                        today_str,
                        row["positions_failed"],
                        row["cancels_failed"],
                    )
                return

            # status == IN_PROGRESS: prior fire crashed mid-execution.
            # Execute recovery fire; if IT also fails the row stays IN_PROGRESS
            # (via the write-ahead at _fire start) so a human operator notices.
            self._log.critical(
                "EOD_RECOVERY_FROM_IN_PROGRESS: prior fire on %s crashed "
                "mid-execution (status=IN_PROGRESS). Recovering.",
                today_str,
            )
            with self._lock:
                self._fired_for_date[today_date] = True
            try:
                self._fire(now, recovery_fire=True)
            except Exception as exc:  # noqa: BLE001
                # Row remains IN_PROGRESS -> operator alert path is the
                # CRITICAL log + optional notifier below. Do NOT reset
                # _fired_for_date: we don't want a polling loop to retry
                # the same broken path.
                log_exception(self._log, exc)
                self._log.critical(
                    "EOD_RECOVERY_FAILED: fired_date=%s status remains "
                    "IN_PROGRESS; manual intervention required. error=%s",
                    today_str, exc,
                )
                if self._notifier is not None:
                    try:
                        self._notifier.send(
                            severity="CRITICAL",
                            title=f"[{self._mode}] 🚨 EOD Recovery Failed",
                            body=(
                                f"Date: {today_str} | Error: {exc}\n"
                                "Action: Manual intervention required."
                            ),
                            source_module="eod_squareoff",
                        )
                    except Exception as notif_exc:  # noqa: BLE001
                        self._log.error(
                            "EOD_RECOVERY_FAILED notifier.send failed: %s",
                            notif_exc,
                        )
            return

        # No log row for today — check if we should auto-fire
        if not self._mw.is_eod_squareoff_due(now):
            return  # Normal startup before EOD time

        if self._mw.is_trading_holiday(now):
            return  # Holiday; EOD not applicable

        if now.time() > self._market_close_time:
            self._log.critical(
                "EOD squareoff was NOT fired today (%s) and it is past market close "
                "(%s). Manual intervention required.",
                today_str,
                self._market_close_time.strftime("%H:%M"),
            )
            # EOD9 visibility: check if open positions remain; write event + alert
            try:
                open_rows = self._store.get_open_intraday_positions()
                open_count = len(open_rows)
            except Exception:
                open_count = -1  # unknown
                open_rows = []

            if open_count != 0:
                symbols = [r["symbol"] for r in open_rows] if open_rows else []
                details = json.dumps({
                    "open_positions_count": open_count,
                    "positions": symbols,
                    "note": "Broker RMS may have auto-squaredoff. Review manually.",
                })
                try:
                    self._store.insert_system_event(
                        event_type="EOD_SKIPPED_LATE",
                        timestamp=now.isoformat(),
                        details=details,
                    )
                except Exception as exc:
                    self._log.error("EOD_SKIPPED_LATE event write failed: %s", exc)

                if self._notifier is not None:
                    symbols_str = (
                        ", ".join(symbols) if isinstance(symbols, (list, tuple))
                        else str(symbols)
                    )
                    body = (
                        f"Symbols: {symbols_str}\n"
                        "Broker RMS will auto-squareoff. Review tomorrow."
                    )
                    try:
                        self._notifier.send(
                            severity="CRITICAL",
                            title=(
                                f"[{self._mode}] 🚨 EOD Squareoff Missed — "
                                "Open Positions Remain"
                            ),
                            body=body,
                            source_module="eod_squareoff",
                        )
                    except Exception as exc:
                        self._log.error("EOD_SKIPPED_LATE notifier.send failed: %s", exc)
            return

        # Past EOD time, before market close, no log row -> recovery fire
        self._log.warning(
            "Restart detected after EOD time with no log for today (%s). "
            "Executing recovery EOD fire.",
            today_str,
        )
        with self._lock:
            self._fired_for_date[today_date] = True
        self._fire(now, recovery_fire=True)
