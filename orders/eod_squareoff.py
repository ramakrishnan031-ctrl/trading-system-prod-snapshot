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
from broker.position_helpers import cancel_co_bracket  # ledger #2d seam
from core.constants import EMERGENCY_FLATTEN_PRODUCTS
from core.effect_telemetry import handle as _effect_handle
from broker.zerodha_adapter import ZerodhaAdapter
from capital.fund_manager import FundManager
from capital.kill_switch import KillSwitch, KillState
from core.events import EodSquareoffComplete, EventBus
from core.exceptions import BrokerError
from core.ids import truncate_tag_for_broker
from core.logger import log_exception
from core.market_windows import MarketWindows
from core.state_store import StateStore
from core.time_authority import now_ist
from orders.price_math import (
    DEFAULT_TICK,
    EMERGENCY_EXIT_BUFFER_PCT,
    marketable_limit_price,
)

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
        # effect-telemetry (ledger #1, frozen contract A2.1): one handle,
        # resolved once — incremented once per completed _fire sweep.
        self._fx_fire = _effect_handle("eod_squareoff")
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
        # SNR-V2: RetestMonitor reference (late-bound via set_retest_monitor) so
        # parked WAIT_FOR_RETEST candidates are cleared at square-off too.
        self._retest_monitor = None

        # FIX-186 (FIX 2): optional stale-order sweep callable (wired post-construction
        # by main.py to OrderReconciler.sweep_stale_orders). Invoked after the
        # two-pass squareoff so any exit legs left non-terminal by a broker-side
        # cancel get finalized before the daily EOD verification.
        self._stale_order_sweep = None

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

    def set_retest_monitor(self, monitor) -> None:
        """SNR-V2: wire the RetestMonitor so EOD clears parked candidates."""
        self._retest_monitor = monitor

    def set_stale_order_sweep(self, sweep_fn) -> None:
        """
        FIX-186 (FIX 2): wire the OrderReconciler.sweep_stale_orders callable so
        the EOD sequence can finalize any orphan order rows after squareoff.
        """
        self._stale_order_sweep = sweep_fn

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

        M-O5: the flag is a concurrency CLAIM, not a "done" marker. If _fire()
        raises, or returns having left positions/cancels un-squared, the flag is
        reset so the scheduled 15:17 check_and_fire() can still run the backstop
        squareoff — mirroring check_and_fire()'s own reset-on-exception (:223-229).
        On FULL success the flag stays set so the scheduled fire does not
        re-square what fire_now already squared. The retry is safe: the E.5
        broker-position filter (_exit_open_positions) re-queries broker truth and
        skips already-flat rows, so a retry cannot double-square. A partial-failure
        retry re-runs reset_daily_pnl(), producing a SECOND RESET_PNL ledger row
        for the day (its pnl_delta is -current_net, NOT zero) — so any RESET_PNL
        verification must SUM pnl_delta, never read a single row. See
        docs/audit/mo5_investigation_22jul2026.md.

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

        try:
            result = self._fire(now, recovery_fire=False)
        except Exception:
            # M-O5: _fire() raised -> release the claim so check_and_fire() can
            # retry the backstop squareoff (mirrors check_and_fire :223-229).
            with self._lock:
                self._fired_for_date[today] = False
            raise
        # M-O5 (Rider 1): a partial failure that did NOT raise (positions or
        # cancels left un-squared) must also not block the scheduled 15:17 retry
        # of the un-squared legs -- "fired" is not "successfully squared
        # everything". Same predicate the restart-recovery path uses (:1717).
        if result.positions_failed > 0 or result.cancels_failed > 0:
            with self._lock:
                self._fired_for_date[today] = False
        return result

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

    def is_alive(self) -> bool:
        """E-4 (audit 02-Jul): read-only liveness for /health. True iff the EOD
        polling thread (started via start_polling) is running — so a dead EOD
        scheduler, which would leave positions un-squared at 15:15, is visible to
        external uptime monitors. Returns False if start_polling was never used
        (external-scheduler mode)."""
        with self._lock:
            t = getattr(self, "_polling_thread", None)
        return t is not None and t.is_alive()

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

        # SNR-V2: clear parked WAIT_FOR_RETEST candidates (in-memory + retest_state)
        # so none survive into the next session (mirrors FIX-046 for the gate).
        if self._retest_monitor is not None:
            try:
                n = self._retest_monitor.clear_all()
                self._log.info(f"SNR-V2: cleared {n} parked retest candidate(s) at EOD")
            except Exception as exc:  # noqa: BLE001
                self._log.error(f"SNR-V2: retest_monitor.clear_all() failed: {exc}")

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

        # FIX-063: Two-pass squareoff with 2s sleep to catch phantom fills
        #
        # PASS 1: Cancel all pending orders (entry + exit legs)
        self._log.info("EOD Pass 1: canceling all pending orders")

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

        self._log.info(
            "EOD Pass 1 complete: %d orders cancelled. Sleeping 2s before Pass 2.",
            c_succeeded,
        )

        # FIX-063: Sleep 2 seconds to allow phantom fills to settle
        # A phantom fill occurs when an order fills at the exchange exactly as
        # the cancel is sent. The 2s sleep gives the fill enough time to:
        # 1. Arrive via order_monitor poll
        # 2. Update the state_machine and DB
        # 3. Appear in the OPEN positions query below
        # This prevents naked positions from slipping into the 15:20-15:30 window.
        time.sleep(2)

        # PASS 2: Re-query and exit ALL open positions (including phantom fills)
        self._log.info("EOD Pass 2: exiting all open positions")

        # Step 4: exit open intraday positions (EOD5)
        p_attempted, p_succeeded, p_failed = self._exit_open_positions(
            now, recovery_fire=recovery_fire,
        )

        self._log.info(
            "EOD Pass 2 complete: %d open positions exited.",
            p_succeeded,
        )

        # FIX-186 (FIX 2): sweep any orders left non-terminal by a broker-side
        # cancel whose parent trade is now terminal, before eod_verify runs.
        # Best-effort: never let the sweep abort the EOD sequence.
        if self._stale_order_sweep is not None:
            try:
                self._stale_order_sweep()
            except Exception as exc:  # noqa: BLE001
                log_exception(self._log, exc)
                self._log.error("EOD stale-order sweep failed: %s", exc)

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

        # effect-telemetry (frozen A2.1): an EOD square-off sequence executed
        # (EodFireResult produced).
        self._fx_fire.inc()
        return result

    # ------------------------------------------------------------------
    # Daily summary (new)
    # ------------------------------------------------------------------

    # FIX-182: statuses that represent a position that actually reached the
    # market and was (or is being) closed today. CLOSED_MANUAL covers RMS /
    # operator closes; EXITING covers a close still in flight at EOD. A
    # CLOSED_MANUAL trade may carry net_pnl=None if the close path could not
    # record financials (pre-FIX-180 race) — we coerce None→0.0 for the math
    # so it is still counted instead of silently dropped.
    _SUMMARY_CLOSED_STATUSES = ("CLOSED", "CLOSED_MANUAL", "EXITING")

    @staticmethod
    def _summary_net_pnl(trade: dict) -> float:
        """FIX-182: net_pnl coerced to float; None / unparseable → 0.0."""
        val = trade.get("net_pnl")
        if val is None:
            return 0.0
        try:
            return float(val)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _disp_dir(trade: dict) -> str:
        """Normalise a trade's direction to LONG/SHORT for display.

        trades.direction is NOT NULL (LONG|SHORT) for real rows; we still
        coerce defensively (BUY→LONG, SELL→SHORT, unknown→LONG)."""
        d = str(trade.get("direction") or "").upper()
        return "SHORT" if d in ("SHORT", "SELL") else "LONG"

    # exit_reason (SL_HIT/TGT_HIT/EOD/MANUAL/TIMEOUT/CIRCUIT_BREAKER/…) → short
    # tag for the per-trade detail sub-line.
    _EXIT_TAGS = {
        "SL_HIT": "SL", "SL": "SL",
        "TGT_HIT": "TGT", "TGT": "TGT",
        "EOD": "EOD",
        "MANUAL": "MAN", "MAN": "MAN",
        "TIMEOUT": "TO", "TO": "TO",
        "CIRCUIT_BREAKER": "CB",
    }

    @classmethod
    def _exit_tag(cls, exit_reason) -> str:
        r = str(exit_reason or "").upper()
        if r in cls._EXIT_TAGS:
            return cls._EXIT_TAGS[r]
        return r[:3] if r else "—"

    @staticmethod
    def _summary_entry_px(trade: dict):
        """Actual fill price preferred; fall back to the planned LIMIT."""
        px = trade.get("entry_actual_price")
        if px is None:
            px = trade.get("entry_target_price")
        return px

    def _human_order_symbols_for_date(self, date_str: str) -> list[str]:
        """FIX-182: distinct symbols flagged ORPHAN_ADOPTION today (human /
        untracked broker positions the system did not place). Best-effort;
        returns [] on any error so the summary never fails on this."""
        try:
            rows = self._store.fetch_all(
                "SELECT DISTINCT symbol FROM reconciliation_log "
                "WHERE check_name = 'ORPHAN_ADOPTION' AND DATE(ts) = ? "
                "AND symbol IS NOT NULL AND symbol != ''",
                (date_str,),
            )
            return [r["symbol"] for r in (rows or [])]
        except Exception as exc:  # noqa: BLE001
            self._log.debug("EOD summary: human-order lookup failed: %s", exc)
            return []

    def _send_daily_summary(self, date_str: str) -> None:
        """Build and send the EOD daily summary Telegram alert.

        Pulls closed trades for date_str from the trades table and reports
        net P&L, win rate, best/worst trade, strategy breakdown, and a
        rough Smart TGT split derived from order_protocol.

        FIX-182: "closed" now spans CLOSED / CLOSED_MANUAL / EXITING so manual
        and RMS closes appear in the summary (previously only pure CLOSED rows
        were counted, which reported "No closed trades" on manual-close days).
        Any human/untracked broker positions detected today are noted too.
        """
        try:
            all_trades = self._store.get_trades_for_date(date_str)
        except Exception as exc:  # noqa: BLE001
            self._log.error("EOD_DAILY_SUMMARY fetch failed: %s", exc)
            return

        human_symbols = self._human_order_symbols_for_date(date_str)
        human_note = (
            f"\nHuman/untracked orders today: {', '.join(human_symbols)} "
            f"(not managed by system)"
            if human_symbols else ""
        )

        closed = [
            t for t in all_trades
            if t.get("status") in self._SUMMARY_CLOSED_STATUSES
        ]

        if not closed:
            body = (
                "\nNo closed trades today.\n"
                f"Attempted today: {len(all_trades)}"
                f"{human_note}"
            )
            self._notifier.send(
                severity="INFO",
                title=f"[{self._mode}] 📊 DAILY SUMMARY — {date_str}",
                body=body,
                source_module="eod_squareoff",
            )
            return

        # Route the per-strategy 🟢/🔴 grouping to the CANONICAL direction
        # (StrategyConfig.direction) instead of a majority-vote over realized sides.
        # Built HERE (the caller does I/O) so _format_summary_body stays PURE; fail-safe
        # → an empty map falls the body back to the vote (byte-identical behaviour).
        try:
            from core.strategy_direction import build_direction_map
            direction_map = build_direction_map("config")
        except Exception as exc:  # noqa: BLE001
            self._log.debug("EOD summary: direction map unavailable (%s); using vote", exc)
            direction_map = {}

        body = self._format_summary_body(closed, human_symbols, log=self._log,
                                         direction_map=direction_map)
        self._notifier.send(
            severity="INFO",
            title=f"[{self._mode}] 📊 DAILY SUMMARY — {date_str}",
            body=body,
            source_module="eod_squareoff",
        )

    @staticmethod
    def _format_summary_body(
        closed: list[dict],
        human_symbols: list[str],
        log: "logging.Logger | None" = None,
        direction_map: "dict | None" = None,
    ) -> str:
        """Build the redesigned EOD DAILY SUMMARY body from closed trades.

        PURE (no I/O) so it is unit-testable and render-able offline against
        real trade rows. `closed` is the list of trades in
        _SUMMARY_CLOSED_STATUSES; `human_symbols` are today's untracked symbols.
        `direction_map` (optional {strategy: LONG|SHORT}, canonical StrategyConfig
        .direction) drives the per-strategy 🟢/🔴 grouping; None/empty → the legacy
        majority-vote over realized sides (byte-identical), keeping this function pure.

        Layout (29-Jun redesign):
            P&L | Win rate
            Avg R | Capital used
            Best / Worst  (+ DIRECTION | exit_reason)
            Long Trades / Short Trades split  (counts + W/L + P&L)
            Strategies:  🟢 long / 🔴 short, each with per-trade detail sub-lines
            Smart TGT: FIXED/TRAIL/DEFEND
            Human/untracked orders
        """
        log = log or logging.getLogger("eod_squareoff")
        npnl = EodSquareoff._summary_net_pnl
        disp = EodSquareoff._disp_dir

        total_n = len(closed)
        total_pnl = sum(npnl(t) for t in closed)
        win_n = sum(1 for t in closed if npnl(t) > 0)
        loss_n = total_n - win_n          # net_pnl <= 0 counts as a loss (unchanged)
        win_rate_pct = (win_n / total_n * 100.0) if total_n else 0.0

        best = max(closed, key=npnl)
        worst = min(closed, key=npnl)
        best_pnl = npnl(best)
        worst_pnl = npnl(worst)
        best_reason = best.get("exit_reason") or "—"
        worst_reason = worst.get("exit_reason") or "—"

        # Avg R = avg of (net_pnl / risk_amount) across closed trades. (unchanged)
        r_values = []
        for t in closed:
            risk = t.get("risk_amount")
            try:
                risk_f = float(risk) if risk is not None else 0.0
            except Exception:
                risk_f = 0.0
            if risk_f > 0:
                r_values.append(npnl(t) / risk_f)
        avg_r = (sum(r_values) / len(r_values)) if r_values else 0.0

        # Capital used = sum of margin_reserved for closed trades. (unchanged)
        capital_used = 0.0
        for t in closed:
            mr = t.get("margin_reserved")
            try:
                capital_used += float(mr) if mr is not None else 0.0
            except (TypeError, ValueError) as exc:
                # LOG-1 (2026-04-26 audit): never silent on data conversion.
                log.warning(
                    "eod_squareoff.margin_float_failed",
                    extra={"trade_id": t.get("trade_id"), "mr": mr, "error": str(exc)},
                )

        # Long/Short split — computed from ACTUAL trade directions; the W and L
        # counts here total the overall win_n/loss_n above.
        def _split(rows: list[dict]):
            w = sum(1 for t in rows if npnl(t) > 0)
            return len(rows), w, len(rows) - w, sum(npnl(t) for t in rows)

        longs = [t for t in closed if disp(t) == "LONG"]
        shorts = [t for t in closed if disp(t) == "SHORT"]
        long_n, long_w, long_l, long_pnl = _split(longs)
        short_n, short_w, short_l, short_pnl = _split(shorts)

        # Strategy breakdown — keep the trade list per strategy for the detail
        # sub-lines, plus its direction (single-direction strategies → 🟢/🔴).
        strat_stats: dict = {}
        for t in closed:
            name = t.get("strategy") or "unknown"
            e = strat_stats.setdefault(name, {"trades": [], "wins": 0, "pnl": 0.0})
            e["trades"].append(t)
            if npnl(t) > 0:
                e["wins"] += 1
            e["pnl"] += npnl(t)

        def _strat_dir(name: str, e: dict) -> str:
            # Canonical StrategyConfig.direction when known; else the legacy majority
            # vote over realized trade sides (byte-identical to pre-registry behaviour).
            d = (direction_map or {}).get(name)
            if d in ("LONG", "SHORT"):
                return d
            n_long = sum(1 for t in e["trades"] if disp(t) == "LONG")
            return "LONG" if n_long * 2 >= len(e["trades"]) else "SHORT"

        # Smart TGT bucket breakdown: CO_PLUS_TGT = TRAIL-eligible, rest = FIXED.
        trail_n = sum(1 for t in closed if t.get("order_protocol") == "CO_PLUS_TGT")
        fixed_n = total_n - trail_n
        defend_n = 0   # not implemented

        def _money(v: float) -> str:
            return f"{'+' if v >= 0 else '-'}₹{abs(v):,.2f}"

        lines = [
            "",
            f"P&L: {_money(total_pnl)} | "
            f"Win rate: {win_rate_pct:.1f}% ({win_n}W {loss_n}L)",
            "",
            f"Avg R: {avg_r:+.2f} | Capital used: ₹{capital_used:,.2f}",
            "",
            f"Best:  {best.get('symbol', '?')} {_money(best_pnl)} "
            f"({disp(best)} | {best_reason})",
            f"Worst: {worst.get('symbol', '?')} {_money(worst_pnl)} "
            f"({disp(worst)} | {worst_reason})",
            "",
            f"🟢 Long Trades : {long_n} ({long_w}W {long_l}L) | P&L: {_money(long_pnl)}",
            f"🔴 Short Trades: {short_n} ({short_w}W {short_l}L) | P&L: {_money(short_pnl)}",
            "",
            "Strategies:",
        ]

        # 🟢 long strategies first, then 🔴 short; each ordered by P&L desc.
        ordered = sorted(
            strat_stats.items(),
            key=lambda kv: (0 if _strat_dir(kv[0], kv[1]) == "LONG" else 1, -kv[1]["pnl"]),
        )
        for name, e in ordered:
            n = len(e["trades"])
            s_wr = (e["wins"] / n * 100.0) if n else 0.0
            emoji = "🟢" if _strat_dir(name, e) == "LONG" else "🔴"
            lines.append(f"{emoji} {name}  {n}T {s_wr:.0f}%WR  {_money(e['pnl'])}")
            for t in e["trades"]:
                ep = EodSquareoff._summary_entry_px(t)
                xp = t.get("exit_price")
                ep_s = f"₹{float(ep):,.2f}" if ep is not None else "—"
                xp_s = f"₹{float(xp):,.2f}" if xp is not None else "—"
                lines.append(
                    f"      • {t.get('symbol', '?')} - {disp(t)} - "
                    f"{ep_s} - {xp_s} ({EodSquareoff._exit_tag(t.get('exit_reason'))}) - "
                    f"{_money(npnl(t))}"
                )

        lines.append("")
        lines.append(f"Smart TGT: FIXED={fixed_n} TRAIL={trail_n} DEFEND={defend_n}")
        if human_symbols:
            lines.append(
                f"Human/untracked orders today: {', '.join(human_symbols)} "
                f"(not managed by system)"
            )
        return "\n".join(lines)

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
                # ledger #2: the FIX-015 intraday set now reads the ONE shared
                # source (core.constants.EMERGENCY_FLATTEN_PRODUCTS) — same
                # {MIS, CO} membership, zero behaviour change; scheduled and
                # emergency vocabularies can no longer drift (G1: one name,
                # no second copy to assert against).
                if int(p.qty) != 0 and p.product in EMERGENCY_FLATTEN_PRODUCTS
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
                    # Ledger #2d: the broker gesture itself now lives in
                    # broker/position_helpers.cancel_co_bracket, shared with the
                    # kill switch's HARD_KILL flatten so the two cannot drift.
                    # ONLY the two lines that called the adapter and normalised
                    # its result moved. The helper neither logs nor catches, so
                    # everything below -- the CRITICAL, its
                    # CO_SQUAREOFF_CANCEL_REJECTED sentinel, _mark_exit_failed,
                    # the counters, and the outer handlers that catch a raising
                    # cancel_order -- is unchanged and stays here.
                    ok, reason = cancel_co_bracket(self._adapter, entry_broker_id)
                    if not ok:
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

        # FIX-182: broker-driven residual sweep. The loop above only acts on
        # local OPEN/PARTIAL trades. A position can be live at the broker with
        # NO local OPEN trade — e.g. an entry that filled at the broker while
        # the local trade was still PENDING (GICRE 16-Jun incident), or a fill
        # the local state lost track of. Those would ride overnight. Flatten
        # any non-zero MIS/CO broker position that the system placed (a local
        # trade exists for the symbol today) but that we did not already exit.
        # Human / untracked positions (no local trade) are left alone per the
        # system-trades-only policy.
        r_attempted, r_succeeded, r_failed = self._sweep_residual_broker_positions(
            handled_symbols={r["symbol"] for r in rows},
        )
        attempted += r_attempted
        succeeded += r_succeeded
        failed += r_failed

        return attempted, succeeded, failed

    def _sweep_residual_broker_positions(
        self, handled_symbols: set[str]
    ) -> tuple[int, int, int]:
        """
        FIX-182: flatten residual broker MIS/CO positions not covered by the
        local-trade exit loop.

        Returns (attempted, succeeded, failed).

        A residual position is flattened only when it is system-owned, i.e. a
        local trade row exists for its symbol on today's date. Positions with
        no local trade are human / untracked orders (operator placed them in
        Kite directly) and are intentionally NOT squared off — the system
        manages only system trades. They are logged once at INFO for the audit
        trail and skipped.

        Fresh get_positions() is queried here (not the top-of-fire snapshot) so
        we only act on what is genuinely still open after Pass-2 + phase-2.
        """
        try:
            positions = self._adapter.get_positions()
        except Exception as exc:  # noqa: BLE001
            log_exception(self._log, exc)
            self._log.critical(
                "EOD residual sweep: get_positions failed; skipping; error=%s",
                exc,
            )
            return (0, 0, 0)

        residual = [
            p for p in positions
            if int(getattr(p, "qty", 0)) != 0
            # ledger #2: shared source (see the EOD6 pass above) — no drift.
            and getattr(p, "product", "") in EMERGENCY_FLATTEN_PRODUCTS
            and getattr(p, "symbol", "") not in handled_symbols
        ]
        if not residual:
            return (0, 0, 0)

        # Symbols the system itself traded today (any status). Used to tell a
        # system-owned residual from a human order.
        try:
            today_str = now_ist().date().isoformat()
            system_symbols = {
                t.get("symbol") for t in self._store.get_trades_for_date(today_str)
            }
        except Exception as exc:  # noqa: BLE001
            log_exception(self._log, exc)
            self._log.error(
                "EOD residual sweep: get_trades_for_date failed; treating all "
                "residuals as system-owned (safer to over-square); error=%s",
                exc,
            )
            system_symbols = {getattr(p, "symbol", "") for p in residual}

        attempted = 0
        succeeded = 0
        failed = 0
        for p in residual:
            symbol = getattr(p, "symbol", "")
            qty = abs(int(p.qty))
            if symbol not in system_symbols:
                self._log.info(
                    "EOD residual sweep: skipping %s qty=%d — human/untracked "
                    "position (no local trade today); system manages system "
                    "trades only",
                    symbol, qty,
                )
                continue

            attempted += 1
            self._log.critical(
                "EOD residual sweep: system position %s qty=%d live at broker "
                "with no local OPEN trade exited — flattening",
                symbol, qty,
            )
            if self._place_marketable_limit_exit(p):
                succeeded += 1
            else:
                failed += 1
            if self._inter_order_delay_sec > 0:
                time.sleep(self._inter_order_delay_sec)

        self._log.info(
            "EOD residual sweep: attempted=%d squared=%d failed=%d",
            attempted, succeeded, failed,
        )
        return attempted, succeeded, failed

    def _place_marketable_limit_exit(self, position) -> bool:
        """
        FIX-182: flatten a single broker position with a marketable LIMIT
        (LTP ± emergency buffer, adapter snaps to tick) so it fills while
        capping slippage; MARKET fallback when no LTP is available. Mirrors
        order_reconciler._flatten_broker_position. Best-effort — never raises;
        returns True if an order was placed.
        """
        try:
            qty = abs(int(position.qty))
            if qty == 0:
                return False
            symbol = position.symbol
            # qty > 0 = long -> SELL to flatten; qty < 0 = short -> BUY.
            exit_side = "SELL" if position.qty > 0 else "BUY"

            ltp = None
            try:
                quotes = self._adapter.get_quote([symbol])
                q = quotes.get(symbol) if quotes else None
                if q is not None:
                    ltp = float(getattr(q, "last_price", 0) or 0) or None
            except Exception:  # noqa: BLE001
                ltp = None

            if ltp and ltp > 0:
                price = marketable_limit_price(
                    exit_side, ltp, EMERGENCY_EXIT_BUFFER_PCT, DEFAULT_TICK
                )
                order_type = "LIMIT"
            else:
                price = 0.0
                order_type = "MARKET"

            placed = self._adapter.place_order(
                symbol=symbol,
                side=exit_side,
                qty=qty,
                price=price,
                order_type=order_type,
                intent="INTRADAY",
                tag=truncate_tag_for_broker("EOD_RESIDUAL"),
            )

            if self._order_monitor is not None:
                try:
                    self._order_monitor.track(
                        internal_order_id=placed.internal_order_id,
                        broker_order_id=placed.broker_order_id,
                        symbol=symbol,
                        side=exit_side,
                        qty=qty,
                        expected_price=placed.price,
                        placed_at=placed.ts,
                        leg="EOD",
                    )
                except Exception as exc:  # noqa: BLE001
                    self._log.error(
                        "EOD residual sweep: order_monitor.track failed for %s: %s",
                        symbol, exc,
                    )

            self._log.info(
                "EOD residual exit OK: symbol=%s side=%s qty=%d type=%s price=%s "
                "broker_order_id=%s",
                symbol, exit_side, qty, order_type, price, placed.broker_order_id,
            )
            return bool(getattr(placed, "broker_order_id", None))
        except Exception as exc:  # noqa: BLE001
            log_exception(self._log, exc)
            self._log.critical(
                "EOD residual sweep: flatten failed for %s: %s",
                getattr(position, "symbol", "?"), exc,
            )
            return False

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
