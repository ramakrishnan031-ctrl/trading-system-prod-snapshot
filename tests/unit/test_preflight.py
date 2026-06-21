"""
tests/unit/test_preflight.py -- Pre-flight orchestrator + framework + first-cut checks.

Covers: the Check contract, the 10 Phase-A checks (PASS/FAIL/WARN/SKIP paths via
monkeypatched readers), the safe auto-fix engine (whitelist / dry-run / timeout /
recheck-clears-critical / idempotency), the atomic sentinel, and run_phase rollup
+ holiday short-circuit. Parity vs premarket_healthcheck lives in
tests/unit/test_preflight_parity.py.
"""
from __future__ import annotations

import sqlite3
import time
from datetime import date
from pathlib import Path

import pytest

from scripts.preflight import autofix, orchestrator, sentinel
from scripts.preflight.base import (
    Check, CheckContext, CheckResult, Criticality, FixResult, Status,
)
from scripts.preflight.checks import broker, config_integrity, database, security, services, vm_health
from scripts.preflight.report import PreflightReport, render_terminal

GB = 1024 ** 3


# ── fixtures / helpers ──────────────────────────────────────────────────────────
def make_ctx(tmp_path: Path, **kw) -> CheckContext:
    db = kw.pop("db_path", tmp_path / "data_store" / "trading_system.db")
    cfg = kw.pop("config_dir", tmp_path / "config")
    kw.setdefault("as_of_date", date(2026, 6, 22))
    return CheckContext(config_dir=cfg, db_path=db, **kw)


def make_db(tmp_path: Path, version=None, journal="wal", with_meta=True) -> Path:
    if version is None:
        version = database.expected_schema_version()
    d = tmp_path / "data_store"
    d.mkdir(parents=True, exist_ok=True)
    db = d / "trading_system.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(f"PRAGMA journal_mode={journal}")
        if with_meta:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_meta(key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("INSERT OR REPLACE INTO schema_meta(key,value) VALUES('schema_version',?)",
                         (str(version),))
        else:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_meta(key TEXT PRIMARY KEY, value TEXT)")
        # minimal state tables so Phase-A state checks have something to read
        conn.execute("CREATE TABLE IF NOT EXISTS kill_switch_state (id INTEGER PRIMARY KEY, "
                     "state TEXT, reason TEXT, triggered_at TEXT, triggered_by TEXT)")
        conn.execute("INSERT OR IGNORE INTO kill_switch_state(id,state) VALUES(1,'INACTIVE')")
        conn.execute("CREATE TABLE IF NOT EXISTS trades (trade_id TEXT PRIMARY KEY, status TEXT, "
                     "needs_tgt_retry INTEGER DEFAULT 0)")
        conn.execute("CREATE TABLE IF NOT EXISTS orders (order_id TEXT PRIMARY KEY, status TEXT)")
        conn.commit()
    finally:
        conn.close()
    return db


def make_config_dir(tmp_path: Path, year=2026, missing=()) -> Path:
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    for f in config_integrity.config_files_for(year):
        if f in missing:
            continue
        (cfg / f).write_text("# test\n", encoding="utf-8")
    return cfg


class _DummyCheck(Check):
    name = "dummy"
    group = "Test"
    criticality = Criticality.CRITICAL

    def __init__(self, status=Status.PASS, fixable=False, fix_action="", fix_ok=True):
        self._status = status
        self.auto_fixable = fixable
        self.fix_action = fix_action
        self._fix_ok = fix_ok
        self._fixed = False

    def run(self, ctx):
        if self._fixed and self._fix_ok:
            return CheckResult(Status.PASS, "fixed now")
        return CheckResult(self._status, "dummy detail")

    def fix(self, ctx):
        self._fixed = True
        return FixResult(self._fix_ok, action=self.fix_action,
                         before_state="x", after_state="y",
                         error_msg="" if self._fix_ok else "nope")


# ── base contract ───────────────────────────────────────────────────────────────
def test_checkresult_ok_property():
    assert CheckResult(Status.PASS).ok
    assert CheckResult(Status.WARN).ok
    assert CheckResult(Status.AUTOFIXED).ok
    assert CheckResult(Status.SKIPPED).ok
    assert not CheckResult(Status.FAIL).ok


def test_check_helpers():
    c = _DummyCheck()
    assert c._passed("x").status is Status.PASS
    assert c._failed("x").status is Status.FAIL
    assert c._warn("x").status is Status.WARN
    assert c._skipped("x").status is Status.SKIPPED


# ── VM Health ────────────────────────────────────────────────────────────────────
def test_vm_ram_pass_fail_warn(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    monkeypatch.setattr(vm_health, "_mem_available_bytes", lambda: 2 * GB)
    assert vm_health.VmRamCheck().run(ctx).status is Status.PASS
    monkeypatch.setattr(vm_health, "_mem_available_bytes", lambda: GB // 2)
    assert vm_health.VmRamCheck().run(ctx).status is Status.FAIL

    def _boom():
        raise OSError("no /proc")
    monkeypatch.setattr(vm_health, "_mem_available_bytes", _boom)
    assert vm_health.VmRamCheck().run(ctx).status is Status.WARN


def test_vm_disk_root(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    monkeypatch.setattr(vm_health, "_disk_usage", lambda p: (10 * GB, 40.0))
    assert vm_health.VmDiskRootCheck().run(ctx).status is Status.PASS
    monkeypatch.setattr(vm_health, "_disk_usage", lambda p: (1 * GB, 40.0))  # low free
    assert vm_health.VmDiskRootCheck().run(ctx).status is Status.FAIL
    monkeypatch.setattr(vm_health, "_disk_usage", lambda p: (10 * GB, 95.0))  # high pct
    assert vm_health.VmDiskRootCheck().run(ctx).status is Status.FAIL


def test_vm_disk_data(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    monkeypatch.setattr(vm_health, "_disk_usage", lambda p: (10 * GB, 20.0))
    assert vm_health.VmDiskDataCheck().run(ctx).status is Status.PASS
    monkeypatch.setattr(vm_health, "_disk_usage", lambda p: (GB // 2, 20.0))
    assert vm_health.VmDiskDataCheck().run(ctx).status is Status.FAIL


def test_vm_ntp(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    monkeypatch.setattr(vm_health, "_ntp_skew_seconds", lambda: 0.10)
    assert vm_health.VmNtpSyncCheck().run(ctx).status is Status.PASS
    monkeypatch.setattr(vm_health, "_ntp_skew_seconds", lambda: 1.0)
    assert vm_health.VmNtpSyncCheck().run(ctx).status is Status.WARN
    monkeypatch.setattr(vm_health, "_ntp_skew_seconds", lambda: 45.0)
    assert vm_health.VmNtpSyncCheck().run(ctx).status is Status.FAIL
    monkeypatch.setattr(vm_health, "_ntp_skew_seconds", lambda: None)
    assert vm_health.VmNtpSyncCheck().run(ctx).status is Status.SKIPPED


# ── Config Integrity ─────────────────────────────────────────────────────────────
def test_config_files_present(tmp_path):
    cfg = make_config_dir(tmp_path)
    ctx = make_ctx(tmp_path, config_dir=cfg)
    assert config_integrity.ConfigFilesPresentCheck().run(ctx).status is Status.PASS


def test_config_files_missing(tmp_path):
    cfg = make_config_dir(tmp_path, missing=("system_config.yaml",))
    ctx = make_ctx(tmp_path, config_dir=cfg)
    res = config_integrity.ConfigFilesPresentCheck().run(ctx)
    assert res.status is Status.FAIL
    assert "system_config.yaml" in res.detail


def test_required_secrets(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    for s in config_integrity.REQUIRED_SECRETS:
        monkeypatch.setenv(s, "x")
    assert config_integrity.RequiredSecretsCheck().run(ctx).status is Status.PASS
    monkeypatch.delenv(config_integrity.REQUIRED_SECRETS[0], raising=False)
    res = config_integrity.RequiredSecretsCheck().run(ctx)
    assert res.status is Status.FAIL
    assert config_integrity.REQUIRED_SECRETS[0] in res.detail


# ── Database ─────────────────────────────────────────────────────────────────────
def test_db_file_exists(tmp_path):
    db = make_db(tmp_path)
    assert database.DbFileExistsCheck().run(make_ctx(tmp_path, db_path=db)).status is Status.PASS
    missing = tmp_path / "nope.db"
    assert database.DbFileExistsCheck().run(make_ctx(tmp_path, db_path=missing)).status is Status.FAIL


def test_db_schema_version_equal_passes(tmp_path):
    exp = database.expected_schema_version()
    db = make_db(tmp_path, version=exp)
    assert database.DbSchemaVersionCheck().run(make_ctx(tmp_path, db_path=db)).status is Status.PASS


def test_db_schema_version_db_behind_code_not_critical(tmp_path):
    # DB < code is the NORMAL pre-migration state at Phase A -> WARN, never CRITICAL.
    exp = database.expected_schema_version()
    db = make_db(tmp_path, version=exp - 1)
    res = database.DbSchemaVersionCheck().run(make_ctx(tmp_path, db_path=db))
    assert res.status is Status.WARN
    assert "migration pending" in res.detail


def test_db_schema_version_db_ahead_of_code_is_critical(tmp_path):
    # DB > code => code rolled back without a DB rollback => blocking CRITICAL.
    exp = database.expected_schema_version()
    db = make_db(tmp_path, version=exp + 1)
    chk = database.DbSchemaVersionCheck()
    res = chk.run(make_ctx(tmp_path, db_path=db))
    assert res.status is Status.FAIL and chk.criticality is Criticality.CRITICAL
    assert "code older than DB" in res.detail


def test_db_schema_version_missing_row(tmp_path):
    db = make_db(tmp_path, with_meta=False)  # schema_meta exists but no row
    res = database.DbSchemaVersionCheck().run(make_ctx(tmp_path, db_path=db))
    assert res.status is Status.FAIL


def test_db_schema_check_does_not_migrate(tmp_path):
    """The schema check must NOT mutate the DB (no StateStore init)."""
    db = make_db(tmp_path, version=database.expected_schema_version() - 1)
    before = db.read_bytes()
    database.DbSchemaVersionCheck().run(make_ctx(tmp_path, db_path=db))
    assert db.read_bytes() == before  # untouched


def test_db_journal_mode_and_fix(tmp_path):
    db = make_db(tmp_path, journal="delete")
    ctx = make_ctx(tmp_path, db_path=db)
    chk = database.DbJournalModeWalCheck()
    assert chk.run(ctx).status is Status.FAIL
    fr = chk.fix(ctx)
    assert fr.success
    assert chk.run(ctx).status is Status.PASS


def test_db_writable(tmp_path):
    db = make_db(tmp_path)
    assert database.DbWritableCheck().run(make_ctx(tmp_path, db_path=db)).status is Status.PASS
    assert database.DbWritableCheck().run(
        make_ctx(tmp_path, db_path=tmp_path / "nope.db")).status is Status.FAIL


# ── Auto-fix engine ──────────────────────────────────────────────────────────────
def test_autofix_success_clears_critical(tmp_path, monkeypatch):
    monkeypatch.setattr(autofix, "_audit", lambda *a, **k: None)
    chk = _DummyCheck(status=Status.FAIL, fixable=True, fix_action="set_journal_mode_wal")
    out = autofix.attempt(chk, make_ctx(tmp_path))
    assert out.attempted and out.final_status is Status.AUTOFIXED


def test_autofix_refuses_non_whitelisted(tmp_path, monkeypatch):
    monkeypatch.setattr(autofix, "_audit", lambda *a, **k: None)
    chk = _DummyCheck(status=Status.FAIL, fixable=True, fix_action="rm_rf_everything")
    out = autofix.attempt(chk, make_ctx(tmp_path))
    assert not out.attempted and "whitelist" in out.refused_reason


def test_autofix_skips_non_fixable(tmp_path):
    chk = _DummyCheck(status=Status.FAIL, fixable=False)
    out = autofix.attempt(chk, make_ctx(tmp_path))
    assert not out.attempted


def test_autofix_dry_run_never_mutates(tmp_path):
    chk = _DummyCheck(status=Status.FAIL, fixable=True, fix_action="set_journal_mode_wal")
    out = autofix.attempt(chk, make_ctx(tmp_path, dry_run=True))
    assert not out.attempted and not chk._fixed


def test_autofix_fail_keeps_critical(tmp_path, monkeypatch):
    monkeypatch.setattr(autofix, "_audit", lambda *a, **k: None)
    chk = _DummyCheck(status=Status.FAIL, fixable=True,
                      fix_action="set_journal_mode_wal", fix_ok=False)
    out = autofix.attempt(chk, make_ctx(tmp_path))
    assert out.attempted and out.final_status is Status.FAIL


def test_autofix_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(autofix, "_audit", lambda *a, **k: None)

    class _Slow(_DummyCheck):
        def fix(self, ctx):
            time.sleep(0.5)
            return FixResult(True, action=self.fix_action)

    chk = _Slow(status=Status.FAIL, fixable=True, fix_action="set_journal_mode_wal")
    out = autofix.attempt(chk, make_ctx(tmp_path), timeout_sec=0.2)
    assert out.attempted and out.final_status is Status.FAIL
    assert "timed out" in out.fix_result.error_msg


def test_autofix_idempotent(tmp_path):
    db = make_db(tmp_path, journal="delete")
    ctx = make_ctx(tmp_path, db_path=db)
    chk = database.DbJournalModeWalCheck()
    a = chk.fix(ctx)
    b = chk.fix(ctx)
    assert a.success and b.success
    assert chk.run(ctx).status is Status.PASS  # same end state


def test_autofix_audit_writes_jsonl(tmp_path):
    chk = _DummyCheck(status=Status.FAIL, fixable=True, fix_action="set_journal_mode_wal")
    out = autofix.attempt(chk, make_ctx(tmp_path), run_id="rid1", audit_dir=tmp_path / "logs")
    assert out.attempted
    files = list((tmp_path / "logs").glob("preflight_autofix_*.jsonl"))
    assert files and "rid1" in files[0].read_text(encoding="utf-8")


# ── Sentinel ─────────────────────────────────────────────────────────────────────
def test_sentinel_roundtrip(tmp_path):
    p = tmp_path / "preflight" / "today.json"
    s = sentinel.Sentinel(run_date="2026-06-22", overall_status=sentinel.READY,
                          critical_count=0, alert_id="abc")
    sentinel.write(s, p)
    loaded = sentinel.load(p)
    assert loaded.run_date == "2026-06-22" and loaded.alert_id == "abc"


def test_sentinel_missing_returns_default(tmp_path):
    s = sentinel.load(tmp_path / "nope.json")
    assert s.overall_status == sentinel.READY and s.phase_a_status == sentinel.NOT_STARTED


def test_sentinel_atomic_no_tmp_left(tmp_path):
    p = tmp_path / "preflight" / "today.json"
    sentinel.write(sentinel.Sentinel(run_date="2026-06-22"), p)
    leftovers = list((tmp_path / "preflight").glob(".today.*.tmp"))
    assert not leftovers


# ── Orchestrator run_phase ───────────────────────────────────────────────────────
def test_run_phase_all_pass(tmp_path):
    checks = [_DummyCheck(Status.PASS), _DummyCheck(Status.PASS)]
    rep = orchestrator.run_phase(make_ctx(tmp_path), checks, "rid")
    assert rep.total == 2 and rep.passed == 2
    assert rep.overall_status == sentinel.READY and rep.severity == "INFO"


def test_run_phase_critical_fail(tmp_path):
    checks = [_DummyCheck(Status.FAIL)]  # CRITICAL by default
    rep = orchestrator.run_phase(make_ctx(tmp_path), checks, "rid")
    assert rep.failed_critical == 1
    assert rep.overall_status == sentinel.CRITICAL_FAILURE and rep.severity == "CRITICAL"
    assert rep.blocking_failures


def test_run_phase_warn_only(tmp_path):
    warn_check = _DummyCheck(Status.WARN)
    warn_check.criticality = Criticality.WARN
    rep = orchestrator.run_phase(make_ctx(tmp_path), [warn_check], "rid")
    assert rep.warnings == 1 and rep.overall_status == sentinel.READY_WITH_WARNINGS


def test_run_phase_autofix_marks_autofixed(tmp_path, monkeypatch):
    monkeypatch.setattr(autofix, "_audit", lambda *a, **k: None)
    db = make_db(tmp_path, journal="delete")
    ctx = make_ctx(tmp_path, db_path=db)
    rep = orchestrator.run_phase(ctx, [database.DbJournalModeWalCheck()], "rid")
    rec = rep.records[0]
    assert rec.status is Status.AUTOFIXED and rec.fix_result == "SUCCESS"
    assert rep.failed_critical == 0


def test_run_phase_check_that_raises_becomes_fail(tmp_path):
    class _Boom(_DummyCheck):
        def run(self, ctx):
            raise RuntimeError("kaboom")
    rep = orchestrator.run_phase(make_ctx(tmp_path), [_Boom()], "rid")
    assert rep.records[0].status is Status.FAIL and "kaboom" in rep.records[0].detail


def test_render_terminal_smoke(tmp_path):
    rep = orchestrator.run_phase(make_ctx(tmp_path), [_DummyCheck(Status.PASS)], "rid")
    out = render_terminal(rep)
    assert "PRE-FLIGHT" in out and "READY" in out


# ── main() holiday guard + happy path ────────────────────────────────────────────
def test_main_holiday_short_circuit(tmp_path, monkeypatch, capsys):
    import utils.holiday_guard as hg
    monkeypatch.setattr(hg, "is_trading_day", lambda d, c: False)
    rc = main_args(tmp_path, monkeypatch, as_of="2026-06-21")
    assert rc == 0
    assert "skipped" in capsys.readouterr().out.lower()


def test_main_happy_path_writes_sentinel(tmp_path, monkeypatch):
    import utils.holiday_guard as hg
    monkeypatch.setattr(hg, "is_trading_day", lambda d, c: True)
    monkeypatch.setattr(vm_health, "_mem_available_bytes", lambda: 2 * GB)
    monkeypatch.setattr(vm_health, "_disk_usage", lambda p: (20 * GB, 30.0))
    monkeypatch.setattr(vm_health, "_ntp_skew_seconds", lambda: 0.05)
    monkeypatch.setattr(services, "_is_active", lambda s: True)   # no systemctl on PC
    monkeypatch.setattr(autofix, "_audit", lambda *a, **k: None)  # no stray audit files
    monkeypatch.setattr(security, "_current_hour_ist", lambda: 10)  # deterministic (outside lock)
    monkeypatch.setattr(broker, "_get_public_ip", lambda: "1.2.3.4")
    monkeypatch.setattr(broker, "_file_mdate", lambda p: date(2026, 6, 22))  # token/instruments fresh
    for s in config_integrity.REQUIRED_SECRETS:
        monkeypatch.setenv(s, "x")
    db = make_db(tmp_path)
    cfg = Path("config")  # the REAL shipped config -> validates app_config/strategies/cron_registry/security
    # project root = db.parent.parent = tmp_path; pre-create dirs + broker/security inputs
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "data_store" / "cron_marks").mkdir(parents=True, exist_ok=True)
    (db.parent / "session").mkdir(parents=True, exist_ok=True)
    (db.parent / "session" / "zerodha_token.json").write_text('{"access_token": "x"}', encoding="utf-8")
    (db.parent / "security_state.json").write_text("{}", encoding="utf-8")  # fresh watcher state
    sp = tmp_path / "preflight" / "today.json"
    rc = orchestrator.main([
        "--phase", "A", "--as-of-date", "2026-06-22",
        "--db-path", str(db), "--config-dir", str(cfg),
        "--sentinel-path", str(sp),
    ])
    assert rc == 0
    s = sentinel.load(sp)
    assert s.run_date == "2026-06-22"
    assert s.phase_a_status in (sentinel.PASSED, sentinel.WARN)
    assert s.overall_status in (sentinel.READY, sentinel.READY_WITH_WARNINGS)


def main_args(tmp_path, monkeypatch, as_of):
    """Helper: run main() with a minimal valid arg set."""
    db = make_db(tmp_path)
    cfg = make_config_dir(tmp_path)
    sp = tmp_path / "preflight" / "today.json"
    return orchestrator.main([
        "--phase", "A", "--as-of-date", as_of,
        "--db-path", str(db), "--config-dir", str(cfg),
        "--sentinel-path", str(sp),
    ])
