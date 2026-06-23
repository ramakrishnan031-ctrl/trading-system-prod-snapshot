"""
tests/unit/test_kill_switch.py

Validates capital/kill_switch.py against KS1-KS13 locked decisions.

Coverage:
  - Initial state INACTIVE when no persisted row (KS2)
  - Construction with persisted SOFT_KILL -> remains SOFT_KILL (KS3, Audit #18)
  - Construction with persisted HARD_KILL -> remains HARD_KILL (KS3, Audit #18)
  - is_active("entry") true on SOFT_KILL and HARD_KILL (KS6)
  - is_active("exit") true only on HARD_KILL (KS6)
  - is_active("any") true on either kill mode (KS6)
  - soft_kill() persists state, publishes event, logs CRITICAL (KS6, KS8, KS9)
  - soft_kill() when already SOFT_KILL -> no-op (DEBUG log) (KS6)
  - soft_kill() when HARD_KILL -> WARNING, no downgrade (KS6)
  - hard_kill() invokes on_hard_kill_cancel_fn, returns CancellationReport (KS6)
  - hard_kill() with no callback -> empty report (KS6)
  - hard_kill() when already HARD_KILL -> re-runs cancel (KS6)
  - resume() from SOFT_KILL -> INACTIVE, publishes event (KS6)
  - resume() from HARD_KILL -> INACTIVE (KS6)
  - resume() from INACTIVE -> ValueError (KS6)
  - resume() with empty triggered_by -> ValueError (KS6)
  - record_api_failure 3 times -> auto soft_kill (KS7, default threshold)
  - record_api_failure resets after record_success (KS7)
  - enable_auto_trip=False -> no auto-trip even at threshold (KS7)
  - get_kill_info() returns full status dict with all 4 fields (KS10)
  - status() dict has all 4 required keys (KS6)
  - Audit Issue #4 regression: record_api_failure -> soft_kill re-entrance
    does NOT deadlock with RLock (KS4)
  - Concurrent soft_kill from 5 threads: exactly one event published,
    final state SOFT_KILL (KS4 idempotency)
  - Persistence atomicity: state_store write failure -> in-memory state
    unchanged, event NOT published (KS9)
  - hard_kill() event payload has correct previous_state / new_state (KS8)

Run: python -m pytest tests/unit/test_kill_switch.py -v
Or:  python tests/unit/test_kill_switch.py  (standalone mode)
"""
from __future__ import annotations

import logging
import sqlite3
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from capital.kill_switch import CancellationReport, KillState, KillSwitch
from core.events import EventBus, KillSwitchActivated
from core.state_store import StateStore


# ─────────────────────────────────────────────────────────────────────────────
# Test doubles
# ─────────────────────────────────────────────────────────────────────────────

class _CapturingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def by_level(self, level: int) -> list[str]:
        return [r.getMessage() for r in self.records if r.levelno == level]

    def criticals(self) -> list[str]:
        return self.by_level(logging.CRITICAL)

    def warnings(self) -> list[str]:
        return self.by_level(logging.WARNING)

    def debugs(self) -> list[str]:
        return self.by_level(logging.DEBUG)


class _FailingStateStore:
    """
    StateStore double that always raises on transaction() to simulate
    disk / write failure. fetch_one() returns None (no persisted state).
    Used for KS9 atomicity tests.
    """
    def fetch_one(self, sql: str, params: tuple = ()) -> None:
        return None

    @contextmanager
    def transaction(self):
        raise sqlite3.OperationalError("simulated disk full")
        yield  # makes it a generator; never reached


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_logger(handler: _CapturingHandler) -> logging.Logger:
    log = logging.getLogger("test_kill_switch")
    log.handlers.clear()
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    return log


def _make_store(tmp_path: Path) -> StateStore:
    return StateStore(tmp_path / "ks_test.db")


def _make_ks(
    store: Any,
    *,
    cancel_fn: Any = None,
    threshold: int = 3,
    auto_trip: bool = True,
    handler: _CapturingHandler | None = None,
) -> tuple[KillSwitch, EventBus, _CapturingHandler]:
    if handler is None:
        handler = _CapturingHandler()
    log = _make_logger(handler)
    bus = EventBus()
    ks = KillSwitch(
        state_store=store,
        bus=bus,
        logger=log,
        on_hard_kill_cancel_fn=cancel_fn,
        api_failure_threshold=threshold,
        enable_auto_trip=auto_trip,
    )
    return ks, bus, handler


def _seed_persisted_state(
    store: StateStore,
    state: str,
    reason: str = "seeded",
    triggered_by: str = "test",
) -> None:
    """Write a kill_switch_state row directly to simulate a previous session."""
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT OR REPLACE INTO kill_switch_state
              (id, state, reason, triggered_at, triggered_by)
            VALUES (1, ?, ?, ?, ?)
            """,
            (state, reason, "2026-04-14T09:00:00+05:30", triggered_by),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

def test_initial_state_inactive_no_row(tmp_path: Path) -> None:
    """No persisted row -> state is INACTIVE after construction."""
    store = _make_store(tmp_path)
    ks, _, _ = _make_ks(store)
    assert ks.current_state() == KillState.INACTIVE
    assert not ks.is_active()
    print("  OK initial state INACTIVE when no persisted row")
    store.close()


def test_startup_recovery_soft_kill(tmp_path: Path) -> None:
    """
    KS3 audit Issue #18 fix: construction with persisted SOFT_KILL row
    must NOT overwrite with INACTIVE. State remains SOFT_KILL.
    """
    store = _make_store(tmp_path)
    _seed_persisted_state(store, "SOFT_KILL", "manual halt by operator")

    handler = _CapturingHandler()
    ks, _, _ = _make_ks(store, handler=handler)

    assert ks.current_state() == KillState.SOFT_KILL
    assert ks.is_active()
    # CRITICAL must have been logged at startup
    assert any("ACTIVE AT STARTUP" in m for m in handler.criticals()), \
        f"Expected CRITICAL about startup recovery; got: {handler.criticals()}"
    print("  OK startup recovery: persisted SOFT_KILL retained (Audit #18 fix)")
    store.close()


def test_startup_recovery_hard_kill(tmp_path: Path) -> None:
    """KS3: persisted HARD_KILL -> remains HARD_KILL after construction."""
    store = _make_store(tmp_path)
    _seed_persisted_state(store, "HARD_KILL", "api failure cascade")

    ks, _, handler = _make_ks(store)

    assert ks.current_state() == KillState.HARD_KILL
    assert ks.is_active("entry")
    assert ks.is_active("exit")
    print("  OK startup recovery: persisted HARD_KILL retained")
    store.close()


def test_is_active_entry(tmp_path: Path) -> None:
    """is_active('entry'): True on SOFT_KILL and HARD_KILL, False on INACTIVE."""
    store = _make_store(tmp_path)
    ks, _, _ = _make_ks(store)
    assert not ks.is_active("entry")

    ks.soft_kill("test", "test")
    assert ks.is_active("entry"), "SOFT_KILL must block entries"

    ks.resume("clear", "operator")
    ks.hard_kill("test", "test")
    assert ks.is_active("entry"), "HARD_KILL must block entries"
    print("  OK is_active('entry') correct for all three states")
    store.close()


def test_is_active_exit(tmp_path: Path) -> None:
    """is_active('exit'): True ONLY on HARD_KILL."""
    store = _make_store(tmp_path)
    ks, _, _ = _make_ks(store)
    assert not ks.is_active("exit")

    ks.soft_kill("test", "test")
    assert not ks.is_active("exit"), "SOFT_KILL must NOT block exits"

    ks.resume("clear", "operator")
    ks.hard_kill("test", "test")
    assert ks.is_active("exit"), "HARD_KILL must block exits"
    print("  OK is_active('exit') only True on HARD_KILL")
    store.close()


def test_is_active_any(tmp_path: Path) -> None:
    """is_active('any'): True on SOFT_KILL or HARD_KILL."""
    store = _make_store(tmp_path)
    ks, _, _ = _make_ks(store)
    assert not ks.is_active("any")

    ks.soft_kill("test", "test")
    assert ks.is_active("any")
    print("  OK is_active('any') True on SOFT_KILL")
    store.close()


def test_soft_kill_persists_publishes_logs_critical(tmp_path: Path) -> None:
    """soft_kill() must persist state, publish KillSwitchActivated event, log CRITICAL."""
    store = _make_store(tmp_path)
    handler = _CapturingHandler()
    events_received: list[KillSwitchActivated] = []
    ks, bus, _ = _make_ks(store, handler=handler)
    bus.subscribe(KillSwitchActivated, events_received.append)

    ks.soft_kill("daily_loss_limit_hit", "fund_manager")

    # Persisted
    row = store.fetch_one("SELECT * FROM kill_switch_state WHERE id = 1")
    assert row is not None
    assert row["state"] == "SOFT_KILL"
    assert row["triggered_by"] == "fund_manager"

    # Event published
    assert len(events_received) == 1
    ev = events_received[0]
    assert ev.kill_type == "soft"
    assert ev.reason == "daily_loss_limit_hit"
    assert ev.triggered_by == "fund_manager"
    assert ev.previous_state == "INACTIVE"
    assert ev.new_state == "SOFT_KILL"

    # CRITICAL logged
    assert any("SOFT_KILL" in m for m in handler.criticals()), \
        f"Expected CRITICAL log; got: {handler.criticals()}"

    print("  OK soft_kill: persisted, event published, CRITICAL logged")
    store.close()


def test_soft_kill_idempotent_noop(tmp_path: Path) -> None:
    """soft_kill() when already SOFT_KILL -> no-op; no second event; DEBUG log."""
    store = _make_store(tmp_path)
    events_received: list = []
    ks, bus, handler = _make_ks(store)
    bus.subscribe(KillSwitchActivated, events_received.append)

    ks.soft_kill("first", "system")
    assert len(events_received) == 1

    ks.soft_kill("second call", "system")  # no-op
    assert len(events_received) == 1, "Second soft_kill must not publish another event"
    assert ks.current_state() == KillState.SOFT_KILL

    assert any("no-op" in d.lower() or "already" in d.lower()
               for d in handler.debugs()), \
        f"Expected DEBUG log about no-op; got: {handler.debugs()}"
    print("  OK soft_kill idempotent: second call is no-op, one event total")
    store.close()


def test_soft_kill_ignored_when_hard_kill(tmp_path: Path) -> None:
    """soft_kill() when HARD_KILL -> WARNING logged, state stays HARD_KILL."""
    store = _make_store(tmp_path)
    events_received: list = []
    ks, bus, handler = _make_ks(store)
    bus.subscribe(KillSwitchActivated, events_received.append)

    ks.hard_kill("hard first", "system")
    events_before = len(events_received)

    ks.soft_kill("attempt downgrade", "system")

    assert ks.current_state() == KillState.HARD_KILL, "Must remain HARD_KILL"
    assert len(events_received) == events_before, "No new event on ignored soft_kill"
    assert any("downgrade" in w.lower() or "hard_kill" in w.lower()
               for w in handler.warnings()), \
        f"Expected WARNING about downgrade attempt; got: {handler.warnings()}"
    print("  OK soft_kill ignored when already HARD_KILL; WARNING logged")
    store.close()


def test_hard_kill_invokes_cancel_fn(tmp_path: Path) -> None:
    """hard_kill() invokes on_hard_kill_cancel_fn and returns its CancellationReport."""
    store = _make_store(tmp_path)
    called = [0]

    def cancel_fn():
        called[0] += 1
        return CancellationReport(attempted=3, succeeded=2, failed=["ord-007"])

    ks, _, _ = _make_ks(store, cancel_fn=cancel_fn)
    report = ks.hard_kill("api_cascade", "zerodha_adapter")

    assert called[0] == 1, "cancel_fn must be called once"
    assert isinstance(report, CancellationReport)
    assert report.attempted == 3
    assert report.succeeded == 2
    assert report.failed == ["ord-007"]
    print(f"  OK hard_kill invokes cancel_fn: report={report}")
    store.close()


def test_hard_kill_no_callback_empty_report(tmp_path: Path) -> None:
    """hard_kill() with no callback returns empty CancellationReport."""
    store = _make_store(tmp_path)
    ks, _, _ = _make_ks(store, cancel_fn=None)
    report = ks.hard_kill("reason", "system")

    assert isinstance(report, CancellationReport)
    assert report.attempted == 0
    assert report.succeeded == 0
    assert report.failed == []
    print("  OK hard_kill with no callback returns empty CancellationReport")
    store.close()


def test_hard_kill_reruns_cancel_when_already_hard_kill(tmp_path: Path) -> None:
    """hard_kill() when already HARD_KILL -> re-runs cancel_fn (idempotent re-try)."""
    store = _make_store(tmp_path)
    call_count = [0]

    def cancel_fn():
        call_count[0] += 1
        return CancellationReport(attempted=1, succeeded=1, failed=[])

    ks, _, _ = _make_ks(store, cancel_fn=cancel_fn)
    ks.hard_kill("first", "system")
    assert call_count[0] == 1

    ks.hard_kill("second", "system")
    assert call_count[0] == 2, \
        f"cancel_fn must be re-run on second hard_kill; got {call_count[0]}"
    assert ks.current_state() == KillState.HARD_KILL
    print(f"  OK hard_kill re-runs cancel on repeat call: cancel_fn called {call_count[0]} times")
    store.close()


def test_resume_from_soft_kill(tmp_path: Path) -> None:
    """resume() from SOFT_KILL -> INACTIVE; event published with kill_type='resume'."""
    store = _make_store(tmp_path)
    events_received: list[KillSwitchActivated] = []
    ks, bus, _ = _make_ks(store)
    bus.subscribe(KillSwitchActivated, events_received.append)

    ks.soft_kill("daily loss", "fund_manager")
    ks.resume("operator cleared halt", "operator_rama")

    assert ks.current_state() == KillState.INACTIVE
    assert not ks.is_active()

    # Persisted
    row = store.fetch_one("SELECT * FROM kill_switch_state WHERE id = 1")
    assert row["state"] == "INACTIVE"
    assert row["triggered_by"] == "operator_rama"

    # Resume event
    resume_events = [e for e in events_received if e.kill_type == "resume"]
    assert len(resume_events) == 1
    ev = resume_events[0]
    assert ev.previous_state == "SOFT_KILL"
    assert ev.new_state == "INACTIVE"
    assert ev.triggered_by == "operator_rama"
    print("  OK resume() from SOFT_KILL -> INACTIVE, event published")
    store.close()


def test_resume_from_hard_kill(tmp_path: Path) -> None:
    """resume() from HARD_KILL -> INACTIVE."""
    store = _make_store(tmp_path)
    ks, _, _ = _make_ks(store)

    ks.hard_kill("hard test", "system")
    ks.resume("cleared by operator", "operator_rama")

    assert ks.current_state() == KillState.INACTIVE
    print("  OK resume() from HARD_KILL -> INACTIVE")
    store.close()


def test_resume_from_inactive_raises(tmp_path: Path) -> None:
    """resume() from INACTIVE raises ValueError."""
    store = _make_store(tmp_path)
    ks, _, _ = _make_ks(store)

    raised = False
    try:
        ks.resume("no reason", "operator_rama")
    except ValueError as e:
        raised = True
        assert "already INACTIVE" in str(e)
        print(f"  OK resume() from INACTIVE raises ValueError: {e}")
    assert raised, "Expected ValueError when resuming from INACTIVE"
    store.close()


def test_resume_empty_triggered_by_raises(tmp_path: Path) -> None:
    """resume() with empty triggered_by raises ValueError (audit trail required)."""
    store = _make_store(tmp_path)
    ks, _, _ = _make_ks(store)
    ks.soft_kill("test", "system")

    raised = False
    try:
        ks.resume("reason", "")
    except ValueError as e:
        raised = True
        assert "resumed_by" in str(e).lower() or "empty" in str(e).lower()
        print(f"  OK empty triggered_by raises ValueError: {e}")
    assert raised, "Expected ValueError for empty triggered_by"
    store.close()


def test_record_api_failure_auto_trips(tmp_path: Path) -> None:
    """record_api_failure() N times -> auto soft_kill (default threshold=3)."""
    store = _make_store(tmp_path)
    events: list = []
    ks, bus, _ = _make_ks(store, threshold=3, auto_trip=True)
    bus.subscribe(KillSwitchActivated, events.append)

    ks.record_api_failure()
    assert ks.current_state() == KillState.INACTIVE

    ks.record_api_failure()
    assert ks.current_state() == KillState.INACTIVE

    ks.record_api_failure()  # 3rd -> triggers soft_kill
    assert ks.current_state() == KillState.SOFT_KILL, \
        "3 consecutive failures must auto-trip to SOFT_KILL"
    assert len(events) == 1
    assert events[0].triggered_by == "auto_trip"
    print("  OK record_api_failure x3 -> auto soft_kill (default threshold=3)")
    store.close()


def test_fix185_broker_auth_error_not_counted(tmp_path: Path) -> None:
    """FIX-185: BrokerAuthError (403/permission) must NOT count toward auto-trip.

    The 18-Jun incident: a VM IP change left the Zerodha dev-console allowlist
    stale, so every place_order returned 403 PermissionException -> BrokerAuthError.
    Counting those as 'consecutive API failures' SOFT_KILLed the session (then
    HALT-crash-looped on restart). A config/credential error must not trip the
    transient-failure breaker — but genuine transient failures still must.
    """
    from core.exceptions import BrokerAuthError, BrokerTimeoutError

    store = _make_store(tmp_path)
    ks, _, _ = _make_ks(store, threshold=3, auto_trip=True)

    # 5 auth errors -> still INACTIVE (not counted)
    for _ in range(5):
        ks.record_api_failure(BrokerAuthError("IP not allowed to place orders"))
    assert ks.current_state() == KillState.INACTIVE, \
        "BrokerAuthError must not count toward the auto-trip"

    # A transient error still counts, and auth errors interleaved do not reset it
    ks.record_api_failure(BrokerTimeoutError("read timeout"))      # count=1
    ks.record_api_failure(BrokerAuthError("403 again"))            # ignored
    ks.record_api_failure(BrokerTimeoutError("read timeout"))      # count=2
    assert ks.current_state() == KillState.INACTIVE
    ks.record_api_failure(BrokerTimeoutError("read timeout"))      # count=3 -> trips
    assert ks.current_state() == KillState.SOFT_KILL, \
        "genuine transient failures must still auto-trip"
    print("  OK FIX-185: BrokerAuthError excluded; transient failures still trip")
    store.close()


def test_fix191_order_rejected_not_counted(tmp_path: Path) -> None:
    """FIX-191 (23-Jun-2026 false SOFT_KILL): the consecutive-API-failure breaker
    is connectivity-only. OrderRejectedError must NOT count — whether it is a
    broker order-reject (e.g. MIS/F&O-ban block) OR the client-side slippage-guard
    abort (order_placer raises OrderRejectedError BEFORE any broker call). Genuine
    transient errors (BrokerTimeoutError / BrokerRateLimitError) must still trip.
    """
    from core.exceptions import (
        BrokerRateLimitError,
        BrokerTimeoutError,
        OrderRejectedError,
    )

    store = _make_store(tmp_path)
    ks, _, _ = _make_ks(store, threshold=3, auto_trip=True)

    # 5 client-side slippage aborts (OrderRejectedError) -> still INACTIVE
    for _ in range(5):
        ks.record_api_failure(
            OrderRejectedError("slippage_exceeded: trigger=115.05 ltp=116.39")
        )
    assert ks.current_state() == KillState.INACTIVE, \
        "OrderRejectedError (slippage abort) must not count toward the auto-trip"

    # 5 broker business-rejects (OrderRejectedError) -> still INACTIVE
    for _ in range(5):
        ks.record_api_failure(
            OrderRejectedError("Zerodha rejected order: MIS orders are blocked")
        )
    assert ks.current_state() == KillState.INACTIVE, \
        "OrderRejectedError (broker reject) must not count toward the auto-trip"

    # Genuine transient errors STILL trip; a non-counted reject interleaved
    # neither increments nor resets the counter.
    ks.record_api_failure(BrokerTimeoutError("read timeout"))      # count=1
    ks.record_api_failure(OrderRejectedError("MIS block"))         # ignored, no reset
    ks.record_api_failure(BrokerRateLimitError("429 rate limited"))  # count=2
    assert ks.current_state() == KillState.INACTIVE
    ks.record_api_failure(BrokerTimeoutError("read timeout"))      # count=3 -> trips
    assert ks.current_state() == KillState.SOFT_KILL, \
        "BrokerTimeoutError/BrokerRateLimitError must still auto-trip at threshold"
    print("  OK FIX-191: OrderRejectedError (slippage + broker) excluded; "
          "transient timeout/rate-limit still trips")
    store.close()


def test_record_success_resets_counter(tmp_path: Path) -> None:
    """record_success() resets the failure counter; no auto-trip occurs."""
    store = _make_store(tmp_path)
    ks, _, _ = _make_ks(store, threshold=3, auto_trip=True)

    ks.record_api_failure()
    ks.record_api_failure()  # count = 2, below threshold
    ks.record_success()       # resets to 0

    ks.record_api_failure()   # count = 1 again (not 3)
    ks.record_api_failure()   # count = 2
    assert ks.current_state() == KillState.INACTIVE, \
        "Counter reset by record_success; no auto-trip at count=2"

    ks.record_api_failure()   # count = 3 -> trips
    assert ks.current_state() == KillState.SOFT_KILL
    print("  OK record_success resets counter; trips at threshold after reset")
    store.close()


def test_enable_auto_trip_false_no_auto_trip(tmp_path: Path) -> None:
    """enable_auto_trip=False -> record_api_failure never triggers soft_kill."""
    store = _make_store(tmp_path)
    ks, _, _ = _make_ks(store, threshold=3, auto_trip=False)

    for _ in range(10):
        ks.record_api_failure()

    assert ks.current_state() == KillState.INACTIVE, \
        "enable_auto_trip=False must never auto-trip"
    print("  OK enable_auto_trip=False: no auto-trip after 10 failures")
    store.close()


def test_get_kill_info_has_all_fields(tmp_path: Path) -> None:
    """get_kill_info() returns dict with all 4 required fields (KS10)."""
    store = _make_store(tmp_path)
    ks, _, _ = _make_ks(store)

    info = ks.get_kill_info()
    assert set(info.keys()) >= {"state", "reason", "triggered_at", "triggered_by"}, \
        f"Missing keys: {set(['state','reason','triggered_at','triggered_by']) - set(info.keys())}"
    assert info["state"] == "INACTIVE"
    print(f"  OK get_kill_info() has all 4 fields: {list(info.keys())}")
    store.close()


def test_status_dict_has_all_fields(tmp_path: Path) -> None:
    """status() returns dict with state, reason, triggered_at, triggered_by."""
    store = _make_store(tmp_path)
    ks, _, _ = _make_ks(store)
    ks.soft_kill("test reason", "module_x")

    s = ks.status()
    assert s["state"] == "SOFT_KILL"
    assert s["reason"] == "test reason"
    assert s["triggered_by"] == "module_x"
    assert s["triggered_at"] is not None
    print(f"  OK status() has all 4 fields after soft_kill: {s}")
    store.close()


def test_no_deadlock_record_api_failure_reentrant(tmp_path: Path) -> None:
    """
    KS4 audit Issue #4 regression: record_api_failure() -> soft_kill() both
    acquire self._lock. With RLock (same thread, re-entrant) this completes.
    With Lock it would deadlock. Test completes in finite time = no deadlock.
    """
    store = _make_store(tmp_path)
    ks, _, _ = _make_ks(store, threshold=3, auto_trip=True)

    # These 3 calls go: record_api_failure -> (count=3) -> soft_kill()
    # soft_kill() tries to acquire the RLock again from the same thread.
    # Must NOT deadlock.
    ks.record_api_failure()
    ks.record_api_failure()
    ks.record_api_failure()  # triggers soft_kill() with lock held -> RLock safe

    assert ks.current_state() == KillState.SOFT_KILL
    print("  OK RLock re-entrance: no deadlock in record_api_failure -> soft_kill")
    store.close()


def test_concurrent_soft_kill_idempotent(tmp_path: Path) -> None:
    """
    5 threads calling soft_kill() simultaneously: exactly 1 event published,
    final state SOFT_KILL (KS4, idempotency under concurrency).
    """
    store = _make_store(tmp_path)
    events_received: list = []
    ks, bus, _ = _make_ks(store)
    bus.subscribe(KillSwitchActivated, events_received.append)

    barrier = threading.Barrier(5)

    def _soft_kill() -> None:
        barrier.wait()  # all threads start simultaneously
        ks.soft_kill("concurrent test", "thread")

    threads = [threading.Thread(target=_soft_kill) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert all(not t.is_alive() for t in threads), "Threads did not finish (deadlock?)"
    assert ks.current_state() == KillState.SOFT_KILL
    assert len(events_received) == 1, \
        f"Expected exactly 1 event (idempotency); got {len(events_received)}"
    print(f"  OK 5 concurrent soft_kill calls: 1 event, state=SOFT_KILL")
    store.close()


def test_persistence_atomicity_state_store_failure(tmp_path: Path) -> None:
    """
    KS9: if state_store write fails, in-memory state is NOT changed
    and KillSwitchActivated event is NOT published.
    """
    failing_store = _FailingStateStore()
    events_received: list = []

    ks, bus, _ = _make_ks(failing_store)  # type: ignore[arg-type]
    bus.subscribe(KillSwitchActivated, events_received.append)

    assert ks.current_state() == KillState.INACTIVE

    # soft_kill() must raise (from persist failure) and leave state unchanged
    raised = False
    try:
        ks.soft_kill("test", "system")
    except (sqlite3.OperationalError, Exception):
        raised = True

    assert raised, "Expected exception from state_store failure"
    assert ks.current_state() == KillState.INACTIVE, \
        f"In-memory state must not change on persist failure; got {ks.current_state()}"
    assert len(events_received) == 0, \
        f"Event must NOT be published on persist failure; got {len(events_received)}"
    print("  OK persist failure: in-memory state unchanged, no event published")


def test_hard_kill_event_payload_ks8(tmp_path: Path) -> None:
    """KS8: hard_kill() event has correct previous_state, new_state, triggered_by."""
    store = _make_store(tmp_path)
    events_received: list[KillSwitchActivated] = []
    ks, bus, _ = _make_ks(store)
    bus.subscribe(KillSwitchActivated, events_received.append)

    ks.hard_kill("broker_auth_failed", "zerodha_adapter")

    assert len(events_received) == 1
    ev = events_received[0]
    assert ev.kill_type == "hard"
    assert ev.previous_state == "INACTIVE"
    assert ev.new_state == "HARD_KILL"
    assert ev.triggered_by == "zerodha_adapter"
    assert ev.reason == "broker_auth_failed"
    print(f"  OK hard_kill event payload: {ev.kill_type}/{ev.previous_state}->{ev.new_state}")
    store.close()




# -- FIX-127: clear_stale_state tests --

def test_clear_stale_state_clears_previous_day(tmp_path: Path) -> None:
    """FIX-127: kill switch from a previous day is auto-cleared."""
    from datetime import date
    store = _make_store(tmp_path)
    _seed_persisted_state(store, 'SOFT_KILL', 'naked_position', 'reconciler')
    ks, _, handler = _make_ks(store)
    assert ks.is_active('any')

    cleared = ks.clear_stale_state(date(2026, 4, 15))
    assert cleared
    assert not ks.is_active('any')
    assert ks.current_state() == KillState.INACTIVE
    assert any('auto-cleared' in w for w in handler.warnings())
    print('  OK stale kill switch from previous day auto-cleared')
    store.close()


# -- FIX-154: auto_clear_scheduled_kill tests --


def _seed_persisted_state_with_ts(
    store: StateStore,
    state: str,
    reason: str,
    triggered_by: str,
    triggered_at: str,
) -> None:
    """Seed kill_switch_state with a custom timestamp."""
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT OR REPLACE INTO kill_switch_state
              (id, state, reason, triggered_at, triggered_by)
            VALUES (1, ?, ?, ?, ?)
            """,
            (state, reason, triggered_at, triggered_by),
        )


def _ensure_trades_table(store: StateStore) -> None:
    """Noop — StateStore already creates the trades table from schema.sql."""
    pass


def test_auto_clear_scheduled_force_close(tmp_path: Path) -> None:
    """FIX-154: force_close kill switch auto-cleared when no open positions."""
    store = _make_store(tmp_path)
    _ensure_trades_table(store)
    _seed_persisted_state_with_ts(
        store, 'SOFT_KILL', 'circuit_breaker_force_close_15:15',
        'order_monitor', '2026-06-07T15:15:00+05:30',
    )
    ks, _, handler = _make_ks(store)
    assert ks.is_active('entry')

    cleared = ks.auto_clear_scheduled_kill()
    assert cleared, "Scheduled force_close should be auto-cleared"
    assert not ks.is_active('any')
    assert ks.current_state() == KillState.INACTIVE
    assert any('auto_clear_scheduled' in m for m in handler.by_level(logging.INFO))
    print('  OK force_close auto-cleared (no open positions)')
    store.close()


def test_auto_clear_scheduled_eod_squareoff(tmp_path: Path) -> None:
    """FIX-154: EOD_SQUAREOFF kill switch auto-cleared when no open positions."""
    store = _make_store(tmp_path)
    _ensure_trades_table(store)
    _seed_persisted_state_with_ts(
        store, 'SOFT_KILL', 'EOD_SQUAREOFF',
        'eod_squareoff', '2026-06-07T15:17:00+05:30',
    )
    ks, _, handler = _make_ks(store)
    assert ks.is_active('entry')

    cleared = ks.auto_clear_scheduled_kill()
    assert cleared, "EOD_SQUAREOFF should be auto-cleared"
    assert ks.current_state() == KillState.INACTIVE
    print('  OK EOD_SQUAREOFF auto-cleared (no open positions)')
    store.close()


def test_auto_clear_does_not_clear_emergency(tmp_path: Path) -> None:
    """FIX-154: emergency kill switch is NOT auto-cleared."""
    store = _make_store(tmp_path)
    _ensure_trades_table(store)
    _seed_persisted_state_with_ts(
        store, 'SOFT_KILL', 'daily_loss_limit_breached',
        'fund_manager', '2026-06-07T14:30:00+05:30',
    )
    ks, _, handler = _make_ks(store)
    assert ks.is_active('entry')

    cleared = ks.auto_clear_scheduled_kill()
    assert not cleared, "Emergency kill should NOT be auto-cleared"
    assert ks.is_active('entry')
    assert ks.current_state() == KillState.SOFT_KILL
    assert any('EMERGENCY' in w for w in handler.warnings())
    print('  OK emergency kill NOT auto-cleared')
    store.close()


def test_auto_clear_blocked_by_open_positions(tmp_path: Path) -> None:
    """FIX-154: scheduled kill NOT cleared when open positions exist."""
    store = _make_store(tmp_path)
    _ensure_trades_table(store)
    # Insert prerequisite signal row (FK constraint) + open trade
    with store.transaction() as cur:
        cur.execute(
            "INSERT INTO signals (signal_id, symbol, scanner, strategy, "
            "triggered_at, received_at, expires_at, status, fingerprint, "
            "fingerprint_date) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ('SIG001', 'RELIANCE', 'gap_fade_short', 'gap_fade_short',
             '2026-06-07T10:00:00+05:30', '2026-06-07T10:00:00+05:30',
             '2026-06-07T10:05:00+05:30', 'TRADED', 'fp001', '2026-06-07'),
        )
        cur.execute(
            "INSERT INTO trades (trade_id, signal_id, symbol, direction, strategy, "
            "qty_planned, entry_target_price, sl_initial, tgt_initial, "
            "margin_reserved, risk_amount, created_at, status, "
            "order_protocol, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ('T001', 'SIG001', 'RELIANCE', 'LONG', 'gap_fade_short',
             10, 100.0, 95.0, 110.0, 200.0, 50.0,
             '2026-06-07T10:00:00+05:30', 'OPEN',
             'CO_PLUS_TGT', '2026-06-07T10:00:00+05:30'),
        )
    _seed_persisted_state_with_ts(
        store, 'SOFT_KILL', 'circuit_breaker_force_close_15:15',
        'order_monitor', '2026-06-07T15:15:00+05:30',
    )
    ks, _, handler = _make_ks(store)

    cleared = ks.auto_clear_scheduled_kill()
    assert not cleared, "Should not clear when open positions exist"
    assert ks.is_active('entry')
    assert any('open positions' in w for w in handler.warnings())
    print('  OK scheduled kill NOT cleared with open positions')
    store.close()


def test_auto_clear_no_op_when_inactive(tmp_path: Path) -> None:
    """FIX-154: auto_clear_scheduled_kill no-op when already INACTIVE."""
    store = _make_store(tmp_path)
    _ensure_trades_table(store)
    ks, _, _ = _make_ks(store)
    assert ks.current_state() == KillState.INACTIVE

    cleared = ks.auto_clear_scheduled_kill()
    assert not cleared, "No-op when already INACTIVE"
    print('  OK no-op when INACTIVE')
    store.close()


def test_auto_clear_does_not_clear_hard_kill(tmp_path: Path) -> None:
    """FIX-154: HARD_KILL is never auto-cleared (even if reason is scheduled)."""
    store = _make_store(tmp_path)
    _ensure_trades_table(store)
    _seed_persisted_state_with_ts(
        store, 'HARD_KILL', 'circuit_breaker_force_close_15:15',
        'order_monitor', '2026-06-07T15:15:00+05:30',
    )
    ks, _, handler = _make_ks(store)

    cleared = ks.auto_clear_scheduled_kill()
    assert not cleared, "HARD_KILL must never be auto-cleared"
    assert ks.current_state() == KillState.HARD_KILL
    print('  OK HARD_KILL not auto-cleared even with scheduled reason')
    store.close()


def test_clear_stale_state_keeps_same_day(tmp_path: Path) -> None:
    """FIX-127: kill switch from today is NOT cleared."""
    from datetime import date
    store = _make_store(tmp_path)
    _seed_persisted_state(store, 'SOFT_KILL', 'naked_position', 'reconciler')
    ks, _, _ = _make_ks(store)
    assert ks.is_active('any')

    cleared = ks.clear_stale_state(date(2026, 4, 14))
    assert not cleared
    assert ks.is_active('any')
    print('  OK same-day kill switch preserved')
    store.close()


def test_clear_stale_state_noop_when_inactive(tmp_path: Path) -> None:
    """FIX-127: no-op when kill switch is already INACTIVE."""
    from datetime import date
    store = _make_store(tmp_path)
    ks, _, _ = _make_ks(store)
    assert not ks.is_active('any')

    cleared = ks.clear_stale_state(date(2026, 5, 30))
    assert not cleared
    assert not ks.is_active('any')
    print('  OK no-op when already INACTIVE')
    store.close()


def test_clear_stale_state_hard_kill_also_cleared(tmp_path: Path) -> None:
    """FIX-127: even HARD_KILL from a previous day is auto-cleared."""
    from datetime import date
    store = _make_store(tmp_path)
    _seed_persisted_state(store, 'HARD_KILL', 'broker_auth_failed', 'adapter')
    ks, _, handler = _make_ks(store)
    assert ks.current_state() == KillState.HARD_KILL

    cleared = ks.clear_stale_state(date(2026, 4, 15))
    assert cleared
    assert ks.current_state() == KillState.INACTIVE
    print('  OK stale HARD_KILL auto-cleared')
    store.close()


def test_clear_stale_state_multi_day_gap(tmp_path: Path) -> None:
    """FIX-137 Item 57: kill switch from Friday cleared on Monday (multi-day gap)."""
    from datetime import date
    store = _make_store(tmp_path)
    # Seed with date 2026-04-14 (Tuesday)
    _seed_persisted_state(store, 'SOFT_KILL', 'daily_loss_limit', 'fund_manager')
    ks, _, handler = _make_ks(store)
    assert ks.is_active('any')

    # Clear with date 5 days later (Sunday -> Monday equivalent gap)
    cleared = ks.clear_stale_state(date(2026, 4, 19))
    assert cleared
    assert not ks.is_active('any')
    assert ks.current_state() == KillState.INACTIVE
    print('  OK multi-day gap (5 days) auto-cleared')
    store.close()


def test_clear_stale_state_weekend_gap(tmp_path: Path) -> None:
    """FIX-137 Item 57: Friday kill switch cleared on Monday restart."""
    from datetime import date
    store = _make_store(tmp_path)
    # Seed with Friday date
    with store.transaction() as cur:
        cur.execute(
            "INSERT OR REPLACE INTO kill_switch_state (id, state, reason, triggered_at, triggered_by) "
            "VALUES (1, 'HARD_KILL', 'circuit_breaker', '2026-05-29T15:20:00+05:30', 'circuit_breaker')"
        )
    ks, _, handler = _make_ks(store)
    assert ks.current_state() == KillState.HARD_KILL

    # Monday restart
    cleared = ks.clear_stale_state(date(2026, 6, 1))
    assert cleared
    assert ks.current_state() == KillState.INACTIVE
    print('  OK Friday kill switch cleared on Monday')
    store.close()


# -- 2026-06-20: KILL_AUTO_CLEARED audit (headless guarantee + Task B bridge) --

def _cleared_events(store: StateStore) -> list:
    return store.fetch_all(
        "SELECT details FROM system_events WHERE event_type='KILL_AUTO_CLEARED' "
        "ORDER BY event_id"
    )


def test_clear_stale_state_audits_prior_day_hard_kill(tmp_path: Path) -> None:
    """Headless: a prior-day HARD_KILL clears AND is audited as emergency."""
    import json
    from datetime import date
    store = _make_store(tmp_path)
    _seed_persisted_state_with_ts(
        store, 'HARD_KILL', 'circuit_breaker_api_failure: broker down', 'order_monitor',
        '2026-06-18T10:00:00+05:30',
    )
    ks, _, _ = _make_ks(store)
    assert ks.clear_stale_state(date(2026, 6, 19)) is True
    rows = _cleared_events(store)
    assert len(rows) == 1
    d = json.loads(rows[0]['details'])
    assert d['previous_state'] == 'HARD_KILL'
    assert d['classification'] == 'emergency'
    assert d['cleared_via'] == 'clear_stale_state'
    assert d['triggered_at'].startswith('2026-06-18')
    store.close()


def test_clear_stale_state_audits_prior_day_emergency_softkill(tmp_path: Path) -> None:
    """Headless: a prior-day loss-limit SOFT_KILL clears AND is audited."""
    import json
    from datetime import date
    store = _make_store(tmp_path)
    _seed_persisted_state_with_ts(
        store, 'SOFT_KILL', 'daily_loss_limit_breached', 'fund_manager',
        '2026-06-18T11:00:00+05:30',
    )
    ks, _, _ = _make_ks(store)
    assert ks.clear_stale_state(date(2026, 6, 19)) is True
    d = json.loads(_cleared_events(store)[0]['details'])
    assert d['classification'] == 'emergency'
    assert d['reason'] == 'daily_loss_limit_breached'
    store.close()


def test_clear_stale_state_same_day_not_cleared_no_audit(tmp_path: Path) -> None:
    """Within-day safety: a SAME-day kill is NOT cleared and writes no audit."""
    from datetime import date
    store = _make_store(tmp_path)
    _seed_persisted_state_with_ts(
        store, 'SOFT_KILL', 'daily_loss_limit_breached', 'fund_manager',
        '2026-06-19T11:00:00+05:30',
    )
    ks, _, _ = _make_ks(store)
    assert ks.clear_stale_state(date(2026, 6, 19)) is False
    assert _cleared_events(store) == []
    assert ks.is_active('any')
    store.close()


def test_auto_clear_scheduled_audits_as_scheduled(tmp_path: Path) -> None:
    """The same-day scheduled clear is audited with classification=scheduled."""
    import json
    store = _make_store(tmp_path)
    _ensure_trades_table(store)
    _seed_persisted_state_with_ts(
        store, 'SOFT_KILL', 'EOD_SQUAREOFF', 'eod_squareoff',
        '2026-06-19T15:17:00+05:30',
    )
    ks, _, _ = _make_ks(store)
    assert ks.auto_clear_scheduled_kill() is True
    d = json.loads(_cleared_events(store)[0]['details'])
    assert d['classification'] == 'scheduled'
    assert d['cleared_via'] == 'auto_clear_scheduled'
    store.close()


# -- 2026-06-20: Kite IP-403 actionable alert (headless: alert, never halt) --

class _CapturingNotifier:
    def __init__(self) -> None:
        self.sent: list = []

    def send(self, **kw):
        from types import SimpleNamespace
        self.sent.append(kw)
        return SimpleNamespace(sentinel_path=None)


def _ip403_crit(note: "_CapturingNotifier") -> list:
    return [m for m in note.sent
            if m.get("severity") == "CRITICAL" and "IP NOT ALLOWLISTED" in m.get("title", "")]


def test_ip403_actionable_alert_once_then_throttled(tmp_path: Path, monkeypatch) -> None:
    import broker.auth_recovery as ar
    from core.exceptions import BrokerAuthError
    monkeypatch.setattr(ar, "get_public_ip", lambda timeout=5.0: "203.0.113.7")
    store = _make_store(tmp_path)
    note = _CapturingNotifier()
    ks, _, _ = _make_ks(store)
    ks.set_notifier(note, mode="LIVE")
    exc = BrokerAuthError("Zerodha permission denied: IP not allowed to place orders")
    ks.record_api_failure(exc)   # 1st -> actionable alert
    ks.record_api_failure(exc)   # 2nd within the hour -> throttled
    crit = _ip403_crit(note)
    assert len(crit) == 1
    assert "203.0.113.7" in crit[0]["body"]          # the IP to allowlist
    assert "no restart needed" in crit[0]["body"].lower()
    store.close()


def test_ip403_alert_not_fired_for_token_expiry(tmp_path: Path) -> None:
    from core.exceptions import BrokerAuthError
    store = _make_store(tmp_path)
    note = _CapturingNotifier()
    ks, _, _ = _make_ks(store)
    ks.set_notifier(note, mode="LIVE")
    ks.record_api_failure(BrokerAuthError("Zerodha token/auth failure: token expired"))
    assert _ip403_crit(note) == []                   # token expiry != IP-403
    store.close()


def test_ip403_never_trips_kill_switch(tmp_path: Path, monkeypatch) -> None:
    """The alert path must preserve FIX-185: a BrokerAuthError never auto-trips."""
    import broker.auth_recovery as ar
    from core.exceptions import BrokerAuthError
    monkeypatch.setattr(ar, "get_public_ip", lambda timeout=5.0: "x")
    store = _make_store(tmp_path)
    ks, _, _ = _make_ks(store)
    ks.set_notifier(_CapturingNotifier(), mode="LIVE")
    for _ in range(5):
        ks.record_api_failure(BrokerAuthError("permission denied: IP not allowed"))
    assert not ks.is_active("any")                   # never halted
    store.close()


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        test_initial_state_inactive_no_row,
        test_startup_recovery_soft_kill,
        test_startup_recovery_hard_kill,
        test_is_active_entry,
        test_is_active_exit,
        test_is_active_any,
        test_soft_kill_persists_publishes_logs_critical,
        test_soft_kill_idempotent_noop,
        test_soft_kill_ignored_when_hard_kill,
        test_hard_kill_invokes_cancel_fn,
        test_hard_kill_no_callback_empty_report,
        test_hard_kill_reruns_cancel_when_already_hard_kill,
        test_resume_from_soft_kill,
        test_resume_from_hard_kill,
        test_resume_from_inactive_raises,
        test_resume_empty_triggered_by_raises,
        test_record_api_failure_auto_trips,
        test_fix185_broker_auth_error_not_counted,
        test_fix191_order_rejected_not_counted,
        test_record_success_resets_counter,
        test_enable_auto_trip_false_no_auto_trip,
        test_get_kill_info_has_all_fields,
        test_status_dict_has_all_fields,
        test_no_deadlock_record_api_failure_reentrant,
        test_concurrent_soft_kill_idempotent,
        test_persistence_atomicity_state_store_failure,
        test_hard_kill_event_payload_ks8,
        test_clear_stale_state_clears_previous_day,
        test_clear_stale_state_keeps_same_day,
        test_clear_stale_state_noop_when_inactive,
        test_clear_stale_state_hard_kill_also_cleared,
        test_clear_stale_state_multi_day_gap,
        test_clear_stale_state_weekend_gap,
    ]

    print("=" * 70)
    print("kill_switch.py -- Test Suite")
    print("=" * 70)

    failed = []
    for test in tests:
        print(f"\n-> {test.__name__}")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
            try:
                test(Path(td))
            except AssertionError as e:
                failed.append((test.__name__, f"AssertionError: {e}"))
                print(f"  FAIL {e}")
            except Exception as e:
                failed.append((test.__name__, f"{type(e).__name__}: {e}"))
                print(f"  ERROR {type(e).__name__}: {e}")

    print("\n" + "=" * 70)
    if failed:
        print(f"FAILED: {len(failed)} of {len(tests)} tests")
        for name, err in failed:
            print(f"  FAIL {name}: {err}")
        return 1

    print(f"PASSED: all {len(tests)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
