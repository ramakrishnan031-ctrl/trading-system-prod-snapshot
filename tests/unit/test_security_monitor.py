"""Tests for scripts/security_monitor.py (VM Security Manager Phase 1)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

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
