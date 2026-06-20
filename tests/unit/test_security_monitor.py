"""Tests for scripts/security_monitor.py (VM Security Manager Phase 1)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import scripts.security_monitor as sm
from scripts.security_monitor import (
    SecConfig,
    Finding,
    sha256_file,
    parse_line_ts,
    scan_authlog,
    check_authorized_keys,
    check_new_login_ips,
    check_failed_spike,
    check_sudo_events,
    check_watched_files,
    check_copy_protection_switch,
    check_copy_bypass,
    parse_ausearch_execve,
    is_outbound_copy,
    recent_allowed_copy_times,
    active_token_windows,
    _dedup,
)

_IST = timezone(timedelta(hours=5, minutes=30))


def _ts(dt: datetime) -> str:
    return dt.isoformat()


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_sha256_file_and_missing(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello")
    h1 = sha256_file(str(f))
    assert h1 and len(h1) == 64
    f.write_text("world")
    assert sha256_file(str(f)) != h1
    assert sha256_file(str(tmp_path / "nope")) is None


def test_parse_line_ts():
    line = "2026-06-19T22:43:11.803963+05:30 host sshd[1]: Accepted publickey for ubuntu from 1.2.3.4"
    ts = parse_line_ts(line)
    assert ts is not None and ts.year == 2026 and ts.hour == 22
    assert parse_line_ts("no timestamp here") is None


def test_scan_authlog_window_and_counts():
    now = datetime(2026, 6, 19, 22, 0, tzinfo=_IST)
    old = now - timedelta(hours=2)
    recent = now - timedelta(minutes=5)
    lines = [
        f"{_ts(old)} h sshd[1]: Accepted publickey for ubuntu from 9.9.9.9",      # too old
        f"{_ts(recent)} h sshd[2]: Accepted publickey for ubuntu from 1.2.3.4",
        f"{_ts(recent)} h sshd[3]: Invalid user admin from 5.6.7.8",
        f"{_ts(recent)} h sshd[4]: Failed password for root from 5.6.7.8",
        f"{_ts(recent)} h sshd[5]: Connection reset by authenticating user root 8.8.8.8 [preauth]",
        f"{_ts(recent)} h sudo:   ubuntu : PWD=/x ; USER=root ; COMMAND=/usr/bin/vi /etc/hosts",
    ]
    scan = scan_authlog(lines, since=now - timedelta(hours=1))
    assert scan["accepted_ips"] == {"1.2.3.4"}          # 9.9.9.9 excluded (too old)
    assert scan["failed"] == 2                            # Invalid user + Failed password
    assert scan["root_probes"] == 1
    assert scan["sudo_events"] == [("ubuntu", "/usr/bin/vi")]


# ── check_authorized_keys (hash-based; no ssh-keygen needed) ─────────────────

def _cfg(tmp_path, **kw) -> SecConfig:
    c = SecConfig()
    c.authorized_keys_path = str(tmp_path / "authorized_keys")
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def test_authkeys_no_change_no_finding(tmp_path):
    ak = tmp_path / "authorized_keys"
    ak.write_text("ssh-ed25519 AAAAfake rama@DESKTOP\n")
    cfg = _cfg(tmp_path)
    state = {}
    check_authorized_keys(cfg, state)          # seeds baseline
    out = check_authorized_keys(cfg, state)    # unchanged
    assert [f for f in out if f.key.startswith("authkeys:hash")] == []


def test_authkeys_change_is_critical(tmp_path):
    ak = tmp_path / "authorized_keys"
    ak.write_text("ssh-ed25519 AAAAfake rama@DESKTOP\n")
    cfg = _cfg(tmp_path)
    state = {}
    check_authorized_keys(cfg, state)          # baseline
    ak.write_text("ssh-ed25519 AAAAfake rama@DESKTOP\nssh-rsa AAAAevil attacker@x\n")
    out = check_authorized_keys(cfg, state)
    crit = [f for f in out if f.severity == "CRITICAL" and "NEW SSH KEY" in f.title]
    assert len(crit) == 1


def test_authkeys_missing_is_critical(tmp_path):
    cfg = _cfg(tmp_path)  # file does not exist
    out = check_authorized_keys(cfg, {})
    assert any(f.severity == "CRITICAL" and "missing" in f.title.lower() for f in out)


# ── new-IP, spike, sudo, files ──────────────────────────────────────────────

def test_new_ip_baseline_then_alert():
    cfg = SecConfig()
    state = {}
    scan = {"accepted_ips": {"1.1.1.1", "2.2.2.2"}}
    # baseline seeds without alerting
    out = check_new_login_ips(cfg, scan, state, baseline=True)
    assert out == []
    assert set(state["known_login_ips"]) == {"1.1.1.1", "2.2.2.2"}
    # a brand-new IP now alerts WARNING; known ones stay silent
    scan2 = {"accepted_ips": {"1.1.1.1", "3.3.3.3"}}
    out2 = check_new_login_ips(cfg, scan2, state, baseline=False)
    assert [f.key for f in out2] == ["newip:3.3.3.3"]
    assert out2[0].severity == "WARNING"


def test_failed_spike_threshold():
    cfg = SecConfig(failed_login_spike_threshold=100)
    now = datetime(2026, 6, 19, 22, 0, tzinfo=_IST)
    assert check_failed_spike(cfg, {"failed": 50}, now) == []
    out = check_failed_spike(cfg, {"failed": 250}, now)
    assert len(out) == 1 and out[0].severity == "WARNING"


def test_sudo_whitelist():
    cfg = SecConfig(sudo_whitelist_prefixes=["/usr/bin/systemctl"])
    scan = {"sudo_events": [("ubuntu", "/usr/bin/systemctl"), ("ubuntu", "/usr/bin/vi")]}
    out = check_sudo_events(cfg, scan)
    keys = [f.key for f in out]
    assert keys == ["sudo:/usr/bin/vi"]      # systemctl whitelisted out


def test_sudo_alert_disabled():
    cfg = SecConfig(sudo_alert=False)
    assert check_sudo_events(cfg, {"sudo_events": [("ubuntu", "/usr/bin/vi")]}) == []


def test_watched_file_change(tmp_path):
    target = tmp_path / "system_config.yaml"
    target.write_text("a: 1")
    cfg = SecConfig(watched_files=[{"path": str(target), "severity": "WARNING", "label": "system_config"}])
    state = {}
    check_watched_files(cfg, state)        # baseline
    assert check_watched_files(cfg, state) == []   # unchanged
    target.write_text("a: 2")
    out = check_watched_files(cfg, state)
    assert len(out) == 1 and out[0].severity == "WARNING" and "system_config" in out[0].title


def test_watched_file_skips_authorized_keys(tmp_path):
    ak = tmp_path / "authorized_keys"
    ak.write_text("x")
    cfg = SecConfig(watched_files=[{"path": str(ak), "severity": "CRITICAL", "label": "ssh_authorized_keys"}])
    state = {}
    check_watched_files(cfg, state)
    ak.write_text("y")
    # authorized_keys is owned by check_authorized_keys, not the file-hash check
    assert check_watched_files(cfg, state) == []


# ── dedup / cooldown ─────────────────────────────────────────────────────────

def test_dedup_cooldown():
    state = {}
    f = [Finding("WARNING", "newip:3.3.3.3", "t", "b")]
    now = 1_000_000.0
    fresh1 = _dedup(list(f), state, cooldown=3600, now_ts=now)
    assert len(fresh1) == 1                       # first time → emitted
    fresh2 = _dedup(list(f), state, cooldown=3600, now_ts=now + 60)
    assert fresh2 == []                            # within cooldown → suppressed
    fresh3 = _dedup(list(f), state, cooldown=3600, now_ts=now + 4000)
    assert len(fresh3) == 1                         # after cooldown → re-emitted


# ── config loading ───────────────────────────────────────────────────────────

def test_config_defaults_when_missing(tmp_path):
    cfg = SecConfig.load(tmp_path / "nope.yaml")
    assert cfg.enabled is True
    assert cfg.max_active_sessions == 2
    assert cfg.watched_files  # defaults populated


def test_config_loads_real_file():
    cfg = SecConfig.load(Path("config/security.yaml"))
    assert cfg.enabled is True
    assert cfg.expected_key_fingerprint.startswith("SHA256:")
    assert cfg.max_active_sessions == 2


def test_config_loads_copy_protection_block():
    cfg = SecConfig.load(Path("config/security.yaml"))
    assert cfg.copy_protection_enabled is True
    assert cfg.copy_audit_log_path.endswith("copy_audit.log")


# ── Phase 2: copy_protection ON->OFF switch ──────────────────────────────────

def test_copy_switch_seeds_silently_then_alerts_on_disable():
    now = datetime(2026, 6, 20, 12, 0, tzinfo=_IST)
    state = {}
    on = SecConfig(copy_protection_enabled=True)
    assert check_copy_protection_switch(on, state, now) == []       # seed
    assert state["copy_protection_enabled"] is True
    off = SecConfig(copy_protection_enabled=False)
    out = check_copy_protection_switch(off, state, now)
    assert len(out) == 1 and out[0].severity == "CRITICAL"
    assert "DISABLED" in out[0].title.upper()
    # persistent OFF must NOT re-alert (transition-based)
    assert check_copy_protection_switch(off, state, now) == []


def test_copy_switch_reenable_is_info():
    now = datetime(2026, 6, 20, 12, 0, tzinfo=_IST)
    state = {"copy_protection_enabled": False}
    out = check_copy_protection_switch(SecConfig(copy_protection_enabled=True), state, now)
    assert len(out) == 1 and out[0].severity == "INFO"


# ── Phase 2: auditd execve parsing + direction ───────────────────────────────

# Mirrors REAL `ausearch -k copy_attempt -i` output on the aarch64 VM:
# 2-digit year, " : " separator, unquoted exe/proctitle (verified live 20-Jun).
_AUSEARCH_SAMPLE = """----
type=PROCTITLE msg=audit(06/20/26 10:15:30.123:4567) : proctitle=scp /etc/hostname user@pc:/tmp/
type=CWD msg=audit(06/20/26 10:15:30.123:4567) : cwd=/home/ubuntu
type=EXECVE msg=audit(06/20/26 10:15:30.123:4567) : argc=3 a0=scp a1=/etc/hostname a2=user@pc:/tmp/
type=SYSCALL msg=audit(06/20/26 10:15:30.123:4567) : arch=aarch64 syscall=execve success=yes exit=0 auid=ubuntu uid=ubuntu comm=scp exe=/usr/bin/scp subj=unconfined key=copy_attempt
"""

# A 4-digit-year build must still parse (defensive: regex/strptime accept both).
_AUSEARCH_SAMPLE_4Y = (
    "type=PROCTITLE msg=audit(06/20/2026 09:05:00.000:42) : proctitle=rsync -a /data pc:/b\n"
    "type=SYSCALL msg=audit(06/20/2026 09:05:00.000:42) : exe=/usr/bin/rsync key=copy_attempt\n"
)


def test_parse_ausearch_execve():
    now = datetime(2026, 6, 20, 10, 20, tzinfo=_IST)
    events = parse_ausearch_execve(_AUSEARCH_SAMPLE, now)
    assert len(events) == 1
    ev = events[0]
    assert ev["id"] == "4567"
    assert ev["exe"] == "/usr/bin/scp"
    assert ev["cmd"] == "scp /etc/hostname user@pc:/tmp/"
    assert ev["ts"] is not None
    assert ev["ts"].year == 2026 and ev["ts"].hour == 10 and ev["ts"].minute == 15


def test_parse_ausearch_execve_four_digit_year():
    now = datetime(2026, 6, 20, 10, 20, tzinfo=_IST)
    events = parse_ausearch_execve(_AUSEARCH_SAMPLE_4Y, now)
    assert len(events) == 1 and events[0]["ts"].year == 2026
    assert events[0]["exe"] == "/usr/bin/rsync"


@pytest.mark.parametrize("exe,cmd,outbound", [
    ("/usr/bin/scp", "scp /etc/hostname user@pc:/tmp/", True),    # push out
    ("/usr/bin/scp", "scp -f /etc/passwd", True),                 # source mode (PC pulling)
    ("/usr/bin/scp", "scp -t /home/ubuntu/in/", False),           # sink mode (PC->VM push)
    ("/usr/bin/scp", "scp pc:/remote/f /local/f", True),          # host:path present
    ("/usr/bin/rsync", "rsync -a /data user@pc:/backup", True),
    ("/usr/bin/rsync", "rsync -a /data /local/backup", False),    # local only
    ("/usr/bin/sftp", "sftp user@pc", True),
])
def test_is_outbound_copy(exe, cmd, outbound):
    assert is_outbound_copy(exe, cmd) is outbound


def test_recent_allowed_copy_times(tmp_path):
    import json
    now = datetime(2026, 6, 20, 10, 15, 31, tzinfo=_IST)
    log = tmp_path / "copy_audit.log"
    fresh = {"event": "COPY_ALLOWED", "ts": (now - timedelta(seconds=2)).isoformat()}
    stale = {"event": "COPY_ALLOWED", "ts": (now - timedelta(hours=2)).isoformat()}
    other = {"event": "COPY_TOKEN_ISSUED", "ts": now.isoformat()}
    log.write_text("\n".join(json.dumps(r) for r in (fresh, stale, other)), encoding="utf-8")
    got = recent_allowed_copy_times(str(log), now, window_sec=120)
    assert len(got) == 1  # only the fresh COPY_ALLOWED within the window


# ── Phase 2: bypass detection (monkeypatched auditd) ─────────────────────────

def test_copy_bypass_flags_unauthorized_outbound(monkeypatch, tmp_path):
    now = datetime(2026, 6, 20, 10, 15, 31, tzinfo=_IST)
    ev = {"id": "4567", "ts": datetime(2026, 6, 20, 10, 15, 30, tzinfo=_IST),
          "exe": "/usr/bin/scp", "cmd": "scp /etc/hostname user@pc:/tmp/"}
    monkeypatch.setattr(sm, "ausearch_copy_attempts", lambda since, n: [ev])
    cfg = SecConfig(copy_audit_log_path=str(tmp_path / "none.log"))  # no COPY_ALLOWED
    out = check_copy_bypass(cfg, {}, now)
    assert len(out) == 1 and out[0].severity == "CRITICAL"
    assert "BYPASS" in out[0].title.upper()


def test_copy_bypass_silent_when_authorized(monkeypatch, tmp_path):
    import json
    now = datetime(2026, 6, 20, 10, 15, 31, tzinfo=_IST)
    ev = {"id": "4567", "ts": datetime(2026, 6, 20, 10, 15, 30, tzinfo=_IST),
          "exe": "/usr/bin/scp", "cmd": "scp /etc/hostname user@pc:/tmp/"}
    monkeypatch.setattr(sm, "ausearch_copy_attempts", lambda since, n: [ev])
    log = tmp_path / "copy_audit.log"
    log.write_text(json.dumps(
        {"event": "COPY_ALLOWED", "ts": datetime(2026, 6, 20, 10, 15, 30, tzinfo=_IST).isoformat()}),
        encoding="utf-8")
    cfg = SecConfig(copy_audit_log_path=str(log))
    assert check_copy_bypass(cfg, {}, now) == []  # matched a COPY_ALLOWED -> no alert


def test_copy_bypass_ignores_inbound_sink(monkeypatch, tmp_path):
    now = datetime(2026, 6, 20, 10, 15, 31, tzinfo=_IST)
    ev = {"id": "99", "ts": datetime(2026, 6, 20, 10, 15, 30, tzinfo=_IST),
          "exe": "/usr/bin/scp", "cmd": "scp -t /home/ubuntu/incoming/"}  # PC->VM push
    monkeypatch.setattr(sm, "ausearch_copy_attempts", lambda since, n: [ev])
    cfg = SecConfig(copy_audit_log_path=str(tmp_path / "none.log"))
    assert check_copy_bypass(cfg, {}, now) == []  # inbound is not a bypass


def test_active_token_windows(tmp_path):
    import json
    now = datetime(2026, 6, 20, 10, 30, tzinfo=_IST)
    log = tmp_path / "copy_audit.log"
    rows = [
        {"event": "COPY_TOKEN_ISSUED", "ts": (now - timedelta(minutes=5)).isoformat(),
         "expires_at": (now + timedelta(minutes=10)).isoformat()},          # fresh
        {"event": "COPY_TOKEN_ISSUED", "ts": (now - timedelta(hours=3)).isoformat(),
         "expires_at": (now - timedelta(hours=2)).isoformat()},             # expired > lookback
        {"event": "COPY_ALLOWED", "ts": now.isoformat()},                   # not a token issue
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    w = active_token_windows(str(log), now)
    assert len(w) == 1 and w[0][0] < now < w[0][1]


def test_copy_bypass_silent_within_token_window(monkeypatch, tmp_path):
    import json
    now = datetime(2026, 6, 20, 10, 15, 31, tzinfo=_IST)
    ev_ts = datetime(2026, 6, 20, 10, 15, 30, tzinfo=_IST)
    ev = {"id": "555", "ts": ev_ts, "exe": "/usr/bin/scp",
          "cmd": "scp /etc/hostname user@pc:/tmp/"}  # outbound
    monkeypatch.setattr(sm, "ausearch_copy_attempts", lambda since, n: [ev])
    log = tmp_path / "copy_audit.log"
    log.write_text(json.dumps({
        "event": "COPY_TOKEN_ISSUED",
        "ts": (ev_ts - timedelta(minutes=2)).isoformat(),
        "expires_at": (ev_ts + timedelta(minutes=13)).isoformat()}), encoding="utf-8")
    cfg = SecConfig(copy_audit_log_path=str(log))
    # a deliberate copy covered by a request-copy token window is NOT a bypass
    assert check_copy_bypass(cfg, {}, now) == []


# ── _send sentinel dedupe (one CRITICAL -> exactly one email) ─────────────────

def _crit() -> Finding:
    return Finding("CRITICAL", "k", "Title", "Body")


def test_send_no_double_sentinel_when_telegram_wrote_it(monkeypatch, tmp_path):
    from types import SimpleNamespace

    class _Stub:
        def send(self, **kw):  # mimics TelegramNotifier.send writing the sentinel (TG5)
            return SimpleNamespace(sentinel_path=tmp_path / "from_telegram.flag")
    monkeypatch.setattr("alerts.telegram_notifier.TelegramNotifier.from_env",
                        lambda *a, **k: _Stub())
    sm._send(_crit(), SecConfig(sentinel_dir=str(tmp_path)))
    # telegram already wrote one -> NO extra explicit sentinel
    assert list(tmp_path.glob("critical_alert_*.flag")) == []


def test_send_writes_sentinel_when_telegram_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr("alerts.telegram_notifier.TelegramNotifier.from_env",
                        lambda *a, **k: None)
    sm._send(_crit(), SecConfig(sentinel_dir=str(tmp_path)))
    # no telegram -> exactly one explicit sentinel so email still fires
    assert len(list(tmp_path.glob("critical_alert_*.flag"))) == 1


def test_send_writes_sentinel_when_telegram_disabled(monkeypatch, tmp_path):
    from types import SimpleNamespace

    class _Stub:
        def send(self, **kw):  # master switch off: send() no-ops, sentinel_path=None
            return SimpleNamespace(sentinel_path=None)
    monkeypatch.setattr("alerts.telegram_notifier.TelegramNotifier.from_env",
                        lambda *a, **k: _Stub())
    sm._send(_crit(), SecConfig(sentinel_dir=str(tmp_path)))
    # disabled telegram still leaves email working via exactly one sentinel
    assert len(list(tmp_path.glob("critical_alert_*.flag"))) == 1


# ── Phase 3: persist copy events to the audit log (single source for EOD) ─────

def test_maybe_copy_audit_persists_only_copy_events(tmp_path):
    import json
    cfg = SecConfig(copy_audit_log_path=str(tmp_path / "copy_audit.log"))
    now = datetime(2026, 6, 20, 12, 0, tzinfo=_IST)
    sm._maybe_copy_audit(cfg, Finding("CRITICAL", "copybypass:99", "COPY BYPASS DETECTED", "scp x pc:/y"), now)
    sm._maybe_copy_audit(cfg, Finding("CRITICAL", "copyprot:disabled", "COPY PROTECTION DISABLED", "off"), now)
    sm._maybe_copy_audit(cfg, Finding("INFO", "copyprot:enabled", "re-enabled", "on"), now)
    sm._maybe_copy_audit(cfg, Finding("WARNING", "newip:1.2.3.4", "x", "y"), now)   # not a copy event
    lines = (tmp_path / "copy_audit.log").read_text(encoding="utf-8").splitlines()
    events = [json.loads(l)["event"] for l in lines]
    assert events == ["COPY_BYPASS_DETECTED", "COPY_PROTECTION_DISABLED", "COPY_PROTECTION_ENABLED"]
