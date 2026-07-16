"""
capital/kill_switch.py -- Trading System v2

Purpose:
    System-wide trading halt mechanism. Three modes: INACTIVE (normal),
    SOFT_KILL (block new entries, allow exits), HARD_KILL (block ALL orders,
    attempt broker cancellation). Last-mile gate per Project Rule 13.

Locked Design Decisions:
    KS1  -- Three modes: INACTIVE / SOFT_KILL / HARD_KILL.
            is_active(intent) tests by intent type.
    KS2  -- Single state: KillState enum. Persisted to kill_switch_state
            table for restart recovery.
    KS3  -- Startup recovery: constructor reads persisted state. If state
            != INACTIVE, log CRITICAL and remain halted. Operator must
            call resume() to clear. Audit Issue #18 fix.
    KS4  -- Deadlock prevention: RLock not Lock. soft_kill() / hard_kill()
            may call internal methods that re-acquire the lock. RLock
            allows reentrant acquisition by the same thread.
            Audit Issue #4 fix.
    KS5  -- Constructor: KillSwitch(state_store, bus, logger,
            on_hard_kill_cancel_fn=None, api_failure_threshold=3,
            enable_auto_trip=True).
    KS6  -- Public API: is_active(intent), current_state(), status(),
            soft_kill(), hard_kill(), resume(), record_api_failure(),
            record_success().
    KS7  -- Auto-trip: api_failure_threshold consecutive failures trigger
            soft_kill(). Disabled when enable_auto_trip=False.
    KS8  -- KillSwitchActivated event on every state change including
            resume(). Payload includes previous_state, new_state,
            triggered_by (per KS8 extension).
    KS9  -- State persisted in kill_switch_state table (single row,
            id=1). Persist BEFORE in-memory update. If persist fails,
            abort: no state change, no event.
    KS10 -- get_kill_info() convenience method for startup_checks /
            --status flag. Audit Issue: was missing.
    KS11 -- Layer 4 (capital/). Imports: stdlib, core.exceptions,
            core.events, core.logger, core.time_authority, core.state_store.
            NO cancel logic inside (injected callback).
    KS12 -- Deterministic: is_active() depends ONLY on current state.
    KS13 -- NOT in scope: deciding when to auto-trip, performing broker
            cancellations, restart after halt.

What This Module Does NOT Do:
    - Does not decide WHEN to call record_api_failure (callers decide)
    - Does not perform broker order cancellations (injected callback)
    - Does not restart trading after halt (manual resume() only)
    - Does not send Telegram alerts (logger CRITICAL is the signal)
    - Does not import from any layer above capital/
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Callable, List, Optional, TYPE_CHECKING

from broker.position_helpers import determine_close_direction  # FIX-190 (Bug A)
from core.constants import PRODUCT_TO_INTENT as _PRODUCT_TO_INTENT

# Part 11 (FIX-180): HARD_KILL emergency-exit retry guards.
# Max wall-clock time to keep retrying a trade that won't exit before we stop
# the loop and escalate (instead of looping forever and freezing the thread).
_HARD_KILL_MAX_RETRY_HOURS = 2.0
# Per-trade Telegram dedup window for the "exit failed" escalation alert.
_EXIT_ALERT_DEDUP_SEC = 300.0
# Throttle for the actionable Kite IP-allowlist (403) alert: one per hour, so a
# burst of failed entries does not spam the channel (the system self-recovers on
# the next signal once the IP is allowlisted).
_IP403_ALERT_THROTTLE_SEC = 3600.0
from core.events import EventBus, KillSwitchActivated
from core.exceptions import (
    BrokerAuthError,
    BrokerRateLimitError,
    BrokerTimeoutError,
)
from core.time_authority import now_ist

if TYPE_CHECKING:
    import logging
    from core.state_store import StateStore

# DUP-1 (2026-04-26 audit): _IST removed; never read locally.

# Kill reasons that are part of normal daily operations (safe to auto-clear on
# next startup when no open positions exist). Emergency kills are everything
# else — they require manual --resume.
SCHEDULED_KILL_REASONS = frozenset({
    "circuit_breaker_force_close_15:15",
    "EOD_SQUAREOFF",
})


def _is_scheduled_reason(reason: str) -> bool:
    """Return True if the kill reason matches a scheduled (non-emergency) pattern."""
    return reason in SCHEDULED_KILL_REASONS


# ─────────────────────────────────────────────────────────────────────────────
# Types
# ─────────────────────────────────────────────────────────────────────────────

class KillState(Enum):
    """The three operational modes of the kill switch (KS1)."""
    INACTIVE  = "INACTIVE"
    SOFT_KILL = "SOFT_KILL"
    HARD_KILL = "HARD_KILL"


@dataclass(frozen=True)
class CancellationReport:
    """
    Result of attempting to cancel all open broker orders on hard_kill() (KS6).

    Fields:
        attempted: number of orders the callback tried to cancel
        succeeded: number successfully cancelled
        failed:    list of order IDs that could not be cancelled
    """
    attempted: int
    succeeded: int
    failed: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# KillSwitch
# ─────────────────────────────────────────────────────────────────────────────

class KillSwitch:
    """
    System-wide trading halt mechanism (KS1-KS13).

    Thread-safe: all public methods acquire self._lock (RLock) before reading
    or mutating state. RLock (not Lock) was Audit Issue #4's fix for the
    record_api_failure -> soft_kill reentrant deadlock (KS4). M-C4 (16-Jul-2026)
    since moved that auto-trip call OUTSIDE the lock — so the lock is never held
    across soft_kill's publish/send — and that path no longer re-enters; RLock is
    retained (other internal calls may still re-acquire; reentrancy-safe).

    Usage::
        ks = KillSwitch(
            state_store=store,
            bus=bus,
            logger=get_logger(__name__),
            on_hard_kill_cancel_fn=order_placer.cancel_all_open,
            api_failure_threshold=3,
            enable_auto_trip=True,
        )
        if ks.is_active():
            return  # block new entry orders
    """

    def __init__(
        self,
        state_store: "StateStore",
        bus: EventBus,
        logger: "logging.Logger",
        on_hard_kill_cancel_fn: Optional[Callable[[], object]] = None,
        api_failure_threshold: int = 3,
        enable_auto_trip: bool = True,
        notifier: Optional[object] = None,   # TelegramNotifier; optional
        mode: str = "LIVE",                   # session mode label for alert title
        adapter: Optional[object] = None,    # FIX-087: ZerodhaAdapter for indestructible exits
        emergency_exit_buffer_pct: float = 0.01,  # FIX-181: marketable-LIMIT buffer
    ) -> None:
        self._store = state_store
        self._bus = bus
        self._log = logger
        self._cancel_fn = on_hard_kill_cancel_fn
        self._threshold = api_failure_threshold
        self._auto_trip = enable_auto_trip
        self._notifier = notifier
        self._mode = mode
        self._adapter = adapter  # FIX-087
        # FIX-181: HARD_KILL exits use a marketable LIMIT (LTP ± buffer) instead
        # of MARKET so they fill but cap worst-case slippage. The adapter snaps
        # the price to a valid tick, so we pass the raw LTP ± buffer here.
        self._emergency_exit_buffer_pct = emergency_exit_buffer_pct

        # KS4: RLock allows same-thread reentrant acquisition (deadlock fix).
        self._lock = threading.RLock()

        # In-memory state — authoritative after construction
        self._state = KillState.INACTIVE
        self._reason = ""
        self._triggered_at: Optional[datetime] = None
        self._triggered_by = ""
        self._api_failure_count = 0

        # Part 11 (FIX-180): per-trade timestamp of the last "exit failed"
        # escalation alert, for 5-min Telegram dedup during the retry loop.
        self._exit_alert_ts: dict[str, float] = {}

        # Monotonic ts of the last Kite IP-403 actionable alert (1/hr throttle);
        # None = never alerted (so the first IP-403 always alerts).
        self._ip403_last_alert_ts: Optional[float] = None

        # KS3: recover persisted state on startup (Audit Issue #18 fix)
        self._load_state_from_store()

    def clear_stale_state(self, today: "date") -> bool:
        """Auto-clear ANY kill switch triggered on a PREVIOUS calendar day.

        HEADLESS GUARANTEE (Rama's 2026-06-20 decision): a new trading day ALWAYS
        starts with a clean slate — EVERY prior-day kill is cleared regardless of
        type (SOFT_KILL / HARD_KILL, scheduled, emergency, loss-limit, System
        Manager EOD). The system never blocks the next-day startup; the safety net
        shifts from "block startup" to the EOD report's analysis of what was
        cleared (Task B). Each clear is audited to system_events
        (event_type=KILL_AUTO_CLEARED) so that report has the data.

        Same-day kills are intentionally NOT touched here (triggered_date >= today
        returns False) — within a trading day an active kill stays active (loss
        limit, HARD_KILL, etc. persist correctly). Returns True if cleared.
        """
        with self._lock:
            if self._state == KillState.INACTIVE:
                return False
            if self._triggered_at is None:
                return False
            triggered_date = self._triggered_at.date()
            if triggered_date >= today:
                return False  # same-day (or future-dated) kill — must persist within the day

            prev_reason = self._reason
            prev_by = self._triggered_by
            prev_state = self._state
            prev_triggered_at = self._triggered_at
            ts = now_ist()
            clear_reason = (
                f"auto_clear_stale: was {prev_state.value} from {triggered_date.isoformat()} "
                f"(reason={prev_reason}, by={prev_by})"
            )

            self._persist_state(KillState.INACTIVE, clear_reason, ts, "main.auto_clear_stale")
            self._state = KillState.INACTIVE
            self._reason = clear_reason
            self._triggered_at = ts
            self._triggered_by = "main.auto_clear_stale"

        # Audit + log OUTSIDE the lock (the insert opens its own transaction).
        self._record_cleared_kill(
            prev_state, prev_reason, prev_by, prev_triggered_at, "clear_stale_state",
        )
        self._log.warning(
            "Kill switch auto-cleared: prior %s from %s (reason=%s by=%s) "
            "-- new day %s starts clean (HEADLESS); audited to system_events",
            prev_state.value, triggered_date, prev_reason, prev_by, today,
        )
        return True

    def auto_clear_scheduled_kill(self) -> bool:
        """Auto-clear kill switch if reason is a scheduled daily operation.

        Scheduled kills (force_close at 15:15, EOD squareoff) are normal daily
        events that should not block the next startup. This clears them
        regardless of date — even same-day restarts — as long as there are no
        open positions. HARD_KILL is never auto-cleared.

        Returns True if state was cleared, False if no action taken.
        """
        with self._lock:
            if self._state == KillState.INACTIVE:
                return False

            if self._state == KillState.HARD_KILL:
                self._log.warning(
                    "HARD_KILL active (reason=%s). Cannot auto-clear. "
                    "Manual --resume required.",
                    self._reason,
                )
                return False

            reason = self._reason
            if not _is_scheduled_reason(reason):
                self._log.warning(
                    "Kill switch active with EMERGENCY reason %r (triggered_by=%s). "
                    "Manual --resume required.",
                    reason, self._triggered_by,
                )
                return False

            open_count = self._count_open_positions()
            if open_count > 0:
                self._log.warning(
                    "Scheduled kill switch (%s) but %d open positions remain. "
                    "Manual --resume required.",
                    reason, open_count,
                )
                return False

            prev_state = self._state
            prev_reason = reason
            prev_by = self._triggered_by
            prev_triggered_at = self._triggered_at
            ts = now_ist()
            clear_reason = (
                f"auto_clear_scheduled: was {prev_state.value} "
                f"(reason={prev_reason}, by={prev_by}), no open positions"
            )

            self._persist_state(KillState.INACTIVE, clear_reason, ts, "auto_clear_scheduled")
            self._state = KillState.INACTIVE
            self._reason = clear_reason
            self._triggered_at = ts
            self._triggered_by = "auto_clear_scheduled"

        self._record_cleared_kill(
            prev_state, prev_reason, prev_by, prev_triggered_at, "auto_clear_scheduled",
        )
        self._log.info(
            "Auto-cleared scheduled kill switch: %s (was %s, reason=%s, by=%s)",
            clear_reason, prev_state.value, prev_reason, prev_by,
        )
        return True

    def _count_open_positions(self) -> int:
        """Count trades with live exposure (OPEN, PARTIAL, or PENDING_FILL)."""
        try:
            row = self._store.fetch_one(
                "SELECT COUNT(*) as cnt FROM trades "
                "WHERE status IN ('OPEN', 'PARTIAL', 'PENDING_FILL')"
            )
            return row["cnt"] if row else 0
        except Exception as exc:
            self._log.error("Failed to count open positions: %s; assuming non-zero", exc)
            return 1

    def _record_cleared_kill(
        self,
        prev_state: KillState,
        prev_reason: str,
        prev_by: str,
        prev_triggered_at: Optional[datetime],
        cleared_via: str,
    ) -> None:
        """Audit an auto-cleared kill to system_events (event_type
        KILL_AUTO_CLEARED) so the EOD report (Task B) can analyse what the headless
        startup cleared — especially a prior-day HARD_KILL / emergency that no
        longer blocks trading. Best-effort: a failure here must NEVER block the
        clear (the headless guarantee comes first)."""
        try:
            import json
            details = json.dumps({
                "previous_state": prev_state.value,
                "reason": prev_reason,
                "triggered_by": prev_by,
                "triggered_at": (
                    prev_triggered_at.isoformat() if prev_triggered_at else None
                ),
                "classification": (
                    "scheduled" if _is_scheduled_reason(prev_reason) else "emergency"
                ),
                "cleared_via": cleared_via,
            })
            self._store.insert_system_event(
                event_type="KILL_AUTO_CLEARED",
                timestamp=now_ist().isoformat(),
                details=details,
            )
        except Exception as exc:
            self._log.error(
                "kill_switch: failed to audit cleared kill (%s): %s", cleared_via, exc
            )

    def set_notifier(
        self,
        notifier: Optional[object],
        mode: Optional[str] = None,
    ) -> None:
        """Wire TelegramNotifier after construction.

        main.py builds KillSwitch BEFORE the notifier (notifier needs config
        that is loaded after KS is used by startup checks). This setter lets
        main wire the notifier after it is constructed so soft_kill alerts
        can still fire. ``mode`` may also be refreshed here since interactive
        startup can change args.mode after KS construction.
        """
        self._notifier = notifier
        if mode is not None:
            self._mode = mode

    def set_adapter(self, adapter: object) -> None:
        """Wire ZerodhaAdapter after construction (FIX-166 F22).

        main.py builds KillSwitch BEFORE the adapter. This setter lets main
        wire the adapter so hard_kill can exit positions via
        ``_exit_all_trades_indestructible``.
        """
        self._adapter = adapter

    # ─────────────────────────────────────────────────────────────────────────
    # Read API (KS6, KS10, KS12)
    # ─────────────────────────────────────────────────────────────────────────

    def is_active(self, intent: str = "entry") -> bool:
        """
        Return True if the kill switch should block the given intent (KS6).

        intent="entry"  -> True for SOFT_KILL or HARD_KILL
        intent="exit"   -> True only for HARD_KILL (soft_kill allows exits)
        intent="any"    -> True for SOFT_KILL or HARD_KILL
        """
        with self._lock:
            if intent in ("entry", "any"):
                return self._state in (KillState.SOFT_KILL, KillState.HARD_KILL)
            elif intent == "exit":
                return self._state == KillState.HARD_KILL
            else:
                raise ValueError(
                    f"Unknown intent {intent!r}. Must be 'entry', 'exit', or 'any'."
                )

    def current_state(self) -> KillState:
        """Return the current KillState enum value."""
        with self._lock:
            return self._state

    def status(self) -> dict:
        """
        Return current status as a dict (KS6).

        Keys: state, reason, triggered_at (ISO str or None), triggered_by.
        """
        with self._lock:
            return {
                "state":        self._state.value,
                "reason":       self._reason,
                "triggered_at": (
                    self._triggered_at.isoformat()
                    if self._triggered_at else None
                ),
                "triggered_by": self._triggered_by,
            }

    def get_kill_info(self) -> dict:
        """
        Convenience method returning the same dict as status() (KS10).
        Exists because audit Issue #10 found calls to a non-existent
        get_kill_info() method in the old codebase. Callers can use either
        name; both are identical.
        """
        return self.status()

    # ─────────────────────────────────────────────────────────────────────────
    # Mutation API (KS6)
    # ─────────────────────────────────────────────────────────────────────────

    def soft_kill(self, reason: str, triggered_by: str = "system") -> None:
        """
        Activate SOFT_KILL: block new entry orders; allow exits and monitoring.

        Idempotent: no-op if already SOFT_KILL (DEBUG log).
        Ignored if already HARD_KILL (WARNING log — cannot downgrade).
        Persist-first: if state_store write fails, in-memory state is NOT
        changed and no event is published (KS9 atomicity).
        """
        with self._lock:
            if self._state == KillState.SOFT_KILL:
                self._log.debug(
                    "soft_kill() called but already SOFT_KILL (no-op): reason=%s", reason
                )
                return
            if self._state == KillState.HARD_KILL:
                self._log.warning(
                    "soft_kill() ignored: cannot downgrade HARD_KILL -> SOFT_KILL "
                    "(reason=%s, triggered_by=%s)",
                    reason, triggered_by,
                )
                return

            prev_state = self._state
            ts = now_ist()

            # Persist FIRST — if this raises, abort (KS9)
            self._persist_state(KillState.SOFT_KILL, reason, ts, triggered_by)

            # Only reached on successful persist
            self._state = KillState.SOFT_KILL
            self._reason = reason
            self._triggered_at = ts
            self._triggered_by = triggered_by

        # Publish and log outside the lock (bus handlers must not re-enter)
        self._publish_event("soft", reason, prev_state, KillState.SOFT_KILL, triggered_by)
        self._log.critical(
            "SOFT_KILL ACTIVATED reason=%s triggered_by=%s", reason, triggered_by
        )

        # HALT alert. FIX-191 addendum (23-Jun-2026): severity CRITICAL (was WARN)
        # so a trading halt reaches the operator via the notifier's CRITICAL
        # email-fallback path even when Telegram is down (a halt IS critical-grade;
        # WARN drops silently on a send failure with no fallback). Routing/severity
        # ONLY — the kill stays a SOFT_KILL. (Never crash on notifier failure.)
        if self._notifier is not None:
            try:
                self._notifier.send(
                    severity="CRITICAL",
                    title=f"[{self._mode}] ⚠️ SOFT KILL ACTIVATED",
                    body=(
                        f"Reason: {reason}\n"
                        "New signals: BLOCKED | Open positions: managed to SL/TGT/EOD"
                    ),
                    source_module="kill_switch",
                )
            except Exception as exc:
                self._log.error("kill_switch: soft_kill notifier.send failed: %s", exc)

    def hard_kill(
        self, reason: str, triggered_by: str = "system"
    ) -> CancellationReport:
        """
        Activate HARD_KILL: block ALL orders, attempt to cancel open broker orders.

        Returns CancellationReport from on_hard_kill_cancel_fn (empty if not set).
        Idempotent: if already HARD_KILL, re-runs cancellation attempt in case
        orders reappeared (no state change, no event re-published).
        Persist-first atomicity same as soft_kill().
        """
        with self._lock:
            prev_state = self._state
            ts = now_ist()

            if self._state != KillState.HARD_KILL:
                # Persist FIRST — if this raises, abort (KS9)
                self._persist_state(KillState.HARD_KILL, reason, ts, triggered_by)

                # Update in-memory state
                self._state = KillState.HARD_KILL
                self._reason = reason
                self._triggered_at = ts
                self._triggered_by = triggered_by

                # Publish and log outside lock scope is preferred, but we need the
                # report first. Release lock for event publication below.
                do_publish = True
            else:
                self._log.warning(
                    "hard_kill() called but already HARD_KILL; re-running cancellation "
                    "(reason=%s triggered_by=%s)", reason, triggered_by
                )
                do_publish = False

        if do_publish:
            self._publish_event(
                "hard", reason, prev_state, KillState.HARD_KILL, triggered_by
            )
            self._log.critical(
                "HARD_KILL ACTIVATED reason=%s triggered_by=%s", reason, triggered_by
            )

        report = self._run_cancel()
        return report

    def resume(self, reason: str, resumed_by: str) -> None:
        """
        Resume trading: set state to INACTIVE.

        Manual operator action only. Logs INFO with reason.
        Persists first. Publishes KillSwitchActivated(kill_type='resume').

        Raises:
            ValueError: if already INACTIVE, or if resumed_by is empty.
        """
        if not resumed_by:
            raise ValueError("resumed_by must be non-empty (required for audit trail)")
        with self._lock:
            if self._state == KillState.INACTIVE:
                raise ValueError(
                    "Cannot resume: kill switch is already INACTIVE"
                )

            prev_state = self._state
            ts = now_ist()

            # Persist FIRST (KS9)
            self._persist_state(KillState.INACTIVE, reason, ts, resumed_by)

            self._state = KillState.INACTIVE
            self._reason = reason
            self._triggered_at = ts
            self._triggered_by = resumed_by

        self._publish_event("resume", reason, prev_state, KillState.INACTIVE, resumed_by)
        self._log.info(
            "Kill switch RESUMED reason=%s resumed_by=%s previous_state=%s",
            reason, resumed_by, prev_state.value,
        )

    def record_api_failure(self, exc: Optional[BaseException] = None) -> None:
        """
        Increment the consecutive API failure counter.
        If counter reaches api_failure_threshold AND enable_auto_trip is True
        AND state is currently INACTIVE, auto-trigger soft_kill() (KS7).
        The RLock (KS4) prevents deadlock when soft_kill() re-acquires the lock
        from within the same thread.

        FIX-185: a BrokerAuthError (auth/permission, e.g. Zerodha 403 "IP not
        allowed to place orders") is a CONFIGURATION/credential problem, not the
        kind of transient API failure this consecutive-failure circuit breaker is
        meant for. Retrying never clears it, so counting it toward the auto-trip
        only produces a misleading "consecutive API failures" SOFT_KILL (which on
        the 18-Jun IP-allowlist incident then HALT-crash-looped the service on
        restart). Such errors are surfaced via CRITICAL logs/alerts at the call
        site and must NOT increment the counter. Callers forward the caught
        exception; ``exc=None`` preserves the legacy "always count" behaviour.

        FIX-191 (23-Jun-2026): generalised to a WHITELIST — only
        BrokerTimeoutError and BrokerRateLimitError (genuine transient/
        connectivity failures) count toward the auto-trip. Business rejections
        (OrderRejectedError from a broker reject OR the client-side slippage
        guard, ProductNotSupportedError, SLUnplaceableError, generic BrokerError)
        are NOT outages and never trip this connectivity breaker.
        """
        if isinstance(exc, BrokerAuthError):
            self._log.critical(
                "record_api_failure: BrokerAuthError NOT counted toward auto-trip "
                "(config/credential error, not a transient API failure): %s", exc,
            )
            # Kite IP-allowlist (403): the token is valid; only the VM IP needs
            # updating. Fire ONE actionable alert/hour with the IP + exact steps.
            # Trading self-recovers on the next signal once allowlisted (no halt,
            # no restart) — record_api_failure deliberately does NOT trip here.
            self._maybe_alert_ip403(exc)
            return

        # FIX-191 (23-Jun-2026 false SOFT_KILL): this is a CONNECTIVITY breaker —
        # it exists to halt trading when the broker API is unreachable (the FIX-069
        # timeout/rate-limit path). ONLY those two transient types may count. A
        # business rejection is NOT an outage and must never trip it:
        #   - OrderRejectedError covers BOTH a broker order-reject (e.g. MIS/F&O-ban
        #     block) AND the client-side slippage-guard abort (order_placer raises
        #     OrderRejectedError BEFORE any broker call) — declining a bad fill is
        #     correct behaviour, not a failure.
        #   - SLUnplaceableError / ProductNotSupportedError / generic BrokerError are
        #     likewise not connectivity outages.
        # On 23-Jun, 1 MIS-block + 2 slippage aborts (all OrderRejectedError) tripped
        # a false SOFT_KILL that 403'd every signal for the rest of the day. So:
        # WHITELIST the genuine transient types; log-and-ignore everything else.
        # exc=None keeps the legacy "always count" path (manual/test callers).
        if exc is not None and not isinstance(
            exc, (BrokerTimeoutError, BrokerRateLimitError)
        ):
            self._log.warning(
                "record_api_failure: %s NOT counted toward auto-trip "
                "(not a transient connectivity error; breaker is connectivity-only)",
                type(exc).__name__,
            )
            return

        # M-C4 (16-Jul-2026): COUNT + DECIDE inside the lock; TRIP outside it.
        # Calling soft_kill() from INSIDE this `with` held self._lock across
        # soft_kill's bus.publish (a slow subscriber) AND its Telegram send
        # (network I/O) — soft_kill releases only its own reentrant acquisition,
        # never this outer one. That blocked is_active()/current_state() — the
        # last-mile order gate checked on every entry/exit — on every thread for
        # the duration of that I/O, exactly during a broker wobble. So: capture
        # the decision + reason under the lock, release, then trip.
        with self._lock:
            self._api_failure_count += 1
            should_trip = (
                self._auto_trip
                and self._api_failure_count >= self._threshold
                and self._state == KillState.INACTIVE
            )
            # Built under the lock so it reports the count at the moment of the
            # decision (byte-identical to the pre-fix message).
            trip_reason = (
                f"Auto-trip: {self._api_failure_count} consecutive "
                f"API failures (threshold={self._threshold})"
            ) if should_trip else ""

        # Lock RELEASED. soft_kill re-acquires it briefly for the persist + state
        # mutation, then publishes/sends with NO lock held. A concurrent
        # double-trip is collapsed by soft_kill's own in-lock idempotency check
        # (already-SOFT_KILL -> return, no republish/renotify), so releasing here
        # cannot produce a second publish or send.
        if should_trip:
            self.soft_kill(reason=trip_reason, triggered_by="auto_trip")

    def record_success(self) -> None:
        """Reset the consecutive API failure counter on any successful API call."""
        with self._lock:
            self._api_failure_count = 0

    def _maybe_alert_ip403(self, exc: object) -> None:
        """If `exc` is a Kite IP-allowlist 403, send ONE actionable CRITICAL alert
        per hour: the VM's current public IP + the exact steps to fix it. The
        token is VALID (do not invalidate it), and new entries self-recover on the
        next signal once the IP is allowlisted — so this is an alert, not a halt.

        Runs OUTSIDE self._lock (record_api_failure returns before acquiring it),
        so the public-IP network probe never blocks the lock. Best-effort: any
        failure here is logged and swallowed — it must never affect trading."""
        try:
            from broker.auth_recovery import (
                build_ip403_alert_body,
                classify_broker_auth_error,
                get_public_ip,
            )
            if classify_broker_auth_error(exc) != "IP_NOT_ALLOWLISTED":
                return
            import time
            now_mono = time.monotonic()
            if (self._ip403_last_alert_ts is not None
                    and (now_mono - self._ip403_last_alert_ts) < _IP403_ALERT_THROTTLE_SEC):
                return  # already alerted within the last hour
            self._ip403_last_alert_ts = now_mono

            if self._notifier is None:
                self._log.critical(
                    "KITE IP NOT ALLOWLISTED (no notifier wired to alert): %s", exc
                )
                return
            ip = get_public_ip()
            self._notifier.send(
                severity="CRITICAL",
                title=f"[{self._mode}] 🚫 KITE IP NOT ALLOWLISTED",
                body=build_ip403_alert_body(ip, str(exc)),
                source_module="kill_switch",
            )
            self._log.critical(
                "KITE IP NOT ALLOWLISTED — actionable alert sent (VM IP %s). New "
                "entries self-recover on the next signal once allowlisted.", ip,
            )
        except Exception as alert_exc:  # noqa: BLE001 — alerting must never raise
            self._log.error("kill_switch: IP-403 alert failed: %s", alert_exc)

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _persist_state(
        self,
        state: KillState,
        reason: str,
        ts: datetime,
        triggered_by: str,
    ) -> None:
        """
        Write (or replace) the single kill_switch_state row (KS9).
        Raises on state_store failure — caller must treat this as abort.
        """
        with self._store.transaction() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO kill_switch_state
                  (id, state, reason, triggered_at, triggered_by)
                VALUES (1, ?, ?, ?, ?)
                """,
                (state.value, reason, ts.isoformat(), triggered_by),
            )

    def _load_state_from_store(self) -> None:
        """
        Read persisted state from state_store on construction (KS3).
        If a non-INACTIVE state is found, log CRITICAL and use it.
        Operator must call resume() to clear.
        """
        try:
            row = self._store.fetch_one(
                "SELECT state, reason, triggered_at, triggered_by "
                "FROM kill_switch_state WHERE id = 1"
            )
        except Exception as exc:
            self._log.error(
                "KillSwitch: could not read persisted state: %s; starting INACTIVE",
                exc,
            )
            return

        if row is None:
            return  # No persisted state — start INACTIVE

        try:
            state = KillState(row["state"])
        except ValueError:
            self._log.error(
                "KillSwitch: unknown persisted state %r; ignoring, starting INACTIVE",
                row["state"],
            )
            return

        if state != KillState.INACTIVE:
            self._state = state
            self._reason = row["reason"]
            self._triggered_by = row["triggered_by"]
            try:
                self._triggered_at = datetime.fromisoformat(row["triggered_at"])
            except (ValueError, TypeError):
                self._triggered_at = None

            self._log.critical(
                "KILL SWITCH ACTIVE AT STARTUP: state=%s reason=%s triggered_by=%s "
                "-- operator must call resume() to clear (Audit Issue #18 fix)",
                state.value, self._reason, self._triggered_by,
            )

    def _publish_event(
        self,
        kill_type: str,
        reason: str,
        prev_state: KillState,
        new_state: KillState,
        triggered_by: str,
    ) -> None:
        """Publish KillSwitchActivated; log ERROR on dispatch failure (never raises)."""
        try:
            self._bus.publish(
                KillSwitchActivated(
                    source_module="capital.kill_switch",
                    kill_type=kill_type,
                    reason=reason,
                    previous_state=prev_state.value,
                    new_state=new_state.value,
                    triggered_by=triggered_by,
                )
            )
        except Exception as exc:
            self._log.error(
                "KillSwitch: failed to publish KillSwitchActivated event: %s", exc
            )

    def _run_cancel(self) -> CancellationReport:
        """
        FIX-087: Indestructible per-trade exit loop.

        If adapter is set, fetch all open trades and exit each with MARKET orders.
        Each trade is wrapped in try/except; failed trades are retried infinitely
        with exponential backoff (5s, 15s, 45s, then capped at 45s). This is
        intentional - during HARD_KILL the system MUST NOT give up on flattening.

        If adapter is not set, falls back to legacy callback (on_hard_kill_cancel_fn).
        """
        # FIX-087: New indestructible exit logic if adapter is available
        if self._adapter is not None:
            return self._exit_all_trades_indestructible()

        # Legacy callback path (backward compat)
        if self._cancel_fn is None:
            return CancellationReport(attempted=0, succeeded=0, failed=[])
        try:
            result = self._cancel_fn()
            # Accept both CancellationReport and plain dict from callbacks
            if isinstance(result, CancellationReport):
                return result
            if isinstance(result, dict):
                return CancellationReport(
                    attempted=result.get("attempted", 0),
                    succeeded=result.get("succeeded", 0),
                    failed=list(result.get("failed", [])),
                )
            self._log.warning(
                "on_hard_kill_cancel_fn returned unexpected type %s; "
                "using empty report", type(result).__name__
            )
            return CancellationReport(attempted=0, succeeded=0, failed=[])
        except Exception as exc:
            self._log.critical(
                "on_hard_kill_cancel_fn raised %s: %s", type(exc).__name__, exc
            )
            return CancellationReport(attempted=0, succeeded=0, failed=[str(exc)])

    def _is_position_flat(self, symbol: str) -> bool:
        """
        Part 11 (FIX-180): True if the broker reports no open position for
        `symbol`.

        Used to avoid re-firing an emergency MARKET exit on a position that is
        already closed (manually at the broker, or an earlier exit that filled).
        Re-firing on a flat position would open a NEW naked position — the exact
        failure that caused repeated SULA exit attempts on first-live-day.

        On any broker error this returns False (cannot confirm flat) so the
        caller falls back to attempting the exit — the kill switch must always
        err toward flattening, never toward leaving a position open.

        Parity: self._adapter is the paper adapter in PAPER mode and the Zerodha
        adapter in LIVE mode, so both paths run this check identically.
        """
        # Fully defensive: this runs inside the "indestructible" exit loop, so
        # ANY failure (broker error, non-iterable/garbage payload, bad qty type)
        # must degrade to "cannot confirm flat" -> return False -> attempt exit,
        # never crash the loop.
        try:
            positions = self._adapter.get_positions()
            for p in positions:
                if getattr(p, "symbol", None) != symbol:
                    continue
                if abs(int(getattr(p, "qty", 0) or 0)) > 0:
                    return False   # position still open
                return True        # symbol present, qty 0 -> flat
            return True            # symbol not present -> flat
        except Exception as exc:
            self._log.warning(
                "kill_switch: could not verify flat for %s (%s); will attempt exit",
                symbol, exc,
            )
            return False

    def _fetch_ltp(self, symbol: str) -> Optional[float]:
        """
        FIX-181: best-effort LTP via the broker adapter for marketable-LIMIT
        emergency exits. Mirrors order_placer._fetch_ltp (adapter.get_quote_raw,
        the same source _check_liquidity uses). Returns None on any error so the
        caller falls back to a MARKET exit (a LIMIT needs a price).
        """
        if self._adapter is None:
            return None
        try:
            raw_quote = self._adapter.get_quote_raw([f"NSE:{symbol}"])
            if not raw_quote:
                return None
            q = raw_quote.get(f"NSE:{symbol}")
            if not q:
                return None
            ltp = float(q.get("last_price", 0) or 0)
            return ltp if ltp > 0 else None
        except Exception:
            return None

    def _marketable_exit_params(
        self, symbol: str, exit_side: str
    ) -> tuple[str, float]:
        """
        FIX-181: compute (order_type, price) for a forced exit. Returns a
        marketable LIMIT (LTP ± buffer) when an LTP is available, else falls
        back to MARKET. The adapter snaps the LIMIT price to a valid tick.

            SELL exit -> price below LTP (sell lower to ensure fill)
            BUY  exit -> price above LTP (buy higher to ensure fill)
        """
        ltp = self._fetch_ltp(symbol)
        if not ltp or ltp <= 0:
            return "MARKET", 0.0
        buf = self._emergency_exit_buffer_pct
        if exit_side == "SELL":
            return "LIMIT", ltp * (1.0 - buf)
        return "LIMIT", ltp * (1.0 + buf)

    def _alert_exit_failed(self, failed_trades: list) -> None:
        """
        Part 11 (FIX-180): escalate trades that could not be exited within the
        max retry window via CRITICAL Telegram, with a per-trade 5-min dedup so
        we do not spam the channel every retry cycle.
        """
        if self._notifier is None:
            return
        import time
        now_mono = time.monotonic()
        for trade_id, symbol, exit_side, qty, intent in failed_trades:
            last = self._exit_alert_ts.get(trade_id, 0.0)
            if now_mono - last < _EXIT_ALERT_DEDUP_SEC:
                continue
            self._exit_alert_ts[trade_id] = now_mono
            try:
                self._notifier.send(
                    severity="CRITICAL",
                    title=f"[{self._mode}] HARD_KILL EXIT FAILED -- {symbol}",
                    body=(
                        f"Could not exit {symbol} ({exit_side} x{qty}) within "
                        f"{_HARD_KILL_MAX_RETRY_HOURS:.0f}h of HARD_KILL.\n"
                        f"MANUAL INTERVENTION REQUIRED — verify/flatten at broker.\n"
                        f"Trade: {trade_id}"
                    ),
                    source_module="kill_switch",
                )
            except Exception:
                pass

    def _mark_trade_exiting(self, trade_id: str) -> None:
        """FIX-190: mark a trade EXITING (best-effort; broker truth > DB). Marking
        BEFORE/right-after placing the flatten keeps a concurrent flatten path
        (the OPEN/PARTIAL/PENDING_FILL query) from re-selecting and double-selling."""
        try:
            with self._store.transaction() as cur:
                cur.execute(
                    "UPDATE trades SET status = ?, updated_at = ? WHERE trade_id = ?",
                    ("EXITING", now_ist().isoformat(), trade_id),
                )
        except Exception as exc:
            self._log.critical(
                "kill_switch: DB write (EXITING) failed for trade %s: %s",
                trade_id, exc,
            )

    def _cancel_trade_resting_exits(self, trade_id: str) -> None:
        """FIX-190 (Bug E): cancel a trade's resting SL/TGT orders at the broker
        BEFORE flattening, so they don't survive as orphans that later re-fire
        into a naked position. Best-effort; broker truth > DB."""
        try:
            rows = self._store.fetch_all(
                "SELECT order_id, leg FROM orders "
                "WHERE trade_id = ? AND leg IN ('SL','TGT') "
                "AND status NOT IN ('CANCELLED','FAILED','EXPIRED','COMPLETE')",
                (trade_id,),
            )
        except Exception as exc:
            self._log.warning(
                "kill_switch: could not query resting exits for %s: %s",
                trade_id, exc,
            )
            return
        cancelled = 0
        for r in rows or []:
            # Defensive: this runs inside the indestructible exit loop, so a row
            # missing the column (e.g. a test mock or odd payload) must never
            # crash the flatten — just skip it.
            try:
                oid = r["order_id"]
            except (KeyError, IndexError, TypeError):
                continue
            if not oid:
                continue
            try:
                # Wave-2 P1 (H-1 twin): order_id IS the broker-assigned id
                # (schema.sql:271). The old dead column broker_order_id made this
                # SELECT raise on every call, so the HARD_KILL flatten never
                # cancelled its resting SL/TGT. Cancel + finalize by the real order_id.
                self._adapter.cancel_order(oid)
            except Exception as exc:
                self._log.warning(
                    "kill_switch: cancel resting %s order %s failed: %s",
                    r["leg"], oid, exc,
                )
                continue
            try:
                with self._store.transaction() as cur:
                    cur.execute(
                        "UPDATE orders SET status = 'CANCELLED', updated_at = ? "
                        "WHERE order_id = ?",
                        (now_ist().isoformat(), oid),
                    )
            except Exception:
                pass  # broker cancel is what matters; reconciler finalizes DB
            cancelled += 1
        if cancelled:
            self._log.critical(
                "kill_switch: cancelled %d resting exit order(s) for trade %s "
                "before flatten (FIX-190 Bug E)", cancelled, trade_id,
            )

    def _exit_all_trades_indestructible(self) -> CancellationReport:
        """
        FIX-087: Exit all open trades with per-trade exception isolation and retry.

        Part 11 (FIX-180): the retry loop now (a) checks the broker position is
        still open before each retry (skip-and-resolve if already flat, so we
        never open a naked position re-firing on a closed one) and (b) stops
        after _HARD_KILL_MAX_RETRY_HOURS, escalating via CRITICAL Telegram
        instead of looping forever and freezing the thread.

        Returns CancellationReport after all trades are flat (or the retry
        deadline is hit, with the unexited trades reported as failed).
        """
        import time

        # FIX-165b: corrected column names (qty_filled not quantity,
        # direction not side) and status values (PENDING_FILL not PENDING,
        # plus PARTIAL for partially-filled positions).
        # Bug C (P0 2026-06-15): also fetch the position's `product` so the
        # emergency exit can pass the required `intent` to place_order (and exit
        # under the SAME product the position was opened with — MIS vs CNC
        # matters). `product` lives on the orders table (ENTRY/CO leg), not on
        # trades, so pull it via a correlated subquery.
        try:
            open_trades = self._store.fetch_all(
                "SELECT t.trade_id, t.symbol, t.qty_filled, t.direction, "
                "       (SELECT o.product FROM orders o "
                "        WHERE o.trade_id = t.trade_id AND o.leg IN ('ENTRY','CO') "
                "        LIMIT 1) AS product "
                "FROM trades t "
                "WHERE t.status IN ('OPEN', 'PARTIAL', 'PENDING_FILL')"
            )
        except Exception as exc:
            self._log.critical(
                "kill_switch: failed to fetch open trades: %s", exc
            )
            return CancellationReport(attempted=0, succeeded=0, failed=["fetch_failed"])

        # FIX-181 LAYER A: do NOT early-return on an empty local set — a broker
        # position can exist with no local OPEN/PARTIAL/PENDING_FILL trade (entry
        # filled after being force-marked CANCELLED, or filled post-kill). The
        # broker-position sweep below must still run to flatten it.
        open_trades = open_trades or []
        attempted = len(open_trades)
        failed_trades = []
        # FIX-181 LAYER A: symbols covered by a local trade exit, so the broker
        # sweep below does not double-fire on a position we already handled.
        handled_symbols: set[str] = set()

        # First pass: try to exit each trade
        for trade in open_trades:
            trade_id = trade["trade_id"]
            symbol = trade["symbol"]
            handled_symbols.add(symbol)
            local_qty = abs(trade["qty_filled"] or 0)
            if local_qty == 0:
                continue
            # Bug C (P0 2026-06-15): derive the product intent from the open
            # position so place_order gets its required `intent` and exits under
            # the same product (MIS/CNC). Unknown product -> INTRADAY (safest:
            # MIS exits are always allowed and the common case).
            intent = _PRODUCT_TO_INTENT.get(trade["product"] or "", "INTRADAY")
            fallback_side = "SELL" if trade["direction"] == "LONG" else "BUY"

            # FIX-190 (Bug E): cancel this trade's resting SL/TGT BEFORE flattening
            # so a late fill can't re-open a naked position and so we leave no
            # orphan exit orders (the AEROENTER orphans of the 19-Jun incident).
            self._cancel_trade_resting_exits(trade_id)

            # FIX-190 (Bug A): reverse-aware close based on the ACTUAL broker
            # position, not the local intended direction. A position already
            # flattened by order_placer's emergency exit reads net 0 -> skip (no
            # second SELL -> no naked short, the THELEELA oversell). A genuine
            # short closes with BUY. On broker error we fall back to the intended
            # exit (err toward flattening).
            close_side, close_qty = determine_close_direction(
                self._adapter, symbol, fallback_side, local_qty
            )
            if close_side is None or close_qty <= 0:
                self._log.info(
                    "kill_switch: trade %s (%s) already flat at broker; marking "
                    "EXITING without re-firing (FIX-190 A)", trade_id, symbol,
                )
                self._mark_trade_exiting(trade_id)
                continue

            try:
                # FIX-181: marketable LIMIT (LTP ± buffer) exit, MARKET fallback.
                exit_order_type, exit_price = self._marketable_exit_params(
                    symbol, close_side
                )
                order_result = self._adapter.place_order(
                    symbol=symbol,
                    side=close_side,
                    qty=close_qty,
                    order_type=exit_order_type,
                    price=exit_price,
                    intent=intent,
                    tag="ks_hard_kill_exit",
                )
                # Bug C (P0 2026-06-15): place_order returns a PlacedOrder on
                # success and RAISES on failure; PlacedOrder has no .success
                # attribute. Treat an empty broker_order_id as the only
                # non-exception failure.
                if not order_result.broker_order_id:
                    raise RuntimeError("Broker returned empty order id for exit")

                self._mark_trade_exiting(trade_id)
                self._log.info(
                    "kill_switch: trade %s exited successfully (%s %d)",
                    trade_id, close_side, close_qty,
                )
            except Exception as exc:
                self._log.critical(
                    "kill_switch: exit failed for trade %s: %s", trade_id, exc
                )
                failed_trades.append((trade_id, symbol, close_side, close_qty, intent))

        # FIX-181 LAYER A (GICRE incident): broker-position-driven sweep. A
        # HARD_KILL must leave NO live broker position, even one with no matching
        # local OPEN/PARTIAL/PENDING_FILL trade — e.g. an entry LIMIT that filled
        # during/after the kill, or one force-marked CANCELLED while it actually
        # filled. Flatten any non-zero broker position not already handled above.
        try:
            broker_positions = self._adapter.get_positions()
            for pos in broker_positions:
                psym = getattr(pos, "symbol", None)
                pqty = int(getattr(pos, "qty", 0) or 0)
                if psym is None or pqty == 0 or psym in handled_symbols:
                    continue
                handled_symbols.add(psym)
                attempted += 1
                exit_side = "SELL" if pqty > 0 else "BUY"
                # H-5: exit under the SAME product the position is held in — mirror
                # the first pass (Bug C). Kite nets per product, so an orphan CNC
                # position swept with an MIS (intent=INTRADAY) exit does NOT offset
                # it: the CNC position stays AND a fresh naked MIS short is created.
                # Map the position's product to its intent (MIS→INTRADAY,
                # CNC/NRML→DELIVERY); absent product → INTRADAY.
                sweep_intent = _PRODUCT_TO_INTENT.get(
                    getattr(pos, "product", "") or "", "INTRADAY"
                )
                self._log.critical(
                    "kill_switch: SWEEP orphan broker position %s qty=%d — no "
                    "matching local trade; flattening (FIX-181)",
                    psym, pqty,
                )
                try:
                    exit_order_type, exit_price = self._marketable_exit_params(
                        psym, exit_side
                    )
                    order_result = self._adapter.place_order(
                        symbol=psym,
                        side=exit_side,
                        qty=abs(pqty),
                        order_type=exit_order_type,
                        price=exit_price,
                        intent=sweep_intent,
                        tag="ks_hard_kill_sweep",
                    )
                    if not order_result.broker_order_id:
                        raise RuntimeError("Broker returned empty order id for sweep")
                except Exception as sweep_exc:
                    self._log.critical(
                        "kill_switch: SWEEP exit failed for %s: %s", psym, sweep_exc
                    )
                    failed_trades.append(("sweep", psym, exit_side, abs(pqty), sweep_intent))
        except Exception as exc:
            self._log.error("kill_switch: broker position sweep failed: %s", exc)

        # Retry loop: exponential backoff, bounded by a max wall-clock deadline
        # (Part 11 / FIX-180) instead of looping forever.
        retry_delays = [5, 15, 45]  # seconds
        retry_attempt = 0
        deadline = now_ist() + timedelta(hours=_HARD_KILL_MAX_RETRY_HOURS)
        flat_resolved = 0  # trades found already flat at broker (no re-fire)

        while failed_trades:
            # Part 11: stop retrying after the deadline; escalate and report the
            # remaining trades as failed rather than freezing the thread forever.
            if now_ist() >= deadline:
                self._log.critical(
                    "kill_switch: max retry duration (%.1fh) exceeded; %d trades "
                    "still unexited — escalating, MANUAL INTERVENTION REQUIRED",
                    _HARD_KILL_MAX_RETRY_HOURS, len(failed_trades),
                )
                self._alert_exit_failed(failed_trades)
                remaining = [t[0] for t in failed_trades]
                return CancellationReport(
                    attempted=attempted,
                    succeeded=attempted - len(remaining),
                    failed=remaining,
                )

            delay = retry_delays[min(retry_attempt, len(retry_delays) - 1)]
            self._log.critical(
                "kill_switch: retrying %d failed trades in %ds (attempt %d)",
                len(failed_trades), delay, retry_attempt + 1,
            )
            time.sleep(delay)
            retry_attempt += 1

            still_failed = []
            for trade_id, symbol, exit_side, qty, intent in failed_trades:
                # H-4: re-derive (close_side, close_qty) from the CURRENT signed
                # broker net on EVERY retry — mirror the first pass
                # (determine_close_direction) — instead of re-firing the STALE
                # first-pass qty. After an ambiguous first exit that partially
                # filled (A-2 BrokerTimeoutError class), the residual is < the
                # captured qty; re-firing the stale full qty oversells into a new
                # naked reverse. determine_close_direction returns (None, 0) when
                # the broker confirms flat (this SUBSUMES the old binary
                # _is_position_flat gate — closed manually / a prior exit filled),
                # and falls back to the captured (exit_side, qty) only on a broker
                # read error (err toward flattening — unchanged from the old
                # cannot-confirm-flat path). Fresh get_positions per retry is the
                # same one call the flat pre-check already made.
                close_side, close_qty = determine_close_direction(
                    self._adapter, symbol, exit_side, qty
                )
                if close_side is None or close_qty <= 0:
                    self._log.info(
                        "kill_switch: trade %s (%s) already flat at broker; "
                        "resolved without re-firing exit", trade_id, symbol,
                    )
                    flat_resolved += 1
                    continue
                try:
                    # FIX-181: marketable LIMIT (LTP ± buffer), MARKET fallback.
                    exit_order_type, exit_price = self._marketable_exit_params(
                        symbol, close_side
                    )
                    order_result = self._adapter.place_order(
                        symbol=symbol,
                        side=close_side,
                        qty=close_qty,
                        order_type=exit_order_type,
                        price=exit_price,
                        intent=intent,
                        tag="ks_hard_kill_exit",
                    )
                    if not order_result.broker_order_id:
                        raise RuntimeError("Broker returned empty order id for exit")

                    # Best-effort DB update
                    try:
                        with self._store.transaction() as cur:
                            cur.execute(
                                "UPDATE trades SET status = ?, updated_at = ? WHERE trade_id = ?",
                                ("EXITING", now_ist().isoformat(), trade_id),
                            )
                    except Exception:
                        pass  # Broker truth > DB truth

                    self._log.info(
                        "kill_switch: trade %s exited successfully (retry)", trade_id
                    )
                except Exception as exc:
                    self._log.critical(
                        "kill_switch: retry failed for trade %s: %s", trade_id, exc
                    )
                    still_failed.append((trade_id, symbol, close_side, close_qty, intent))

            failed_trades = still_failed

        # All trades exited or confirmed flat at broker.
        succeeded = attempted
        return CancellationReport(attempted=attempted, succeeded=succeeded, failed=[])
