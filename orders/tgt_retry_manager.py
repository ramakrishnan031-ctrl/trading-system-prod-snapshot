"""
orders/tgt_retry_manager.py — Trading System v2

Task (2026-06-19): standalone TGT retry mechanism.

FIX-190 Bug C made a TGT-only failure non-fatal: when the SL is placed but the
TGT cannot be (circuit band, rate limit, transient broker reject), the position
stays protected by the live SL and the system no longer HARD_KILLs. The gap that
left: the TGT was then never re-attempted, so a position rode to its SL or EOD
square-off with no profit target (the 19-Jun THELEELA TGT that failed at
10:00:28 and was never retried).

This manager closes that gap. OrderPlacer flags such a trade
(``trades.needs_tgt_retry=1``, via ``state_store.mark_needs_tgt_retry``); this
daemon wakes every ``poll_interval_sec`` and, for each flagged OPEN/PARTIAL
trade whose backoff window has elapsed, calls
``order_placer.retry_tgt_for_trade(trade_id)`` — which re-checks the SL is still
standing, re-clamps the TGT into the CURRENT circuit band (Bug D — the band may
have relaxed), places the TGT, and registers it for the software OCO. On success
the flag clears + Telegram INFO; after ``max_attempts`` failures it gives up +
Telegram WARNING (the position remains SL-protected).

Design notes:
  * State lives in the trades table (flag + count + last-attempt timestamp), so
    retries survive a restart — the loop reads candidates from the DB each cycle.
  * Exponential backoff: the Nth attempt waits ``backoff_base * 2**(N-1)`` sec
    (default 30/60/120/240/480 — give up after 5).
  * NEVER touches the SL and NEVER places a second TGT (OrderPlacer guards).
  * Skips while the kill switch is active (a flatten is in progress, not a
    target) and outside market hours (a resting TGT pre-open is pointless).
  * Parity-safe: no paper/live special-casing here; the adapter owns fill
    synthesis.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Any, List, Optional

from core.market_windows import (
    DEFAULT_MARKET_CLOSE,
    DEFAULT_MARKET_OPEN,
    is_within_market_hours,
)
from core.effect_telemetry import handle as _effect_handle
from core.time_authority import now_ist


class TGTRetryManager:
    """Periodically re-attempts TGT placement for SL-only-protected trades."""

    def __init__(
        self,
        *,
        state_store: Any,
        order_placer: Any,
        notifier: Any = None,
        kill_switch: Any = None,
        logger: Optional[logging.Logger] = None,
        poll_interval_sec: int = 30,
        max_attempts: int = 5,
        backoff_base_sec: int = 30,
        enabled: bool = True,
        mode: str = "LIVE",
        market_hours_guard: bool = True,
        crash_alert_threshold: int = 3,
        crash_realert_interval_sec: int = 3600,
    ) -> None:
        self._store = state_store
        self._placer = order_placer
        self._notifier = notifier
        self._kill_switch = kill_switch
        # effect-telemetry (ledger #1, frozen contract A2.3): dormant tripwire
        # — a TGT retry enacted (constructed-idle today: 0 acts ever).
        self._fx_retry = _effect_handle("tgt_retry")
        self._log = logger or logging.getLogger("tgt_retry_manager")
        self._poll_interval = max(1, int(poll_interval_sec))
        self._max_attempts = max(1, int(max_attempts))
        self._backoff_base = max(1, int(backoff_base_sec))
        self._enabled = bool(enabled)
        self._mode = mode
        self._market_hours_guard = market_hours_guard
        # Crash-loop self-detection (post-mortem 24-Jun): the loop must never die,
        # but a *persistent* cycle exception (e.g. a cross-module signature drift
        # like the 22-23 Jun is_within_market_hours TypeError) used to spin
        # silently — 840 ERRORs/day, ZERO alerts, the safety net dead for 2 days.
        # Now N consecutive failures fire ONE throttled CRITICAL so a dead safety
        # daemon surfaces in minutes, not days.
        self._crash_alert_threshold = max(1, int(crash_alert_threshold))
        self._crash_realert_interval_sec = max(1, int(crash_realert_interval_sec))
        self._consecutive_failures = 0
        self._last_crash_alert_at: Optional[datetime] = None
        self._last_clean_cycle_at: Optional[datetime] = None

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._cycle_lock = threading.Lock()  # non-reentrant cycle guard

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not self._enabled:
            self._log.info("tgt_retry_manager.disabled")
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="tgt-retry-manager", daemon=True
        )
        self._thread.start()
        self._log.info(
            "tgt_retry_manager.started",
            extra={
                "poll_interval_sec": self._poll_interval,
                "max_attempts": self._max_attempts,
                "backoff_base_sec": self._backoff_base,
            },
        )

    def stop(self) -> None:
        self._stop_event.set()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=5.0)
        self._thread = None

    def _loop(self) -> None:
        # Run one cycle at startup, then on the poll interval.
        while not self._stop_event.is_set():
            try:
                self.run_once()
            except Exception as exc:  # the loop must never die
                self._record_cycle_failure(exc)
            else:
                self._record_cycle_success()
            self._stop_event.wait(timeout=self._poll_interval)

    # ── Crash-loop self-detection (post-mortem 24-Jun) ─────────────────────────

    def _record_cycle_success(self) -> None:
        """A clean cycle (incl. a no-op skip — kill-switch / off-hours / not-due).
        Reset the failure counter; if we had been crash-looping, log + INFO the
        recovery so the resolution is as visible as the failure was."""
        if self._consecutive_failures >= self._crash_alert_threshold:
            self._log.warning(
                "tgt_retry_manager.recovered",
                extra={"after_consecutive_failures": self._consecutive_failures},
            )
            self._notify(
                severity="INFO",
                title=f"[{self._mode}] TGT retry manager recovered",
                body=(
                    f"TGTRetryManager resumed clean cycles after "
                    f"{self._consecutive_failures} consecutive failures; the TGT "
                    f"safety net is healthy again."
                ),
            )
        self._consecutive_failures = 0
        self._last_crash_alert_at = None
        self._last_clean_cycle_at = now_ist()

    def _record_cycle_failure(self, exc: Exception) -> None:
        """A cycle raised. Count it; once it crosses the threshold fire ONE
        throttled CRITICAL — a crash-looping safety daemon must surface in minutes,
        not sit dead for days (the 22-23 Jun is_within_market_hours regression)."""
        self._consecutive_failures += 1
        self._log.error(
            "tgt_retry_manager.cycle_error",
            extra={
                "error": str(exc),
                "consecutive_failures": self._consecutive_failures,
            },
            exc_info=True,
        )
        if (
            self._consecutive_failures >= self._crash_alert_threshold
            and self._crash_alert_due()
        ):
            self._fire_crash_alert(exc)

    def _crash_alert_due(self) -> bool:
        """First crossing alerts immediately; thereafter at most once per
        crash_realert_interval_sec while the failure persists (no 840-alert spam)."""
        if self._last_crash_alert_at is None:
            return True
        try:
            elapsed = (now_ist() - self._last_crash_alert_at).total_seconds()
        except Exception:
            return True
        return elapsed >= self._crash_realert_interval_sec

    def _fire_crash_alert(self, exc: Exception) -> None:
        self._last_crash_alert_at = now_ist()
        self._log.critical(
            "tgt_retry_manager.crash_loop",
            extra={
                "consecutive_failures": self._consecutive_failures,
                "error": str(exc),
                "exc_type": type(exc).__name__,
            },
        )
        self._notify(
            severity="CRITICAL",
            title=f"[{self._mode}] TGT retry manager crash-looping",
            body=(
                f"TGTRetryManager has failed {self._consecutive_failures} consecutive "
                f"cycles (every {self._poll_interval}s): {type(exc).__name__}: {exc}. "
                f"The TGT safety net is DOWN — a TGT left unplaced by FIX-190 Bug C "
                f"will not be retried (open positions remain SL-protected). "
                f"Investigate immediately."
            ),
        )

    def health_snapshot(self) -> dict:
        """Liveness snapshot for the /health endpoint (HC) + pre-flight Phase B.
        ``ok`` is False iff the worker thread should be running but is dead or in a
        sustained crash-loop — so a silently-dead safety daemon turns /health 503
        and shows up as a Phase-B failing check, not just a CRITICAL email."""
        enabled = self._enabled
        alive = bool(self._thread is not None and self._thread.is_alive())
        in_crash_loop = self._consecutive_failures >= self._crash_alert_threshold
        if not enabled:
            ok, state = True, "disabled"   # intentionally off is not unhealthy
        elif not alive:
            ok, state = False, "dead"
        elif in_crash_loop:
            ok, state = False, "crash_loop"
        else:
            ok, state = True, "running"
        return {
            "ok": ok,
            "state": state,
            "consecutive_failures": self._consecutive_failures,
            "last_clean_cycle_at": (
                self._last_clean_cycle_at.isoformat()
                if self._last_clean_cycle_at else None
            ),
        }

    # ── Core ──────────────────────────────────────────────────────────────────

    def _backoff_for(self, retry_count: int) -> int:
        """Seconds to wait before the (retry_count+1)-th attempt: base*2**count."""
        return self._backoff_base * (2 ** max(0, int(retry_count)))

    def _is_due(self, retry_count: int, last_retry_at: Optional[str], now: datetime) -> bool:
        """A candidate is due when at least backoff_for(count) seconds have passed
        since the last attempt. A missing/unparseable timestamp is treated as due."""
        if not last_retry_at:
            return True
        try:
            last = datetime.fromisoformat(last_retry_at)
        except (ValueError, TypeError):
            return True
        elapsed = (now - last).total_seconds()
        return elapsed >= self._backoff_for(retry_count)

    def run_once(self) -> List[str]:
        """One retry sweep. Returns the per-trade outcome strings (for tests)."""
        if not self._cycle_lock.acquire(blocking=False):
            return []  # a cycle is already running (RC13-style non-reentrancy)
        try:
            return self._run_once_locked()
        finally:
            self._cycle_lock.release()

    def _run_once_locked(self) -> List[str]:
        outcomes: List[str] = []

        # Guard: never place TGTs while a kill/flatten is in progress.
        if self._kill_switch is not None:
            try:
                if self._kill_switch.is_active():
                    return outcomes
            except Exception:
                pass  # fail-open: a kill-switch read error must not block retries

        # Guard: only place during market hours (a resting TGT pre-open/overnight
        # is pointless and may be rejected). Skippable for tests.
        now = now_ist()
        # is_within_market_hours(now_t, open_t, close_t) — 3 args (FIX-169 F18).
        # The prior 1-arg call raised TypeError every cycle, silently disabling
        # the retry sweep (the bug this P2 fix closes). Mirror the working callers
        # (token_monitor / gemini_watchman): pass now.time() + the session bounds.
        if self._market_hours_guard and not is_within_market_hours(
            now.time(), DEFAULT_MARKET_OPEN, DEFAULT_MARKET_CLOSE
        ):
            return outcomes

        try:
            candidates = self._store.get_tgt_retry_candidates()
        except Exception as exc:
            self._log.error(
                "tgt_retry_manager.candidate_query_failed",
                extra={"error": str(exc)},
            )
            return outcomes

        for cand in candidates:
            trade_id = cand["trade_id"]
            symbol = cand["symbol"]
            retry_count = int(cand["tgt_retry_count"] or 0)
            last_retry_at = cand["tgt_last_retry_at"]
            if not self._is_due(retry_count, last_retry_at, now):
                continue

            try:
                # effect-telemetry (frozen A2.3): a TGT retry enacted —
                # counted at dispatch, success or raise (dormancy tripwire).
                self._fx_retry.inc()
                outcome = self._placer.retry_tgt_for_trade(trade_id)
            except Exception as exc:
                self._log.error(
                    "tgt_retry_manager.retry_raised",
                    extra={"trade_id": trade_id, "symbol": symbol, "error": str(exc)},
                    exc_info=True,
                )
                outcome = "failed"
            outcomes.append(outcome)
            self._apply_outcome(trade_id, symbol, retry_count, outcome)

        return outcomes

    def _apply_outcome(
        self, trade_id: str, symbol: str, prior_count: int, outcome: str
    ) -> None:
        if outcome == "placed":
            self._store.clear_needs_tgt_retry(trade_id)
            attempts = prior_count + 1
            self._log.info(
                "tgt_retry_manager.placed",
                extra={"trade_id": trade_id, "symbol": symbol, "attempts": attempts},
            )
            self._notify(
                severity="INFO",
                title=f"[{self._mode}] TGT placed on retry — {symbol}",
                body=(
                    f"TGT placed for {symbol} ({trade_id}) on retry "
                    f"#{attempts}; position now has SL + TGT."
                ),
            )
            return

        if outcome in ("skipped_closed", "skipped_no_sl", "skipped_has_tgt"):
            # No longer applicable — clear the flag silently (DEBUG only).
            self._store.clear_needs_tgt_retry(trade_id)
            self._log.info(
                "tgt_retry_manager.cleared",
                extra={"trade_id": trade_id, "symbol": symbol, "reason": outcome},
            )
            return

        # "failed" or "skipped_unplaceable": count this attempt; give up at max.
        new_count = self._store.bump_tgt_retry(trade_id)
        if new_count >= self._max_attempts:
            self._store.clear_needs_tgt_retry(trade_id)
            self._log.warning(
                "tgt_retry_manager.gave_up",
                extra={
                    "trade_id": trade_id, "symbol": symbol,
                    "attempts": new_count, "last_outcome": outcome,
                },
            )
            self._notify(
                severity="WARNING",
                title=f"[{self._mode}] TGT retry gave up — {symbol}",
                body=(
                    f"TGT could not be placed for {symbol} ({trade_id}) after "
                    f"{new_count} attempts ({outcome}); position remains "
                    f"SL-protected (exits via SL or EOD square-off)."
                ),
            )
        else:
            self._log.info(
                "tgt_retry_manager.attempt_failed",
                extra={
                    "trade_id": trade_id, "symbol": symbol,
                    "attempt": new_count, "outcome": outcome,
                    "next_backoff_sec": self._backoff_for(new_count),
                },
            )

    def _notify(self, *, severity: str, title: str, body: str) -> None:
        if self._notifier is None:
            return
        try:
            self._notifier.send(
                severity=severity, title=title, body=body,
                source_module="tgt_retry_manager",
            )
        except Exception as exc:
            self._log.error("tgt_retry_manager.notify_failed", extra={"error": str(exc)})
