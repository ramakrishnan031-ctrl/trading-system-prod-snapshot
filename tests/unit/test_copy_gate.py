"""Tests for scripts/copy_gate.py + scripts/request_copy.py (VM Security Phase 2)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scripts.copy_gate import (
    CopyConfig,
    CopyGate,
    CopyToken,
    in_time_lock,
    send_alert,
)

_IST = timezone(timedelta(hours=5, minutes=30))


def _cfg(tmp_path, **kw) -> CopyConfig:
    c = CopyConfig()
    c.token_path = str(tmp_path / "copy_token.json")
    c.audit_log_path = str(tmp_path / "copy_audit.log")
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _at(h, m=0):
    return datetime(2026, 6, 20, h, m, tzinfo=_IST)


# ── time-lock (the absolute, top-priority window) ────────────────────────────

@pytest.mark.parametrize("hour,locked", [
    (18, True), (19, True), (23, True), (0, True), (3, True), (7, True),
    (8, False), (9, False), (12, False), (17, False),
])
def test_in_time_lock_wraps_midnight(hour, locked):
    assert in_time_lock(_at(hour), 18, 8) is locked


def test_in_time_lock_non_wrapping_window():
    assert in_time_lock(_at(10), 9, 17) is True
    assert in_time_lock(_at(8), 9, 17) is False
    assert in_time_lock(_at(17), 9, 17) is False   # end is exclusive


def test_in_time_lock_equal_start_end_never_locks():
    assert in_time_lock(_at(0), 0, 0) is False
    assert in_time_lock(_at(13), 12, 12) is False


# ── token expiry ─────────────────────────────────────────────────────────────

def test_token_expiry():
    now = _at(10)
    fresh = CopyToken("t1", "r", "u", now.isoformat(),
                      (now + timedelta(minutes=5)).isoformat())
    assert fresh.is_expired(now) is False
    stale = CopyToken("t2", "r", "u", now.isoformat(),
                      (now - timedelta(minutes=1)).isoformat())
    assert stale.is_expired(now) is True
    bad = CopyToken("t3", "r", "u", "", "not-a-date")
    assert bad.is_expired(now) is True   # unparseable -> fail closed


# ── decision priority ────────────────────────────────────────────────────────

def test_timelock_overrides_even_a_valid_token(tmp_path):
    cfg = _cfg(tmp_path)
    gate = CopyGate(cfg)
    night = _at(19)
    gate.write_token(CopyToken("t", "r", "u", night.isoformat(),
                               (night + timedelta(minutes=10)).isoformat()))
    dec = gate.check_copy_allowed(now=night, active_sessions=1)
    assert dec.allowed is False and dec.reason == "TIME_LOCK"


def test_timelock_overrides_even_protection_off(tmp_path):
    cfg = _cfg(tmp_path, enabled=False)
    dec = CopyGate(cfg).check_copy_allowed(now=_at(19), active_sessions=1)
    assert dec.allowed is False and dec.reason == "TIME_LOCK"   # absolute


def test_protection_off_allows_outside_lock(tmp_path):
    cfg = _cfg(tmp_path, enabled=False)
    dec = CopyGate(cfg).check_copy_allowed(now=_at(10), active_sessions=9)
    assert dec.allowed is True and dec.reason == "PROTECTION_OFF"


def test_session_limit_blocks(tmp_path):
    cfg = _cfg(tmp_path, max_sessions_for_copy=2)
    dec = CopyGate(cfg).check_copy_allowed(now=_at(10), active_sessions=3)
    assert dec.allowed is False and dec.reason == "SESSION_LIMIT"


def test_no_token_blocks(tmp_path):
    cfg = _cfg(tmp_path)
    dec = CopyGate(cfg).check_copy_allowed(now=_at(10), active_sessions=1)
    assert dec.allowed is False and dec.reason == "NO_TOKEN"


def test_expired_token_blocks(tmp_path):
    cfg = _cfg(tmp_path)
    gate = CopyGate(cfg)
    now = _at(10)
    gate.write_token(CopyToken("t", "r", "u", now.isoformat(),
                               (now - timedelta(minutes=1)).isoformat()))
    dec = gate.check_copy_allowed(now=now, active_sessions=1)
    assert dec.allowed is False and dec.reason == "NO_TOKEN"


def test_valid_token_allows(tmp_path):
    cfg = _cfg(tmp_path)
    gate = CopyGate(cfg)
    now = _at(10)
    gate.write_token(CopyToken("tok9", "pull report", "ubuntu", now.isoformat(),
                               (now + timedelta(minutes=10)).isoformat()))
    dec = gate.check_copy_allowed(now=now, active_sessions=1)
    assert dec.allowed is True and dec.reason == "TOKEN_VALID"
    assert dec.token is not None and dec.token.token_id == "tok9"


# ── token store + audit log ──────────────────────────────────────────────────

def test_token_roundtrip_and_clear(tmp_path):
    cfg = _cfg(tmp_path)
    gate = CopyGate(cfg)
    assert gate.read_token() is None
    now = _at(10)
    gate.write_token(CopyToken("abc", "why", "ubuntu", now.isoformat(),
                               (now + timedelta(minutes=15)).isoformat()))
    got = gate.read_token()
    assert got is not None and got.token_id == "abc" and got.reason == "why"
    assert gate.clear_token() is True
    assert gate.read_token() is None


def test_audit_appends_json_lines(tmp_path):
    import json
    cfg = _cfg(tmp_path)
    gate = CopyGate(cfg)
    gate.audit("COPY_TOKEN_ISSUED", token_id="x1", reason="test")
    gate.audit("COPY_DENIED_TIME_LOCK", reason="test2")
    lines = (tmp_path / "copy_audit.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["event"] == "COPY_TOKEN_ISSUED" and rec["token_id"] == "x1"
    assert "ts" in rec and "user" in rec


# ── send_alert: email fallback when Telegram is unavailable ──────────────────
# (email is the only channel while Telegram is banned; copy work itself never
#  depends on any of this — the token is written before send_alert runs.)

def _flags(d):
    return list(d.glob("critical_alert_*.flag"))


def test_send_alert_emails_when_telegram_unavailable(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr("alerts.telegram_notifier.TelegramNotifier.from_env",
                        lambda *a, **k: None)
    cfg = _cfg(tmp_path, sentinel_dir=str(tmp_path))
    send_alert(cfg, "INFO", "Copy token issued", "body")
    flags = _flags(tmp_path)
    assert len(flags) == 1                                   # email fallback fired
    payload = json.loads(flags[0].read_text(encoding="utf-8"))
    assert payload["title"] == "Copy token issued"
    assert payload["context"]["severity"] == "INFO"         # correctly labelled, not CRITICAL


def test_send_alert_emails_when_telegram_send_fails(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    class _Stub:
        def send(self, **kw):
            return SimpleNamespace(sentinel_path=None, success=False)   # Telegram down
    monkeypatch.setattr("alerts.telegram_notifier.TelegramNotifier.from_env",
                        lambda *a, **k: _Stub())
    cfg = _cfg(tmp_path, sentinel_dir=str(tmp_path))
    send_alert(cfg, "WARNING", "Copy blocked (TIME_LOCK)", "body")
    flags = _flags(tmp_path)
    assert len(flags) == 1
    assert json.loads(flags[0].read_text())["context"]["severity"] == "WARNING"


def test_send_alert_no_email_spam_when_telegram_delivers(tmp_path, monkeypatch):
    from types import SimpleNamespace

    class _Stub:
        def send(self, **kw):
            return SimpleNamespace(sentinel_path=None, success=True)    # delivered
    monkeypatch.setattr("alerts.telegram_notifier.TelegramNotifier.from_env",
                        lambda *a, **k: _Stub())
    cfg = _cfg(tmp_path, sentinel_dir=str(tmp_path))
    send_alert(cfg, "INFO", "Copy token issued", "body")
    assert _flags(tmp_path) == []                            # no email when Telegram works


def test_send_alert_critical_single_sentinel_no_double(tmp_path, monkeypatch):
    from types import SimpleNamespace

    class _Stub:
        def send(self, **kw):  # send() itself wrote the CRITICAL sentinel (TG5)
            return SimpleNamespace(sentinel_path=tmp_path / "from_tg.flag", success=True)
    monkeypatch.setattr("alerts.telegram_notifier.TelegramNotifier.from_env",
                        lambda *a, **k: _Stub())
    cfg = _cfg(tmp_path, sentinel_dir=str(tmp_path))
    send_alert(cfg, "CRITICAL", "Copy bypass", "body")
    assert _flags(tmp_path) == []                            # no SECOND sentinel
