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
from datetime import datetime
from enum import Enum
from typing import Callable, List, Optional, TYPE_CHECKING

from core.events import EventBus, KillSwitchActivated
from core.time_authority import now_ist

if TYPE_CHECKING:
    import logging
    from core.state_store import StateStore

# DUP-1 (2026-04-26 audit): _IST removed; never read locally.


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
    or mutating state. RLock (not Lock) prevents the record_api_failure ->
    soft_kill reentrant deadlock identified in Audit Issue #4 (KS4).

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
    ) -> None:
        self._store = state_store
        self._bus = bus
        self._log = logger
        self._cancel_fn = on_hard_kill_cancel_fn
        self._threshold = api_failure_threshold
        self._auto_trip = enable_auto_trip
        self._notifier = notifier
        self._mode = mode

        # KS4: RLock allows same-thread reentrant acquisition (deadlock fix).
        self._lock = threading.RLock()

        # In-memory state — authoritative after construction
        self._state = KillState.INACTIVE
        self._reason = ""
        self._triggered_at: Optional[datetime] = None
        self._triggered_by = ""
        self._api_failure_count = 0

        # KS3: recover persisted state on startup (Audit Issue #18 fix)
        self._load_state_from_store()

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

        # Telegram alert (optional; never crash on notifier failure)
        if self._notifier is not None:
            try:
                self._notifier.send(
                    severity="WARN",
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

    def record_api_failure(self) -> None:
        """
        Increment the consecutive API failure counter.
        If counter reaches api_failure_threshold AND enable_auto_trip is True
        AND state is currently INACTIVE, auto-trigger soft_kill() (KS7).
        The RLock (KS4) prevents deadlock when soft_kill() re-acquires the lock
        from within the same thread.
        """
        with self._lock:
            self._api_failure_count += 1
            if (
                self._auto_trip
                and self._api_failure_count >= self._threshold
                and self._state == KillState.INACTIVE
            ):
                # KS4: same thread re-acquires RLock inside soft_kill() — safe
                self.soft_kill(
                    reason=(
                        f"Auto-trip: {self._api_failure_count} consecutive "
                        f"API failures (threshold={self._threshold})"
                    ),
                    triggered_by="auto_trip",
                )

    def record_success(self) -> None:
        """Reset the consecutive API failure counter on any successful API call."""
        with self._lock:
            self._api_failure_count = 0

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
        Invoke on_hard_kill_cancel_fn if set. Return CancellationReport.
        The callback runs OUTSIDE the lock so it can call broker APIs
        freely without blocking other threads.
        """
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
