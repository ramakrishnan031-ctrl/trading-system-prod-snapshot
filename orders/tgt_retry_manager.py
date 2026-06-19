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

from core.market_windows import is_within_market_hours
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
    ) -> None:
        self._store = state_store
        self._placer = order_placer
        self._notifier = notifier
        self._kill_switch = kill_switch
        self._log = logger or logging.getLogger("tgt_retry_manager")
        self._poll_interval = max(1, int(poll_interval_sec))
        self._max_attempts = max(1, int(max_attempts))
        self._backoff_base = max(1, int(backoff_base_sec))
        self._enabled = bool(enabled)
        self._mode = mode
        self._market_hours_guard = market_hours_guard

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
                self._log.error(
                    "tgt_retry_manager.cycle_error", extra={"error": str(exc)},
                    exc_info=True,
                )
            self._stop_event.wait(timeout=self._poll_interval)

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
        if self._market_hours_guard and not is_within_market_hours(now):
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
