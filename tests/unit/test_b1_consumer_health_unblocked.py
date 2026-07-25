"""
tests/unit/test_b1_consumer_health_unblocked.py

B1 (25-Jul-2026): FIX-029's consumer-thread death detector used to sit INSIDE the
watchdog's tick-age pre-arm loop. `_watchdog_loop` waited for `_last_tick_at is not
None` before entering the loop that calls `_check_consumer_health()` -- so while
nothing was subscribed to the WebSocket (the live production state: the socket
connected 32 times and never carried a token), the consumer-thread health check
NEVER RAN. A protection for a thread that has nothing to do with ticks was disabled
by an unrelated module's wiring gap.

These tests pin the restored behaviour AND the deliberate non-behaviour:
  - the health check runs with NO tick ever having arrived   (B1, was RED)
  - the tick-age watchdog still does NOT fire without ticks  (B2: do NOT arm it)
  - paper mode is unchanged                                  (PARITY, Rule #5)
"""

from __future__ import annotations

import os
import sys
import threading
import time
from unittest.mock import MagicMock, create_autospec

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def _make_logger() -> MagicMock:
    log = MagicMock()
    log.info = MagicMock()
    log.warning = MagicMock()
    log.error = MagicMock()
    log.critical = MagicMock()
    return log


def _feed(logger, **kw):
    from data.live_feed import LiveFeedManager
    return LiveFeedManager(
        api_key="k",
        access_token="t",
        logger=logger,
        watchdog_check_interval_sec=kw.pop("interval", 1),
        **kw,
    )


def _dead_thread():
    """A thread object that reports itself dead (autospec, per the mock rule)."""
    t = create_autospec(threading.Thread, instance=True)
    t.is_alive.return_value = False
    return t


def _live_thread():
    t = create_autospec(threading.Thread, instance=True)
    t.is_alive.return_value = True
    return t


def _critical_text(logger) -> str:
    return " ".join(str(c) for c in logger.critical.call_args_list)


def _run_watchdog(feed, seconds: float = 0.6) -> None:
    """Drive _watchdog_loop directly in a daemon thread, then stop it."""
    feed._watchdog_check_interval_sec = 0.02
    th = threading.Thread(target=feed._watchdog_loop, daemon=True)
    th.start()
    time.sleep(seconds)
    feed._stop_event.set()
    th.join(timeout=2.0)


# ---------------------------------------------------------------------------
# B1 -- the regression this batch fixes. RED on HEAD before the fix.
# ---------------------------------------------------------------------------

def test_consumer_health_check_runs_when_no_tick_has_ever_arrived():
    """B1: a DEAD consumer thread is detected even though _last_tick_at is None.

    On the pre-fix code the watchdog spins forever in its pre-arm loop waiting
    for a first tick, so _check_consumer_health() is never reached and this
    assertion fails -- which is exactly the production state.
    """
    log = _make_logger()
    feed = _feed(log)
    feed._consumer_thread = _dead_thread()
    assert feed._last_tick_at is None, "precondition: no tick has ever arrived"

    _run_watchdog(feed)

    assert "DEAD" in _critical_text(log), (
        "consumer-thread death was not detected with no ticks present; "
        "the health check is still gated behind the tick-age pre-arm loop"
    )


def test_healthy_consumer_thread_is_not_restarted():
    """Anti-vacuity: the check must not fire merely because it now runs."""
    log = _make_logger()
    feed = _feed(log)
    feed._consumer_thread = _live_thread()

    _run_watchdog(feed)

    assert "DEAD" not in _critical_text(log)
    assert "restarted" not in " ".join(
        str(c) for c in log.warning.call_args_list
    ).lower()


def test_no_consumer_thread_yet_is_a_no_op():
    """connect() not called yet -> nothing to protect, nothing logged."""
    log = _make_logger()
    feed = _feed(log)
    assert feed._consumer_thread is None

    _run_watchdog(feed)

    assert "DEAD" not in _critical_text(log)


# ---------------------------------------------------------------------------
# B2 -- the deliberate NON-change. Pins "do not arm the tick-age alarm".
# ---------------------------------------------------------------------------

def test_tick_age_watchdog_stays_silent_when_no_tick_ever_arrives():
    """B2: with zero ticks the tick-age alarm must NOT fire.

    Silence here is CORRECT for the current dormant-feed state: nothing is
    subscribed, so 'no tick for 30s' is not an anomaly. Arming it now would
    alarm every interval of every day. This test exists so that a future change
    cannot arm it silently.
    """
    log = _make_logger()
    on_critical = MagicMock()
    feed = _feed(log, on_critical_failure=on_critical)
    feed._connected = True                 # connected, but nothing subscribed
    feed._consumer_thread = _live_thread()
    feed._tick_stale_threshold_sec = 0     # would fire instantly if armed

    _run_watchdog(feed)

    on_critical.assert_not_called()
    assert not feed._force_reconnect.is_set()
    assert feed._watchdog_alert_fired is False


def test_tick_age_watchdog_still_fires_once_ticks_have_been_seen():
    """Anti-vacuity for B2: the alarm is dormant, NOT removed."""
    from core.time_authority import now_ist

    log = _make_logger()
    on_critical = MagicMock()
    feed = _feed(log, on_critical_failure=on_critical)
    feed._connected = True
    feed._consumer_thread = _live_thread()
    feed._tick_stale_threshold_sec = 0
    feed._last_tick_at = now_ist()         # a tick HAS arrived -> armed

    _run_watchdog(feed)

    on_critical.assert_called()
    assert feed._force_reconnect.is_set()


# ---------------------------------------------------------------------------
# PARITY (Rule #5)
# ---------------------------------------------------------------------------

def test_parity_paper_mode_starts_no_watchdog_and_has_no_consumer_thread():
    """Paper mode: connect() starts no consumer thread, so there is nothing for
    the health check to protect and _start_watchdog stays a no-op. B1 changes
    live-mode behaviour only."""
    log = _make_logger()
    feed = _feed(log, paper_mode=True)

    feed.connect()

    assert feed._consumer_thread is None
    assert feed._watchdog_thread is None
