"""
tests/unit/test_preflight_parity.py -- parity: the ported checks must match the
legacy scripts/premarket_healthcheck.py bit-for-bit on detection BEFORE the old
script is retired (Fork 3, 21-Jun-2026).

The 5 ported checks: config_files_present, required_secrets, db_schema_version,
vm_disk_data, vm_ntp_sync. Where the new check is intentionally SAFER (the schema
check reads schema_meta raw instead of constructing a StateStore that could
migrate), parity is asserted on the DECISION boundary + the shared threshold/
constant, not the implementation.
"""
from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest

import scripts.premarket_healthcheck as pm
from scripts.preflight.base import CheckContext, Status
from scripts.preflight.checks import config_integrity, database, vm_health

GB = 1024 ** 3


def _ctx(tmp_path, **kw):
    kw.setdefault("as_of_date", date.today())
    return CheckContext(config_dir=kw.pop("config_dir", tmp_path / "config"),
                        db_path=kw.pop("db_path", tmp_path / "trading_system.db"), **kw)


# ── config files: identical expected set ─────────────────────────────────────────
def test_config_file_list_parity():
    assert config_integrity.config_files_for(date.today().year) == list(pm.CONFIG_FILES)


def test_required_secrets_parity():
    assert tuple(config_integrity.REQUIRED_SECRETS) == tuple(pm.REQUIRED_SECRETS)


def test_secrets_detection_parity(tmp_path, monkeypatch):
    # all set -> both clean
    for s in pm.REQUIRED_SECRETS:
        monkeypatch.setenv(s, "x")
    assert pm.check_secrets() == []
    assert config_integrity.RequiredSecretsCheck().run(_ctx(tmp_path)).status is Status.PASS
    # one missing -> both flag the SAME secret
    monkeypatch.delenv(pm.REQUIRED_SECRETS[0], raising=False)
    pm_missing = pm.check_secrets()
    res = config_integrity.RequiredSecretsCheck().run(_ctx(tmp_path))
    assert pm_missing == [pm.REQUIRED_SECRETS[0]]
    assert res.status is Status.FAIL and res.metrics["missing"] == pm_missing


# ── disk: same 2GB threshold + same decision ─────────────────────────────────────
def test_disk_threshold_parity():
    # premarket default min_gb=2.0
    assert vm_health.DATA_DISK_MIN_BYTES == 2 * GB


def test_disk_decision_parity(tmp_path, monkeypatch):
    def low(_):  # free 1GB < 2GB
        return (10 * GB, 9 * GB, 1 * GB)
    def high(_):  # free 5GB > 2GB
        return (10 * GB, 5 * GB, 5 * GB)

    monkeypatch.setattr(shutil, "disk_usage", low)
    assert pm.check_disk_space()[0] is False
    assert vm_health.VmDiskDataCheck().run(_ctx(tmp_path)).status is Status.FAIL

    monkeypatch.setattr(shutil, "disk_usage", high)
    assert pm.check_disk_space()[0] is True
    assert vm_health.VmDiskDataCheck().run(_ctx(tmp_path)).status is Status.PASS


# ── clock: same 30s fail threshold (premarket's only blocking case) ──────────────
def test_ntp_fail_threshold_parity():
    assert vm_health.NTP_FAIL_SEC == 30.0


def test_ntp_decision_parity(tmp_path, monkeypatch):
    # premarket fails only when skew > 30s; mine must FAIL there too, and must NOT
    # FAIL where premarket passed (<=30s -> PASS/WARN, both non-blocking).
    monkeypatch.setattr(vm_health, "_ntp_skew_seconds", lambda: 45.0)
    assert vm_health.VmNtpSyncCheck().run(_ctx(tmp_path)).status is Status.FAIL
    monkeypatch.setattr(vm_health, "_ntp_skew_seconds", lambda: 10.0)
    assert vm_health.VmNtpSyncCheck().run(_ctx(tmp_path)).status is not Status.FAIL


# ── schema: shared EXPECTED constant + decision boundary ─────────────────────────
def test_schema_expected_constant_parity():
    from core.state_store import EXPECTED_SCHEMA_VERSION
    assert database.expected_schema_version() == int(EXPECTED_SCHEMA_VERSION)


def test_schema_decision_boundary(tmp_path):
    import sqlite3
    exp = database.expected_schema_version()

    def _mk(version):
        db = tmp_path / f"db_{version}.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO schema_meta VALUES('schema_version', ?)", (str(version),))
        conn.commit()
        conn.close()
        return db

    # == PASS (shares the EXPECTED constant with premarket). Direction matters
    # post-Fix-1 (21-Jun): DB<code is the NORMAL pre-migration state -> WARN
    # (premarket alarmed on any mismatch -> a deliberate de-escalation that removes
    # a false-alarm every migration morning); DB>code is dangerous -> FAIL.
    assert database.DbSchemaVersionCheck().run(_ctx(tmp_path, db_path=_mk(exp))).status is Status.PASS
    assert database.DbSchemaVersionCheck().run(_ctx(tmp_path, db_path=_mk(exp - 1))).status is Status.WARN
    assert database.DbSchemaVersionCheck().run(_ctx(tmp_path, db_path=_mk(exp + 1))).status is Status.FAIL
