"""tests/unit/test_alert_delivery_phase1.py — PHASE 1 of the alert remediation.

⭐ SAME contract as Phase 0, ⛔ no second mechanism:
    ① a trading safety action must NEVER depend on a notification succeeding
    ② a notification failure must ALWAYS remain observable

Two sites, ⛔ and only two:
    · `live_feed._on_noreconnect` — the feed is DEAD and SOFT_KILL was triggered
    · `main`'s eod-self-exit loop — shutdown DEFERRED, positions still open

🔑 THE SAFETY BOUNDARY WAS TRACED BEFORE EITHER WAS TOUCHED, ⛔ not assumed from
Phase 0's shape:
  · live_feed: `_log.critical` → `_on_critical_failure(...)` → `kill_switch.soft_kill(...)`
    ALL run BEFORE the notifier block. `_on_critical_failure` itself calls `soft_kill`
    first and only then notifies (and logs its own notifier failure). ⇒ a notification
    failure cannot skip, delay or alter the SOFT_KILL.
  · eod-self-exit: the "defer" is the ABSENCE of `shutdown_event.set()`, decided by
    `_eod_self_exit_due(...)` before the alert. The alert is purely informational.
    ⚠️ The swallow matters here for a second reason: an escaping exception would kill
    the `eod-self-exit` THREAD, and the service would then never self-exit at all.

⚠️ PARITY, PER TEST — ⛔ never as a ratio. Both sites need a real-world failure the
paper broker may not naturally produce: a websocket that exhausts every reconnect,
and a book that is still open past the EOD window. **Every test below SIMULATES that
failure** — the exhausted-reconnect state and the open-position count are supplied
directly. ⛔ A green paper run is NOT evidence that paper reaches these branches in
production, and that wording must not weaken later.
"""
from __future__ import annotations

import inspect
from typing import Any, List

import pytest


class _RecordingLog:
    def __init__(self) -> None:
        self.calls: List[dict] = []

    def _cap(self, level, msg, *a, **kw):
        self.calls.append({"level": level, "msg": msg, "extra": kw.get("extra") or {}})

    def info(self, m, *a, **kw):      self._cap("info", m, *a, **kw)
    def warning(self, m, *a, **kw):   self._cap("warning", m, *a, **kw)
    def error(self, m, *a, **kw):     self._cap("error", m, *a, **kw)
    def critical(self, m, *a, **kw):  self._cap("critical", m, *a, **kw)
    def debug(self, m, *a, **kw):     self._cap("debug", m, *a, **kw)

    def outcomes(self):
        return [c["extra"].get("outcome") for c in self.calls if "outcome" in (c["extra"] or {})]


class _OkNotifier:
    def send(self, **kw):
        return type("R", (), {"success": True, "delivered_to": ["c"], "failed_to": [],
                              "sentinel_path": None, "failed_log_written": False})()


class _RaisingNotifier:
    def send(self, **kw):
        raise RuntimeError("telegram timeout")


class _SpyKill:
    def __init__(self):
        self.soft_kills: List[str] = []

    def soft_kill(self, reason, **kw):
        self.soft_kills.append(reason)


# ═════════════════════════════════════════════════════════════════════════════
# SITE A — live_feed: the feed is DEAD, SOFT_KILL triggered
# ⚠️ SIMULATION: a websocket that has exhausted every reconnect is supplied by
#    calling `_on_noreconnect` directly. UNMEASURED whether paper produces one.
# ═════════════════════════════════════════════════════════════════════════════

def _feed_stub(notifier, log, kill, *, in_hours=True):
    from data.live_feed import LiveFeedManager
    f = LiveFeedManager.__new__(LiveFeedManager)     # ⛔ no __init__: alert path only
    f._notifier = notifier
    f._log = log
    f._kill_switch = kill
    f._mode_label = "LIVE"
    f._max_reconnect_attempts = 5
    f._reconnect_count = 5
    f._on_critical_failure = None
    f._market_windows = None
    f._within_market_hours = lambda *a, **k: in_hours
    return f


def _call_noreconnect(feed):
    """Call the real `_on_noreconnect`, tolerating the off-hours early return."""
    from data.live_feed import LiveFeedManager
    LiveFeedManager._on_noreconnect(feed, None)


@pytest.mark.parametrize("mode", ["PAPER", "LIVE"])
def test_feed_death_records_delivered_when_the_alert_lands(mode):
    log, kill = _RecordingLog(), _SpyKill()
    f = _feed_stub(_OkNotifier(), log, kill)
    f._mode_label = mode
    _call_noreconnect(f)
    assert "delivered" in log.outcomes(), log.calls


@pytest.mark.parametrize("mode", ["PAPER", "LIVE"])
def test_feed_death_SOFT_KILL_still_fires_when_the_notifier_RAISES(mode):
    """🔑 INVARIANT ①, and the test that must exist for this phase.
    ⚠️ SIMULATED: the exhausted-reconnect state is supplied, not produced."""
    log, kill = _RecordingLog(), _SpyKill()
    f = _feed_stub(_RaisingNotifier(), log, kill)
    f._mode_label = mode
    _call_noreconnect(f)                      # ⛔ must not raise
    assert kill.soft_kills == ["LIVEFEED_RECONNECT_EXHAUSTED"], \
        "the SOFT_KILL must be unaffected by a notification failure"
    assert "failed" in log.outcomes(), "invariant ②: the failure must be observable"


def test_feed_death_soft_kill_is_ordered_BEFORE_the_alert():
    """⭐ The boundary, pinned structurally so a later edit cannot reorder it."""
    from data.live_feed import LiveFeedManager
    src = inspect.getsource(LiveFeedManager._on_noreconnect)
    i_kill = src.index("soft_kill(")
    i_alert = src.index("WebSocket DEAD")
    assert i_kill < i_alert, "soft_kill must run BEFORE the alert, never after"


# ═════════════════════════════════════════════════════════════════════════════
# SITE B — main: EOD shutdown DEFERRED, positions still open
# ⚠️ SIMULATION: the open-position count is supplied via a stubbed
#    `_eod_self_exit_due`; UNMEASURED whether paper naturally carries a book past
#    the EOD window.
# ═════════════════════════════════════════════════════════════════════════════

def test_eod_defer_alert_uses_the_contract_and_ignores_its_result():
    """⛔ Same helper, ⛔ same vocabulary, ⛔ return value unused (invariant ①
    made structural: nobody can later write `if not send_alert_recorded(...)`)."""
    import main as _main
    src = inspect.getsource(_main._start_eod_self_exit_thread)
    assert "send_alert_recorded" in src, "the defer alert must record its outcome"
    assert "EOD shutdown deferred" in src
    for banned in ("if send_alert_recorded", "if not send_alert_recorded",
                   "= send_alert_recorded"):
        assert banned not in src, f"the result must not be branched on: {banned}"


def test_eod_defer_decision_is_taken_BEFORE_the_alert():
    """⭐ The defer is the ABSENCE of `shutdown_event.set()`, decided by
    `_eod_self_exit_due` above the alert. Pin the order."""
    import main as _main
    src = inspect.getsource(_main._start_eod_self_exit_thread)
    i_due = src.index("_eod_self_exit_due(")
    i_alert = src.index("EOD shutdown deferred")
    assert i_due < i_alert


def test_eod_clean_shutdown_alert_is_NOT_converted_in_phase_1():
    """⛔ Phase 1 is TWO sites. `main:1238` (EOD CLEAN shutdown) is a DIFFERENT
    alert and stays exactly as it was — similarity is not scope."""
    import main as _main
    src = inspect.getsource(_main._start_eod_self_exit_thread)
    i_clean = src.index("EOD Clean Shutdown")
    window = src[max(0, i_clean - 400):i_clean + 400]
    assert "send_alert_recorded" not in window, \
        "the clean-shutdown alert must be left untouched in Phase 1"


# ═════════════════════════════════════════════════════════════════════════════
# CROSS-CUTTING — one mechanism, one vocabulary, and `suppressed` stays narrow
# ═════════════════════════════════════════════════════════════════════════════

def test_no_second_mechanism_was_invented():
    from alerts.delivery import ALERT_OUTCOMES, send_alert_recorded
    assert ALERT_OUTCOMES == ("delivered", "failed", "suppressed")
    assert callable(send_alert_recorded)


def test_suppressed_means_no_delivery_was_attempted_and_nothing_else():
    """⭐ §4.3 — `suppressed` must never become a bucket for unexpected failures,
    which is exactly what this phase exists to expose."""
    from alerts.delivery import send_alert_recorded
    log = _RecordingLog()
    send_alert_recorded(None, log, severity="INFO", title="t", body="b",
                        source_module="unit")
    assert log.outcomes() == ["suppressed"]

    log2 = _RecordingLog()
    send_alert_recorded(_RaisingNotifier(), log2, severity="INFO", title="t",
                        body="b", source_module="unit")
    assert log2.outcomes() == ["failed"], "a raise is `failed`, ⛔ never `suppressed`"


def test_phase_1_did_not_touch_the_out_of_scope_sites():
    """⛔ `live_feed:397` / `:457`, the ten other swallows, `N9-14`, the limiters and
    `alert_watcher` are all out of scope. Pin the two that live in this same file."""
    from data.live_feed import LiveFeedManager
    recon = inspect.getsource(LiveFeedManager._on_reconnect)
    assert "send_alert_recorded" not in recon, "the reconnect alert is Phase 1+"
    src = inspect.getsource(LiveFeedManager._on_noreconnect)
    i_idle = src.index("WebSocket idle off-hours")
    assert "send_alert_recorded" not in src[max(0, i_idle - 400):i_idle + 400], \
        "the off-hours idle alert is out of Phase 1 scope"
