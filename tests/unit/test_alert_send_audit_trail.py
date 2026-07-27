"""tests/unit/test_alert_send_audit_trail.py

27-Jul-2026. The alert stream had NO send-side audit trail below CRITICAL:
  · no `telegram_notifier` line in system_<date>.log on a normal day (send() logged
    only on the disabled branch);
  · sentinels exist only for CRITICAL;
  · failed_alerts.log records only FAILURES — its last entry was 2-Jul.

So "did every broker event produce an alert?" was unanswerable from the system's
own records. On 27-Jul that was concrete: three placement failures (PYRAMID x2,
KECL) and no local way to tell whether each alerted.

⭐ The OUTCOME field is the point, not the send. "we tried" and "it arrived" are
different facts, and only the first was ever knowable.
"""
from __future__ import annotations

import logging

import pytest

from alerts.telegram_notifier import SendResult, TelegramNotifier


class _Rec(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    def audits(self):
        return [r for r in self.records if r.getMessage() == "alert_send"]


@pytest.fixture
def notifier_and_log(tmp_path):
    log = logging.getLogger("test_alert_audit")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    rec = _Rec()
    log.addHandler(rec)
    n = TelegramNotifier(
        bot_token="t", channels=[{"chat_id": "-100", "label": "c", "enabled": True}],
        failed_alerts_log_path=str(tmp_path / "failed.log"),
        sentinel_dir=str(tmp_path), logger=log, enabled=True,
    )
    return n, rec


def _force(n, result: SendResult):
    """Drive send() with each tier handler stubbed to a known SendResult, so the
    audit line is tested against the OUTCOME rather than against the network."""
    n._handle_critical = lambda *a, **k: result
    n._handle_error = lambda *a, **k: result
    n._handle_info_warn = lambda *a, **k: result


# ── the outcome field ─────────────────────────────────────────────────────────

def test_delivered_is_recorded_as_delivered(notifier_and_log):
    n, rec = notifier_and_log
    _force(n, SendResult(success=True, tier="INFO", delivered_to=["-100"]))
    n.send("INFO", "TARGET HIT", "body", "order_placer")
    a = rec.audits()
    assert len(a) == 1
    assert a[0].outcome == "delivered"
    assert a[0].delivered_to == ["-100"]
    assert a[0].severity == "INFO" and a[0].source_module == "order_placer"


def test_a_failed_delivery_is_recorded_as_failed_not_as_sent(notifier_and_log):
    """THE distinction the trail exists for: the send was ATTEMPTED and did NOT
    arrive. Before this, that was indistinguishable from success."""
    n, rec = notifier_and_log
    _force(n, SendResult(success=False, tier="WARNING", failed_to=["-100"]))
    n.send("WARNING", "ORDER REJECTED -- PYRAMID", "body", "order_placer")
    a = rec.audits()
    assert len(a) == 1 and a[0].outcome == "failed"
    assert a[0].failed_to == ["-100"] and a[0].delivered_to == []


def test_a_suppressed_send_is_distinguished_from_a_delivered_one(notifier_and_log):
    """success=True with nothing delivered (e.g. paper-mode suppression) is NOT
    'delivered'. Collapsing the two would recreate the gap this closes."""
    n, rec = notifier_and_log
    _force(n, SendResult(success=True, tier="INFO", delivered_to=[]))
    n.send("INFO", "x", "body", "m")
    assert rec.audits()[0].outcome == "suppressed"


def test_the_disabled_branch_is_audited_too(tmp_path):
    """The early return had its own path out of send(); without covering it the
    trail would have a hole exactly where alerting is switched off."""
    log = logging.getLogger("test_alert_audit_disabled")
    log.setLevel(logging.INFO)
    log.handlers.clear()
    rec = _Rec()
    log.addHandler(rec)
    n = TelegramNotifier(
        bot_token="t", channels=[{"chat_id": "-100", "label": "c", "enabled": True}],
        failed_alerts_log_path=str(tmp_path / "f.log"), sentinel_dir=str(tmp_path),
        logger=log, enabled=False,
    )
    n.send("CRITICAL", "x", "b", "m")
    a = rec.audits()
    assert len(a) == 1 and a[0].outcome == "suppressed_disabled"


def test_every_severity_tier_is_audited(notifier_and_log):
    n, rec = notifier_and_log
    _force(n, SendResult(success=True, tier="X", delivered_to=["-100"]))
    for sev in ("INFO", "WARNING", "ERROR", "CRITICAL"):
        n.send(sev, f"t-{sev}", "b", "m")
    assert [r.severity for r in rec.audits()] == ["INFO", "WARNING", "ERROR", "CRITICAL"]


# ── it must never break a send, and never become an alert ─────────────────────

def test_an_audit_failure_cannot_break_the_send(notifier_and_log):
    """THE most important test here. An alert failing because its own audit line
    failed would be strictly worse than having no trail at all."""
    n, rec = notifier_and_log
    expected = SendResult(success=True, tier="CRITICAL", delivered_to=["-100"])
    _force(n, expected)

    class _Boom:
        def info(self, *a, **k):
            raise RuntimeError("logging exploded")
        def error(self, *a, **k):
            raise RuntimeError("logging exploded")

    n._log = _Boom()
    out = n.send("CRITICAL", "capital breach", "body", "fund_manager")
    assert out is expected, "the caller's result must be returned unchanged"
    assert out.success is True


def test_the_audit_is_a_LOG_and_never_raises_an_alert(notifier_and_log):
    """It must not become the next noise problem: no sentinel, no send, no
    recursion back into the notifier."""
    n, rec = notifier_and_log
    sent: list = []
    _force(n, SendResult(success=True, tier="INFO", delivered_to=["-100"]))
    real_audit = n._audit_send
    n._audit_send = lambda *a, **k: (sent.append(1), real_audit(*a, **k))[1]
    n.send("INFO", "x", "b", "m")
    assert len(sent) == 1, "exactly one audit per send -- no recursion"
    assert all(r.levelno == logging.INFO for r in rec.audits()), "audit must be INFO"
