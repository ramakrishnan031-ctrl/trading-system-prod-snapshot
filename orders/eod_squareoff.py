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

_MARKET_CLOSE_HARD_STOP = datetime.strptime("15:30", "%H:%M").time()


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

        # EOD3: per-date "already fired" flag
        self._fired_for_date: dict[date, bool] = {}
        self._lock = threading.Lock()

        # Track whether WE set soft_kill (so we can resume safely)
        self._we_set_soft_kill: bool = False

        # EOD9: on construction, check if EOD already ran today with failures
        self._check_restart_recovery()

    # ── public API ────────────────────────────────────────────────────────────

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

        # Step 4: exit open intraday positions (EOD5)
        p_attempted, p_succeeded, p_failed = self._exit_open_positions(now)

        duration_sec = time.monotonic() - start_ts

        # Step 5: log summary (EOD5)
        self._log.info(
            "EOD summary: positions_squared=%d/%d cancels=%d/%d duration=%.2fs",
            p_succeeded, p_attempted,
            c_succeeded, c_attempted,
            duration_sec,
        )

        # Step 8: persist to eod_squareoff_log (EOD8)
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
        return result

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

    def _exit_open_positions(self, now: datetime) -> tuple[int, int, int]:
        """
        Place MARKET exit orders for all open intraday positions.
        Returns (attempted, succeeded, failed).

        For each position (sorted by symbol per Foundation 3.7):
          - Place MARKET order in opposite direction
          - Register new exit order with state_machine
          - Hand off to order_monitor (if injected)
          - Delay inter_order_delay_sec between orders (audit EOD5)
          - On failure: log CRITICAL, mark EOD_EXIT_FAILED in DB (EOD5 step 4d)
        """
        rows = self._store.get_open_intraday_positions()
        attempted = len(rows)
        succeeded = 0
        failed = 0

        for i, row in enumerate(rows):
            trade_id = row["trade_id"]
            symbol = row["symbol"]
            direction = row["direction"]
            qty = row["qty_filled"]

            # Determine exit side: LONG position -> SELL exit; SHORT -> BUY
            exit_side = "SELL" if direction == "LONG" else "BUY"

            try:
                placed = self._adapter.place_order(
                    symbol=symbol,
                    side=exit_side,
                    qty=qty,
                    price=0.0,          # MARKET order
                    order_type="MARKET",
                    intent="INTRADAY",
                    tag="EOD_SQUAREOFF",
                )

                # Register exit order with state_machine (EOD5 step 4c)
                # The adapter already registered it via ZA7; state_machine
                # tracks it by internal_order_id
                internal_oid = placed.internal_order_id

                # Hand off to order_monitor for fill tracking (EOD7)
                if self._order_monitor is not None:
                    self._order_monitor.track(
                        internal_order_id=internal_oid,
                        broker_order_id=placed.broker_order_id,
                        symbol=symbol,
                        side=exit_side,
                        qty=qty,
                        expected_price=placed.price,
                        placed_at=placed.ts,
                    )

                # Record EOD exit order in orders table
                with self._store.transaction() as cur:
                    cur.execute(
                        """
                        INSERT OR IGNORE INTO orders
                          (order_id, trade_id, leg, leg_index,
                           transaction_type, order_type, product, variety,
                           qty_requested, price, trigger_price,
                           status, qty_filled, avg_fill_price,
                           placed_at, updated_at)
                        VALUES (?, ?, 'EOD', 0, ?, 'MARKET', 'MIS', 'regular',
                                ?, NULL, NULL, 'OPEN', 0, NULL, ?, ?)
                        """,
                        (
                            placed.broker_order_id, trade_id,
                            exit_side, qty,
                            placed.ts.isoformat(), placed.ts.isoformat(),
                        ),
                    )

                succeeded += 1
                self._log.info(
                    "EOD exit OK: trade_id=%s symbol=%s side=%s qty=%d "
                    "broker_order_id=%s",
                    trade_id, symbol, exit_side, qty, placed.broker_order_id,
                )

            except BrokerError as exc:
                log_exception(self._log, exc)
                self._log.critical(
                    "EOD exit BrokerError: trade_id=%s symbol=%s qty=%d",
                    trade_id, symbol, qty,
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

        return attempted, succeeded, failed

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
        EOD9: on construction, check today's eod_squareoff_log.
        - If a row exists with failures, log WARNING (reconciler handles it).
        - If no row AND now is past EOD time AND before 15:30, fire once.
        - If no row AND now is past 15:30, log CRITICAL but do NOT fire.
        - If a row already exists (even with failures), set _fired_for_date to
          prevent check_and_fire() from double-firing.
        """
        now = now_ist()
        today_str = now.date().isoformat()
        today_date = now.date()

        row = self._store.get_eod_squareoff_log_for_date(today_str)

        if row is not None:
            # EOD already ran today; mark fired so check_and_fire stays quiet
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

        # No log row for today — check if we should auto-fire
        if not self._mw.is_eod_squareoff_due(now):
            return  # Normal startup before EOD time

        if self._mw.is_trading_holiday(now):
            return  # Holiday; EOD not applicable

        if now.time() > _MARKET_CLOSE_HARD_STOP:
            self._log.critical(
                "EOD squareoff was NOT fired today (%s) and it is past market close "
                "(%s). Manual intervention required.",
                today_str,
                _MARKET_CLOSE_HARD_STOP.strftime("%H:%M"),
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
                    body = (
                        f"System restarted after 15:30 with "
                        f"{open_count} open position(s): {symbols}. "
                        f"Broker RMS will auto-squareoff. Review trades manually tomorrow."
                    )
                    try:
                        self._notifier.send(
                            severity="CRITICAL",
                            title="EOD squareoff MISSED — open positions remain",
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
