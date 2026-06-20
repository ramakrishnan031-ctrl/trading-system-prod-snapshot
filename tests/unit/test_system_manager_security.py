"""Tests for the System Manager 9th check — VM copy protection (Phase 3)."""
from __future__ import annotations

import json

import scripts.system_manager as sysm


# ── pure: copy-audit reader ──────────────────────────────────────────────────

def test_read_copy_audit_counts_and_day_filter(tmp_path):
    log = tmp_path / "copy_audit.log"
    rows = [
        {"ts": "2026-06-20T10:00:00+05:30", "event": "COPY_TOKEN_ISSUED"},
        {"ts": "2026-06-20T10:05:00+05:30", "event": "COPY_ALLOWED"},
        {"ts": "2026-06-20T10:06:00+05:30", "event": "COPY_DENIED_NO_TOKEN"},
        {"ts": "2026-06-20T10:07:00+05:30", "event": "COPY_BYPASS_DETECTED"},
        {"ts": "2026-06-20T10:08:00+05:30", "event": "COPY_PROTECTION_DISABLED"},
        {"ts": "2026-06-19T10:00:00+05:30", "event": "COPY_BYPASS_DETECTED"},  # other day
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    counts, bypasses, disables = sysm._read_copy_audit(log, "2026-06-20")
    assert counts["COPY_TOKEN_ISSUED"] == 1 and counts["COPY_ALLOWED"] == 1
    assert bypasses == 1 and disables == 1   # only today's


def test_read_copy_audit_missing_file(tmp_path):
    counts, b, d = sysm._read_copy_audit(tmp_path / "nope.log", "2026-06-20")
    assert counts == {} and b == 0 and d == 0


# ── security_check integration (auditd/state helpers monkeypatched) ──────────

def _security_yaml(config_dir, audit_log, enabled=True):
    (config_dir / "security.yaml").write_text(
        "copy_protection:\n"
        f"  enabled: {'true' if enabled else 'false'}\n"
        f"  audit_log_path: {audit_log}\n",
        encoding="utf-8")


def _patch_env(monkeypatch, rules=3, age=10.0):
    monkeypatch.setattr(sysm, "_auditd_copy_rules", lambda: rules)
    monkeypatch.setattr(sysm, "_security_state_age_sec", lambda root: age)


def test_security_check_flags_bypass_and_disable_no_softkill(tmp_path, monkeypatch):
    cfgdir = tmp_path / "config"; cfgdir.mkdir()
    audit = tmp_path / "copy_audit.log"
    _security_yaml(cfgdir, audit, enabled=True)
    audit.write_text("\n".join(json.dumps(r) for r in [
        {"ts": "2026-06-20T10:07:00+05:30", "event": "COPY_BYPASS_DETECTED"},
        {"ts": "2026-06-20T10:08:00+05:30", "event": "COPY_PROTECTION_DISABLED"},
    ]), encoding="utf-8")
    _patch_env(monkeypatch)
    res = sysm.security_check("2026-06-20", tmp_path, cfgdir)
    assert res.violations == 2              # bypass + disable
    assert res.soft_kill_reason is None     # security escalates but never halts trading


def test_security_check_clean_day(tmp_path, monkeypatch):
    cfgdir = tmp_path / "config"; cfgdir.mkdir()
    audit = tmp_path / "copy_audit.log"
    _security_yaml(cfgdir, audit, enabled=True)
    audit.write_text("", encoding="utf-8")
    _patch_env(monkeypatch)
    res = sysm.security_check("2026-06-20", tmp_path, cfgdir)
    assert res.violations == 0


def test_security_check_disabled_is_violation(tmp_path, monkeypatch):
    cfgdir = tmp_path / "config"; cfgdir.mkdir()
    audit = tmp_path / "copy_audit.log"
    _security_yaml(cfgdir, audit, enabled=False)
    audit.write_text("", encoding="utf-8")
    _patch_env(monkeypatch)
    res = sysm.security_check("2026-06-20", tmp_path, cfgdir)
    assert res.violations == 1 and res.soft_kill_reason is None


def test_security_check_stale_watcher_warns(tmp_path, monkeypatch):
    cfgdir = tmp_path / "config"; cfgdir.mkdir()
    audit = tmp_path / "copy_audit.log"
    _security_yaml(cfgdir, audit, enabled=True)
    audit.write_text("", encoding="utf-8")
    _patch_env(monkeypatch, rules=3, age=9999.0)   # watcher stale
    res = sysm.security_check("2026-06-20", tmp_path, cfgdir)
    assert res.warnings >= 1 and res.violations == 0


def test_security_check_degraded_auditd_warns(tmp_path, monkeypatch):
    cfgdir = tmp_path / "config"; cfgdir.mkdir()
    audit = tmp_path / "copy_audit.log"
    _security_yaml(cfgdir, audit, enabled=True)
    audit.write_text("", encoding="utf-8")
    _patch_env(monkeypatch, rules=0, age=10.0)     # rules not loaded
    res = sysm.security_check("2026-06-20", tmp_path, cfgdir)
    assert res.warnings >= 1
