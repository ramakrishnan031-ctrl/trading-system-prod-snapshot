"""tests/unit/test_alert_delivery_contract.py — PHASE 0 of the alert remediation.

⭐ THE CONTRACT THIS PINS, written before any site was touched:

    ① A trading safety action must NEVER depend on a notification succeeding.
    ② A notification failure must ALWAYS remain observable.

🔑 Today the codebase satisfies ① by SWALLOWING, which buys ① at the cost of ②.
⛔ Phase 0 does NOT remove the swallows — the swallow is what protects the action.
⭐ It makes them RECORD before they pass.

🔑 WHY THE RECORD MUST BE CALLER-SIDE: `TelegramNotifier._audit_send` already writes
`delivered/failed/suppressed`, but it lives INSIDE `send()`. It can only see a send
that was ATTEMPTED. A call that never reaches `send()` — a missing method (`N9-14`),
a bad kwarg, an adapter swap — writes nothing anywhere. That exact class is what this
records, so the record has to be written by the CALLER.

⛔ AND THE RECORD IS A LOG, NEVER AN ALERT. Alerting about a failed alert on the
channel that just failed is circular; the log is the only honest sink.

⚠️ PARITY, PER TEST — stated rather than assumed. Both Phase-0 sites need a
BROKER-SIDE failure to arise (a position that will not flatten; an SL that will not
place). Whether the PAPER broker can produce either is UNMEASURED. Every test below
is therefore a SIMULATION of the failure, in both modes, and ⛔ a green paper run is
NOT evidence that paper can reach these branches in production.
"""
from __future__ import annotations

import logging
from typing import Any, List

import pytest


# ═════════════════════════════════════════════════════════════════════════════
# doubles
# ═════════════════════════════════════════════════════════════════════════════

class _RecordingLog:
    """Captures structured log calls. `extra` is where the record lives."""

    def __init__(self) -> None:
        self.calls: List[dict] = []

    def _cap(self, level, msg, *a, **kw):
        self.calls.append({"level": level, "msg": msg,
                           "extra": kw.get("extra") or {}})

    def info(self, msg, *a, **kw):      self._cap("info", msg, *a, **kw)
    def warning(self, msg, *a, **kw):   self._cap("warning", msg, *a, **kw)
    def error(self, msg, *a, **kw):     self._cap("error", msg, *a, **kw)
    def critical(self, msg, *a, **kw):  self._cap("critical", msg, *a, **kw)
    def debug(self, msg, *a, **kw):     self._cap("debug", msg, *a, **kw)

    def outcomes(self) -> List[str]:
        return [c["extra"].get("outcome") for c in self.calls
                if "outcome" in (c["extra"] or {})]


class _OkNotifier:
    def __init__(self, success=True, delivered=("chat1",)):
        self._success, self._delivered = success, delivered
        self.sent: List[dict] = []

    def send(self, **kw):
        self.sent.append(kw)
        return type("R", (), {"success": self._success,
                              "delivered_to": list(self._delivered),
                              "failed_to": [], "sentinel_path": None,
                              "failed_log_written": False})()


class _RaisingNotifier:
    """The N9-14 class, generalised: the call does not come back."""

    def __init__(self, exc=None):
        self._exc = exc or AttributeError("'TelegramNotifier' object has no attribute 'send_warning'")

    def send(self, **kw):
        raise self._exc


# ═════════════════════════════════════════════════════════════════════════════
# §1 — the contract helper itself
# ═════════════════════════════════════════════════════════════════════════════

def test_helper_returns_true_and_records_delivered_on_success():
    from alerts.delivery import send_alert_recorded
    log = _RecordingLog()
    n = _OkNotifier()
    ok = send_alert_recorded(n, log, severity="CRITICAL", title="t", body="b",
                             source_module="unit")
    assert ok is True
    assert log.outcomes() == ["delivered"]
    assert n.sent and n.sent[0]["severity"] == "CRITICAL"


def test_helper_records_failed_when_the_notifier_reports_failure():
    from alerts.delivery import send_alert_recorded
    log = _RecordingLog()
    ok = send_alert_recorded(_OkNotifier(success=False, delivered=()), log,
                             severity="CRITICAL", title="t", body="b",
                             source_module="unit")
    assert ok is False
    assert log.outcomes() == ["failed"]


def test_helper_NEVER_raises_when_the_notifier_raises__and_records_failed():
    """🔑 INVARIANT ① at the helper level, and the exact `N9-14` shape."""
    from alerts.delivery import send_alert_recorded
    log = _RecordingLog()
    ok = send_alert_recorded(_RaisingNotifier(), log, severity="CRITICAL",
                             title="t", body="b", source_module="unit")
    assert ok is False
    assert log.outcomes() == ["failed"]
    rec = [c for c in log.calls if c["extra"].get("outcome") == "failed"][0]
    assert rec["level"] == "error", "a failure must be observable at error level"
    assert "AttributeError" in str(rec["extra"].get("error", ""))


def test_helper_reuses_the_audit_send_vocabulary__no_second_vocabulary():
    """⛔ `delivered` / `failed` / `suppressed` — the words `_audit_send` already
    uses. A second vocabulary would split the trail in two."""
    from alerts.delivery import ALERT_OUTCOMES
    assert ALERT_OUTCOMES == ("delivered", "failed", "suppressed")


def test_a_missing_notifier_records_suppressed_and_never_raises():
    from alerts.delivery import send_alert_recorded
    log = _RecordingLog()
    assert send_alert_recorded(None, log, severity="INFO", title="t", body="b",
                               source_module="unit") is False
    assert log.outcomes() == ["suppressed"]


def test_the_recorder_itself_cannot_raise():
    """⚠️ §1.3 — a failure-recorder that throws inside a swallow is the same bug one
    layer down. A logger that explodes must still leave the action untouched."""
    from alerts.delivery import send_alert_recorded

    class _ExplodingLog:
        def info(self, *a, **kw):     raise RuntimeError("log is down")
        def error(self, *a, **kw):    raise RuntimeError("log is down")
        def warning(self, *a, **kw):  raise RuntimeError("log is down")

    assert send_alert_recorded(_OkNotifier(), _ExplodingLog(), severity="INFO",
                               title="t", body="b", source_module="unit") is True
    assert send_alert_recorded(_RaisingNotifier(), _ExplodingLog(), severity="INFO",
                               title="t", body="b", source_module="unit") is False


def test_no_alert_is_emitted_about_the_failed_alert():
    """⛔ The record is a LOG. Alerting about a failed alert on the channel that
    just failed is circular — the notifier must be called exactly once."""
    from alerts.delivery import send_alert_recorded
    n = _RaisingNotifier()
    calls = {"n": 0}
    orig = n.send

    def counting(**kw):
        calls["n"] += 1
        return orig(**kw)

    n.send = counting
    send_alert_recorded(n, _RecordingLog(), severity="CRITICAL", title="t",
                        body="b", source_module="unit")
    assert calls["n"] == 1


# ═════════════════════════════════════════════════════════════════════════════
# §2 — SITE 1: kill_switch — HARD_KILL fired and a position could NOT be exited
# ⚠️ SIMULATION. Needs a broker-side failure (a position that will not flatten);
#    whether PAPER can produce one is UNMEASURED. Both modes are exercised below,
#    but ⛔ that is not evidence paper reaches this branch in production.
# ═════════════════════════════════════════════════════════════════════════════

def _ks_stub(notifier, log, mode="LIVE"):
    from capital.kill_switch import KillSwitch
    ks = KillSwitch.__new__(KillSwitch)          # ⛔ no __init__: this is the alert path only
    ks._notifier = notifier
    ks._log = log
    ks._mode = mode
    ks._exit_alert_ts = {}
    return ks


_FAILED = [("T1", "RELIANCE", "SELL", 5, "INTRADAY", None)]


@pytest.mark.parametrize("mode", ["PAPER", "LIVE"])
def test_kill_switch_exit_failure_alert_records_delivered(mode):
    log = _RecordingLog()
    ks = _ks_stub(_OkNotifier(), log, mode)
    ks._alert_exit_failed(list(_FAILED))
    assert "delivered" in log.outcomes()


@pytest.mark.parametrize("mode", ["PAPER", "LIVE"])
def test_kill_switch_alert_failure_does_NOT_break_the_kill_path(mode):
    """🔑 INVARIANT ①. The notifier raises; `_alert_exit_failed` must return
    normally so the caller's HARD_KILL retry loop continues untouched.
    ⚠️ SIMULATED broker failure — the `failed_trades` list is supplied directly."""
    log = _RecordingLog()
    ks = _ks_stub(_RaisingNotifier(), log, mode)
    ks._alert_exit_failed(list(_FAILED))          # ⛔ must not raise
    assert "failed" in log.outcomes(), "invariant ②: the failure must be observable"


def test_kill_switch_exit_and_kill_logic_stay_OUTSIDE_the_swallow():
    """⭐ The 09-Aug trace found the swallow correctly scoped to the alert alone.
    This pins it: `_alert_exit_failed` RECEIVES already-failed trades and must not
    place, cancel, or mutate anything — it only reports."""
    import inspect
    from capital.kill_switch import KillSwitch
    src = inspect.getsource(KillSwitch._alert_exit_failed)
    for forbidden in ("place_order", "cancel_order", "exit_position",
                      "_flatten_one", "hard_kill(", "soft_kill("):
        assert forbidden not in src, f"{forbidden} must never live inside the alert path"


# ═════════════════════════════════════════════════════════════════════════════
# §2 — SITE 2: order_placer — SL failed permanently, EMERGENCY market exit placed
# ⚠️ SIMULATION, same caveat: needs an SL that will not place. UNMEASURED in paper.
# ═════════════════════════════════════════════════════════════════════════════

def test_order_placer_emergency_exit_alert_is_recorded_and_cannot_break_the_exit():
    """🔑 INVARIANT ①: the emergency exit has ALREADY been placed by the time the
    alert runs, and the function must still return True when the alert explodes."""
    import inspect
    from orders.order_placer import OrderPlacer
    src = inspect.getsource(OrderPlacer)
    i = src.find("EMERGENCY EXIT --")
    assert i > 0, "the emergency-exit alert site moved; re-measure before editing"
    window = src[max(0, i - 2500):i]
    # the order is placed before the alert — the alert cannot precede the action
    assert "emergency" in window.lower()
    # and the alert must go through the recorded helper, not a bare swallow
    assert "send_alert_recorded" in src[max(0, i - 800):i + 800], \
        "the emergency-exit alert must record its outcome (invariant ②)"


def test_both_phase0_sites_use_the_recorded_helper():
    """⛔ Phase 0 is TWO sites. This pins that both were converted — and, by only
    naming two, that the other twelve swallows were left alone."""
    import inspect
    from capital.kill_switch import KillSwitch
    from orders.order_placer import OrderPlacer
    assert "send_alert_recorded" in inspect.getsource(KillSwitch._alert_exit_failed)
    assert "send_alert_recorded" in inspect.getsource(OrderPlacer)
