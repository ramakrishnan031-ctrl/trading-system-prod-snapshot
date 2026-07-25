"""
tests/unit/test_kill_persist_timing.py

§A (25-Jul-2026): time `KillSwitch._persist_state` and WARN above a threshold.

WHY THIS EXISTS (from docs/audit/kill_persist_busy_timeout_25jul2026.md):
`_persist_state` runs INSIDE `self._lock`, and `is_active()` -- the last-mile
order check before every placement -- takes that SAME lock. So a slow persist
stalls order placement, not merely /health. The write inherits
`PRAGMA busy_timeout = 30000`, and cross-process writers genuinely exist (33 cron
heartbeat jobs, two of them on */5 during market hours).

Production evidence says the 30 s CEILING has never been hit (zero SQLITE_BUSY /
"database is locked" in any log, against 25 real kill activations) -- but the call
was never timed, so "it has never blocked for >=30 s" was proven while "it has
never blocked" was not. This closes that gap with a measurement, NOT a redesign:
⛔ busy_timeout is unchanged and the write stays inside the lock (KS9 persist-first
is deliberate -- it trades latency for atomicity on purpose).

The instrumentation must obey three constraints, each pinned below:
  * it must not slow the kill path        -> a monotonic() pair + a conditional log
  * it must still time a FAILING write    -> `finally`, since the 30 s busy-timeout
                                             case is exactly a slow FAILURE
  * it must never break the kill          -> the log is wrapped; the original
                                             exception propagates untouched
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
from contextlib import contextmanager
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from capital.kill_switch import KillSwitch, KillState  # noqa: E402
from core.events import EventBus  # noqa: E402


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------

class _OkStore:
    """StateStore double whose transaction() succeeds and records nothing."""

    def fetch_one(self, sql: str, params: tuple = ()) -> None:
        return None

    @contextmanager
    def transaction(self):
        class _Cur:
            def execute(self, *a, **kw):
                return None
        yield _Cur()


class _FailingStore:
    """StateStore double that raises inside transaction() (KS9 abort path)."""

    def fetch_one(self, sql: str, params: tuple = ()) -> None:
        return None

    @contextmanager
    def transaction(self):
        raise sqlite3.OperationalError("simulated disk full")
        yield  # pragma: no cover - never reached


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def text(self, level: int | None = None) -> str:
        return " | ".join(
            r.getMessage() for r in self.records
            if level is None or r.levelno == level
        )


class _ExplodingLogger(logging.Logger):
    """A logger whose .warning() always raises -- the instrumentation must
    survive this, because the kill path is the last thing that should die of an
    instrumentation error."""

    def warning(self, *a, **kw):  # noqa: D102
        raise RuntimeError("logging backend exploded")


def _ks(store, *, mode: str = "LIVE", logger: logging.Logger | None = None):
    if logger is None:
        logger = logging.getLogger(f"test_kpt_{id(store)}_{mode}")
        logger.handlers.clear()
        logger.setLevel(logging.DEBUG)
    return KillSwitch(
        state_store=store,
        bus=EventBus(),
        logger=logger,
        enable_auto_trip=False,
        mode=mode,
    )


def _clock(elapsed: float):
    """Patch time.monotonic so _persist_state observes exactly `elapsed`.

    _persist_state does a function-local `import time` (the file's own idiom),
    so the module attribute is resolved at call time and this patch reaches it.
    """
    ticks = iter([1000.0, 1000.0 + elapsed])
    return patch("time.monotonic", side_effect=lambda: next(ticks))


_MARKER = "_persist_state"


# ---------------------------------------------------------------------------
# A4 -- the WARNING fires above the threshold, and is silent below it
# ---------------------------------------------------------------------------

def test_slow_persist_emits_a_warning():
    """RED on HEAD: nothing times _persist_state, so no warning exists."""
    store, cap = _OkStore(), _Capture()
    ks = _ks(store)
    ks._log.addHandler(cap)

    with _clock(2.5):
        ks.soft_kill("slow-write-test", triggered_by="unit_test")

    warned = cap.text(logging.WARNING)
    assert _MARKER in warned, (
        "a persist slower than the threshold must WARN -- otherwise 'it has "
        f"never blocked' stays unmeasurable. Got: {warned!r}"
    )
    assert "2.5" in warned, f"the measured duration must be reported. Got: {warned!r}"


def test_fast_persist_is_silent():
    """Anti-vacuity: it must not fire on a normal sub-millisecond write, or the
    warning becomes noise and stops meaning anything."""
    store, cap = _OkStore(), _Capture()
    ks = _ks(store)
    ks._log.addHandler(cap)

    with _clock(0.004):          # a realistic single-row INSERT OR REPLACE
        ks.soft_kill("fast-write-test", triggered_by="unit_test")

    assert _MARKER not in cap.text(logging.WARNING), (
        "a fast persist must log nothing at WARNING"
    )
    assert ks.is_active("entry") is True, "the kill itself must still have taken"


# ---------------------------------------------------------------------------
# A3 -- instrumentation must never break the kill
# ---------------------------------------------------------------------------

def test_kill_survives_a_logging_failure():
    """If the logging backend raises, the kill must STILL take effect.

    This is the constraint that matters most: the kill path is the last thing
    that should die of an instrumentation error.
    """
    store = _OkStore()
    ks = _ks(store, logger=_ExplodingLogger("test_kpt_exploding"))

    with _clock(5.0):            # slow enough to reach the (exploding) warning
        ks.soft_kill("logging-explodes", triggered_by="unit_test")

    assert ks.is_active("entry") is True, (
        "a failure inside the timing log must not prevent the SOFT_KILL"
    )
    assert ks.current_state() is KillState.SOFT_KILL


# ---------------------------------------------------------------------------
# A5 / KS9 -- a slow FAILING write is still timed, and still aborts
# ---------------------------------------------------------------------------

def test_slow_failing_persist_is_timed_and_still_aborts():
    """The 30 s busy-timeout case is a slow FAILURE, so `finally` (not a happy
    path) is what makes it observable -- while KS9's abort is preserved: the
    original exception propagates and in-memory state does NOT change."""
    store, cap = _FailingStore(), _Capture()
    ks = _ks(store)
    ks._log.addHandler(cap)

    with _clock(12.0):
        with pytest.raises(sqlite3.OperationalError):
            ks.soft_kill("slow-and-failing", triggered_by="unit_test")

    assert _MARKER in cap.text(logging.WARNING), (
        "the case we most want to see -- a write that waits then fails -- must "
        "still be timed"
    )
    assert ks.current_state() is KillState.INACTIVE, (
        "KS9: a failed persist must leave in-memory state unchanged"
    )


# ---------------------------------------------------------------------------
# PARITY (Rule #5)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["LIVE", "PAPER"])
def test_parity_timing_is_identical_in_both_modes(mode):
    """_persist_state has no mode branch, so the measurement must behave the
    same in paper and live. Asserted rather than assumed."""
    store, cap = _OkStore(), _Capture()
    ks = _ks(store, mode=mode)
    ks._log.addHandler(cap)

    with _clock(3.0):
        ks.soft_kill(f"parity-{mode}", triggered_by="unit_test")

    assert _MARKER in cap.text(logging.WARNING), f"{mode}: expected the warning"
    assert ks.is_active("entry") is True
